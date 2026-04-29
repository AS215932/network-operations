#!/usr/bin/env python3
"""
Render the firewall role's templates locally without invoking ansible.

This is a stand-in for `ansible-playbook --check --diff --tags validate`
when ansible isn't installed on the workstation. It walks the inventory
+ group_vars + host_vars, picks the right template per host, and writes
the rendered output to ansible/generated/<host>/{nftables.conf,pf.conf}.

Usage:
    cd ansible
    ./scripts/render-firewall.py            # render every host
    ./scripts/render-firewall.py rtr dns    # render specific hosts
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ANSIBLE_DIR = Path(__file__).resolve().parent.parent
INV_DIR = ANSIBLE_DIR / "inventory"
ROLE_DIR = ANSIBLE_DIR / "roles" / "firewall"
GEN_DIR = ANSIBLE_DIR / "generated"


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def merge(*dicts: dict) -> dict:
    out: dict = {}
    for d in dicts:
        out.update(d or {})
    return out


def host_groups(inv: dict, host: str) -> list[str]:
    """Return groups (in inheritance order) that contain `host`."""
    groups = []

    def walk(node: dict, path: list[str]):
        for name, child in (node.get("children") or {}).items():
            sub_path = path + [name]
            if (child.get("hosts") or {}).get(host) is not None:
                groups.append(name)
            if child.get("children"):
                walk(child, sub_path)

    walk(inv["all"], ["all"])
    return groups


def all_hosts(inv: dict) -> list[str]:
    seen = set()

    def walk(node: dict):
        for h in (node.get("hosts") or {}):
            seen.add(h)
        for child in (node.get("children") or {}).values():
            walk(child)

    walk(inv["all"])
    return sorted(seen)


def host_inv_vars(inv: dict, host: str) -> dict:
    """Walk groups looking for the host's leaf vars (ansible_host etc)."""

    def walk(node: dict):
        if (node.get("hosts") or {}).get(host) is not None:
            return node["hosts"][host] or {}
        for child in (node.get("children") or {}).values():
            r = walk(child)
            if r is not None:
                return r
        return None

    return walk(inv["all"]) or {}


def build_vars(inv: dict, host: str) -> dict:
    groups = host_groups(inv, host)
    # Precedence: all < OS family < routers/infra_vms < public_facing < host_vars
    files: list[Path] = [INV_DIR / "group_vars" / "all.yml"]
    for g in ["linux", "freebsd", "routers", "infra_vms", "public_facing"]:
        if g in groups:
            files.append(INV_DIR / "group_vars" / f"{g}.yml")
    files.append(INV_DIR / "host_vars" / f"{host}.yml")

    layered: dict = {}
    for f in files:
        layered.update(load_yaml(f))
    layered.update(host_inv_vars(inv, host))
    layered["inventory_hostname"] = host
    return layered


def template_for_host(host: str, groups: list[str]) -> tuple[str, str]:
    """Return (template_name, output_filename)."""
    if host == "rtr":
        return ("nftables-rtr.conf.j2", "nftables.conf")
    if "linux" in groups:
        return ("nftables.conf.j2", "nftables.conf")
    if "freebsd" in groups:
        return ("pf.conf.j2", "pf.conf")
    raise ValueError(f"host {host}: unknown OS — not in linux or freebsd group")


def resolve_recursively(value, ctx: dict, env: Environment, max_passes: int = 5):
    """Re-render strings in ctx until they stabilise (resolves nested {{ }}'s)."""
    for _ in range(max_passes):
        if isinstance(value, str) and "{{" in value:
            value = env.from_string(value).render(ctx)
        elif isinstance(value, list):
            value = [resolve_recursively(v, ctx, env, 1) for v in value]
        elif isinstance(value, dict):
            value = {k: resolve_recursively(v, ctx, env, 1) for k, v in value.items()}
        else:
            break
    return value


def render_host(host: str, inv: dict) -> Path:
    groups = host_groups(inv, host)
    template_name, out_name = template_for_host(host, groups)
    ctx = build_vars(inv, host)

    env = Environment(
        loader=FileSystemLoader(str(ROLE_DIR / "templates")),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )

    # Two-pass: first resolve {{ }} inside variable values (peers.mon.ipv6 etc.),
    # then render the template proper with the resolved context.
    resolved_ctx = {k: resolve_recursively(v, ctx, env) for k, v in ctx.items()}

    out_dir = GEN_DIR / host
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = env.get_template(template_name).render(resolved_ctx)
    out_path = out_dir / out_name
    out_path.write_text(rendered)
    return out_path


def main() -> int:
    inv = load_yaml(INV_DIR / "hosts.yml")
    targets = sys.argv[1:] or [h for h in all_hosts(inv) if h != "dom0"]
    for host in targets:
        try:
            out = render_host(host, inv)
            print(f"  rendered {host:<10} -> {out.relative_to(ANSIBLE_DIR)}")
        except Exception as e:
            print(f"  FAILED   {host:<10} -> {e!r}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
