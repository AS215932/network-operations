#!/usr/bin/env python3
"""Normalize FRRouting config into stable JSON for semantic diffing.

Production desired state remains configs/<host>/frr.conf. This script parses
that CLI config into a deterministic, YANG-adjacent structure that can be used
by CI, reviews, and future NETCONF/YANG read-only audits before any write path
is considered.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_GENERATED_ROOT = REPO / "ansible" / "generated"


def _tokens(value: str) -> list[str]:
    return value.strip().split()


def _default_vrf(vrf: str | None) -> str:
    return vrf or "default"


def _empty_ospf6_interface() -> dict[str, Any]:
    return {"areas": [], "networks": [], "passive": False}


def _empty_interface(name: str, vrf: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "vrf": _default_vrf(vrf),
        "ipv6_addresses": [],
        "ospf6": _empty_ospf6_interface(),
        "raw_commands": [],
    }


def _empty_bgp_instance(asn: int, vrf: str | None = None) -> dict[str, Any]:
    return {
        "asn": asn,
        "vrf": _default_vrf(vrf),
        "router_id": None,
        "flags": [],
        "neighbors": {},
        "address_families": {},
        "raw_commands": [],
    }


def _empty_bgp_neighbor(address: str) -> dict[str, Any]:
    return {
        "address": address,
        "remote_as": None,
        "description": None,
        "update_source": None,
        "enforce_first_as": True,
        "raw_commands": [],
    }


def _empty_bgp_af() -> dict[str, Any]:
    return {"networks": [], "neighbors": {}, "raw_commands": []}


def _empty_bgp_af_neighbor(address: str) -> dict[str, Any]:
    return {
        "address": address,
        "activate": False,
        "next_hop_self": False,
        "soft_reconfiguration": [],
        "route_maps": {},
        "raw_commands": [],
    }


def _empty_ospf6_instance(vrf: str | None = None) -> dict[str, Any]:
    return {
        "vrf": _default_vrf(vrf),
        "router_id": None,
        "redistribute": [],
        "raw_commands": [],
    }


def _parse_static_route(line: str) -> dict[str, Any]:
    parts = _tokens(line)
    route: dict[str, Any] = {"raw": line}
    if len(parts) >= 3:
        route["afi"] = parts[0]
        route["prefix"] = parts[2]
    if len(parts) >= 4:
        route["next_hop"] = parts[3]
    if len(parts) >= 5:
        route["distance_or_table"] = " ".join(parts[4:])
    return route


def _parse_interface_header(line: str) -> tuple[str, str | None]:
    parts = _tokens(line)
    name = parts[1]
    vrf = None
    if "vrf" in parts:
        idx = parts.index("vrf")
        if idx + 1 < len(parts):
            vrf = parts[idx + 1]
    return name, vrf


def _parse_bgp_header(line: str) -> tuple[int, str | None]:
    parts = _tokens(line)
    asn = int(parts[2])
    vrf = None
    if "vrf" in parts:
        idx = parts.index("vrf")
        if idx + 1 < len(parts):
            vrf = parts[idx + 1]
    return asn, vrf


def _parse_ospf6_header(line: str) -> str | None:
    parts = _tokens(line)
    if "vrf" in parts:
        idx = parts.index("vrf")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _route_map_entry(route_maps: dict[str, Any], name: str, action: str, seq: int) -> dict[str, Any]:
    rm = route_maps.setdefault(name, {"name": name, "sequences": {}})
    seq_key = str(seq)
    return rm["sequences"].setdefault(
        seq_key,
        {
            "action": action,
            "seq": seq,
            "matches": [],
            "sets": [],
            "on_match": [],
            "raw_commands": [],
        },
    )


def _sort_semantic(data: dict[str, Any]) -> dict[str, Any]:
    for family in data["prefix_lists"].values():
        for entries in family.values():
            entries.sort(key=lambda item: (item.get("seq", -1), item.get("raw", "")))

    for entries in data["as_path_access_lists"].values():
        entries.sort(key=lambda item: (item.get("seq") is None, item.get("seq") or -1, item.get("raw", "")))

    for routes in data["static_routes"].values():
        routes.sort(key=lambda item: item.get("raw", ""))

    for interface in data["interfaces"].values():
        interface["ipv6_addresses"].sort()
        interface["ospf6"]["areas"].sort()
        interface["ospf6"]["networks"].sort()
        interface["raw_commands"].sort()

    data["bgp"]["instances"].sort(key=lambda item: (item["vrf"], item["asn"]))
    for instance in data["bgp"]["instances"]:
        instance["flags"].sort()
        instance["raw_commands"].sort()
        for neighbor in instance["neighbors"].values():
            neighbor["raw_commands"].sort()
        for af in instance["address_families"].values():
            af["networks"].sort()
            af["raw_commands"].sort()
            for neighbor in af["neighbors"].values():
                neighbor["soft_reconfiguration"].sort()
                neighbor["raw_commands"].sort()

    data["ospf6"]["instances"].sort(key=lambda item: item["vrf"])
    for instance in data["ospf6"]["instances"]:
        instance["raw_commands"].sort()

    for rm in data["route_maps"].values():
        for seq in rm["sequences"].values():
            seq["matches"].sort()
            seq["sets"].sort()
            seq["on_match"].sort()
            seq["raw_commands"].sort()

    return data


def source_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def parse_frr_config(path: Path, *, host: str | None = None) -> dict[str, Any]:
    text = path.read_text()
    data: dict[str, Any] = {
        "schema_version": 1,
        "host": host or path.parent.name,
        "source": source_label(path),
        "frr_version": None,
        "hostname": None,
        "logging": [],
        "services": [],
        "vrfs": {},
        "static_routes": {"default": []},
        "interfaces": {},
        "prefix_lists": {"ipv4": {}, "ipv6": {}},
        "as_path_access_lists": {},
        "route_maps": {},
        "bgp": {"instances": []},
        "ospf6": {"instances": []},
    }

    current: tuple[str, Any] | None = None
    current_bgp: dict[str, Any] | None = None
    current_af_name: str | None = None
    current_route_map_seq: dict[str, Any] | None = None
    current_interface: dict[str, Any] | None = None
    current_ospf6: dict[str, Any] | None = None
    current_vrf: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue

        if line == "exit-address-family":
            current_af_name = None
            continue
        if line in {"exit", "exit-vrf"}:
            if current and current[0] == "bgp" and current_af_name:
                current_af_name = None
            else:
                current = None
                current_bgp = None
                current_route_map_seq = None
                current_interface = None
                current_ospf6 = None
                current_vrf = None
            continue

        if match := re.match(r"^frr version\s+(\S+)$", line):
            data["frr_version"] = match.group(1)
            continue
        if match := re.match(r"^hostname\s+(.+)$", line):
            data["hostname"] = match.group(1)
            continue
        if line.startswith("log "):
            data["logging"].append(line)
            continue
        if line.startswith("service "):
            data["services"].append(line)
            continue

        if match := re.match(r"^(ip|ipv6) prefix-list\s+(\S+)\s+seq\s+(\d+)\s+(permit|deny)\s+(.+)$", line):
            afi = "ipv6" if match.group(1) == "ipv6" else "ipv4"
            name = match.group(2)
            data["prefix_lists"][afi].setdefault(name, []).append(
                {
                    "name": name,
                    "seq": int(match.group(3)),
                    "action": match.group(4),
                    "value": match.group(5),
                    "raw": line,
                }
            )
            continue

        if match := re.match(r"^bgp as-path access-list\s+(\S+)\s+(?:seq\s+(\d+)\s+)?(permit|deny)\s+(.+)$", line):
            name = match.group(1)
            data["as_path_access_lists"].setdefault(name, []).append(
                {
                    "name": name,
                    "seq": int(match.group(2)) if match.group(2) else None,
                    "action": match.group(3),
                    "pattern": match.group(4),
                    "raw": line,
                }
            )
            continue

        if line.startswith("vrf "):
            current_vrf = _tokens(line)[1]
            data["vrfs"].setdefault(current_vrf, {"name": current_vrf, "static_routes": []})
            data["static_routes"].setdefault(current_vrf, [])
            current = ("vrf", current_vrf)
            continue

        if line.startswith("interface "):
            name, vrf = _parse_interface_header(line)
            current_interface = data["interfaces"].setdefault(name, _empty_interface(name, vrf))
            if vrf:
                current_interface["vrf"] = vrf
            current = ("interface", name)
            continue

        if line.startswith("router bgp "):
            asn, vrf = _parse_bgp_header(line)
            current_bgp = _empty_bgp_instance(asn, vrf)
            data["bgp"]["instances"].append(current_bgp)
            current = ("bgp", len(data["bgp"]["instances"]) - 1)
            continue

        if line.startswith("router ospf6"):
            vrf = _parse_ospf6_header(line)
            current_ospf6 = _empty_ospf6_instance(vrf)
            data["ospf6"]["instances"].append(current_ospf6)
            current = ("ospf6", len(data["ospf6"]["instances"]) - 1)
            continue

        if match := re.match(r"^route-map\s+(\S+)\s+(permit|deny)\s+(\d+)$", line):
            current_route_map_seq = _route_map_entry(data["route_maps"], match.group(1), match.group(2), int(match.group(3)))
            current = ("route-map", match.group(1), int(match.group(3)))
            continue

        if line.startswith("address-family ") and current_bgp is not None:
            current_af_name = line.removeprefix("address-family ")
            current_bgp["address_families"].setdefault(current_af_name, _empty_bgp_af())
            continue

        if line.startswith("ipv6 route "):
            route = _parse_static_route(line)
            vrf = current_vrf or "default"
            data["static_routes"].setdefault(vrf, []).append(route)
            if current_vrf:
                data["vrfs"].setdefault(current_vrf, {"name": current_vrf, "static_routes": []})["static_routes"].append(route)
            continue

        if current and current[0] == "interface" and current_interface is not None:
            current_interface["raw_commands"].append(line)
            if line.startswith("ipv6 address "):
                current_interface["ipv6_addresses"].append(line.removeprefix("ipv6 address "))
            elif line.startswith("ipv6 ospf6 area "):
                current_interface["ospf6"]["areas"].append(line.removeprefix("ipv6 ospf6 area "))
            elif line == "ipv6 ospf6 passive":
                current_interface["ospf6"]["passive"] = True
            elif line.startswith("ipv6 ospf6 network "):
                current_interface["ospf6"]["networks"].append(line.removeprefix("ipv6 ospf6 network "))
            continue

        if current and current[0] == "bgp" and current_bgp is not None:
            if current_af_name:
                af = current_bgp["address_families"].setdefault(current_af_name, _empty_bgp_af())
                af["raw_commands"].append(line)
                if line.startswith("network "):
                    af["networks"].append(line.removeprefix("network "))
                elif line.startswith("neighbor "):
                    parts = _tokens(line)
                    if len(parts) >= 3:
                        address = parts[1]
                        neighbor = af["neighbors"].setdefault(address, _empty_bgp_af_neighbor(address))
                        neighbor["raw_commands"].append(line)
                        command = parts[2]
                        if command == "activate":
                            neighbor["activate"] = True
                        elif command == "next-hop-self":
                            neighbor["next_hop_self"] = True
                        elif command == "soft-reconfiguration":
                            neighbor["soft_reconfiguration"].append(" ".join(parts[3:]) or "enabled")
                        elif command == "route-map" and len(parts) >= 5:
                            neighbor["route_maps"][parts[4]] = parts[3]
                continue

            current_bgp["raw_commands"].append(line)
            if line.startswith("bgp router-id "):
                current_bgp["router_id"] = line.removeprefix("bgp router-id ")
            elif line.startswith("no bgp ") or line.startswith("bgp "):
                current_bgp["flags"].append(line)
            elif line.startswith("neighbor "):
                parts = _tokens(line)
                if len(parts) >= 3:
                    address = parts[1]
                    neighbor = current_bgp["neighbors"].setdefault(address, _empty_bgp_neighbor(address))
                    neighbor["raw_commands"].append(line)
                    command = parts[2]
                    if command == "remote-as" and len(parts) >= 4:
                        neighbor["remote_as"] = int(parts[3])
                    elif command == "description" and len(parts) >= 4:
                        neighbor["description"] = " ".join(parts[3:])
                    elif command == "update-source" and len(parts) >= 4:
                        neighbor["update_source"] = " ".join(parts[3:])
            elif line.startswith("no neighbor "):
                parts = _tokens(line)
                if len(parts) >= 4:
                    address = parts[2]
                    neighbor = current_bgp["neighbors"].setdefault(address, _empty_bgp_neighbor(address))
                    neighbor["raw_commands"].append(line)
                    if parts[3:] == ["enforce-first-as"]:
                        neighbor["enforce_first_as"] = False
            continue

        if current and current[0] == "ospf6" and current_ospf6 is not None:
            current_ospf6["raw_commands"].append(line)
            if line.startswith("ospf6 router-id "):
                current_ospf6["router_id"] = line.removeprefix("ospf6 router-id ")
            elif line.startswith("redistribute "):
                current_ospf6["redistribute"].append(line.removeprefix("redistribute "))
            continue

        if current and current[0] == "route-map" and current_route_map_seq is not None:
            current_route_map_seq["raw_commands"].append(line)
            if line.startswith("match "):
                current_route_map_seq["matches"].append(line.removeprefix("match "))
            elif line.startswith("set "):
                current_route_map_seq["sets"].append(line.removeprefix("set "))
            elif line.startswith("on-match "):
                current_route_map_seq["on_match"].append(line.removeprefix("on-match "))
            continue

    return _sort_semantic(data)


def load_audit_dir(audit_dir: Path) -> dict[str, Any]:
    audit: dict[str, Any] = {"path": str(audit_dir), "json_artifacts": {}, "text_artifacts": {}}
    audit_json = audit_dir / "audit.json"
    if audit_json.exists():
        try:
            audit["metadata"] = json.loads(audit_json.read_text())
        except json.JSONDecodeError as exc:
            audit["metadata_error"] = str(exc)

    for artifact in sorted(audit_dir.glob("*.stdout")):
        text = artifact.read_text()
        key = artifact.name.removesuffix(".stdout")
        try:
            audit["json_artifacts"][key] = json.loads(text)
        except json.JSONDecodeError:
            audit["text_artifacts"][key] = text
    return audit


def semantic_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def write_semantic(config: Path, output: Path, *, host: str | None = None, audit_dir: Path | None = None) -> None:
    data = parse_frr_config(config, host=host)
    if audit_dir is not None:
        data["audit"] = load_audit_dir(audit_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(semantic_json(data))


def render_all() -> list[Path]:
    configs = sorted((REPO / "configs").glob("*/frr.conf"))
    outputs = []
    for config in configs:
        host = config.parent.name
        output = DEFAULT_GENERATED_ROOT / host / "frr-semantic.json"
        write_semantic(config, output, host=host)
        outputs.append(output)
    return outputs


def diff_configs(left: Path, right: Path) -> int:
    left_json = semantic_json(parse_frr_config(left))
    right_json = semantic_json(parse_frr_config(right))
    if left_json == right_json:
        return 0
    sys.stdout.writelines(
        difflib.unified_diff(
            left_json.splitlines(keepends=True),
            right_json.splitlines(keepends=True),
            fromfile=str(left),
            tofile=str(right),
        )
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="render all configs/*/frr.conf files to ansible/generated/<host>/frr-semantic.json")
    parser.add_argument("--config", type=Path, action="append", help="FRR config file to parse; may be passed multiple times")
    parser.add_argument("--host", help="host name for a single --config render; defaults to config parent directory")
    parser.add_argument("--output", type=Path, help="output path for a single --config render; defaults to ansible/generated/<host>/frr-semantic.json")
    parser.add_argument("--audit-dir", type=Path, help="optional frr_yang audit artifact directory to attach to a single render")
    parser.add_argument("--diff", nargs=2, type=Path, metavar=("LEFT", "RIGHT"), help="print a unified diff of normalized semantic JSON for two configs")
    args = parser.parse_args(argv)

    if args.diff:
        return diff_configs(args.diff[0], args.diff[1])

    if args.all or not args.config:
        outputs = render_all()
        for output in outputs:
            print(output)
        return 0

    if args.audit_dir and len(args.config) != 1:
        parser.error("--audit-dir is only supported with a single --config")
    if args.host and len(args.config) != 1:
        parser.error("--host is only supported with a single --config")
    if args.output and len(args.config) != 1:
        parser.error("--output is only supported with a single --config")

    for config in args.config:
        host = args.host or config.parent.name
        output = args.output or DEFAULT_GENERATED_ROOT / host / "frr-semantic.json"
        write_semantic(config, output, host=host, audit_dir=args.audit_dir)
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
