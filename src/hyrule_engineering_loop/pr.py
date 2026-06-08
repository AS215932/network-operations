"""Commit, push, and optional draft-PR boundary for promoted worktrees."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.policy import validate_pr_remote


class PRBoundaryError(RuntimeError):
    """Raised when PR boundary safety checks fail."""


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise PRBoundaryError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def _approved(state: dict[str, Any]) -> bool:
    return state.get("approval_decision") == "approved"


def _github_pr_enabled(create_github_pr: bool) -> bool:
    if create_github_pr:
        return True
    return os.environ.get("HYRULE_CREATE_GITHUB_PR", "0").lower() in {"1", "true", "yes"}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PRBoundaryError("PR labels and reviewers must be lists of strings")
    return value


def _repos_touched(state: dict[str, Any]) -> list[str]:
    repos: list[str] = []
    for promotion in state.get("promotion_results", []):
        repo = str(promotion.get("repo", ""))
        if repo and repo not in repos:
            repos.append(repo)
    for raw_path in state.get("proposed_mutations", {}):
        if isinstance(raw_path, str) and ":" in raw_path:
            repo = raw_path.split(":", 1)[0]
            if repo and repo not in repos:
                repos.append(repo)
    return repos


def render_pr_body(state: dict[str, Any], *, operator_body: str | None = None) -> str:
    """Render a high-signal PR body from graph state and handoff metadata."""
    handoff_path = state.get("noc_handoff_path")
    noc_handoff = state.get("noc_handoff_metadata", {})
    role_approvals = state.get("role_approvals", {})
    gate_results = state.get("gate_results", [])
    post_deploy_checks = noc_handoff.get("post_deploy_checks", ["review graph state"])

    body_parts = [
        "## Change class",
        str(state.get("change_class", "unknown")),
        "",
        "## Repos touched",
        "\n".join(f"- {repo}" for repo in _repos_touched(state)) or "- none",
        "",
        "## Senior role reviews",
        "\n".join(f"- {role}: {'approved' if approved else 'not approved'}" for role, approved in role_approvals.items())
        or "- none recorded",
        "",
        "## Source-of-truth files consulted",
        "\n".join(f"- {path}" for path in state.get("source_of_truth_files", [])) or "- none recorded",
        "",
        "## Validation gates run",
        "\n".join(f"- {result.get('command', 'unknown')}: {result.get('status', 'unknown')}" for result in gate_results)
        or f"- graph gate status: {state.get('gate_status', 'not_run')}",
        "",
        "## Expected production impact",
        str(state.get("customer_impact", "unknown")),
        "",
        "## Rollback plan",
        str(state.get("rollback_plan") or "Revert this tranche and rerun validation gates."),
        "",
        "## NOC handoff",
        f"- handoff artifact: {handoff_path}" if handoff_path else "- handoff artifact: not rendered",
        f"- rollback trigger: {noc_handoff.get('rollback_trigger', 'operator rejection or post-deploy regression')}",
        f"- expected duration: {noc_handoff.get('expected_duration', 'not specified')}",
        "",
        "## Post-deploy checks",
        "\n".join(f"- {check}" for check in post_deploy_checks) or "- review monitoring",
    ]
    if operator_body:
        body_parts.extend(["", "## Operator notes", operator_body])
    return "\n".join(body_parts).rstrip() + "\n"


def _require_policy_passed(state: dict[str, Any]) -> None:
    if state.get("policy_status") != "passed":
        raise PRBoundaryError("policy_status must be passed before PR publication")


def _require_handoff_for_github_pr(state: dict[str, Any]) -> None:
    raw_path = state.get("noc_handoff_path")
    if not raw_path:
        raise PRBoundaryError("noc_handoff_path is required before GitHub PR creation")
    path = Path(str(raw_path))
    if not path.exists():
        raise PRBoundaryError(f"noc_handoff_path does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PRBoundaryError(f"noc_handoff_path is not valid JSON: {path}") from exc
    if payload.get("schema_version") != 1:
        raise PRBoundaryError("noc_handoff_path must contain schema_version 1")


def _require_pushed_branch(*, worktree_path: Path, remote: str, branch: str, commit_sha: str) -> None:
    completed = _run(["git", "ls-remote", "--heads", remote, branch], cwd=worktree_path)
    if commit_sha not in completed.stdout:
        raise PRBoundaryError(f"pushed branch verification failed for {remote}/{branch}")


def _create_github_pr(
    *,
    worktree_path: Path,
    branch: str,
    title: str,
    body: str,
    labels: list[str],
    reviewers: list[str],
    enabled: bool,
) -> dict[str, Any]:
    if not _github_pr_enabled(enabled):
        return {"created": False, "url": None, "provider": "disabled"}

    args = [
        "gh",
        "pr",
        "create",
        "--draft",
        "--title",
        title,
        "--body",
        body,
        "--head",
        branch,
    ]
    for label in labels:
        args.extend(["--label", label])
    for reviewer in reviewers:
        args.extend(["--reviewer", reviewer])

    completed = _run(
        args,
        cwd=worktree_path,
    )
    return {"created": True, "url": completed.stdout.strip(), "provider": "gh"}


def publish_promoted_worktrees(
    state: dict[str, Any],
    *,
    remote: str = "origin",
    commit_message: str | None = None,
    pr_title: str | None = None,
    pr_body: str | None = None,
    pr_labels: list[str] | None = None,
    pr_reviewers: list[str] | None = None,
    create_github_pr: bool = False,
) -> list[dict[str, Any]]:
    """Commit and push promoted worktrees after human approval."""
    validate_pr_remote(state, remote=remote)
    if not _approved(state):
        raise PRBoundaryError("approval_decision must be approved before PR publication")
    _require_policy_passed(state)

    promotion_results = list(state.get("promotion_results", []))
    if not promotion_results:
        raise PRBoundaryError("promotion_results are required before PR publication")

    message = commit_message or state.get("commit_message") or f"{state['change_id']}: apply changes"
    title = pr_title or state.get("pr_title") or str(message)
    operator_body = pr_body or state.get("pr_body")
    body = render_pr_body(state, operator_body=operator_body)
    labels = pr_labels if pr_labels is not None else _string_list(state.get("pr_labels"))
    reviewers = pr_reviewers if pr_reviewers is not None else _string_list(state.get("pr_reviewers"))
    if _github_pr_enabled(create_github_pr):
        _require_handoff_for_github_pr(state)

    results: list[dict[str, Any]] = []
    for promotion in promotion_results:
        worktree_path = Path(str(promotion["worktree_path"]))
        branch = str(promotion["branch"])
        if not worktree_path.exists():
            raise PRBoundaryError(f"promoted worktree is missing: {worktree_path}")

        status = _run(["git", "status", "--porcelain"], cwd=worktree_path).stdout.strip()
        if not status:
            raise PRBoundaryError(f"promoted worktree has no changes: {worktree_path}")

        _run(["git", "add", "."], cwd=worktree_path)
        _run(["git", "commit", "-m", message], cwd=worktree_path)
        commit_sha = _run(["git", "rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
        _run(["git", "push", remote, f"HEAD:{branch}"], cwd=worktree_path)
        _require_pushed_branch(worktree_path=worktree_path, remote=remote, branch=branch, commit_sha=commit_sha)
        github_pr = _create_github_pr(
            worktree_path=worktree_path,
            branch=branch,
            title=title,
            body=body,
            labels=labels,
            reviewers=reviewers,
            enabled=create_github_pr,
        )

        results.append(
            {
                "repo": promotion["repo"],
                "branch": branch,
                "remote": remote,
                "commit": commit_sha,
                "pushed": True,
                "title": title,
                "body": body,
                "labels": labels,
                "reviewers": reviewers,
                "github_pr": github_pr,
            }
        )

    return results
