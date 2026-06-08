from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hyrule_engineering_loop.canary import (
    CANARY_PATH,
    CanaryDryRunError,
    run_sibling_repo_canary,
)
from hyrule_engineering_loop.cli import main


def _run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    assert completed.returncode == 0, completed.stderr


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "loop@example.invalid"], path)
    _run(["git", "config", "user.name", "Engineering Loop"], path)
    (path / "README.md").write_text(f"{path.name}\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "initial"], path)


def test_sibling_canary_targets_real_repo_and_cleans_up(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "hyrule-cloud"
    output_root = tmp_path / "canary-output"
    _init_repo(repo_path)

    result = run_sibling_repo_canary(
        workspace_root=workspace_root,
        output_root=output_root,
        repo_name="hyrule-cloud",
    )

    state = json.loads(Path(result["state_path"]).read_text(encoding="utf-8"))
    promotion = state["promotion_results"][0]
    worktree_path = Path(promotion["worktree_path"])

    assert state["repo_adapter_status"] == "passed"
    assert state["policy_status"] == "passed"
    assert state["promotion_status"] == "passed"
    assert state["approval_decision"] == "pending"
    assert "pr_status" not in state
    assert state["canary_cleanup_performed"] is True
    assert result["cleanup_performed"] is True
    assert promotion["repo"] == "hyrule-cloud"
    assert CANARY_PATH in promotion["diff"]
    assert not worktree_path.exists()
    assert Path(str(result["handoff_path"])).exists()
    assert not (repo_path / CANARY_PATH).exists()


def test_sibling_canary_cli_reports_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    output_root = tmp_path / "canary-output"

    exit_code = main(
        [
            "sibling-canary",
            "--workspace-root",
            str(workspace_root),
            "--repo-name",
            "hyrule-cloud",
            "--output-root",
            str(output_root),
            "--change-id",
            "CLI_CANARY",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out[captured.out.index("{") :])
    state = json.loads(Path(summary["state_path"]).read_text(encoding="utf-8"))

    assert summary["repo_name"] == "hyrule-cloud"
    assert summary["canary_path"] == CANARY_PATH
    assert summary["cleanup_performed"] is True
    assert summary["promotion_count"] == 1
    assert Path(summary["handoff_path"]).exists()
    assert state["change_id"] == "CLI_CANARY"
    assert state["promotion_results"][0]["repo"] == "hyrule-cloud"


def test_sibling_canary_refuses_dirty_target_repo(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "hyrule-cloud"
    _init_repo(repo_path)
    (repo_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(CanaryDryRunError, match="repo adapter did not pass"):
        run_sibling_repo_canary(
            workspace_root=workspace_root,
            output_root=tmp_path / "canary-output",
            repo_name="hyrule-cloud",
        )
