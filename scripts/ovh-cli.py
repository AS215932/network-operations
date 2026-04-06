#!/usr/bin/env python3
"""OVH dedicated server management CLI for AS215932 infrastructure."""

import argparse
import json
import sys
import time
from urllib.parse import quote

import ovh


# --- Helpers ---

def get_client():
    try:
        return ovh.Client()
    except ovh.exceptions.InvalidConfiguration as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Create ovh.conf or set OVH_ENDPOINT / OVH_APPLICATION_KEY / "
              "OVH_APPLICATION_SECRET / OVH_CONSUMER_KEY env vars.", file=sys.stderr)
        sys.exit(1)


def resolve_server(client, explicit=None):
    if explicit:
        return explicit
    servers = client.get("/dedicated/server")
    if len(servers) == 0:
        print("No dedicated servers found on this account.", file=sys.stderr)
        sys.exit(1)
    if len(servers) == 1:
        return servers[0]
    print("Multiple servers found — specify one with --server:", file=sys.stderr)
    for s in servers:
        print(f"  {s}", file=sys.stderr)
    sys.exit(1)


def normalize_ip(ip_str):
    if "/" not in ip_str:
        return ip_str + ("/128" if ":" in ip_str else "/32")
    return ip_str


def ip_path(ip_str):
    """URL-encode an IP/CIDR for use in OVH API paths."""
    return quote(normalize_ip(ip_str), safe="")


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


def fmt_json(data):
    print(json.dumps(data, indent=2))


def wait_for_task(client, server, task_id, timeout=300):
    print(f"Waiting for task {task_id}...", end="", flush=True)
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        task = client.get(f"/dedicated/server/{server}/task/{task_id}")
        status = task.get("status", "unknown")
        if status != last_status:
            print(f" {status}", end="", flush=True)
            last_status = status
        if status == "done":
            print()
            return task
        if status in ("cancelled", "customerError", "ovhError"):
            print()
            print(f"Task {task_id} failed: {status}", file=sys.stderr)
            comment = task.get("comment", "")
            if comment:
                print(f"  {comment}", file=sys.stderr)
            sys.exit(1)
        time.sleep(5)
        print(".", end="", flush=True)
    print()
    print(f"Timeout waiting for task {task_id} after {timeout}s. "
          f"Check with: ovh-cli.py task info {task_id}", file=sys.stderr)
    sys.exit(1)


# --- Server commands ---

def cmd_server_list(client, args):
    servers = client.get("/dedicated/server")
    for s in servers:
        print(s)


def cmd_server_info(client, args):
    info = client.get(f"/dedicated/server/{args.server}")
    out = fmt_json if args.json else fmt
    out(info)


def cmd_server_ips(client, args):
    ips = client.get(f"/dedicated/server/{args.server}/ips")
    for ip in ips:
        print(ip)


# --- IP commands ---

def cmd_ip_list(client, args):
    kwargs = {}
    if args.type:
        kwargs["type"] = args.type
    ips = client.get("/ip", **kwargs)
    for ip in ips:
        print(ip)


def cmd_ip_info(client, args):
    info = client.get(f"/ip/{ip_path(args.ip)}")
    out = fmt_json if args.json else fmt
    out(info)


def cmd_ip_reverse(client, args):
    bare_ip = args.ip.split("/")[0]
    result = client.post(f"/ip/{ip_path(args.ip)}/reverse", ipReverse=bare_ip, reverse=args.hostname)
    out = fmt_json if args.json else fmt
    out(result)


# --- Virtual MAC commands ---

def cmd_vmac_list(client, args):
    macs = client.get(f"/dedicated/server/{args.server}/virtualMac")
    if not macs:
        print("No virtual MACs configured.")
        return
    for mac in macs:
        info = client.get(f"/dedicated/server/{args.server}/virtualMac/{mac}")
        addrs = client.get(f"/dedicated/server/{args.server}/virtualMac/{mac}/virtualAddress")
        ips = []
        for addr_ip in addrs:
            detail = client.get(f"/dedicated/server/{args.server}/virtualMac/{mac}/virtualAddress/{addr_ip}")
            vm_name = detail.get("virtualMachineName", "")
            ips.append(f"{addr_ip} ({vm_name})" if vm_name else addr_ip)
        mac_type = info.get("type", "?")
        print(f"{mac}  type={mac_type}  ips={', '.join(ips) if ips else '(none)'}")


