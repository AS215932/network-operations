#!/usr/bin/env python3
"""Independent, bounded Icinga check of the NOC delivery worker."""

import ipaddress
import http.client
import json
import sys
import urllib.error
import urllib.request


REASONS = {"worker_not_running", "worker_runtime_unavailable", "worker_stale", "reports_overdue", "report_timestamp_invalid"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def assess(code, body):
    if code not in (200, 503):
        return 3, "Unexpected delivery health HTTP status"
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
            raise ValueError
        delivery = payload.get("delivery", {})
        worker = payload.get("outbox_worker", {})
        if not isinstance(delivery, dict) or not isinstance(worker, dict):
            raise ValueError
        reasons = delivery.get("reasons")
        if reasons is not None and not isinstance(reasons, list):
            raise ValueError
        safe_reasons = sorted({value for value in reasons or [] if isinstance(value, str) and value in REASONS})
    except (ValueError, TypeError, RecursionError):
        return (2 if code == 503 else 3), "Invalid delivery health response"
    if code == 503 or payload.get("status") in {"degraded", "disabled"}:
        return 2, "NOC delivery unavailable" + (": " + ", ".join(safe_reasons) if safe_reasons else "")
    if not delivery or payload.get("status") != "ok" or delivery.get("status") != "ok":
        return 3, "Delivery health contract missing or invalid; verify deployed NOC version"
    if not isinstance(reasons, list) or any(not isinstance(value, str) for value in reasons):
        return 3, "Delivery health reasons missing or invalid"
    if any(type(worker.get(key)) is not bool for key in ("enabled", "running")):
        return 3, "Delivery worker contract missing or invalid"
    if worker.get("enabled") is not True or worker.get("running") is not True:
        return 2, "NOC delivery worker disabled or stopped"
    if reasons:
        return 2, "NOC delivery health reports a failure"
    return 0, "NOC delivery worker and outstanding report age healthy"


def main(args=None):
    args = sys.argv[1:] if args is None else args
    try:
        if len(args) != 2:
            raise ValueError
        address = ipaddress.IPv6Address(args[0])
        port = int(args[1])
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print("UNKNOWN - usage: check_noc_agent_delivery_health.py <ipv6> <port>")
        return 3
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        try:
            response = opener.open(f"http://[{address}]:{port}/health/cases", timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(65537)
            code = response.code
        if len(body) > 65536:
            result, message = 3, "Delivery health response too large"
        else:
            result, message = assess(code, body)
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        result, message = 2, "NOC delivery endpoint unreachable from monitor"
    print(f"{ {0: 'OK', 2: 'CRITICAL', 3: 'UNKNOWN'}[result]} - {message}")
    return result


if __name__ == "__main__":
    sys.exit(main())
