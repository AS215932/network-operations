#!/usr/bin/env python3
"""Emit promote-app-pins.py flags for old-branch pins still ahead of the working tree.

Used by promote-apps.yml when rebuilding the promotion branch from main:
compares each app pin on the previous branch tip against the value in the
working tree (main). Pins whose old value is strictly ahead in the app repo
are still-pending promotions and get carried forward; anything identical,
behind, or diverged has been superseded on main and is dropped.

Prints the carry-forward flags on stdout (single line, possibly empty);
per-pin decisions go to stderr. Requires `gh` with repo read access.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def load_promotion_contract() -> tuple[dict[str, tuple[str, str, str]], dict[str, str]]:
    spec = importlib.util.spec_from_file_location(
        "promote_app_pins", HERE / "promote-app-pins.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PIN_TARGETS, module.PROMOTION_FLAGS


def extract_pin(text: str, key: str) -> str | None:
    match = re.search(
        rf"^{re.escape(key)}:\s*[\"']?([0-9a-fA-F]{{40}}|main)[\"']?\s*$",
        text,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def git_show(ref: str, rel_path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    return result.stdout if result.returncode == 0 else None


def compare_status(repo: str, base: str, head: str) -> str:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # A vanished SHA (force-pushed away) means the pin is undeployable
        # anyway - drop it. Anything else (auth, network) must fail loudly
        # rather than silently discard a pending promotion.
        if "404" in result.stderr or "Not Found" in result.stderr:
            return "gone"
        raise SystemExit(
            f"gh api compare failed for {repo} {base}...{head}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--old-ref", required=True, help="previous promotion branch tip"
    )
    args = parser.parse_args()

    flags: list[str] = []
    pin_targets, promotion_flags = load_promotion_contract()
    emitted: set[tuple[str, str]] = set()
    for key, (rel_path, repo, _playbook) in pin_targets.items():
        current = extract_pin((REPO / rel_path).read_text(), key)
        old_text = git_show(args.old_ref, rel_path)
        old = extract_pin(old_text, key) if old_text else None
        if not current or not old or old == current:
            continue
        # A moving `main` value is permitted only for a disabled first-deploy
        # scaffold. It is not a deployed baseline and must never cause an
        # immutable pending first promotion to be discarded on a self-heal.
        status = "ahead" if current == "main" else compare_status(repo, current, old)
        if status == "ahead":
            flag = promotion_flags[key]
            request = (flag, old)
            if request not in emitted:
                flags.extend(request)
                emitted.add(request)
            print(f"carry {key}: {old} (ahead of {current})", file=sys.stderr)
        else:
            print(
                f"drop {key}: {old} superseded by {current} (compare: {status})",
                file=sys.stderr,
            )

    print(" ".join(flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
