from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.feature import build_feature_state
from hyrule_engineering_loop.graph import build_graph
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


def test_feature_command_uses_implementation_writer_for_scaffold(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Add a feature writer scaffold.\n", encoding="utf-8")

    assert (
        main(
            [
                "feature",
                "WRITER_SCAFFOLD",
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
        == 0
    )

    summary = _summary_from_stdout(capsys.readouterr().out)
    state = json.loads(Path(str(summary["state_path"])).read_text(encoding="utf-8"))
    promotion = state["promotion_results"][0]

    assert state["implementation_writer_status"] == "complete"
    assert state["gate_status"] == "passed"
    assert state["proposed_mutation_operations"][0]["operation"] == "create"
    assert "docs/engineering-loop/writer_scaffold.md" in promotion["diff"]
    summary_preview = cast(list[dict[str, object]], summary["diff_preview"])
    assert summary_preview[0]["written_files"] == ["docs/engineering-loop/writer_scaffold.md"]

    rollback_promotions(state["promotion_results"])


def test_mock_implementation_writer_promotes_multi_file_tranche(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "hyrule-cloud"
    _init_repo(repo_path)
    request_path = tmp_path / "request.md"
    request_path.write_text("Replace README and add docs.\n", encoding="utf-8")

    state = build_feature_state(
        change_id="WRITER_MULTI_FILE",
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "feature-output",
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=["README.md", "docs"],
        source_files=["README.md"],
        scaffold_plan=False,
    )
    state["llm_mock_responses"] = {
        "implementation_writer": {
            "approved": True,
            "notes": "write multi-file tranche",
            "proposed_mutations": [
                {
                    "path": "hyrule-cloud:README.md",
                    "content": "updated readme\n",
                    "operation": "replace",
                },
                {
                    "path": "hyrule-cloud:docs/feature.md",
                    "content": "# Feature\n",
                    "operation": "create",
                },
            ],
        }
    }

    final_state = build_graph().invoke(state)

    assert final_state["implementation_writer_status"] == "complete"
    assert final_state["gate_status"] == "passed"
    assert final_state["promotion_status"] == "passed"
    assert final_state["gate_results"][0]["returncode"] == 0
    assert len(final_state["promotion_results"]) == 1
    promotion = final_state["promotion_results"][0]
    assert sorted(promotion["written_files"]) == ["README.md", "docs/feature.md"]
    assert {item["operation"] for item in promotion["mutation_operations"]} == {"create", "replace"}
    assert "+updated readme" in promotion["diff"]
    assert "+# Feature" in promotion["diff"]
    assert final_state["diff_preview"][0]["written_files"] == ["README.md", "docs/feature.md"]
    assert final_state["repo_context_bundle"]["repos"][0]["source_files"][0]["path"] == "README.md"

    rollback_promotions(final_state["promotion_results"])


def test_create_operation_refuses_existing_file(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "hyrule-cloud"
    _init_repo(repo_path)
    request_path = tmp_path / "request.md"
    request_path.write_text("Try unsafe create.\n", encoding="utf-8")

    state = build_feature_state(
        change_id="WRITER_CREATE_EXISTS",
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "feature-output",
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=["README.md"],
        source_files=["README.md"],
        scaffold_plan=False,
    )
    state["llm_mock_responses"] = {
        "implementation_writer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "hyrule-cloud:README.md",
                    "content": "bad create\n",
                    "operation": "create",
                }
            ],
        }
    }

    final_state = build_graph().invoke(state)

    assert final_state["requires_human_signoff"] is True
    assert final_state["retry_counters"]["backend"] == 3
    assert any("create mutation target already exists" in error["message"] for error in final_state["validation_errors"])
