from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

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


def _summary_from_stdout(output: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(output[output.index("{") :]))


def test_feature_command_writes_compact_loop_trace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_text = "Add a compact trace to the loop so operators can inspect node data flow."
    request_path.write_text(request_text, encoding="utf-8")

    exit_code = main(
        [
            "feature",
            "TRACE_FEATURE",
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
    trace_path = Path(str(summary["trace_path"]))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    nodes = {event["node"] for event in trace["events"]}
    serialized = json.dumps(trace)

    assert state["loop_trace_path"] == str(trace_path)
    assert trace["schema_version"] == 1
    assert trace["change"]["change_id"] == "TRACE_FEATURE"
    assert trace["event_count"] == len(trace["events"])
    assert {
        "classification",
        "planner",
        "systems_engineer",
        "devops_netops",
        "repo_adapter",
        "worktree_setup",
        "delegate_implementation",
        "gate_execution",
        "workspace_cleanup",
        "policy",
        "role_judgment",
        "promotion",
        "package_pr",
    }.issubset(nodes)
    assert "docs/engineering-loop/trace_feature.md" in serialized
    assert request_text not in serialized
    assert "diff --git" not in serialized

    assert main(["state-cleanup", "--state-path", str(summary["state_path"])]) == 0


def test_state_approve_and_cleanup_commands_update_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Add a trace action test.\n", encoding="utf-8")

    assert (
        main(
            [
                "feature",
                "TRACE_ACTIONS",
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
        == 0
    )
    summary = _summary_from_stdout(capsys.readouterr().out)
    state_path = Path(str(summary["state_path"]))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    worktree_path = Path(state["promotion_results"][0]["worktree_path"])
    assert worktree_path.exists()

    assert main(["state-approve", "--state-path", str(state_path)]) == 0
    approved = json.loads(state_path.read_text(encoding="utf-8"))
    assert approved["approval_decision"] == "approved"
    assert approved["requires_human_signoff"] is False

    assert main(["state-cleanup", "--state-path", str(state_path)]) == 0
    cleaned = json.loads(state_path.read_text(encoding="utf-8"))
    assert cleaned["promotion_cleanup_performed"] is True
    assert not worktree_path.exists()
