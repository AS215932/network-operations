from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.repo_adapter import RepoAdapterError, verify_repository
from hyrule_engineering_loop.promotion import rollback_promotions


def _run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr


def _init_repo(path: Path) -> None:
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "loop@example.invalid"], path)
    _run(["git", "config", "user.name", "Engineering Loop"], path)
    (path / "README.md").write_text(f"{path.name}\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "initial"], path)


def test_dry_run_discovers_sibling_repo_and_promotes_allowed_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _init_repo(workspace_root / "hyrule-cloud")
    _init_repo(workspace_root / "hyrule-infra")
    state_dir = tmp_path / "state"
    worktree_root = tmp_path / "worktrees"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hyrule-engineering-loop",
            "--state-dir",
            str(state_dir),
            "dry-run",
            "DRY_ALLOWED",
            "app_bugfix",
            "--repo-workspace-root",
            str(workspace_root),
            "--promotion-repo-name",
            "hyrule-cloud",
            "--promotion-allow",
            "hyrule-cloud=docs",
            "--promotion-worktree-root",
            str(worktree_root),
            "--mutation",
            "hyrule-cloud:docs/smoke.md=hello from dry run\n",
        ],
    )

    assert main() == 0
    state = json.loads((state_dir / "DRY_ALLOWED.json").read_text(encoding="utf-8"))

    assert state["repo_adapter_status"] == "passed"
    assert state["promotion_status"] == "passed"
    assert state["approval_decision"] == "pending"
    assert "pr_status" not in state
    result = state["promotion_results"][0]
    assert result["repo"] == "hyrule-cloud"
    assert "docs/smoke.md" in result["diff"]
    assert Path(result["worktree_path"]).exists()

    rollback_promotions(state["promotion_results"])


def test_dry_run_denies_second_repo_secret_mutation_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    _init_repo(workspace_root / "hyrule-cloud")
    _init_repo(workspace_root / "hyrule-infra")
    state_dir = tmp_path / "state"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hyrule-engineering-loop",
            "--state-dir",
            str(state_dir),
            "dry-run",
            "DRY_DENIED",
            "app_bugfix",
            "--repo-workspace-root",
            str(workspace_root),
            "--promotion-repo-name",
            "hyrule-cloud",
            "--promotion-repo-name",
            "hyrule-infra",
            "--promotion-allow",
            "hyrule-cloud=docs",
            "--promotion-allow",
            "hyrule-infra=docs",
            "--promotion-worktree-root",
            str(tmp_path / "worktrees"),
            "--mutation",
            "hyrule-cloud:docs/smoke.md=allowed\n",
            "--mutation",
            "hyrule-infra:secrets/token.yml=token = 'nope'\n",
        ],
    )

    assert main() == 0
    state = json.loads((state_dir / "DRY_DENIED.json").read_text(encoding="utf-8"))

    assert state["repo_adapter_status"] == "passed"
    assert state["policy_status"] == "failed"
    assert "promotion_status" not in state
    assert state.get("promotion_results", []) == []
    assert any("denied by pattern" in error["message"] for error in state["validation_errors"])


def test_repo_adapter_refuses_dirty_target_repo(tmp_path: Path) -> None:
    repo = tmp_path / "hyrule-cloud"
    _init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RepoAdapterError, match="uncommitted changes"):
        verify_repository(repo)
