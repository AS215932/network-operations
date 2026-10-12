#!/usr/bin/env python3
"""OVH service-expiry collector for extmon.

Polls OVH's API for the dedicated server, its failover IPs, and any unpaid
invoices, then writes Prometheus textfile-collector metrics that node_exporter
serves on :9100. Driven from extmon (outside AS215932) so a billing lapse on
the OVH side cannot silence its own alarm.
"""

from __future__ import annotations

import configparser
import hashlib
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CONF_PATH = "/etc/extmon/ovh.conf"
TEXTFILE = "/var/lib/node_exporter/textfile_collector/ovh_expiry.prom"
SERVER_NAME = "ns3526203.ip-193-70-32.eu"
FAILOVER_IPS = ["46.105.40.223", "51.91.236.215", "54.38.14.218"]



def load_creds() -> dict:
    cp = configparser.ConfigParser()
    cp.read(CONF_PATH)
    section = cp["ovh-eu"]
    return {
        "endpoint": "https://eu.api.ovh.com/1.0",
        "application_key": section["application_key"],
        "application_secret": section["application_secret"],
        "consumer_key": section["consumer_key"],
    }


def signature(secret: str, consumer_key: str, method: str, url: str, body: str, ts: str) -> str:
    raw = f"{secret}+{consumer_key}+{method}+{url}+{body}+{ts}"
    return "$1$" + hashlib.sha1(raw.encode()).hexdigest()


def ovh_get(creds: dict, path: str):
    url = creds["endpoint"] + path
    ts = str(int(time.time()))
    sig = signature(
        creds["application_secret"], creds["consumer_key"], "GET", url, "", ts
    )
    headers = {
        "X-Ovh-Application": creds["application_key"],
        "X-Ovh-Consumer": creds["consumer_key"],
        "X-Ovh-Signature": sig,
        "X-Ovh-Timestamp": ts,
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def expires_seconds(iso_str: str) -> int:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() - time.time())


def main() -> int:
    creds = load_creds()
    metrics: list[str] = [
        "# HELP ovh_service_expires_seconds Seconds until OVH service expires (negative = already expired)",
        "# TYPE ovh_service_expires_seconds gauge",
    ]
    error_count = 0

    # --- Dedicated server itself ---
    try:
        info = ovh_get(creds, f"/dedicated/server/{SERVER_NAME}/serviceInfos")
        secs = expires_seconds(info["expiration"])
        metrics.append(
            f'ovh_service_expires_seconds{{service="{SERVER_NAME}",type="server"}} {secs}'
        )
    except Exception as e:
        print(f"server lookup failed: {e}", file=sys.stderr)
        error_count += 1

    # --- Failover IPs (the kind that just expired and broke NAT64) ---
    for ip in FAILOVER_IPS:
        try:
            info = ovh_get(creds, f"/ip/{ip}/serviceInfos")
            secs = expires_seconds(info["expiration"])
            metrics.append(
                f'ovh_service_expires_seconds{{service="{ip}",type="ip"}} {secs}'
            )
        except Exception as e:
            print(f"ip {ip} lookup failed: {e}", file=sys.stderr)
            error_count += 1

    # --- Unpaid invoices ---
    try:
        # OVH expects a plain date here. A full ISO timestamp with timezone is
        # rejected with HTTP 400.
        month_start = datetime.now(timezone.utc).replace(day=1).date().isoformat()
        bills = ovh_get(creds, "/me/bill?date.from=" + month_start)
        unpaid = 0
        for bill_id in bills:
            b = ovh_get(creds, f"/me/bill/{bill_id}")
            if b.get("paid") is False:
                unpaid += 1
        metrics.append("# HELP ovh_unpaid_bill_count Number of unpaid OVH invoices in current period")
        metrics.append("# TYPE ovh_unpaid_bill_count gauge")
        metrics.append(f"ovh_unpaid_bill_count {unpaid}")
    except Exception as e:
        print(f"bill lookup failed: {e}", file=sys.stderr)
        error_count += 1

    metrics.append("# HELP ovh_collector_last_run_timestamp Unix ts of last successful collector run")
    metrics.append("# TYPE ovh_collector_last_run_timestamp gauge")
    metrics.append(f"ovh_collector_last_run_timestamp {int(time.time())}")
    metrics.append("# HELP ovh_collector_errors_total Number of errors during the last collector run")
    metrics.append("# TYPE ovh_collector_errors_total gauge")
    metrics.append(f"ovh_collector_errors_total {error_count}")

    # Atomic write so node_exporter never reads a half-written file.
    out_path = Path(os.environ.get("EXTMON_TEXTFILE_OVERRIDE")) if os.environ.get("EXTMON_TEXTFILE_OVERRIDE") else None
    out = str(out_path) if out_path else TEXTFILE
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(Path(out).parent), prefix=".ovh_expiry-", suffix=".prom")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(metrics) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

    # Always return success after writing the textfile. Partial OVH API
    # failures are represented by ovh_collector_errors_total and should alert
    # through Prometheus; failing the oneshot leaves stale/missing metrics.
    return 0


if __name__ == "__main__":
    sys.exit(main())
