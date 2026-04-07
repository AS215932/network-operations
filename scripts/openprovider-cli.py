#!/usr/bin/env python3
"""Openprovider management CLI for AS215932 infrastructure."""

import argparse
import configparser
import json
import os
import sys

import requests

API_BASE = "https://api.openprovider.eu/v1beta"
CONFIG_FILENAME = "openprovider.conf"


# --- Client ---

class OPClient:
    """Thin wrapper around the Openprovider REST API."""

    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    @classmethod
    def login(cls, username, password):
        r = requests.post(f"{API_BASE}/auth/login", json={
            "username": username,
            "password": password,
        })
        data = _check(r)
        token = data["data"]["token"]
        return cls(token)

    def get(self, path, params=None):
        r = self.session.get(f"{API_BASE}{path}", params=params)
        return _check(r)

    def post(self, path, body=None):
        r = self.session.post(f"{API_BASE}{path}", json=body or {})
        return _check(r)

    def put(self, path, body=None):
        r = self.session.put(f"{API_BASE}{path}", json=body or {})
        return _check(r)

    def delete(self, path, params=None):
        r = self.session.delete(f"{API_BASE}{path}", params=params)
        return _check(r)


def _check(r):
    try:
        data = r.json()
    except ValueError:
        print(f"HTTP {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    code = data.get("code", 0)
    if code != 0:
        desc = data.get("desc", "Unknown error")
        print(f"API error {code}: {desc}", file=sys.stderr)
        # Print warnings if any
        for w in data.get("warnings", []):
            print(f"  warning: {w}", file=sys.stderr)
        sys.exit(1)
    return data


# --- Config & auth ---

def find_config():
    """Search for openprovider.conf: ./openprovider.conf, then script dir."""
    candidates = [
        os.path.join(os.getcwd(), CONFIG_FILENAME),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), CONFIG_FILENAME),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def get_client():
    """Authenticate and return an OPClient. Reads creds from config or env."""
    username = os.environ.get("OP_USERNAME")
    password = os.environ.get("OP_PASSWORD")

    if not (username and password):
        config_path = find_config()
        if config_path:
            cfg = configparser.ConfigParser()
            cfg.read(config_path)
            section = cfg["openprovider"] if "openprovider" in cfg else cfg[cfg.default_section]
            username = username or section.get("username")
            password = password or section.get("password")

    if not (username and password):
        print("Credentials not found. Set OP_USERNAME/OP_PASSWORD env vars "
              "or create openprovider.conf.", file=sys.stderr)
        sys.exit(1)

    return OPClient.login(username, password)


# --- Helpers ---

def split_domain(fqdn):
    """Split 'servify.network' → {'name': 'servify', 'extension': 'network'}."""
    parts = fqdn.rstrip(".").rsplit(".", 1)
    if len(parts) != 2:
        print(f"Cannot split domain '{fqdn}' into name + extension.", file=sys.stderr)
        sys.exit(1)
    return {"name": parts[0], "extension": parts[1]}


