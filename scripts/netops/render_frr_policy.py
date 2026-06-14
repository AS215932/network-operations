#!/usr/bin/env python3
"""Render the structured FRR policy intent pilot.

The generated policy artifacts are a parity gate only. They do not replace the
committed configs/<host>/frr.conf files and are not deployed directly.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


REPO = Path(__file__).resolve().parents[2]
DEFAULT_INTENT = REPO / "configs" / "frr-policy-intent.yml"
DEFAULT_GENERATED_ROOT = REPO / "ansible" / "generated"


def load_intent(path: Path = DEFAULT_INTENT) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def _merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        result[key] = deepcopy(value)
    return result


def effective_policy(intent: dict[str, Any], host: str) -> dict[str, Any]:
    common = intent.get("common") or {}
    host_intent = (intent.get("hosts") or {}).get(host) or {}
    route_maps = host_intent.get("route_maps")
    if route_maps is None:
        route_maps = common.get("route_maps") or {}

    policy = {
        "schema_version": intent.get("schema_version", 1),
        "host": host,
        "ipv6_prefix_lists": _merge_mapping(
            common.get("ipv6_prefix_lists") or {},
            host_intent.get("ipv6_prefix_lists") or {},
        ),
        "as_path_access_lists": _merge_mapping(
            common.get("as_path_access_lists") or {},
            host_intent.get("as_path_access_lists") or {},
        ),
        "route_maps": deepcopy(route_maps),
        "neighbor_route_maps": deepcopy(host_intent.get("neighbor_route_maps") or {}),
    }
    return normalize_policy(policy)


def normalize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    for entries in policy["ipv6_prefix_lists"].values():
        entries.sort(key=lambda item: (item.get("seq", -1), item.get("action", ""), item.get("value", "")))
    for entries in policy["as_path_access_lists"].values():
        entries.sort(key=lambda item: (item.get("seq") is None, item.get("seq") or -1, item.get("pattern", "")))
    for sequences in policy["route_maps"].values():
        for seq in sequences:
            seq.setdefault("matches", [])
            seq.setdefault("sets", [])
            seq.setdefault("on_match", [])
            seq["matches"].sort()
            seq["sets"].sort()
            seq["on_match"].sort()
        sequences.sort(key=lambda item: (item.get("seq", -1), item.get("action", "")))
    return policy


def policy_json(policy: dict[str, Any]) -> str:
    return json.dumps(policy, indent=2, sort_keys=True) + "\n"


def render_policy_conf(policy: dict[str, Any]) -> str:
    lines = [
        f"! Generated FRR policy parity artifact for {policy['host']}",
        "! Source: configs/frr-policy-intent.yml",
        "! Not deployed directly; configs/<host>/frr.conf remains canonical.",
        "!",
    ]

    for name, entries in sorted(policy["ipv6_prefix_lists"].items()):
        for entry in entries:
            lines.append(f"ipv6 prefix-list {name} seq {entry['seq']} {entry['action']} {entry['value']}")
    lines.append("!")

    for name, entries in sorted(policy["as_path_access_lists"].items()):
        for entry in entries:
            seq = f" seq {entry['seq']}" if entry.get("seq") is not None else ""
            lines.append(f"bgp as-path access-list {name}{seq} {entry['action']} {entry['pattern']}")
    lines.append("!")

    for name, sequences in sorted(policy["route_maps"].items()):
        for sequence in sequences:
            lines.append(f"route-map {name} {sequence['action']} {sequence['seq']}")
            for match in sequence.get("matches", []):
                lines.append(f" match {match}")
            for set_action in sequence.get("sets", []):
                lines.append(f" set {set_action}")
            for on_match in sequence.get("on_match", []):
                lines.append(f" on-match {on_match}")
            lines.append("exit")
            lines.append("!")

    if policy["neighbor_route_maps"]:
        lines.append("! Neighbor route-map attachments for address-family ipv6 unicast")
        for neighbor, attachments in sorted(policy["neighbor_route_maps"].items()):
            for direction in ("in", "out"):
                if direction in attachments:
                    lines.append(f"neighbor {neighbor} route-map {attachments[direction]} {direction}")
        lines.append("!")

    return "\n".join(lines).rstrip() + "\n"


def render_all(intent_path: Path = DEFAULT_INTENT, generated_root: Path = DEFAULT_GENERATED_ROOT) -> list[Path]:
    intent = load_intent(intent_path)
    outputs: list[Path] = []
    for host in sorted((intent.get("hosts") or {})):
        policy = effective_policy(intent, host)
        host_dir = generated_root / host
        host_dir.mkdir(parents=True, exist_ok=True)
        json_path = host_dir / "frr-policy.json"
        conf_path = host_dir / "frr-policy.conf"
        json_path.write_text(policy_json(policy))
        conf_path.write_text(render_policy_conf(policy))
        outputs.extend([json_path, conf_path])
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, default=DEFAULT_INTENT)
    parser.add_argument("--generated-root", type=Path, default=DEFAULT_GENERATED_ROOT)
    args = parser.parse_args(argv)

    for output in render_all(args.intent, args.generated_root):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
