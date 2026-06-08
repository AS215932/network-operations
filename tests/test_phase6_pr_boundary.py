from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.pr import PRBoundaryError, publish_promoted_worktrees
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import GraphState


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed


def _init_repo_with_remote(repo_path: Path, remote_path: Path) -> None:
    repo_path.mkdir()
    _run(["git", "init"], repo_path)
    _run(["git", "config", "user.email", "loop@example.invalid"], repo_path)
    _run(["git", "config", "user.name", "Engineering Loop"], repo_path)
    (repo_path / "README.md").write_text("demo\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo_path)
    _run(["git", "commit", "-m", "initial"], repo_path)
    _run(["git", "init", "--bare", str(remote_path)], repo_path)
    _run(["git", "remote", "add", "origin", str(remote_path)], repo_path)


def _base_state(change_id: str, repo_path: Path, worktree_root: Path) -> GraphState:
    return {
        "change_id": change_id,
        "change_class": "app_bugfix",
        "risk_level": "low",
        "customer_impact": "none",
        "source_of_truth_files": [],
        "proposed_mutations": {},
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
        "validation_errors": [],
        "role_approvals": {
            "network_architect": False,
            "systems_engineer": False,
            "devops_netops": False,
            "security_auditor": False,
            "finops_integrity": False,
        },
        "retry_counters": {},
        "rollback_plan": "",
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
        "approval_decision": "approved",
        "promotion_enabled": True,
        "promotion_repositories": {"demo": str(repo_path)},
        "promotion_allowed_paths": {"demo": ["docs"]},
        "promotion_worktree_root": str(worktree_root),
        "promotion_branch_prefix": "hyrule-test",
    }


def _promoted_state(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    repo_path = tmp_path / "demo"
    remote_path = tmp_path / "demo.git"
    worktree_root = tmp_path / "worktrees"
    _init_repo_with_remote(repo_path, remote_path)

    state = _base_state("PR_OK", repo_path, worktree_root)
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "demo:docs/output.txt",
                    "content": "ready for pr\n",
                }
            ],
        },
        "devops_netops": {"approved": True},
    }

    final_state = build_graph().invoke(state)
    assert final_state["promotion_status"] == "passed"
    return dict(final_state), remote_path


def test_pr_boundary_commits_and_pushes_promoted_worktree(tmp_path: Path) -> None:
    state, remote_path = _promoted_state(tmp_path)

    results = publish_promoted_worktrees(
        state,
        commit_message="Apply promoted mutation",
        pr_title="Apply promoted mutation",
        pr_body="Generated locally.",
    )

    assert results[0]["pushed"] is True
    assert results[0]["github_pr"] == {"created": False, "url": None, "provider": "disabled"}
    branch = results[0]["branch"]
    remote_commit = _run(["git", "rev-parse", branch], remote_path).stdout.strip()
    assert remote_commit == results[0]["commit"]

    rollback_promotions(state["promotion_results"])


def test_pr_boundary_refuses_without_approval(tmp_path: Path) -> None:
    state, _remote_path = _promoted_state(tmp_path)
    state["approval_decision"] = "pending"

    with pytest.raises(PRBoundaryError, match="approval_decision must be approved"):
        publish_promoted_worktrees(
            state,
            commit_message="Nope",
            pr_title="Nope",
            pr_body="Nope",
        )

    rollback_promotions(state["promotion_results"])


def test_pr_boundary_refuses_without_promotion_results() -> None:
    state: dict[str, Any] = {
        "change_id": "NO_PROMOTION",
        "approval_decision": "approved",
        "policy_status": "passed",
    }

    with pytest.raises(PRBoundaryError, match="promotion_results are required"):
        publish_promoted_worktrees(
            state,
            commit_message="Nope",
            pr_title="Nope",
            pr_body="Nope",
        )
