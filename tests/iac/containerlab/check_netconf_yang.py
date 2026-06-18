#!/usr/bin/env python3
"""Dynamic NETCONF/YANG assertions for the trusted AS215932 FRR lab."""

from __future__ import annotations

import json
import subprocess
import sys
import time


NODES = ("rtr", "cr1-nl1", "cr1-de1")
LAB_PREFIX = "clab-as215932-netconf"
CONVERGENCE_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 3


def docker_exec(node: str, *command: str) -> str:
    return subprocess.check_output(["docker", "exec", f"{LAB_PREFIX}-{node}", *command], text=True)


def vtysh_json(node: str, command: str) -> dict:
    return json.loads(docker_exec(node, "vtysh", "-c", f"{command} json"))


def bgp_summary(node: str) -> dict:
    summary = vtysh_json(node, "show bgp ipv6 summary")
    return summary.get("ipv6Unicast", summary)


def non_established_peers(summary: dict) -> dict[str, str | None]:
    peers = summary.get("peers", {})
    return {peer: data.get("state") for peer, data in peers.items() if data.get("state") != "Established"}


def check_all_bgp_established() -> list[str]:
    errors = []
    for node in NODES:
        try:
            summary = bgp_summary(node)
        except Exception as exc:  # noqa: BLE001 - lab diagnostic path.
            errors.append(f"{node}: failed to query BGP summary: {exc}")
            continue
        peers = summary.get("peers", {})
        if not peers:
            errors.append(f"{node}: no BGP peers visible in lab")
            continue
        for peer, state in non_established_peers(summary).items():
            errors.append(f"{node}: BGP peer {peer} is {state}, expected Established")
    return errors


def wait_for_bgp() -> None:
    deadline = time.monotonic() + CONVERGENCE_TIMEOUT_SECONDS
    last_errors = []
    while time.monotonic() < deadline:
        last_errors = check_all_bgp_established()
        if not last_errors:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError("BGP did not converge: " + "; ".join(last_errors))


def run_netconf_smoke(node: str) -> dict:
    output = docker_exec(node, "python3", "/usr/local/bin/as215932-netconf-smoke.py")
    return json.loads(output)


def main() -> int:
    wait_for_bgp()
    netconf_summary = run_netconf_smoke("rtr")
    wait_for_bgp()

    print(json.dumps({"netconf": netconf_summary, "nodes": list(NODES)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - lab diagnostic path.
        print(f"NETCONF/YANG lab failed: {exc}", file=sys.stderr)
        raise
