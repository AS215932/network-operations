from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.nodes import (
    devops_netops_node,
    finops_integrity_node,
    network_architect_node,
    security_auditor_node,
    systems_engineer_node,
)
from hyrule_engineering_loop.state import GraphState


def _base_state(change_id: str, change_class: str) -> GraphState:
    return {
        "change_id": change_id,
        "change_class": change_class,  # type: ignore[typeddict-item]
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
    }


def test_green_path_execution(capsys: pytest.CaptureFixture[str]) -> None:
    graph = build_graph()

    final_state = graph.invoke(_base_state("APP_BUGFIX_GREEN", "app_bugfix"))
    output = capsys.readouterr().out

    assert "[Node: Senior Systems Engineer]" in output
    assert "[Node: Senior DevOps/NetOps Engineer]" in output
    assert "[Node: Senior Network Architect]" not in output
    assert "[Node: Senior Security & Cryptographic Auditor]" not in output
    assert "[Node: FinOps & Billing Integrity Engineer]" not in output
    assert "[Node: PR Packaging]" in output

    assert final_state["role_approvals"]["systems_engineer"] is True
    assert final_state["role_approvals"]["devops_netops"] is True
    assert final_state["role_approvals"]["network_architect"] is False
    assert final_state["role_approvals"]["security_auditor"] is False
    assert final_state["role_approvals"]["finops_integrity"] is False
    assert final_state["validation_errors"] == []
    assert final_state["requires_human_signoff"] is False
    assert final_state["noc_handoff_metadata"]["status"] == "ready_for_pr_signoff"


def test_state_reducer_merging() -> None:
    review_graph = StateGraph(GraphState)
    review_graph.add_node("network_architect", network_architect_node)
    review_graph.add_node("systems_engineer", systems_engineer_node)
    review_graph.add_node("devops_netops", devops_netops_node)
    review_graph.add_node("security_auditor", security_auditor_node)
    review_graph.add_node("finops_integrity", finops_integrity_node)
    review_graph.add_edge(START, "network_architect")
    review_graph.add_edge(START, "systems_engineer")
    review_graph.add_edge(START, "devops_netops")
    review_graph.add_edge(START, "security_auditor")
    review_graph.add_edge(START, "finops_integrity")
    review_graph.add_edge("network_architect", END)
    review_graph.add_edge("systems_engineer", END)
    review_graph.add_edge("devops_netops", END)
    review_graph.add_edge("security_auditor", END)
    review_graph.add_edge("finops_integrity", END)
    graph = review_graph.compile()

    final_state: dict[str, Any] = graph.invoke(_base_state("MIXED_MERGE_ERRORS", "mixed"))

    assert final_state["role_approvals"] == {
        "network_architect": True,
        "systems_engineer": True,
        "devops_netops": True,
        "security_auditor": True,
        "finops_integrity": True,
    }
    assert len(final_state["validation_errors"]) == 5
    assert {error["node"] for error in final_state["validation_errors"]} == {
        "network_architect",
        "systems_engineer",
        "devops_netops",
        "security_auditor",
        "finops_integrity",
    }


def test_circuit_breaker_depth() -> None:
    graph = build_graph()

    final_state = graph.invoke(_base_state("FAIL_GATES_SECURITY", "noc_runtime"))

    assert final_state["retry_counters"]["security"] == 3
    assert final_state["requires_human_signoff"] is True
    assert len(final_state["validation_errors"]) == 3
    assert final_state["validation_errors"][-1]["domain"] == "security"
