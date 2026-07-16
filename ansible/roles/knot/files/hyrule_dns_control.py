#!/usr/bin/env python3
"""Small, HMAC-authenticated control plane for Hyrule customer DNS zones.

The public API owns authorization and desired state. This service is reachable
only from the API host and applies that state to Knot. OpenProvider is never
used as a DNS host.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit


DOMAIN_RE = re.compile(
    r"^(?!xn--)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"(?!xn--)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
OWNER_LABEL_RE = re.compile(r"^(?:\*|[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)$")
ALLOWED_TYPES = {"A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV", "NS", "TLSA", "SVCB", "HTTPS"}
MAX_BODY_BYTES = 1_048_576
MAX_RRSETS = 500


class APIError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    secret: str
    listen_host: str = "::1"
    listen_port: int = 8453
    knot_config: Path = Path("/etc/knot/knot.conf")
    knotc: str = "/usr/sbin/knotc"
    kzonecheck: str = "/usr/bin/kzonecheck"
    state_file: Path = Path("/var/lib/knot/hyrule-dns-control/state.json")
    generated_config: Path = Path("/var/lib/knot/customer-zones.conf")
    zones_dir: Path = Path("/var/lib/knot/customer-zones")
    member_template: str = "customer-member"
    expected_nameservers: tuple[str, ...] = ("ns1.hyrule.host", "ns2.hyrule.host")
    expected_soa_mname: str = "ns1.hyrule.host"
    expected_soa_rname: str = "hostmaster.hyrule.host"
    max_clock_skew_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("HYRULE_DNS_CONTROL_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError("HYRULE_DNS_CONTROL_SECRET must be at least 32 characters")
        return cls(
            secret=secret,
            listen_host=os.environ.get("HYRULE_DNS_CONTROL_HOST", "::1"),
            listen_port=int(os.environ.get("HYRULE_DNS_CONTROL_PORT", "8453")),
            knot_config=Path(os.environ.get("HYRULE_DNS_KNOT_CONFIG", "/etc/knot/knot.conf")),
            state_file=Path(
                os.environ.get(
                    "HYRULE_DNS_STATE_FILE", "/var/lib/knot/hyrule-dns-control/state.json"
                )
            ),
            generated_config=Path(
                os.environ.get(
                    "HYRULE_DNS_GENERATED_CONFIG", "/var/lib/knot/customer-zones.conf"
                )
            ),
            zones_dir=Path(
                os.environ.get("HYRULE_DNS_ZONES_DIR", "/var/lib/knot/customer-zones")
            ),
        )


class KnotRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def knot(
        self,
        *args: str,
        blocking: bool = False,
        force: bool = False,
        check: bool = True,
    ) -> str:
        command = [self.settings.knotc, "-c", str(self.settings.knot_config)]
        if blocking:
            command.append("-b")
        if force:
            command.append("-f")
        command.extend(args)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "knot command failed").strip()
            raise CommandError(message[:500])
        return completed.stdout

    def check_zonefile(self, zone: str, path: Path) -> None:
        completed = subprocess.run(
            [self.settings.kzonecheck, "-o", zone, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "zone validation failed").strip()
            raise APIError(422, "invalid_zone", message[:300])


class ZoneStore:
    def __init__(self, settings: Settings, runner: KnotRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or KnotRunner(settings)
        self.lock = threading.RLock()
        self.settings.zones_dir.mkdir(parents=True, exist_ok=True)
        self.settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.generated_config.parent.mkdir(parents=True, exist_ok=True)
        with self._mutation_guard():
            self._state = self._load_state()
            self._write_generated_config(self._state)
            if self._state["pending"]:
                # A state transition is journaled before Knot is mutated. Replaying
                # it here makes every crash point converge on the intended zone.
                self._recover_pending()

    @contextmanager
    def _mutation_guard(self) -> Iterator[None]:
        """Serialize mutations with other threads and the online backup process."""
        with self.lock:
            lock_path = self.settings.state_file.parent / "mutation.lock"
            with lock_path.open("a", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_state(self) -> dict[str, Any]:
        if not self.settings.state_file.exists():
            return {"version": 2, "zones": {}, "pending": {}}
        try:
            state = json.loads(self.settings.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("DNS control state is unreadable") from exc
        if not isinstance(state, dict) or not isinstance(state.get("zones"), dict):
            raise RuntimeError("DNS control state has an invalid shape")
        pending = state.setdefault("pending", {})
        if not isinstance(pending, dict):
            raise RuntimeError("DNS control state has an invalid pending journal")
        for zone, transition in pending.items():
            if not isinstance(zone, str) or not isinstance(transition, dict):
                raise RuntimeError("DNS control state has an invalid pending transition")
            action = transition.get("action")
            previous = transition.get("previous")
            if action == "upsert":
                if not isinstance(transition.get("desired"), dict) or (
                    previous is not None and not isinstance(previous, dict)
                ):
                    raise RuntimeError("DNS control state has an invalid upsert transition")
            elif action == "delete":
                if not isinstance(previous, dict):
                    raise RuntimeError("DNS control state has an invalid delete transition")
            else:
                raise RuntimeError("DNS control state has an unknown pending transition")
        state["version"] = 2
        return state

    @staticmethod
    def _atomic_write(path: Path, content: str, mode: int = 0o640) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _save_state(self, state: dict[str, Any]) -> None:
        self._atomic_write(
            self.settings.state_file,
            json.dumps(state, sort_keys=True, indent=2) + "\n",
            0o600,
        )
        self._state = state

    def _write_generated_config(self, state: dict[str, Any]) -> None:
        lines = ["# Generated by hyrule-dns-control. Do not edit."]
        zones = set(state["zones"])
        for zone, transition in state["pending"].items():
            if transition["action"] == "upsert":
                zones.add(zone)
            else:
                zones.discard(zone)
        zones = sorted(zones)
        if zones:
            lines.append("zone:")
            for zone in zones:
                lines.extend(
                    [
                        f"  - domain: {zone}",
                        f"    template: {self.settings.member_template}",
                    ]
                )
        self._atomic_write(self.settings.generated_config, "\n".join(lines) + "\n")

    def apply(self, zone: str, payload: dict[str, Any]) -> dict[str, Any]:
        zone = validate_zone(zone)
        normalized = validate_payload(payload, self.settings)
        with self._mutation_guard():
            self._recover_pending()
            current = self._state["zones"].get(zone)
            revision = normalized["revision"]
            if current is not None:
                current_revision = int(current["revision"])
                if revision < current_revision:
                    raise APIError(409, "stale_revision", "The requested zone revision is stale.")
                if revision == current_revision:
                    if current == normalized:
                        return self._result(zone, normalized, created=False)
                    raise APIError(
                        409,
                        "revision_reused",
                        "A zone revision cannot be reused with different records.",
                    )
            created = current is None
            if created:
                self._prepare_zonefile(zone, normalized)
            staged = json.loads(json.dumps(self._state))
            staged["pending"][zone] = {
                "action": "upsert",
                "previous": current,
                "desired": normalized,
            }
            self._save_state(staged)
            self._write_generated_config(staged)
            self._recover_one(zone)
            return self._result(zone, normalized, created=created)

    def _prepare_zonefile(self, zone: str, payload: dict[str, Any]) -> None:
        zone_path = self.settings.zones_dir / f"{zone}.zone"
        rendered = render_zonefile(zone, payload)
        self._atomic_write(zone_path, rendered)
        self.runner.check_zonefile(zone, zone_path)

    def _update_existing(
        self, zone: str, current: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        begun = False
        try:
            self.runner.knot("zone-begin", zone, "+benevolent")
            begun = True
            # Include desired RRsets in the unset list. If the process crashed
            # after committing but before clearing its journal, replaying this
            # transaction still produces exactly the desired state.
            rrsets = {
                (record["name"], record["type"])
                for record in [*current["records"], *desired["records"]]
            }
            for owner, record_type in sorted(rrsets):
                self.runner.knot("zone-unset", zone, owner, record_type)
            for record in desired["records"]:
                for value in record["values"]:
                    self.runner.knot(
                        "zone-set",
                        zone,
                        record["name"],
                        str(record["ttl"]),
                        record["type"],
                        value,
                    )
            self.runner.knot("zone-commit", zone, blocking=True)
            begun = False
            self.runner.knot("zone-sign", zone, blocking=True)
            self.runner.knot("zone-flush", zone, blocking=True)
        except Exception:
            if begun:
                try:
                    self.runner.knot("zone-abort", zone)
                except Exception:
                    pass
            raise

    def _recover_pending(self) -> None:
        for zone in sorted(self._state["pending"]):
            self._recover_one(zone)

    def _recover_one(self, zone: str) -> None:
        transition = self._state["pending"].get(zone)
        if transition is None:
            return
        # Re-render membership on every replay. The preceding state write and
        # generated include write cannot be one filesystem transaction.
        self._write_generated_config(self._state)
        if transition["action"] == "upsert":
            desired = transition["desired"]
            previous = transition["previous"]
            if previous is None:
                # Rewrite the file on replay so a partial/corrupt file never
                # reaches Knot merely because the catalog transition survived.
                self._prepare_zonefile(zone, desired)
                self.runner.knot("reload", blocking=True)
                self.runner.knot("zone-sign", zone, blocking=True)
            else:
                self._update_existing(zone, previous, desired)
            committed = json.loads(json.dumps(self._state))
            committed["zones"][zone] = desired
        else:
            self.runner.knot("reload", blocking=True)
            self.runner.knot(
                "zone-purge",
                zone,
                "+orphan",
                "+journal",
                "+timers",
                "+kaspdb",
                blocking=True,
                force=True,
            )
            (self.settings.zones_dir / f"{zone}.zone").unlink(missing_ok=True)
            committed = json.loads(json.dumps(self._state))
            committed["zones"].pop(zone, None)
        committed["pending"].pop(zone, None)
        self._save_state(committed)
        self._write_generated_config(committed)

    def delete(self, zone: str) -> dict[str, Any]:
        zone = validate_zone(zone)
        with self._mutation_guard():
            self._recover_pending()
            if zone not in self._state["zones"]:
                return {"zone": zone, "deleted": False}
            staged = json.loads(json.dumps(self._state))
            staged["pending"][zone] = {
                "action": "delete",
                "previous": staged["zones"][zone],
            }
            self._save_state(staged)
            self._write_generated_config(staged)
            self._recover_one(zone)
            return {"zone": zone, "deleted": True}

    def dnskeys(self, zone: str) -> dict[str, Any]:
        zone = validate_zone(zone)
        with self._mutation_guard():
            self._recover_pending()
            if zone not in self._state["zones"]:
                raise APIError(404, "zone_not_found", "The managed zone does not exist.")
        output = self.runner.knot("zone-read", zone, "@", "DNSKEY")
        keys: list[dict[str, Any]] = []
        for line in output.splitlines():
            fields = line.split()
            try:
                index = fields.index("DNSKEY")
                flags, protocol, algorithm = map(int, fields[index + 1 : index + 4])
                public_key = "".join(fields[index + 4 :])
            except (ValueError, IndexError):
                continue
            if public_key:
                keys.append(
                    {
                        "flags": flags,
                        "protocol": protocol,
                        "algorithm": algorithm,
                        "alg": algorithm,
                        "public_key": public_key,
                        "pub_key": public_key,
                    }
                )
        if not keys:
            raise APIError(503, "dnskey_not_ready", "DNSSEC keys are not ready yet.")
        return {"zone": zone, "dnskey": keys}

    def health(self) -> dict[str, Any]:
        try:
            with self._mutation_guard():
                self._recover_pending()
            self.runner.knot("status")
        except CommandError as exc:
            raise APIError(503, "knot_unavailable", str(exc)) from exc
        return {"status": "ok", "zones": len(self._state["zones"])}

    @staticmethod
    def _result(zone: str, payload: dict[str, Any], *, created: bool) -> dict[str, Any]:
        return {
            "zone": zone,
            "revision": payload["revision"],
            "created": created,
            "dnssec": True,
        }


def validate_zone(value: str) -> str:
    zone = value.strip().lower().rstrip(".")
    try:
        zone.encode("ascii")
    except UnicodeEncodeError as exc:
        raise APIError(422, "invalid_zone", "Only ASCII second-level domains are supported.") from exc
    if not DOMAIN_RE.fullmatch(zone):
        raise APIError(422, "invalid_zone", "Only ASCII second-level domains are supported.")
    return zone


def _normalize_hostname(value: Any, field: str) -> str:
    hostname = str(value).strip().lower().rstrip(".")
    if not HOST_RE.fullmatch(hostname):
        raise APIError(422, "invalid_zone", f"{field} is not a valid hostname.")
    return hostname


def _validate_owner(value: Any) -> str:
    owner = str(value).strip().lower().rstrip(".") or "@"
    if owner == "@":
        return owner
    labels = owner.split(".")
    if any(not OWNER_LABEL_RE.fullmatch(label) for label in labels):
        raise APIError(422, "invalid_record", "A DNS record owner is invalid.")
    if any(label == "*" for label in labels[1:]):
        raise APIError(422, "invalid_record", "A wildcard is only valid in the first label.")
    return owner


QUOTED_TXT_RE = re.compile(r'(?:"(?:\\.|[^"\\])*"(?:\s+|$))+')


def _normalize_txt_rdata(value: str) -> str:
    """Keep valid quoted TXT chunks or safely quote plain-text input."""
    if QUOTED_TXT_RE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _validate_non_txt_rdata(value: str) -> str:
    """Reject master-file syntax that could change the stored RDATA.

    Whitespace and quoted strings are part of valid structured RDATA such as
    MX, CAA, SRV, and HTTPS, so rejecting metacharacters wholesale would block
    legitimate records. Track quotes and escapes instead: an unquoted
    semicolon starts a comment, while parentheses alter master-file grouping.
    """
    quoted = False
    escaped = False
    for character in value:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif not quoted and character in ";()":
            raise APIError(
                422,
                "invalid_record",
                "DNS values cannot contain unquoted zone-file metacharacters.",
            )
    if quoted or escaped:
        raise APIError(422, "invalid_record", "A DNS value has invalid quoting.")
    return value


def validate_payload(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise APIError(400, "invalid_json", "The request body must be a JSON object.")
    try:
        revision = int(payload["revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise APIError(422, "invalid_revision", "revision must be a positive integer.") from exc
    if revision < 1:
        raise APIError(422, "invalid_revision", "revision must be a positive integer.")
    nameservers = tuple(
        _normalize_hostname(item, "nameserver") for item in payload.get("nameservers", [])
    )
    if nameservers != settings.expected_nameservers:
        raise APIError(422, "invalid_nameservers", "The managed nameserver set is fixed.")
    soa_mname = _normalize_hostname(payload.get("soa_mname"), "soa_mname")
    soa_rname = _normalize_hostname(payload.get("soa_rname"), "soa_rname")
    if soa_mname != settings.expected_soa_mname or soa_rname != settings.expected_soa_rname:
        raise APIError(422, "invalid_soa", "The managed SOA identity is fixed.")
    if payload.get("dnssec") is not True:
        raise APIError(422, "dnssec_required", "Managed zones must enable DNSSEC.")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) > MAX_RRSETS:
        raise APIError(422, "invalid_records", "records must contain at most 500 RRsets.")
    normalized_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise APIError(422, "invalid_record", "Every DNS record must be an object.")
        owner = _validate_owner(raw.get("name", "@"))
        record_type = str(raw.get("type", "")).upper()
        if record_type not in ALLOWED_TYPES:
            raise APIError(422, "invalid_record", f"DNS type {record_type!r} is not supported.")
        if owner == "@" and record_type in {"NS", "CNAME"}:
            raise APIError(422, "protected_record", "Apex NS and CNAME records are protected.")
        key = (owner, record_type)
        if key in seen:
            raise APIError(422, "duplicate_rrset", "Each owner/type RRset must be unique.")
        seen.add(key)
        try:
            ttl = int(raw.get("ttl", 3600))
        except (TypeError, ValueError) as exc:
            raise APIError(422, "invalid_record", "TTL must be an integer.") from exc
        if not 60 <= ttl <= 86400:
            raise APIError(422, "invalid_record", "TTL must be between 60 and 86400 seconds.")
        values = raw.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 64:
            raise APIError(422, "invalid_record", "An RRset must contain 1 to 64 values.")
        normalized_values: list[str] = []
        for raw_value in values:
            if not isinstance(raw_value, str):
                raise APIError(422, "invalid_record", "DNS values must be strings.")
            value = raw_value.strip()
            if (
                not value
                or len(value) > 4096
                or "\n" in value
                or "\r" in value
                or any(ord(character) < 32 and character != "\t" for character in value)
            ):
                raise APIError(422, "invalid_record", "A DNS value is invalid.")
            if record_type in {"A", "AAAA"}:
                try:
                    address = ipaddress.ip_address(value)
                except ValueError as exc:
                    raise APIError(422, "invalid_record", "An address record is invalid.") from exc
                expected = 4 if record_type == "A" else 6
                if address.version != expected:
                    raise APIError(422, "invalid_record", "An address family is invalid.")
                value = str(address)
            elif record_type == "TXT":
                # In a zone file an unquoted semicolon begins a comment. Store
                # TXT presentation format canonically so rendering and knotc
                # transactions cannot silently truncate customer data.
                value = _normalize_txt_rdata(value)
            else:
                value = _validate_non_txt_rdata(value)
            if value not in normalized_values:
                normalized_values.append(value)
        normalized_records.append(
            {"name": owner, "type": record_type, "ttl": ttl, "values": normalized_values}
        )
    normalized_records.sort(key=lambda row: (row["name"], row["type"]))
    return {
        "revision": revision,
        "nameservers": list(nameservers),
        "soa_mname": soa_mname,
        "soa_rname": soa_rname,
        "records": normalized_records,
        "dnssec": True,
    }


def render_zonefile(zone: str, payload: dict[str, Any]) -> str:
    serial = max(int(time.strftime("%Y%m%d00", time.gmtime())), int(payload["revision"]))
    lines = [
        f"$ORIGIN {zone}.",
        "$TTL 3600",
        (
            f"@ 3600 IN SOA {payload['soa_mname']}. {payload['soa_rname']}. "
            f"{serial} 3600 900 604800 300"
        ),
    ]
    lines.extend(f"@ 3600 IN NS {nameserver}." for nameserver in payload["nameservers"])
    for record in payload["records"]:
        for value in record["values"]:
            lines.append(
                f"{record['name']} {record['ttl']} IN {record['type']} {value}"
            )
    return "\n".join(lines) + "\n"


def verify_signature(
    settings: Settings, timestamp: str | None, signature: str | None, method: str, path: str, body: bytes
) -> None:
    try:
        numeric_timestamp = int(timestamp or "")
    except ValueError as exc:
        raise APIError(401, "invalid_signature", "The request signature is invalid.") from exc
    if abs(int(time.time()) - numeric_timestamp) > settings.max_clock_skew_seconds:
        raise APIError(401, "expired_signature", "The request signature has expired.")
    digest = hashlib.sha256(body).hexdigest()
    signing_input = "\n".join([str(numeric_timestamp), method.upper(), path, digest]).encode()
    expected = hmac.new(settings.secret.encode(), signing_input, hashlib.sha256).hexdigest()
    provided = (signature or "").removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise APIError(401, "invalid_signature", "The request signature is invalid.")


def parse_content_length(value: str | None) -> int:
    try:
        length = int(value or "0")
    except ValueError as exc:
        raise APIError(
            400, "invalid_content_length", "Content-Length must be a non-negative integer."
        ) from exc
    if length < 0:
        raise APIError(
            400, "invalid_content_length", "Content-Length must be a non-negative integer."
        )
    if length > MAX_BODY_BYTES:
        raise APIError(413, "body_too_large", "The request body is too large.")
    return length


class DNSControlHandler(BaseHTTPRequestHandler):
    server_version = "HyruleDNSControl/1"

    @property
    def store(self) -> ZoneStore:
        return self.server.store  # type: ignore[attr-defined,no-any-return]

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format_string: str, *args: Any) -> None:
        print(
            json.dumps(
                {
                    "service": "hyrule-dns-control",
                    "remote": self.client_address[0],
                    "message": format_string % args,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )

    def _dispatch(self) -> None:
        try:
            length = parse_content_length(self.headers.get("Content-Length"))
            body = self.rfile.read(length) if length else b""
            path = urlsplit(self.path).path
            verify_signature(
                self.settings,
                self.headers.get("X-Hyrule-Timestamp"),
                self.headers.get("X-Hyrule-Signature"),
                self.command,
                path,
                body,
            )
            if path == "/health" and self.command == "GET":
                self._json(200, self.store.health())
                return
            match = re.fullmatch(r"/v1/zones/([^/]+)(/dnssec)?", path)
            if not match:
                raise APIError(404, "not_found", "Not found.")
            zone = unquote(match.group(1))
            if match.group(2):
                if self.command != "GET":
                    raise APIError(405, "method_not_allowed", "Method not allowed.")
                self._json(200, self.store.dnskeys(zone))
                return
            if self.command == "PUT":
                try:
                    payload = json.loads(body)
                except (UnicodeDecodeError, ValueError) as exc:
                    raise APIError(400, "invalid_json", "The request body is invalid JSON.") from exc
                result = self.store.apply(zone, payload)
                self._json(201 if result["created"] else 200, result)
                return
            if self.command == "DELETE":
                self._json(200, self.store.delete(zone))
                return
            raise APIError(405, "method_not_allowed", "Method not allowed.")
        except APIError as exc:
            self._json(exc.status, {"code": exc.code, "detail": exc.detail})
        except (CommandError, OSError, RuntimeError) as exc:
            self._json(
                503,
                {"code": "dns_control_unavailable", "detail": str(exc)[:300]},
            )
        except Exception:
            self._json(500, {"code": "internal_error", "detail": "Internal DNS control error."})

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    do_GET = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True


def main() -> None:
    settings = Settings.from_env()
    store = ZoneStore(settings)
    server = IPv6ThreadingHTTPServer((settings.listen_host, settings.listen_port), DNSControlHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.store = store  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    main()
