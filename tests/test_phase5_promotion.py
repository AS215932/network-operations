from __future__ import annotations

import subprocess
from pathlib import Path

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import GraphState


def _run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr


def _init_repo(path: Path) -> None:
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "loop@example.invalid"], path)
    _run(["git", "config", "user.name", "Engineering Loop"], path)
    (path / "README.md").write_text("demo\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "initial"], path)


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
        "promotion_enabled": True,
        "promotion_repositories": {"demo": str(repo_path)},
        "promotion_allowed_paths": {"demo": ["docs"]},
        "promotion_worktree_root": str(worktree_root),
        "promotion_branch_prefix": "hyrule-test",
    }


def test_promotion_creates_branch_worktree_and_diff(tmp_path: Path) -> None:
    repo_path = tmp_path / "demo"
    worktree_root = tmp_path / "worktrees"
    _init_repo(repo_path)

    state = _base_state("PROMOTE_OK", repo_path, worktree_root)
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "demo:docs/output.txt",
                    "content": "promoted\n",
                }
            ],
        },
        "devops_netops": {"approved": True},
    }

    final_state = build_graph().invoke(state)

    assert final_state["promotion_status"] == "passed"
    assert final_state["requires_human_signoff"] is True
    result = final_state["promotion_results"][0]
    assert result["repo"] == "demo"
    assert result["branch"] == "hyrule-test/PROMOTE_OK/demo"
    assert "docs/output.txt" in result["diff"]
    assert "+promoted" in result["diff"]
    assert Path(result["worktree_path"]).exists()
    assert (Path(result["worktree_path"]) / "docs" / "output.txt").read_text(
        encoding="utf-8"
    ) == "promoted\n"

    rollback_promotions(final_state["promotion_results"])
    assert not Path(result["worktree_path"]).exists()


def test_promotion_rejects_disallowed_paths_and_rolls_back(tmp_path: Path) -> None:
    repo_path = tmp_path / "demo"
    worktree_root = tmp_path / "worktrees"
    _init_repo(repo_path)

    state = _base_state("PROMOTE_DENIED", repo_path, worktree_root)
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "demo:secrets/token.txt",
                    "content": "nope\n",
                }
            ],
        },
        "devops_netops": {"approved": True},
    }

    final_state = build_graph().invoke(state)

    assert final_state["policy_status"] == "failed"
    assert "promotion_status" not in final_state
    assert final_state["retry_counters"]["policy"] == 1
    assert final_state["requires_human_signoff"] is True
    assert any("denied by pattern" in error["message"] for error in final_state["validation_errors"])
    if worktree_root.exists():
        assert list(worktree_root.glob("*")) == []
