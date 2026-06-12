from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hyrule_engineering_loop.feature import build_feature_state
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.model_policy import select_model_for_node
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.prompts import load_role_prompts
from hyrule_engineering_loop.state import GraphState


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


def _feature_state(tmp_path: Path) -> GraphState:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Add live writer plumbing.\n", encoding="utf-8")
    return build_feature_state(
        change_id="LIVE_WRITER",
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "feature-output",
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=["docs"],
        source_files=["README.md"],
        scaffold_plan=False,
    )


def test_implementation_writer_prompt_and_model_policy() -> None:
    prompts = load_role_prompts()
    state: GraphState = {
        "change_id": "MODEL_PREVIEW",
        "change_class": "app_feature",
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
            "virtual_lab_chaos": False,
        },
        "retry_counters": {},
        "rollback_plan": "",
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
    }

    selection = select_model_for_node("implementation_writer", state)

    assert "implementation_writer" in prompts
    assert "implementation-tranche" in prompts["implementation_writer"]
    assert selection.provider == "openrouter"
    assert selection.model == "moonshotai/kimi-k2.6"
    assert selection.tier == "mid"


def test_live_mode_uses_mocked_writer_response_and_records_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYRULE_MOCK_LLM", "0")
    state = _feature_state(tmp_path)
    state["llm_mock_responses"] = {
        "systems_engineer": {"approved": True},
        "devops_netops": {"approved": True},
        "judge_systems_engineer": {"verdict": "approve"},
        "judge_devops_netops": {"verdict": "approve"},
        "implementation_writer": {
            "approved": True,
            "notes": "mocked live writer",
            "proposed_mutations": [
                {
                    "path": "hyrule-cloud:docs/live-writer.md",
                    "content": "# Live Writer\n",
                    "operation": "create",
                }
            ],
        },
    }

    final_state = build_graph().invoke(state)

    assert final_state["implementation_writer_status"] == "complete"
    assert final_state["promotion_status"] == "passed"
    writer_output = next(
        output
        for output in final_state["llm_outputs"]
        if output["role"] == "implementation_writer"
    )
    assert writer_output["model_selection"]["model"] == "moonshotai/kimi-k2.6"
    assert writer_output["proposed_mutation_paths"] == ["hyrule-cloud:docs/live-writer.md"]

    rollback_promotions(final_state["promotion_results"])


def test_live_writer_missing_token_routes_to_human_signoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYRULE_MOCK_LLM", "0")
    for key in (
        "HYRULE_LLM_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    state = _feature_state(tmp_path)
    state["llm_mock_responses"] = {
        "systems_engineer": {"approved": True},
        "devops_netops": {"approved": True},
    }

    final_state = build_graph().invoke(state)

    assert final_state["requires_human_signoff"] is True
    assert final_state["retry_counters"]["llm_implementation_writer"] == 3
    assert any(
        error["node"] == "implementation_writer"
        and "requires HYRULE_LLM_API_KEY" in error["message"]
        for error in final_state["validation_errors"]
    )