def confirm(msg):
    try:
        answer = input(f"{msg} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if answer not in ("y", "yes"):
        print("Aborted.", file=sys.stderr)
        sys.exit(1)


def fmt(data, indent=0):
    prefix = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                print(f"{prefix}{k}:")
                fmt(v, indent + 1)
            else:
                print(f"{prefix}{k}: {v}")
    elif isinstance(data, list):
        if not data:
            print(f"{prefix}(empty)")
        elif all(isinstance(x, (str, int, float, bool)) for x in data):
            for item in data:
                print(f"{prefix}- {item}")
        else:
            for item in data:
                fmt(item, indent)
                if indent == 0:
                    print()
    else:
        print(f"{prefix}{data}")


def out(args, data):
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        fmt(data)


# --- Domain commands ---

def cmd_domain_list(client, args):
    params = {"limit": args.limit, "offset": 0}
    if args.extension:
        params["extension"] = args.extension
    if args.pattern:
        params["domain_name_pattern"] = args.pattern
    data = client.get("/domains", params=params)
    results = data.get("data", {}).get("results", [])
    if not results:
        print("No domains found.")
        return
    for d in results:
        dom = d.get("domain", {})
        name = f"{dom.get('name', '?')}.{dom.get('extension', '?')}"
        status = d.get("status", "?")
        exp = d.get("expiration_date", "?")
        ns_group = d.get("ns_group", "")
        ns_info = f"  ns_group={ns_group}" if ns_group else ""
        print(f"  {d.get('id', '?'):>8}  {name:<35} {status:<10} exp={exp}{ns_info}")


def cmd_domain_info(client, args):
    data = client.get(f"/domains/{args.id}")
    out(args, data.get("data", {}))


def cmd_domain_update_ns(client, args):
    """Update nameservers on a domain at the registrar level."""
    # Parse nameserver args: name,ip,ip6 or just name
    ns_list = []
    for i, ns_str in enumerate(args.nameservers):
        parts = ns_str.split(",")
        ns = {"name": parts[0], "seq_nr": i + 1}
        if len(parts) > 1 and parts[1]:
            ns["ip"] = parts[1]
        if len(parts) > 2 and parts[2]:
            ns["ip6"] = parts[2]
        ns_list.append(ns)

    print("Setting nameservers:")
    for ns in ns_list:
        glue = ""
        if ns.get("ip") or ns.get("ip6"):
            glue = f"  (glue: {ns.get('ip', '')} {ns.get('ip6', '')})"
        print(f"  {ns['seq_nr']}. {ns['name']}{glue}")

    if not args.yes:
        confirm(f"Update nameservers for domain ID {args.id}?")

    body = {"name_servers": ns_list}
    data = client.put(f"/domains/{args.id}", body=body)
    print("Nameservers updated.")
    if args.json:
        out(args, data)


# --- DNS zone commands ---

def cmd_zone_list(client, args):
    params = {"limit": args.limit, "offset": 0}
    if args.type:
        params["type"] = args.type
    if args.pattern:
        params["name_pattern"] = args.pattern
    data = client.get("/dns/zones", params=params)
    results = data.get("data", {}).get("results", [])
    if not results:
        print("No zones found.")
        return
    for z in results:
        ztype = z.get("type", "?")
        active = "active" if z.get("active") else "inactive"
        ip = z.get("ip", "")
        ip_info = f"  master={ip}" if ip else ""
        print(f"  {z.get('name', '?'):<45} {ztype:<8} {active}{ip_info}")


def cmd_zone_info(client, args):
    params = {}
    if args.records:
        params["with_records"] = "true"
    data = client.get(f"/dns/zones/{args.zone}", params=params)
    out(args, data.get("data", {}))


def cmd_zone_create(client, args):
    body = {
        "domain": split_domain(args.zone),
        "type": args.type,
    }
    if args.type == "slave":
        if not args.master_ip:
            print("--master-ip is required for slave zones.", file=sys.stderr)
            sys.exit(1)
        body["master_ip"] = args.master_ip

    print(f"Creating {args.type} zone: {args.zone}")
    if args.type == "slave":
        print(f"  master: {args.master_ip}")

    if not args.yes:
        confirm("Proceed?")

    data = client.post("/dns/zones", body=body)
    print(f"Zone created: {args.zone}")
    if args.json:
        out(args, data)


def cmd_zone_delete(client, args):
    if not args.yes:
        confirm(f"Delete zone {args.zone}?")
    client.delete(f"/dns/zones/{args.zone}")
    print(f"Zone deleted: {args.zone}")


def cmd_zone_records(client, args):
    params = {"limit": 250, "offset": 0}
    if args.type:
        params["type"] = args.type
    data = client.get(f"/dns/zones/{args.zone}/records", params=params)
    results = data.get("data", {}).get("results", [])
    if not results:
        print("No records.")
        return
    if args.json:
        print(json.dumps(results, indent=2))
        return
    for r in results:
        prio = f"  prio={r['prio']}" if r.get("prio") else ""
        print(f"  {r.get('name', '@'):<40} {r.get('ttl', ''):>6}  {r.get('type', '?'):<8} {r.get('value', '')}{prio}")


def cmd_zone_update(client, args):
    """Add, remove, or replace records in a zone."""
    body = {"records": {}}

    if args.action == "add":
        body["records"]["add"] = [_parse_record(args)]
    elif args.action == "remove":
        body["records"]["remove"] = [_parse_record(args)]
    elif args.action == "replace":
        # Replace removes all records of the given type+name, then adds the new one
        body["records"]["replace"] = [_parse_record(args)]

    print(f"{args.action.capitalize()} record in {args.zone}:")
    fmt(_parse_record(args), indent=1)

    if not args.yes:
        confirm("Proceed?")

    data = client.put(f"/dns/zones/{args.zone}", body=body)
    print("Zone updated.")
    if args.json:
        out(args, data)


def _parse_record(args):
    rec = {
        "name": args.name,
        "type": args.rtype,
        "value": args.value,
        "ttl": args.ttl,
    }
    if args.prio is not None:
        rec["prio"] = args.prio
    return rec


# --- Nameserver commands ---

def cmd_ns_list(client, args):
    params = {"limit": 100, "offset": 0}
    if args.pattern:
        params["pattern"] = args.pattern
    data = client.get("/dns/nameservers", params=params)
    results = data.get("data", {}).get("results", [])
    if not results:
        print("No nameservers found.")
        return
    for ns in results:
        ip = ns.get("ip", "")
        ip6 = ns.get("ip6", "")
        addrs = ", ".join(filter(None, [ip, ip6]))
        print(f"  {ns.get('name', '?'):<40} {addrs}")


def cmd_ns_info(client, args):
    data = client.get(f"/dns/nameservers/{args.name}")
    out(args, data.get("data", {}))


def cmd_ns_create(client, args):
    body = {"name": args.name}
    if args.ip:
        body["ip"] = args.ip
    if args.ip6:
        body["ip6"] = args.ip6
    print(f"Creating nameserver: {args.name}")
    if args.ip:
        print(f"  IPv4: {args.ip}")
    if args.ip6:
        print(f"  IPv6: {args.ip6}")
    if not args.yes:
        confirm("Proceed?")
    data = client.post("/dns/nameservers", body=body)
    print(f"Nameserver created: {args.name}")
    if args.json:
        out(args, data)


def cmd_ns_delete(client, args):
    if not args.yes:
        confirm(f"Delete nameserver {args.name}?")
    client.delete(f"/dns/nameservers/{args.name}")
    print(f"Nameserver deleted: {args.name}")


# --- NS group commands ---

def cmd_nsgroup_list(client, args):
    params = {"limit": 100, "offset": 0}
    data = client.get("/dns/nameservers/groups", params=params)
    results = data.get("data", {}).get("results", [])
    if not results:
        print("No nameserver groups found.")
        return
    for g in results:
        nss = g.get("name_servers", [])
        names = ", ".join(ns.get("name", "?") for ns in nss)
        domains = g.get("domain_count", 0)
        print(f"  {g.get('ns_group', '?'):<30} domains={domains}  ns=[{names}]")


def cmd_nsgroup_info(client, args):
    data = client.get(f"/dns/nameservers/groups/{args.group}")
    out(args, data.get("data", {}))


def cmd_nsgroup_create(client, args):
    ns_list = []
    for i, ns_str in enumerate(args.nameservers):
        parts = ns_str.split(",")
        ns = {"name": parts[0], "seq_nr": i + 1}
        if len(parts) > 1 and parts[1]:
            ns["ip"] = parts[1]
        if len(parts) > 2 and parts[2]:
            ns["ip6"] = parts[2]
        ns_list.append(ns)

    print(f"Creating NS group: {args.group}")
    for ns in ns_list:
        print(f"  {ns['seq_nr']}. {ns['name']}")

    if not args.yes:
        confirm("Proceed?")

    body = {"ns_group": args.group, "name_servers": ns_list}
    data = client.post("/dns/nameservers/groups", body=body)
    print(f"NS group created: {args.group}")
    if args.json:
        out(args, data)


def cmd_nsgroup_delete(client, args):
    if not args.yes:
        confirm(f"Delete NS group {args.group}?")
    client.delete(f"/dns/nameservers/groups/{args.group}")
    print(f"NS group deleted: {args.group}")


# --- Argparse ---

def build_parser():
    parser = argparse.ArgumentParser(
        prog="openprovider-cli.py",
        description="Openprovider management CLI",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")

    subs = parser.add_subparsers(dest="command")

    # --- domain ---
    dom_p = subs.add_parser("domain", help="Domain management")
    dom_subs = dom_p.add_subparsers(dest="subcommand")

    dom_list = dom_subs.add_parser("list", help="List domains")
    dom_list.add_argument("--extension", help="Filter by TLD (e.g. network)")
    dom_list.add_argument("--pattern", help="Domain name pattern")
    dom_list.add_argument("--limit", type=int, default=100)
    dom_list.set_defaults(func=cmd_domain_list)

    dom_info = dom_subs.add_parser("info", help="Show domain details")
    dom_info.add_argument("id", type=int, help="Domain ID")
    dom_info.set_defaults(func=cmd_domain_info)

    dom_ns = dom_subs.add_parser("update-ns", help="Set nameservers on a domain")
    dom_ns.add_argument("id", type=int, help="Domain ID")
    dom_ns.add_argument("nameservers", nargs="+",
                        help="Nameservers: name[,ip][,ip6] (e.g. ns1.example.com,1.2.3.4,2001:db8::1)")
    dom_ns.set_defaults(func=cmd_domain_update_ns)

    # --- zone ---
    zone_p = subs.add_parser("zone", help="DNS zone management")
    zone_subs = zone_p.add_subparsers(dest="subcommand")

    zone_list = zone_subs.add_parser("list", help="List DNS zones")
    zone_list.add_argument("--type", choices=["master", "slave"], help="Filter by zone type")
    zone_list.add_argument("--pattern", help="Zone name pattern")
    zone_list.add_argument("--limit", type=int, default=100)
    zone_list.set_defaults(func=cmd_zone_list)

    zone_info = zone_subs.add_parser("info", help="Show zone details")
    zone_info.add_argument("zone", help="Zone name (e.g. servify.network)")
    zone_info.add_argument("--records", action="store_true", help="Include records")
    zone_info.set_defaults(func=cmd_zone_info)

    zone_create = zone_subs.add_parser("create", help="Create DNS zone")
    zone_create.add_argument("zone", help="Zone name")
    zone_create.add_argument("--type", choices=["master", "slave"], default="master")
    zone_create.add_argument("--master-ip", help="Master IP (required for slave zones)")
    zone_create.set_defaults(func=cmd_zone_create)

    zone_del = zone_subs.add_parser("delete", help="Delete DNS zone")
    zone_del.add_argument("zone", help="Zone name")
    zone_del.set_defaults(func=cmd_zone_delete)

    zone_rec = zone_subs.add_parser("records", help="List zone records")
    zone_rec.add_argument("zone", help="Zone name")
    zone_rec.add_argument("--type", help="Filter by record type (A, AAAA, etc.)")
    zone_rec.set_defaults(func=cmd_zone_records)

    zone_upd = zone_subs.add_parser("record", help="Add/remove/replace a record")
    zone_upd.add_argument("zone", help="Zone name")
    zone_upd.add_argument("action", choices=["add", "remove", "replace"])
    zone_upd.add_argument("name", help="Record name (e.g. www)")
    zone_upd.add_argument("rtype", help="Record type (A, AAAA, CNAME, etc.)")
    zone_upd.add_argument("value", help="Record value")
    zone_upd.add_argument("--ttl", type=int, default=3600, help="TTL (default: 3600)")
    zone_upd.add_argument("--prio", type=int, help="Priority (for MX, SRV)")
    zone_upd.set_defaults(func=cmd_zone_update)

    # --- ns ---
    ns_p = subs.add_parser("ns", help="Nameserver management")
    ns_subs = ns_p.add_subparsers(dest="subcommand")

    ns_list = ns_subs.add_parser("list", help="List nameservers")
    ns_list.add_argument("--pattern", help="Name pattern")
    ns_list.set_defaults(func=cmd_ns_list)

    ns_info = ns_subs.add_parser("info", help="Show nameserver details")
    ns_info.add_argument("name", help="Nameserver FQDN")
    ns_info.set_defaults(func=cmd_ns_info)

    ns_create = ns_subs.add_parser("create", help="Create nameserver (glue record)")
    ns_create.add_argument("name", help="Nameserver FQDN (e.g. ns1.servify.network)")
    ns_create.add_argument("--ip", help="IPv4 address")
    ns_create.add_argument("--ip6", help="IPv6 address")
    ns_create.set_defaults(func=cmd_ns_create)

    ns_del = ns_subs.add_parser("delete", help="Delete nameserver")
    ns_del.add_argument("name", help="Nameserver FQDN")
    ns_del.set_defaults(func=cmd_ns_delete)

    # --- nsgroup ---
    nsg_p = subs.add_parser("nsgroup", help="Nameserver group management")
    nsg_subs = nsg_p.add_subparsers(dest="subcommand")

    nsg_list = nsg_subs.add_parser("list", help="List NS groups")
    nsg_list.set_defaults(func=cmd_nsgroup_list)

    nsg_info = nsg_subs.add_parser("info", help="Show NS group details")
    nsg_info.add_argument("group", help="Group name")
    nsg_info.set_defaults(func=cmd_nsgroup_info)

    nsg_create = nsg_subs.add_parser("create", help="Create NS group")
    nsg_create.add_argument("group", help="Group name")
    nsg_create.add_argument("nameservers", nargs="+",
                            help="Nameservers: name[,ip][,ip6]")
    nsg_create.set_defaults(func=cmd_nsgroup_create)

    nsg_del = nsg_subs.add_parser("delete", help="Delete NS group")
    nsg_del.add_argument("group", help="Group name")
    nsg_del.set_defaults(func=cmd_nsgroup_delete)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    client = get_client()

    try:
        args.func(client, args)
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("Request timed out.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
