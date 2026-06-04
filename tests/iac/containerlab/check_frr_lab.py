#!/usr/bin/env python3
"""Minimal dynamic FRR assertions for the AS215932 Containerlab topology."""

import json
import subprocess
import sys
import time


NODES = ("rtr", "cr1-nl1", "cr1-de1")
CONVERGENCE_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 3


def vtysh(node, command):
    return subprocess.check_output(
        ["docker", "exec", f"clab-as215932-{node}", "vtysh", "-c", f"{command} json"],
        text=True,
    )


def bgp_summary(node):
    summary = json.loads(vtysh(node, "show bgp ipv6 summary"))
    return summary.get("ipv6Unicast", summary)


def non_established_peers(summary):
    peers = summary.get("peers", {})
    return {
        peer: data.get("state")
        for peer, data in peers.items()
        if data.get("state") != "Established"
    }


def check_all_nodes():
    errors = []
    for node in NODES:
        try:
            summary = bgp_summary(node)
        except Exception as exc:  # noqa: BLE001 - CI diagnostic path.
            errors.append(f"{node}: failed to query BGP summary: {exc}")
            continue
        peers = summary.get("peers", {})
        if not peers:
            errors.append(f"{node}: no BGP peers visible in lab")
            continue
        for peer, state in non_established_peers(summary).items():
            errors.append(f"{node}: BGP peer {peer} is {state}, expected Established")
    return errors


def main():
    deadline = time.monotonic() + CONVERGENCE_TIMEOUT_SECONDS
    last_errors = []

    while time.monotonic() < deadline:
        last_errors = check_all_nodes()
        if not last_errors:
            return 0
        time.sleep(POLL_INTERVAL_SECONDS)

    for error in last_errors:
        print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