def cmd_vmac_info(client, args):
    info = client.get(f"/dedicated/server/{args.server}/virtualMac/{args.mac}")
    addrs = client.get(f"/dedicated/server/{args.server}/virtualMac/{args.mac}/virtualAddress")
    out = fmt_json if args.json else fmt
    out(info)
    if addrs:
        print("\nBound IPs:")
        for addr_ip in addrs:
            detail = client.get(f"/dedicated/server/{args.server}/virtualMac/{args.mac}/virtualAddress/{addr_ip}")
            out(detail, indent=1) if not args.json else out(detail)


def cmd_vmac_create(client, args):
    mac_type = args.type or "ovh"
    print(f"Creating virtual MAC for {args.ip} (type={mac_type}, vm={args.vm_name})...")
    result = client.post(
        f"/dedicated/server/{args.server}/virtualMac",
        ipAddress=args.ip,
        type=mac_type,
        virtualMachineName=args.vm_name,
    )
    task_id = result.get("taskId")
    if not task_id:
        print("Unexpected response:", file=sys.stderr)
        fmt(result)
        sys.exit(1)

    if args.no_wait:
        print(f"Task created: {task_id}")
        return

    wait_for_task(client, args.server, task_id)

    # Fetch the newly created MAC
    macs = client.get(f"/dedicated/server/{args.server}/virtualMac")
    for mac in macs:
        addrs = client.get(f"/dedicated/server/{args.server}/virtualMac/{mac}/virtualAddress")
        if args.ip in addrs:
            info = client.get(f"/dedicated/server/{args.server}/virtualMac/{mac}")
            print(f"\nVirtual MAC created:")
            print(f"  MAC: {mac}")
            print(f"  Type: {info.get('type', '?')}")
            print(f"  IP: {args.ip}")
            print(f"  VM: {args.vm_name}")
            return
    print("Task completed but could not find the new MAC. Check: ovh-cli.py vmac list")


def cmd_vmac_add_ip(client, args):
    result = client.post(
        f"/dedicated/server/{args.server}/virtualMac/{args.mac}/virtualAddress",
        ipAddress=args.ip,
        virtualMachineName=args.vm_name,
    )
    out = fmt_json if args.json else fmt
    out(result)


def cmd_vmac_remove_ip(client, args):
    if not args.yes:
        confirm(f"Remove {args.ip} from virtual MAC {args.mac}?")
    client.delete(
        f"/dedicated/server/{args.server}/virtualMac/{args.mac}/virtualAddress/{args.ip}"
    )
    print(f"Removed {args.ip} from {args.mac}.")


# --- Task commands ---

def cmd_task_list(client, args):
    tasks = client.get(f"/dedicated/server/{args.server}/task")
    if not tasks:
        print("No tasks.")
        return
    # Show most recent first, fetch details for last 10
    for task_id in sorted(tasks, reverse=True)[:10]:
        task = client.get(f"/dedicated/server/{args.server}/task/{task_id}")
        status = task.get("status", "?")
        func = task.get("function", "?")
        done_date = task.get("doneDate", "")
        print(f"  {task_id}  {status:<15} {func:<30} {done_date}")


def cmd_task_info(client, args):
    task = client.get(f"/dedicated/server/{args.server}/task/{args.task_id}")
    out = fmt_json if args.json else fmt
    out(task)


def cmd_task_wait(client, args):
    wait_for_task(client, args.server, args.task_id, timeout=args.timeout)
    print("Done.")


# --- Argparse ---

