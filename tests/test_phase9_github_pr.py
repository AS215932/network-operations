from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import hyrule_engineering_loop.pr as pr_module
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.pr import PRBoundaryError, publish_promoted_worktrees
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import GraphState


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed


def _option_values(args: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, item in enumerate(args[:-1]):
        if item == option:
            values.append(args[index + 1])
    return values


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
        "source_of_truth_files": ["README.md"],
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
            "virtual_lab_chaos": False,
        },
        "retry_counters": {},
        "rollback_plan": "",
        "noc_handoff_metadata": {
            "expected_alerts": [],
            "expected_duration": "none",
            "affected_hosts_services": [],
            "rollback_trigger": "operator rejection",
            "operator_command_workflow": "hyrule-engineering-loop pr",
        },
        "requires_human_signoff": False,
        "approval_decision": "approved",
        "promotion_enabled": True,
        "promotion_repositories": {"demo": str(repo_path)},
        "promotion_allowed_paths": {"demo": ["docs"]},
        "promotion_worktree_root": str(worktree_root),
        "promotion_branch_prefix": "hyrule-test",
    }


def _promoted_state(tmp_path: Path, *, handoff: bool) -> dict[str, Any]:
    repo_path = tmp_path / "demo"
    remote_path = tmp_path / "demo.git"
    worktree_root = tmp_path / "worktrees"
    _init_repo_with_remote(repo_path, remote_path)

    state = _base_state("GITHUB_PR", repo_path, worktree_root)
    if handoff:
        state["handoff_output_dir"] = str(tmp_path / "handoff")
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "demo:docs/output.txt",
                    "content": "ready for github pr\n",
                }
            ],
        },
        "devops_netops": {"approved": True},
    }

    final_state = build_graph().invoke(state)
    assert final_state["policy_status"] == "passed"
    assert final_state["promotion_status"] == "passed"
    final_state["remote_path"] = str(remote_path)
    return dict(final_state)


def test_github_pr_create_uses_rendered_body_labels_and_reviewers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = _promoted_state(tmp_path, handoff=True)
    captured: dict[str, Any] = {}
    real_run = pr_module._run

    def fake_run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["gh", "pr", "create"]:
            captured["args"] = args
            captured["cwd"] = cwd
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="https://github.example.invalid/org/demo/pull/1\n",
                stderr="",
            )
        return real_run(args, cwd=cwd)

    monkeypatch.setattr(pr_module, "_run", fake_run)

    results = publish_promoted_worktrees(
        state,
        commit_message="Apply GitHub PR mutation",
        pr_title="Apply GitHub PR mutation",
        pr_body="Operator reviewed graph state.",
        pr_labels=["engineering-loop", "needs-review"],
        pr_reviewers=["alice", "bob"],
        create_github_pr=True,
    )

    gh_args = captured["args"]
    body = gh_args[gh_args.index("--body") + 1]
    assert "--draft" in gh_args
    assert _option_values(gh_args, "--label") == ["engineering-loop", "needs-review"]
    assert _option_values(gh_args, "--reviewer") == ["alice", "bob"]
    assert results[0]["github_pr"] == {
        "created": True,
        "url": "https://github.example.invalid/org/demo/pull/1",
        "provider": "gh",
    }
    assert results[0]["pushed"] is True
    assert "## Change class" in body
    assert "app_bugfix" in body
    assert "## NOC handoff" in body
    assert state["noc_handoff_path"] in body
    assert "Operator reviewed graph state." in body

    rollback_promotions(state["promotion_results"])


def test_github_pr_refuses_without_rendered_noc_handoff(tmp_path: Path) -> None:
    state = _promoted_state(tmp_path, handoff=False)

    with pytest.raises(PRBoundaryError, match="noc_handoff_path is required"):
        publish_promoted_worktrees(
            state,
            commit_message="No handoff",
            pr_title="No handoff",
            create_github_pr=True,
        )

    rollback_promotions(state["promotion_results"])


def test_pr_publication_refuses_without_passed_policy(tmp_path: Path) -> None:
    state = _promoted_state(tmp_path, handoff=True)
    state["policy_status"] = "failed"

    with pytest.raises(PRBoundaryError, match="policy_status must be passed"):
        publish_promoted_worktrees(
            state,
            commit_message="No policy",
            pr_title="No policy",
        )

    rollback_promotions(state["promotion_results"])
