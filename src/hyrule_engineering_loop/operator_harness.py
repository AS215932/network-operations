"""End-to-end offline operator dry-run harness."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.nodes import ALL_ROLES
from hyrule_engineering_loop.pr import publish_promoted_worktrees
from hyrule_engineering_loop.state import GraphState


class OperatorHarnessError(RuntimeError):
    """Raised when the offline operator harness cannot complete."""


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, cwd=cwd, capture_output=True, check=False, text=True)
    if completed.returncode != 0:
        raise OperatorHarnessError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _init_repo_with_remote(repo_path: Path, remote_path: Path) -> None:
    repo_path.mkdir(parents=True)
    _run(["git", "init"], cwd=repo_path)
    _run(["git", "config", "user.email", "loop@example.invalid"], cwd=repo_path)
    _run(["git", "config", "user.name", "Engineering Loop"], cwd=repo_path)
    (repo_path / "README.md").write_text("operator harness\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo_path)
    _run(["git", "commit", "-m", "initial"], cwd=repo_path)
    _run(["git", "init", "--bare", str(remote_path)], cwd=repo_path)
    _run(["git", "remote", "add", "origin", str(remote_path)], cwd=repo_path)


def _base_state(root: Path, change_id: str) -> GraphState:
    repo_path = root / "demo"
    return {
        "change_id": change_id,
        "change_class": "app_bugfix",
        "risk_level": "low",
        "customer_impact": "none",
        "source_of_truth_files": ["README.md"],
        "proposed_mutations": {
            "demo:docs/operator-dry-run.txt": "phase 10 operator dry run\n",
        },
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
        "validation_errors": [],
        "role_approvals": {role: False for role in ALL_ROLES},
        "retry_counters": {},
        "rollback_plan": "Delete the dry-run branch and discard the disposable fixture.",
        "noc_handoff_metadata": {
            "expected_alerts": [],
            "expected_duration": "none",
            "affected_hosts_services": [],
            "rollback_trigger": "operator rejection or failed dry-run assertion",
            "operator_command_workflow": "hyrule-engineering-loop operator-dry-run",
        },
        "requires_human_signoff": False,
        "approval_decision": "pending",
        "promotion_enabled": True,
        "promotion_repositories": {"demo": str(repo_path)},
        "promotion_allowed_paths": {"demo": ["docs"]},
        "promotion_worktree_root": str(root / "worktrees"),
        "promotion_branch_prefix": "hyrule-operator-dry-run",
        "handoff_output_dir": str(root / "handoff"),
    }


def _remote_branch_commit(remote_path: Path, branch: str) -> str:
    completed = _run(["git", "rev-parse", branch], cwd=remote_path)
    return completed.stdout.strip()


def run_operator_dry_run(
    *,
    root: Path,
    change_id: str = "OPERATOR_DRY_RUN",
    mock_github_pr_url: str = "https://github.example.invalid/hyrule/demo/pull/1",
    labels: list[str] | None = None,
    reviewers: list[str] | None = None,
) -> dict[str, Any]:
    """Run the full offline dry-run, approval, and mocked PR publication workflow."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    repo_path = root / "demo"
    remote_path = root / "demo.git"
    state_path = root / "state" / f"{change_id}.json"
    _init_repo_with_remote(repo_path, remote_path)

    state = _base_state(root, change_id)
    graph = build_graph(checkpointer=MemorySaver())
    dry_run_state = dict(graph.invoke(state, {"configurable": {"thread_id": change_id}}))
    _write_state(state_path, dry_run_state)

    if dry_run_state.get("promotion_status") != "passed":
        raise OperatorHarnessError("operator dry-run did not produce a promoted worktree")
    if not dry_run_state.get("requires_human_signoff", False):
        raise OperatorHarnessError("operator dry-run did not pause for human sign-off")

    approved_state = {
        **dry_run_state,
        "approval_decision": "approved",
        "requires_human_signoff": False,
    }
    _write_state(state_path, approved_state)

    old_mock_url = os.environ.get("HYRULE_MOCK_GITHUB_PR_URL")
    os.environ["HYRULE_MOCK_GITHUB_PR_URL"] = mock_github_pr_url
    try:
        pr_results = publish_promoted_worktrees(
            approved_state,
            remote="origin",
            commit_message="Phase 10 operator dry-run mutation",
            pr_title="Phase 10 operator dry-run mutation",
            pr_body="Offline harness verified approval, push, handoff, and PR body rendering.",
            pr_labels=labels or ["engineering-loop", "dry-run"],
            pr_reviewers=reviewers or [],
            create_github_pr=True,
        )
    finally:
        if old_mock_url is None:
            os.environ.pop("HYRULE_MOCK_GITHUB_PR_URL", None)
        else:
            os.environ["HYRULE_MOCK_GITHUB_PR_URL"] = old_mock_url

    published_state = {
        **approved_state,
        "pr_status": "pushed",
        "pr_remote": "origin",
        "pr_results": pr_results,
        "pr_create_github": True,
    }
    _write_state(state_path, published_state)

    first_result = pr_results[0]
    return {
        "root": str(root),
        "state_path": str(state_path),
        "repo_path": str(repo_path),
        "remote_path": str(remote_path),
        "handoff_path": published_state.get("noc_handoff_path"),
        "branch": first_result["branch"],
        "remote_commit": _remote_branch_commit(remote_path, str(first_result["branch"])),
        "pr_results": pr_results,
        "final_state": published_state,
    }
