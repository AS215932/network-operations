#!/usr/bin/env python3
"""Decide whether a diff only bumps app SHA pins (an app-promotion PR).

Usage: is-app-pin-only.py BASE HEAD   -> prints "true" or "false"

A diff is pin-only when every changed file is ansible/inventory/host_vars/*.yml
and every added/removed line is a known `<app>_version: <40-hex sha>` pin
(scripts/ci/promote-app-pins.py PIN_TARGETS plus slaved pins). Anything else,
including any error, prints "false" so the full IaC matrix runs.
"""
from __future__ import annotations

import re
import subprocess
import sys

PIN_FILE_RE = re.compile(r"^ansible/inventory/host_vars/[A-Za-z0-9_.-]+\.ya?ml$")
PIN_LINE_RE = re.compile(r"^[a-z][a-z0-9_]*_version:\s*['\"]?[0-9a-f]{40}['\"]?\s*(#.*)?$")


def is_pin_only(files: list[str], diff: str) -> bool:
    if not files or not all(PIN_FILE_RE.match(f) for f in files):
        return False
    changed = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        body = line[1:]
        if not body.strip():
            return False
        if not PIN_LINE_RE.match(body):
            return False
        changed += 1
    return changed > 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("false")
        return 0
    base, head = argv[1], argv[2]
    try:
        files = subprocess.run(
            ["git", "diff", "--name-only", base, head], check=True, capture_output=True, text=True
        ).stdout.split()
        diff = subprocess.run(
            ["git", "diff", "--unified=0", "--no-color", base, head],
            check=True, capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        print("false")
        return 0
    print("true" if is_pin_only(files, diff) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
