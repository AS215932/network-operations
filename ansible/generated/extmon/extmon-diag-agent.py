#!/usr/bin/env python3
"""Token-protected active diagnostic agent for extmon."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("EXTMON_DIAG_AGENT_TOKEN", "")
BLOCKED_PORTS = {0, 135, 137, 138, 139, 445, 3306, 5432, 6379, 11211, 27017}
ALLOWED_PORTS = {22, 25, 53, 80, 110, 143, 443, 465, 587, 993, 995, 2525, 8080, 8443}


def blocked_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def resolve_public(host: str) -> list[str]:
    try:
        if blocked_ip(host):
            raise ValueError(f"blocked non-public target {host}")
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    addrs = []
    for info in infos:
        addr = info[4][0]
        if blocked_ip(addr):
            raise ValueError(f"blocked resolved non-public target {addr}")
        if addr not in addrs:
            addrs.append(addr)
    return addrs


def tcp(host: str, port: int, timeout: float = 10.0):
    if port in BLOCKED_PORTS or port not in ALLOWED_PORTS:
        raise ValueError(f"port {port} is not allowed")
    addr = resolve_public(host)[0]
    with socket.create_connection((addr, port), timeout=timeout):
        return {"ok": True, "address": addr, "port": port}


def http(url: str, timeout: float = 10.0):
    if "://" not in url:
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http/https allowed")
    resolve_public(parsed.hostname or "")
    req = urllib.request.Request(url, headers={"User-Agent": "AS215932-extmon-diag/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"ok": True, "status": resp.status, "headers": dict(resp.headers), "body_sample": resp.read(2048).decode("utf-8", "replace")}


def run_probe(kind: str, payload: dict):
    if kind == "tcp":
        return tcp(str(payload["host"]), int(payload["port"]), float(payload.get("timeout", 10)))
    if kind in {"http", "https"}:
        return http(str(payload["url"]), float(payload.get("timeout", 10)))
    if kind in {"ping", "trace"}:
        host = str(payload["host"])
        addr = resolve_public(host)[0]
        cmd = ["ping", "-c", "4", "-W", "2", addr] if kind == "ping" else ["traceroute", "-m", "20", addr]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "output": (proc.stdout or proc.stderr)[-4096:]}
    if kind == "smtp":
        host = str(payload["host"])
        return tcp(host, int(payload.get("port", 25)), float(payload.get("timeout", 10)))
    raise ValueError(f"unsupported probe {kind}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok\n"); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if TOKEN and self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(403); self.end_headers(); return
        kind = self.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        try:
            result = run_probe(kind, payload)
            status = 200
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            status = 400
        body = json.dumps(result).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    host, port_s = os.environ.get("EXTMON_DIAG_AGENT_LISTEN", "127.0.0.1:9190").rsplit(":", 1)
    ThreadingHTTPServer((host, int(port_s)), Handler).serve_forever()