def build_parser():
    parser = argparse.ArgumentParser(
        prog="ovh-cli.py",
        description="OVH dedicated server management CLI",
    )
    parser.add_argument("--server", help="Server name (auto-detected if only one)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")

    subs = parser.add_subparsers(dest="command")

    # --- server ---
    server_p = subs.add_parser("server", help="Server management")
    server_subs = server_p.add_subparsers(dest="subcommand")

    server_subs.add_parser("list", help="List dedicated servers").set_defaults(
        func=cmd_server_list, needs_server=False)
    server_subs.add_parser("info", help="Show server details").set_defaults(
        func=cmd_server_info, needs_server=True)
    server_subs.add_parser("ips", help="List server IPs").set_defaults(
        func=cmd_server_ips, needs_server=True)

    # --- ip ---
    ip_p = subs.add_parser("ip", help="IP management")
    ip_subs = ip_p.add_subparsers(dest="subcommand")

    ip_list = ip_subs.add_parser("list", help="List IPs")
    ip_list.add_argument("--type", help="Filter by type (e.g. failover)")
    ip_list.set_defaults(func=cmd_ip_list, needs_server=False)

    ip_info = ip_subs.add_parser("info", help="Show IP details")
    ip_info.add_argument("ip", help="IP address (with or without CIDR)")
    ip_info.set_defaults(func=cmd_ip_info, needs_server=False)

    ip_rev = ip_subs.add_parser("reverse", help="Set reverse DNS")
    ip_rev.add_argument("ip", help="IP address")
    ip_rev.add_argument("hostname", help="Reverse DNS hostname (FQDN with trailing dot)")
    ip_rev.set_defaults(func=cmd_ip_reverse, needs_server=False)

    # --- vmac ---
    vmac_p = subs.add_parser("vmac", help="Virtual MAC management")
    vmac_subs = vmac_p.add_subparsers(dest="subcommand")

    vmac_subs.add_parser("list", help="List virtual MACs").set_defaults(
        func=cmd_vmac_list, needs_server=True)

    vmac_info = vmac_subs.add_parser("info", help="Show virtual MAC details")
    vmac_info.add_argument("mac", help="MAC address")
    vmac_info.set_defaults(func=cmd_vmac_info, needs_server=True)

    vmac_create = vmac_subs.add_parser("create", help="Create virtual MAC for a failover IP")
    vmac_create.add_argument("ip", help="Failover IPv4 address")
    vmac_create.add_argument("vm_name", help="Virtual machine name (e.g. rtr)")
    vmac_create.add_argument("--type", choices=["ovh", "vmware"], default="ovh",
                             help="MAC type (default: ovh)")
    vmac_create.add_argument("--no-wait", action="store_true", help="Don't wait for task completion")
    vmac_create.set_defaults(func=cmd_vmac_create, needs_server=True)

    vmac_add = vmac_subs.add_parser("add-ip", help="Add IP to existing virtual MAC")
    vmac_add.add_argument("mac", help="MAC address")
    vmac_add.add_argument("ip", help="IP address to bind")
    vmac_add.add_argument("vm_name", help="Virtual machine name")
    vmac_add.set_defaults(func=cmd_vmac_add_ip, needs_server=True)

    vmac_rm = vmac_subs.add_parser("remove-ip", help="Remove IP from virtual MAC")
    vmac_rm.add_argument("mac", help="MAC address")
    vmac_rm.add_argument("ip", help="IP address to unbind")
    vmac_rm.set_defaults(func=cmd_vmac_remove_ip, needs_server=True)

    # --- task ---
    task_p = subs.add_parser("task", help="Task management")
    task_subs = task_p.add_subparsers(dest="subcommand")

    task_subs.add_parser("list", help="List recent tasks").set_defaults(
        func=cmd_task_list, needs_server=True)

    task_info = task_subs.add_parser("info", help="Show task details")
    task_info.add_argument("task_id", type=int, help="Task ID")
    task_info.set_defaults(func=cmd_task_info, needs_server=True)

    task_wait = task_subs.add_parser("wait", help="Wait for task completion")
    task_wait.add_argument("task_id", type=int, help="Task ID")
    task_wait.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")
    task_wait.set_defaults(func=cmd_task_wait, needs_server=True)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    client = get_client()

    if getattr(args, "needs_server", False):
        args.server = resolve_server(client, args.server)

    try:
        args.func(client, args)
    except ovh.exceptions.ResourceNotFoundError as e:
        print(f"Not found: {e}", file=sys.stderr)
        sys.exit(1)
    except ovh.exceptions.BadParametersError as e:
        print(f"Bad parameters: {e}", file=sys.stderr)
        sys.exit(1)
    except ovh.exceptions.ResourceConflictError as e:
        print(f"Conflict: {e}", file=sys.stderr)
        sys.exit(1)
    except ovh.exceptions.APIError as e:
        print(f"OVH API error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
