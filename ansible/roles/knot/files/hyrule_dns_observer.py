#!/usr/bin/env python3
"""Forward only session-scoped Knot dnstap queries to hyrule-cloud."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime


_QUIET_LINE = re.compile(
    r"\sAQ\s+(?P<peer>\S+)\s+(?:UDP|TCP|DOT|DOH|TLS)\s+\d+b\s+"
    r'"(?P<name>[^"\s]+)"\s+IN\s+\S+',
    re.IGNORECASE,
)
_LABEL = re.compile(r"^[0-9a-f]{32}$")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _peer_address(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    else:
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            host, separator, port = candidate.rpartition(":")
            if not separator or not port.isdigit():
                return None
            candidate = host
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def parse_dnstap_line(line: str, zone: str) -> tuple[str, str, str] | None:
    """Return (label, resolver address, qname) for a valid probe query."""

    match = _QUIET_LINE.search(line)
    if match is None:
        return None
    qname = match.group("name").lower().rstrip(".")
    suffix = "." + zone.lower().rstrip(".")
    if not qname.endswith(suffix):
        return None
    label = qname[: -len(suffix)]
    if not _LABEL.fullmatch(label):
        return None
    address = _peer_address(match.group("peer"))
    if address is None:
        return None
    return label, address, qname + "."


class Forwarder:
    def __init__(self, api_url: str, secret: str) -> None:
        self.api_url = api_url
        self.secret = secret.encode()
        self.work: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=256)
        self.seen: dict[tuple[str, str], float] = {}
        self.last_warning = 0.0

    def submit(self, item: tuple[str, str, str]) -> None:
        label, address, _ = item
        now = time.monotonic()
        key = (label, address)
        if self.seen.get(key, 0.0) > now - 60:
            return
        self.seen[key] = now
        if len(self.seen) > 1024:
            self.seen = {k: value for k, value in self.seen.items() if value > now - 60}
        try:
            self.work.put_nowait(item)
        except queue.Full:
            self.warn("observer queue full; dropping bounded probe evidence")

    def warn(self, message: str) -> None:
        now = time.monotonic()
        if now - self.last_warning >= 60:
            print(message, file=sys.stderr, flush=True)
            self.last_warning = now

    def run(self) -> None:
        while True:
            label, address, qname = self.work.get()
            body = json.dumps(
                {
                    "dns_label": label,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "query_name": qname,
                    "resolver_address": address,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            timestamp = str(int(time.time()))
            signature = hmac.new(
                self.secret,
                timestamp.encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            request = urllib.request.Request(
                self.api_url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Hyrule-Signature": signature,
                    "X-Hyrule-Timestamp": timestamp,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    if response.status != 202:
                        self.warn(f"observer API returned HTTP {response.status}")
            except urllib.error.HTTPError as exc:
                # 404 is expected while the API feature flag is dark or a
                # session expired. Other status codes indicate configuration.
                if exc.code != 404:
                    self.warn(f"observer API returned HTTP {exc.code}")
            except (OSError, urllib.error.URLError) as exc:
                self.warn(f"observer API unavailable: {type(exc).__name__}")
            finally:
                self.work.task_done()


def main() -> int:
    secret = _env("HYRULE_IP_CHECK_DNS_OBSERVER_SECRET")
    if len(secret) < 32:
        raise RuntimeError("observer secret must contain at least 32 characters")
    zone = _env("HYRULE_IP_CHECK_DNS_OBSERVER_ZONE").lower().rstrip(".")
    socket_path = _env("HYRULE_IP_CHECK_DNSTAP_SOCKET")
    cli = _env("HYRULE_IP_CHECK_DNSTAP_CLI")
    forwarder = Forwarder(_env("HYRULE_IP_CHECK_DNS_OBSERVER_API_URL"), secret)
    threading.Thread(target=forwarder.run, name="api-forwarder", daemon=True).start()

    process = subprocess.Popen(
        [cli, "-u", socket_path, "-q"],
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        item = parse_dnstap_line(line, zone)
        if item is not None:
            forwarder.submit(item)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
