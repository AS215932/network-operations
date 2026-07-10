#!/usr/bin/env python3
"""Update production app SHA pins and write a promotion PR body."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
PIN_TARGETS = {
    "noc_agent_version": ("ansible/inventory/host_vars/noc.yml", "AS215932/noc-agent", "noc"),
    "hyrule_mcp_version": ("ansible/inventory/host_vars/noc.yml", "AS215932/hyrule-mcp", "noc"),
    "hyrule_cloud_version": ("ansible/inventory/host_vars/api.yml", "AS215932/hyrule-cloud", "cloud"),
    "hyrule_web_version": ("ansible/inventory/host_vars/web.yml", "AS215932/hyrule-web", "web"),
    "hyrule_network_proxy_version": (
        "ansible/inventory/host_vars/netproxy.yml",
        "AS215932/hyrule-network-proxy",
        "network-proxy",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--noc-agent-sha", default="")
    parser.add_argument("--hyrule-mcp-sha", default="")
    parser.add_argument("--hyrule-cloud-sha", default="")
    parser.add_argument("--hyrule-web-sha", default="")
    parser.add_argument("--hyrule-network-proxy-sha", default="")
    parser.add_argument("--title", default="Promote app SHAs")
    parser.add_argument("--impact", default="Automated app SHA promotion.")
    parser.add_argument("--body-file", default="")
    parser.add_argument(
        "--body-from-ref",
        default="",
        help="render the body from the pin diff between this git ref and the "
        "working tree instead of editing pins; covers carried-forward pins",
    )
    args = parser.parse_args()

    requested = {
        "noc_agent_version": args.noc_agent_sha.strip(),
        "hyrule_mcp_version": args.hyrule_mcp_sha.strip(),
        "hyrule_cloud_version": args.hyrule_cloud_sha.strip(),
        "hyrule_web_version": args.hyrule_web_sha.strip(),
        "hyrule_network_proxy_version": args.hyrule_network_proxy_sha.strip(),
    }
    requested = {key: value for key, value in requested.items() if value}

    if args.body_from_ref:
        if requested:
            raise SystemExit("--body-from-ref cannot be combined with app SHA inputs")
        if not args.body_file:
            raise SystemExit("--body-from-ref requires --body-file")
        changes = ref_changes(args.body_from_ref)
        Path(args.body_file).write_text(render_body(args.title, args.impact, changes))
        for key, _repo, _playbook, old_sha, new_sha in changes:
            print(f"{key}: {old_sha} -> {new_sha}")
        return 0

    if not requested:
        raise SystemExit("no app SHA inputs provided")

    for key, sha in requested.items():
        if not SHA_RE.match(sha):
            raise SystemExit(f"{key} must be a 40-character commit SHA, got {sha!r}")

    changes: list[tuple[str, str, str, str, str]] = []
    for key, new_sha in requested.items():
        rel_path, repo, playbook = PIN_TARGETS[key]
        path = REPO / rel_path
        old_sha = update_pin(path, key, new_sha)
        changes.append((key, repo, playbook, old_sha, new_sha))

    if args.body_file:
        Path(args.body_file).write_text(render_body(args.title, args.impact, changes))

    for key, _repo, _playbook, old_sha, new_sha in changes:
        print(f"{key}: {old_sha} -> {new_sha}")
    return 0


def find_pin(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*([0-9a-fA-F]{{40}})\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def ref_changes(ref: str) -> list[tuple[str, str, str, str, str]]:
    """Pin deltas between a git ref (old) and the working tree (new)."""
    changes: list[tuple[str, str, str, str, str]] = []
    for key, (rel_path, repo, playbook) in PIN_TARGETS.items():
        new_sha = find_pin((REPO / rel_path).read_text(), key)
        shown = subprocess.run(
            ["git", "show", f"{ref}:{rel_path}"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        old_sha = find_pin(shown.stdout, key) if shown.returncode == 0 else None
        if new_sha and old_sha and new_sha != old_sha:
            changes.append((key, repo, playbook, old_sha, new_sha))
    return changes


def update_pin(path: Path, key: str, new_sha: str) -> str:
    text = path.read_text()
    pattern = re.compile(rf"^({re.escape(key)}:\s*)([0-9a-fA-F]{{40}})(\s*)$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"{path.relative_to(REPO)} does not contain {key} with a SHA value")
    old_sha = match.group(2)
    path.write_text(pattern.sub(rf"\g<1>{new_sha}\g<3>", text, count=1))
    return old_sha


def render_body(title: str, impact: str, changes: list[tuple[str, str, str, str, str]]) -> str:
    affected = sorted({playbook for _key, _repo, playbook, old, new in changes if old != new})
    lines = [
        "## Promotion",
        "",
        f"Title: {title}",
        "",
        "App PRs:",
        "- Automated promotion workflow; see linked app commits below.",
        "",
        "Pinned versions:",
    ]
    for key, repo, _playbook, old_sha, new_sha in changes:
        compare = f"https://github.com/{repo}/compare/{old_sha}...{new_sha}"
        lines.append(f"- `{key}`: `{new_sha}` ({compare})")

    lines.extend(
        [
            "",
            "Deploy impact:",
            f"- Affected playbooks: {', '.join(affected) if affected else 'none'}",
            f"- {impact}",
            "",
            "Rollback:",
        ]
    )
    for key, _repo, _playbook, old_sha, _new_sha in changes:
        lines.append(f"- Previous `{key}`: `{old_sha}`")

    lines.extend(
        [
            "",
            "Validation:",
            "- [ ] App CI is green for every promoted SHA.",
            "- [ ] `scripts/ci/iac-static.sh` passes.",
            "- [ ] Promotion PR checks are green.",
            "- [ ] Production environment gate approved after merge.",
            "- [ ] Icinga pre/post snapshot diff reviewed.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
