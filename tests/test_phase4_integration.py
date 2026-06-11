from __future__ import annotations

import json
from pathlib import Path

import pytest
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.llm import invoke_role_review
from hyrule_engineering_loop.state import GraphState


def _base_state(change_id: str, change_class: str) -> GraphState:
    return {
        "change_id": change_id,
        "change_class": change_class,  # type: ignore[typeddict-item]
        "risk_level": "medium",
        "customer_impact": "possible",
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
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
    }


def test_live_llm_missing_token_maps_to_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYRULE_MOCK_LLM", "0")
    monkeypatch.delenv("HYRULE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    output = invoke_role_review(
        role="systems_engineer",
        system_prompt="system",
        source_context={},
        state=_base_state("TOKEN_FAILURE", "app_bugfix"),
    )

    assert output.approved is False
    assert output.validation_errors[0]["domain"] == "llm"
    assert "requires HYRULE_LLM_API_KEY" in output.validation_errors[0]["message"]


def test_configurable_workspace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace_parent = tmp_path / "workspace-parent"
    monkeypatch.setenv("HYRULE_WORKSPACE_ROOT", str(workspace_parent))

    graph = build_graph()
    state = _base_state("WORKSPACE_ROOT", "app_bugfix")
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [{"path": "repo/file.txt", "content": "content\n"}],
        },
        "devops_netops": {"approved": True},
    }

    final_state = graph.invoke(state)

    workspace_root = Path(final_state["workspace_root"])
    assert workspace_root.parent == workspace_parent.resolve()
    assert final_state["workspace_cleaned_up"] is True
    assert not workspace_root.exists()


def test_noc_handoff_json_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    handoff_dir = tmp_path / "handoff"
    monkeypatch.setenv("HYRULE_HANDOFF_DIR", str(handoff_dir))

    graph = build_graph()
    state = _base_state("NOC_HANDOFF", "app_bugfix")
    state["noc_handoff_metadata"] = {
        "expected_alerts": [],
        "expected_duration": "none",
        "affected_hosts_services": [],
        "rollback_trigger": "gate failure or operator rejection",
        "operator_command_workflow": "hyrule-engineering-loop approve NOC_HANDOFF",
    }

    final_state = graph.invoke(state)

    handoff_path = Path(final_state["noc_handoff_path"])
    assert handoff_path == handoff_dir / "noc_handoff.json"
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["change"] == {
        "change_id": "NOC_HANDOFF",
        "change_class": "app_bugfix",
        "risk_level": "medium",
        "customer_impact": "possible",
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
    }
    assert payload["validation"]["gate_status"] == "passed"
    assert payload["validation"]["error_count"] == 0
    assert "retry_counters" in payload["validation"]
    assert set(payload["roles"]["approvals"]) == {
        "network_architect",
        "systems_engineer",
        "devops_netops",
        "security_auditor",
        "finops_integrity",
        "virtual_lab_chaos",
    }
    assert payload["workspace"]["cleaned_up"] is True
    assert payload["rollback"]["plan"]
    assert payload["rollback"]["requires_human_signoff"] is False
    assert payload["noc"]["status"] == "ready_for_pr_signoff"
    assert payload["noc"]["rollback_trigger"] == "gate failure or operator rejection"
