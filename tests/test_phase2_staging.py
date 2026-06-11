from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.prompts import load_role_prompts
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
            "virtual_lab_chaos": False,
        },
        "retry_counters": {},
        "rollback_plan": "",
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
    }


def test_local_gate_command_success() -> None:
    graph = build_graph()
    state = _base_state("COMMAND_GATE_GREEN", "app_bugfix")
    state["gate_commands"] = [[sys.executable, "-c", "print('ok')"]]

    final_state = graph.invoke(state)

    assert final_state["gate_status"] == "passed"
    assert final_state["gate_results"][0]["returncode"] == 0
    assert final_state["validation_errors"] == []
    assert final_state["requires_human_signoff"] is False


def test_role_prompt_loader_reads_markdown() -> None:
    prompts = load_role_prompts()

    assert "network_architect" in prompts
    assert "Senior Network Architect" in prompts["network_architect"]
    assert "finops_integrity" in prompts
    assert "FinOps" in prompts["finops_integrity"]
    assert "virtual_lab_chaos" in prompts
    assert "Virtual Lab" in prompts["virtual_lab_chaos"]


def test_cli_run_show_and_approve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "state"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hyrule-engineering-loop",
            "--state-dir",
            str(state_dir),
            "run",
            "CLI_GREEN",
            "app_bugfix",
            "--gate-command",
            sys.executable,
            "-c",
            "print('cli gate ok')",
        ],
    )
    assert main() == 0

    state_path = state_dir / "CLI_GREEN.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["noc_handoff_metadata"]["status"] == "ready_for_pr_signoff"
    assert state["gate_results"][0]["returncode"] == 0

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hyrule-engineering-loop",
            "--state-dir",
            str(state_dir),
            "approve",
            "CLI_GREEN",
        ],
    )
    assert main() == 0

    approved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert approved_state["approval_decision"] == "approved"
