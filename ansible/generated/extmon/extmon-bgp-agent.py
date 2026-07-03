#!/usr/bin/env python3
"""AS215932 external BGP metrics agent.

Stdlib-only exporter for extmon. Polls RIPEstat, optional Cloudflare Radar,
bgp.tools table export, and local Routinator. Receives BGPalerter reportHTTP
webhooks on /bgpalerter and exposes Prometheus metrics on /metrics.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ASN = int(os.environ.get("EXTMON_BGP_ASN", "215932"))
PREFIXES = json.loads(os.environ.get("EXTMON_BGP_PREFIXES", '[{"prefix": "2a0c:b641:b50::/44", "description": "AS215932 aggregate", "expected_origin": 215932, "rpki_max_length": 48}]'))
CF_TOKEN = os.environ.get("EXTMON_BGP_CLOUDFLARE_API_TOKEN", "")
BGPTOOLS_UA = os.environ.get("EXTMON_BGP_BGPTOOLS_USER_AGENT", "AS215932-bgp-observer/1.0")
POLL_SECONDS = int(os.environ.get("EXTMON_BGP_POLL_SECONDS", "300"))
# bgp.tools regenerates table.jsonl only ~every 30m and asks clients not to pull
# it more often (https://bgp.tools/kb/api). Rate-limit our successful downloads
# independently of the fast source-poll loop.
BGPTOOLS_MIN_INTERVAL = int(os.environ.get("EXTMON_BGP_BGPTOOLS_MIN_INTERVAL", "1800"))
_bgp_tools_last_fetch = 0.0
ROUTINATOR_URL = os.environ.get("EXTMON_ROUTINATOR_URL", "http://127.0.0.1:8323")
INGEST_URL = os.environ.get("EXTMON_BGP_HYRULE_INGEST_URL", "").rstrip("/")
INGEST_TOKEN = os.environ.get("EXTMON_BGP_INGEST_TOKEN", "")

STATE_LOCK = threading.Lock()
STATE: dict[str, object] = {
    "sources": {},
    "prefixes": {},
    "rpki": {},
    "bgp_tools_hits": {},
    "cf_events": {},
    "bgpalerter": {},
    "bgpalerter_last_event": {},
    "last_poll": 0,
}


def _fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, object], *, timeout: int = 10) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AS215932-extmon-bgp-agent/1.0",
    }
    if INGEST_TOKEN:
        headers["X-Hyrule-BGP-Ingest-Token"] = INGEST_TOKEN
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _set_source(name: str, ok: bool, message: str = "") -> None:
    now = time.time()
    source = {"up": 1 if ok else 0, "message": message, "last_success": now if ok else 0, "last_error": 0 if ok else now}
    with STATE_LOCK:
        old = STATE["sources"].get(name, {}) if isinstance(STATE["sources"], dict) else {}
        if ok and isinstance(old, dict) and old.get("last_error"):
            source["last_error"] = old.get("last_error", 0)
        if not ok and isinstance(old, dict) and old.get("last_success"):
            source["last_success"] = old.get("last_success", 0)
        STATE["sources"][name] = source


def poll_ripestat(prefix: str) -> None:
    q = urllib.parse.quote(prefix, safe="")
    routing = _fetch_json(f"https://stat.ripe.net/data/routing-status/data.json?resource={q}")
    data = routing.get("data", {})
    origins = [int(o.get("origin")) for o in data.get("origins", []) if str(o.get("origin", "")).isdigit()]
    # Base visibility on the CURRENT announcer set, not `last_seen` — the latter
    # is historical ever-seen metadata that stays populated after a withdrawal,
    # which would keep bgp_prefix_visible=1 and silence AS215932PrefixNotVisible.
    visible = bool(origins)
    visibility = data.get("visibility", {})
    with STATE_LOCK:
        STATE["prefixes"].setdefault(prefix, {})["ripestat"] = {"visible": visible, "origins": origins, "visibility": visibility}
    for origin in origins or [ASN]:
        rpki = _fetch_json(f"https://stat.ripe.net/data/rpki-validation/data.json?resource={origin}&prefix={q}")
        status = rpki.get("data", {}).get("status", "unknown")
        with STATE_LOCK:
            STATE["rpki"][(prefix, str(origin), "ripestat")] = status
    _set_source("ripestat", True)


def poll_routinator(prefix: str) -> None:
    q = urllib.parse.urlencode({"asn": f"AS{ASN}", "prefix": prefix})
    data = _fetch_json(f"{ROUTINATOR_URL}/validity?{q}", timeout=10)
    validity = data.get("validated_route", {}).get("validity", {}).get("state", "unknown")
    with STATE_LOCK:
        STATE["rpki"][(prefix, str(ASN), "routinator")] = validity
    _set_source("routinator", True)


def poll_bgp_tools() -> None:
    global _bgp_tools_last_fetch
    # Skip if we pulled the full table within the min interval. Gated on the last
    # *successful* fetch, so a transient failure still retries on the next cycle.
    if time.time() - _bgp_tools_last_fetch < BGPTOOLS_MIN_INTERVAL:
        return
    headers = {"User-Agent": BGPTOOLS_UA}
    req = urllib.request.Request("https://bgp.tools/table.jsonl", headers=headers)
    wanted = {p["prefix"] for p in PREFIXES}
    found = {}
    with urllib.request.urlopen(req, timeout=45) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace")
            if not any(prefix in line for prefix in wanted):
                continue
            row = json.loads(line)
            cidr = row.get("CIDR")
            if cidr in wanted:
                found[cidr] = {"asn": int(row.get("ASN", 0)), "hits": int(row.get("Hits", 0))}
    with STATE_LOCK:
        # Update *every* wanted prefix, not just the ones present this fetch, so a
        # withdrawn/removed prefix flips to invisible instead of exporting its last
        # visible=True origin forever. Clear stale per-origin hit counters too.
        for prefix in wanted:
            row = found.get(prefix)
            STATE["prefixes"].setdefault(prefix, {})["bgp_tools"] = (
                {"visible": True, "origins": [row["asn"]]} if row else {"visible": False, "origins": []}
            )
            for stale in [k for k in STATE["bgp_tools_hits"] if k[0] == prefix]:
                del STATE["bgp_tools_hits"][stale]
            if row:
                STATE["bgp_tools_hits"][(prefix, str(row["asn"]))] = row["hits"]
    _set_source("bgp_tools", True)
    _bgp_tools_last_fetch = time.time()


def poll_cloudflare(prefix: str) -> None:
    if not CF_TOKEN:
        # Cloudflare Radar is an optional feed; when no token is configured the
        # source is disabled, not down. Emitting bgp_source_up=0 here would make
        # BGPSourceDown alert permanently on every token-less deployment, so skip
        # registering the source entirely.
        return
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "User-Agent": "AS215932-extmon/1.0"}
    q = urllib.parse.quote(prefix, safe="")
    realtime = _fetch_json(f"https://api.cloudflare.com/client/v4/radar/bgp/routes/realtime?prefix={q}", headers=headers)
    if not realtime.get("success", True):
        raise RuntimeError(f"cloudflare radar error: {realtime.get('errors') or 'unsuccessful'}")
    # Derive visibility and origins from the actual route set, not just HTTP
    # success — otherwise a withdrawal or MOAS/hijack still exports visible=1 with
    # no origins, defeating the feed's whole purpose.
    routes = (realtime.get("result") or {}).get("routes") or []
    origins = []

    def _add_origin(value):
        try:
            asn = int(value)
        except (TypeError, ValueError):
            return
        if asn not in origins:
            origins.append(asn)

    for route in routes:
        # Radar realtime route objects carry the origin in meta.prefix_origins
        # or as the last AS in as_path — not a flat origin_asn field. Try all
        # known shapes so a MOAS/hijack the Radar feed sees still yields origins.
        meta = route.get("meta") or {}
        for entry in (meta.get("prefix_origins") or []):
            _add_origin(entry.get("origin") if isinstance(entry, dict) else entry)
        as_path = route.get("as_path") or route.get("asPath") or []
        if isinstance(as_path, list) and as_path:
            _add_origin(as_path[-1])
        for key in ("origin_asn", "originASN"):
            if route.get(key) is not None:
                _add_origin(route.get(key))
    with STATE_LOCK:
        STATE["prefixes"].setdefault(prefix, {})["cloudflare_radar"] = {"visible": bool(routes), "origins": origins}
    _set_source("cloudflare_radar", True)


def _ingest_source_statuses() -> None:
    if not INGEST_URL or not INGEST_TOKEN:
        return
    with STATE_LOCK:
        sources = dict(STATE["sources"])
        prefixes = dict(STATE["prefixes"])
        rpki = {"|".join(key): value for key, value in dict(STATE["rpki"]).items()}
        last_poll = STATE["last_poll"]
    for name, source in sources.items():
        ok = bool(source.get("up"))
        payload = {
            "source_name": f"extmon:{name}",
            "status": "ok" if ok else "error",
            "error": None if ok else str(source.get("message") or "source unavailable"),
            "payload": {
                "asn": ASN,
                "prefixes": prefixes,
                "rpki": rpki,
                "source": source,
                "last_poll": last_poll,
            },
        }
        try:
            _post_json(f"{INGEST_URL}/ingest/status", payload)
        except Exception:
            pass


def poll_once() -> None:
    for item in PREFIXES:
        prefix = item["prefix"]
        try:
            poll_ripestat(prefix)
        except Exception as exc:
            _set_source("ripestat", False, str(exc))
        try:
            poll_routinator(prefix)
        except Exception as exc:
            _set_source("routinator", False, str(exc))
        try:
            poll_cloudflare(prefix)
        except Exception as exc:
            _set_source("cloudflare_radar", False, str(exc))
    try:
        poll_bgp_tools()
    except Exception as exc:
        _set_source("bgp_tools", False, str(exc))
    with STATE_LOCK:
        STATE["last_poll"] = time.time()
    _ingest_source_statuses()


def poll_loop() -> None:
    while True:
        poll_once()
        time.sleep(POLL_SECONDS)


def esc(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def metrics() -> str:
    lines = [
        "# HELP bgp_source_up Whether a BGP data source is currently healthy.",
        "# TYPE bgp_source_up gauge",
    ]
    with STATE_LOCK:
        sources = dict(STATE["sources"])
        prefixes = dict(STATE["prefixes"])
        rpki = dict(STATE["rpki"])
        hits = dict(STATE["bgp_tools_hits"])
        bgpalerter = dict(STATE["bgpalerter"])
        bgpalerter_last = dict(STATE["bgpalerter_last_event"])
        last_poll = STATE["last_poll"]
    for name, source in sources.items():
        lines.append('bgp_source_up{source="%s"} %s' % (esc(name), source.get("up", 0)))
        lines.append('bgp_source_last_success_timestamp{source="%s"} %s' % (esc(name), source.get("last_success", 0)))
    for prefix, by_source in prefixes.items():
        for source, data in by_source.items():
            visible = 1 if data.get("visible") else 0
            lines.append('bgp_prefix_visible{prefix="%s",source="%s"} %s' % (esc(prefix), esc(source), visible))
            for origin in data.get("origins", []):
                rpki_status = rpki.get((prefix, str(origin), "ripestat"), "unknown")
                lines.append('bgp_prefix_origin_visible{prefix="%s",origin="%s",source="%s",rpki="%s"} %s' % (esc(prefix), origin, esc(source), esc(rpki_status), visible))
    for (prefix, origin, validator), status in rpki.items():
        lines.append('bgp_rpki_valid{prefix="%s",origin="%s",validator="%s"} %s' % (esc(prefix), esc(origin), esc(validator), 1 if status == "valid" else 0))
    for (prefix, origin), value in hits.items():
        lines.append('bgp_bgp_tools_hits{prefix="%s",origin="%s"} %s' % (esc(prefix), esc(origin), value))
    for key, count in bgpalerter.items():
        channel, typ, severity = key.split("|", 2)
        lines.append('bgpalerter_alerts_total{channel="%s",type="%s",severity="%s"} %s' % (esc(channel), esc(typ), esc(severity), count))
    # Per-severity timestamp of the most recent event. Alerting on this gauge
    # catches the very first event, which increase(counter) cannot (a counter's
    # first sample has no prior point to diff against).
    for severity, ts in bgpalerter_last.items():
        lines.append('bgpalerter_last_event_timestamp{severity="%s"} %s' % (esc(severity), ts))
    lines.append(f"bgp_agent_last_poll_timestamp {last_poll}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok\n"); return
        if self.path == "/metrics":
            body = metrics().encode()
            self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/bgpalerter":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}
        channel = str(payload.get("channel") or payload.get("type") or "unknown")
        typ = str(payload.get("type") or "unknown")
        severity = "critical" if channel in {"hijack", "rpki", "visibility"} else "warning"
        key = f"{channel}|{typ}|{severity}"
        with STATE_LOCK:
            STATE["bgpalerter"][key] = int(STATE["bgpalerter"].get(key, 0)) + 1
            STATE["bgpalerter_last_event"][severity] = time.time()
        self.send_response(204); self.end_headers()

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    poll_once()
    threading.Thread(target=poll_loop, daemon=True).start()
    host, port_s = os.environ.get("EXTMON_BGP_AGENT_LISTEN", "127.0.0.1:9188").rsplit(":", 1)
    ThreadingHTTPServer((host, int(port_s)), Handler).serve_forever()
