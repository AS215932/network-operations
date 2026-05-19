#!/usr/bin/env python3
"""Minimal dynamic FRR assertions for the AS215932 Containerlab topology."""

import json
import subprocess
import sys


NODES = ("rtr", "cr1-nl1", "cr1-de1")


def vtysh(node, command):
    return subprocess.check_output(
        ["docker", "exec", f"clab-as215932-{node}", "vtysh", "-c", command, "json"],
        text=True,
    )


def main():
    failed = False
    for node in NODES:
        try:
            summary = json.loads(vtysh(node, "show bgp ipv6 summary"))
        except Exception as exc:  # noqa: BLE001 - CI diagnostic path.
            print(f"{node}: failed to query BGP summary: {exc}", file=sys.stderr)
            failed = True
            continue
        peers = summary.get("peers", {})
        if not peers:
            print(f"{node}: no BGP peers visible in lab", file=sys.stderr)
            failed = True
        for peer, data in peers.items():
            state = data.get("state")
            if state != "Established":
                print(f"{node}: BGP peer {peer} is {state}, expected Established", file=sys.stderr)
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
