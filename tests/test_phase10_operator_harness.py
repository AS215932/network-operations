from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.operator_harness import run_operator_dry_run


def test_operator_dry_run_harness_executes_full_workflow(tmp_path: Path) -> None:
    result = run_operator_dry_run(
        root=tmp_path / "operator",
        labels=["engineering-loop", "phase-10"],
        reviewers=["zelda"],
    )

    state_path = Path(result["state_path"])
    handoff_path = Path(str(result["handoff_path"]))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))

    assert state["approval_decision"] == "approved"
    assert state["pr_status"] == "pushed"
    assert state["policy_status"] == "passed"
    assert state["promotion_status"] == "passed"
    assert state["requires_human_signoff"] is False
    assert handoff["schema_version"] == 1
    assert handoff["change"]["change_id"] == "OPERATOR_DRY_RUN"
    assert result["remote_commit"] == state["pr_results"][0]["commit"]

    pr_result = state["pr_results"][0]
    assert pr_result["github_pr"] == {
        "created": True,
        "url": "https://github.example.invalid/hyrule/demo/pull/1",
        "provider": "mock-gh",
    }
    assert pr_result["labels"] == ["engineering-loop", "phase-10"]
    assert pr_result["reviewers"] == ["zelda"]
    assert "## Change class" in pr_result["body"]
    assert "## NOC handoff" in pr_result["body"]
    assert str(handoff_path) in pr_result["body"]
    assert "Offline harness verified approval" in pr_result["body"]


def test_operator_dry_run_cli_command_persists_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "operator-dry-run",
            "--root",
            str(tmp_path / "cli-operator"),
            "--change-id",
            "CLI_OPERATOR",
            "--mock-github-pr-url",
            "https://github.example.invalid/hyrule/demo/pull/2",
            "--label",
            "cli",
            "--reviewer",
            "link",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out[captured.out.index("{") :])
    state = json.loads(Path(summary["state_path"]).read_text(encoding="utf-8"))

    assert summary["github_pr"] == {
        "created": True,
        "url": "https://github.example.invalid/hyrule/demo/pull/2",
        "provider": "mock-gh",
    }
    assert Path(summary["handoff_path"]).exists()
    assert state["change_id"] == "CLI_OPERATOR"
    assert state["pr_results"][0]["labels"] == ["cli"]
    assert state["pr_results"][0]["reviewers"] == ["link"]
