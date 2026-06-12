from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.promotion import rollback_promotions


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


def _summary_from_stdout(output: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(output[output.index("{") :]))


def test_feature_command_scaffolds_request_into_promoted_worktree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text(
        "# Add customer note export\n\nCreate a small docs-visible feature intake artifact.\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "feature",
            "ADD_CUSTOMER_NOTE_EXPORT",
            "--request",
            str(request_path),
            "--repo",
            "hyrule-cloud",
            "--workspace-root",
            str(workspace_root),
            "--output-root",
            str(tmp_path / "feature-output"),
            "--allow",
            "docs",
            "--source",
            "README.md",
        ]
    )

    assert exit_code == 0
    summary = _summary_from_stdout(capsys.readouterr().out)
    state = json.loads(Path(str(summary["state_path"])).read_text(encoding="utf-8"))
    promotion = state["promotion_results"][0]
    worktree_path = Path(promotion["worktree_path"])
    plan_path = worktree_path / "docs" / "engineering-loop" / "add_customer_note_export.md"

    assert summary["promotion_count"] == 1
    assert summary["requires_human_signoff"] is True
    assert summary["signoff_status"] == "ready_for_review"
    assert "failure_summary" not in summary
    assert "signoff_summary" in summary
    assert state["approval_decision"] == "pending"
    assert state["signoff_status"] == "ready_for_review"
    assert "failure_summary" not in state
    assert state["signoff_summary"]["status"] == "ready_for_review"
    assert state["feature_target_repo"] == "hyrule-cloud"
    assert "Create a small docs-visible feature" in state["feature_request"]
    assert promotion["repo"] == "hyrule-cloud"
    assert "docs/engineering-loop/add_customer_note_export.md" in promotion["diff"]
    assert plan_path.exists()
    assert "ADD_CUSTOMER_NOTE_EXPORT" in plan_path.read_text(encoding="utf-8")
    assert state["signoff_summary"]["review_targets"][0]["branch"] == promotion["branch"]
    assert "git -C" in state["signoff_summary"]["next_operator_commands"][1]
    assert any(str(request_path) in output["source_files"] for output in state["llm_outputs"])
    assert any("hyrule-cloud:README.md" in output["source_files"] for output in state["llm_outputs"])

    rollback_promotions(state["promotion_results"])


def test_feature_command_accepts_explicit_mock_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Add a mock docs mutation.\n", encoding="utf-8")

    exit_code = main(
        [
            "feature",
            "MOCK_DOC_FEATURE",
            "--request",
            str(request_path),
            "--repo",
            "hyrule-cloud",
            "--workspace-root",
            str(workspace_root),
            "--output-root",
            str(tmp_path / "feature-output"),
            "--allow",
            "docs",
            "--mock-mutation",
            "docs/mock-feature.md=mock feature content\n",
        ]
    )

    assert exit_code == 0
    summary = _summary_from_stdout(capsys.readouterr().out)
    state = json.loads(Path(str(summary["state_path"])).read_text(encoding="utf-8"))
    promotion = state["promotion_results"][0]

    assert "docs/mock-feature.md" in promotion["diff"]
    assert "docs/engineering-loop/mock_doc_feature.md" not in promotion["diff"]
    assert (Path(promotion["worktree_path"]) / "docs" / "mock-feature.md").read_text(
        encoding="utf-8"
    ) == "mock feature content\n"

    rollback_promotions(state["promotion_results"])


def test_feature_command_refuses_unknown_repo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    request_path = tmp_path / "request.md"
    request_path.write_text("Missing repo.\n", encoding="utf-8")

    exit_code = main(
        [
            "feature",
            "UNKNOWN_REPO",
            "--request",
            str(request_path),
            "--repo",
            "hyrule-cloud",
            "--workspace-root",
            str(workspace_root),
            "--output-root",
            str(tmp_path / "feature-output"),
            "--allow",
            "docs",
        ]
    )

    assert exit_code == 1
    assert "unknown sibling repo" in capsys.readouterr().out
