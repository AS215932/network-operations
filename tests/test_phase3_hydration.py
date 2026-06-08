from __future__ import annotations

import sys
from pathlib import Path

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.state import GraphState


def _base_state(change_id: str, change_class: str) -> GraphState:
    return {
        "change_id": change_id,
        "change_class": change_class,  # type: ignore[typeddict-item]
        "risk_level": "low",
        "customer_impact": "none",
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
        },
        "retry_counters": {},
        "rollback_plan": "",
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
    }


def test_mock_llm_mutation_is_written_and_cleaned() -> None:
    graph = build_graph()
    state = _base_state("PHASE3_MUTATION", "app_bugfix")
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "notes": "write test artifact",
            "proposed_mutations": [
                {
                    "path": "phase3/output.txt",
                    "content": "hello from phase 3\n",
                }
            ],
        },
        "devops_netops": {
            "approved": True,
            "notes": "gate command is local-only",
        },
    }
    state["gate_commands"] = [
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "assert Path('phase3/output.txt').read_text() == 'hello from phase 3\\n'"
            ),
        ]
    ]

    final_state = graph.invoke(state)

    workspace_root = Path(final_state["workspace_root"])
    assert "phase3/output.txt" in final_state["workspace_written_files"]
    assert final_state["gate_status"] == "passed"
    assert final_state["gate_results"][0]["returncode"] == 0
    assert final_state["workspace_cleaned_up"] is True
    assert not workspace_root.exists()
    assert final_state["validation_errors"] == []
    assert any(output["source_files"] == ["README.md"] for output in final_state["llm_outputs"])


def test_failed_gate_still_cleans_workspace_before_signoff() -> None:
    graph = build_graph()
    state = _base_state("PHASE3_FAILING_COMMAND", "app_bugfix")
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "phase3/fail.txt",
                    "content": "this workspace should be removed\n",
                }
            ],
        },
        "devops_netops": {"approved": True},
    }
    state["gate_commands"] = [[sys.executable, "-c", "import sys; sys.exit(2)"]]

    final_state = graph.invoke(state)

    workspace_root = Path(final_state["workspace_root"])
    assert final_state["retry_counters"]["ci"] == 3
    assert final_state["requires_human_signoff"] is True
    assert final_state["workspace_cleaned_up"] is True
    assert not workspace_root.exists()
