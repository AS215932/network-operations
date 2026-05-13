#!/usr/bin/env python3
"""
ai-review.py — invoked by .github/workflows/ai-review.yml.

Reads a PR diff, calls the Claude API with the repo's reviewer prompt,
parses the response, and posts review comments via `gh`.

Env vars (set by the workflow):
  PR_NUMBER             — GitHub PR number to review
  REPO                  — owner/repo (e.g. AS215932/network-operations)
  ANTHROPIC_API_KEY     — repo secret
  GITHUB_TOKEN          — provided by Actions

Caching strategy:
  System prompt + CLAUDE.md are marked cache_control=ephemeral so the
  per-PR diff is the only cache miss after the first run. At ~50k input /
  ~5k output, hot-path cost is roughly 90% input-cache discount.

Limits:
  - MAX_INPUT_CHARS bounds the diff sent to Claude (truncate after).
  - Output JSON is parsed strictly; malformed responses post as a single
    summary comment with the parse error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import anthropic
except ImportError:
    sys.stderr.write("ERROR: anthropic SDK not installed (pip install anthropic)\n")
    sys.exit(2)

REPO = os.environ["REPO"]
PR_NUMBER = os.environ["PR_NUMBER"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("AI_REVIEW_MODEL", "claude-opus-4-7")
MAX_INPUT_CHARS = int(os.environ.get("AI_REVIEW_MAX_INPUT_CHARS", "180000"))
MAX_OUTPUT_TOKENS = int(os.environ.get("AI_REVIEW_MAX_OUTPUT_TOKENS", "5000"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
REVIEWER_PROMPT = REPO_ROOT / "docs" / "ci" / "ai-review-prompt.md"


def gh(*args: str, check: bool = True) -> str:
    """Run `gh` and return stdout. Inherits GITHUB_TOKEN from env."""
    res = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=check
    )
    return res.stdout


def fetch_pr_context() -> dict[str, Any]:
    diff = gh("pr", "diff", PR_NUMBER, "--repo", REPO)
    meta = json.loads(
        gh(
            "pr",
            "view",
            PR_NUMBER,
            "--repo",
            REPO,
            "--json",
            "title,body,author,changedFiles,additions,deletions",
        )
    )
    return {"diff": diff, "meta": meta}


def build_messages(ctx: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Return (system_blocks, message_blocks). System blocks are cached."""
    system_blocks = [
        {
            "type": "text",
            "text": REVIEWER_PROMPT.read_text(),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "## Repository conventions (CLAUDE.md)\n\n"
                "Treat the following as authoritative project conventions. "
                "Reference specific sections when applicable in your findings.\n\n"
                + CLAUDE_MD.read_text()
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]

    diff = ctx["diff"]
    truncated = False
    if len(diff) > MAX_INPUT_CHARS:
        diff = diff[:MAX_INPUT_CHARS] + "\n\n[truncated]"
        truncated = True

    meta = ctx["meta"]
    user_text = (
        f"# PR #{PR_NUMBER}: {meta.get('title', '(no title)')}\n\n"
        f"Author: {meta.get('author', {}).get('login', 'unknown')}\n"
        f"Files changed: {meta.get('changedFiles', 0)} "
        f"(+{meta.get('additions', 0)}, -{meta.get('deletions', 0)})\n"
        f"{'**Diff truncated to fit input budget.**' if truncated else ''}\n\n"
        "## PR description\n\n"
        f"{meta.get('body', '') or '(no description provided)'}\n\n"
        "## Unified diff\n\n"
        f"```diff\n{diff}\n```\n\n"
        "Review per the rules in the system prompt. Return JSON only."
    )
    return system_blocks, [{"role": "user", "content": user_text}]


def call_claude(system_blocks, messages) -> dict[str, Any]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_blocks,
        messages=messages,
    )
    body = "".join(b.text for b in resp.content if b.type == "text")
    # Robust to leading prose: try to find the first '{' and last '}'.
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in response: {body[:300]}")
    try:
        return json.loads(body[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"could not parse JSON: {e}\nbody={body[:500]}")


def post_review(review: dict[str, Any]) -> None:
    summary = review.get("summary", "(no summary)")
    classification = review.get("classification", "needs-review")
    findings = review.get("findings", [])

    overall = (
        f"## AI review (classification: `{classification}`)\n\n"
        f"{summary}\n\n"
        + (
            "_No file-level findings._"
            if not findings
            else f"_{len(findings)} file-level finding(s) below._"
        )
        + "\n\n<sub>Posted by `.github/workflows/ai-review.yml`. The AI never approves source PRs.</sub>"
    )

    # Post the overall summary as a comment review.
    gh(
        "pr",
        "comment",
        PR_NUMBER,
        "--repo",
        REPO,
        "--body",
        overall,
    )

    # Then per-finding inline comments via gh api.
    for f in findings:
        path = f.get("file")
        line = f.get("line")
        severity = f.get("severity", "info")
        body = f.get("body", "")
        if not path or not line or not body:
            continue
        prefix = {
            "error": ":x: **error** — ",
            "warning": ":warning: **warning** — ",
            "info": ":information_source: ",
        }.get(severity, "")
        # Use gh api to create a single-line PR review comment.
        # Spec: POST /repos/{owner}/{repo}/pulls/{pull_number}/comments needs
        # commit_id; we use the PR head sha.
        head_sha = gh(
            "pr", "view", PR_NUMBER, "--repo", REPO, "--json", "headRefOid", "-q", ".headRefOid"
        ).strip()
        try:
            subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"/repos/{REPO}/pulls/{PR_NUMBER}/comments",
                    "-f",
                    f"body={prefix}{body}",
                    "-f",
                    f"commit_id={head_sha}",
                    "-f",
                    f"path={path}",
                    "-F",
                    f"line={line}",
                    "-f",
                    "side=RIGHT",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            # Inline-comment failures are non-fatal — fall back to a PR comment.
            sys.stderr.write(
                f"warning: inline comment failed for {path}:{line}: {e.stderr}\n"
            )
            gh(
                "pr",
                "comment",
                PR_NUMBER,
                "--repo",
                REPO,
                "--body",
                f"{prefix}`{path}:{line}` — {body}",
            )

    # If safe-class, set the label so auto-merge can pick it up.
    if classification == "safe-class":
        try:
            gh(
                "pr",
                "edit",
                PR_NUMBER,
                "--repo",
                REPO,
                "--add-label",
                "safe-class",
            )
        except subprocess.CalledProcessError:
            sys.stderr.write("warning: could not add safe-class label\n")


def main() -> int:
    try:
        ctx = fetch_pr_context()
        system_blocks, messages = build_messages(ctx)
        review = call_claude(system_blocks, messages)
        post_review(review)
        return 0
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ai-review failed: {exc}\n")
        # Surface the failure on the PR so it's visible.
        try:
            gh(
                "pr",
                "comment",
                PR_NUMBER,
                "--repo",
                REPO,
                "--body",
                f":x: AI review failed: `{type(exc).__name__}: {exc}`\n\n<sub>See workflow run logs for details.</sub>",
            )
        except subprocess.CalledProcessError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
