from __future__ import annotations

from pathlib import Path

import pytest

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.model_policy import select_model_for_role
from hyrule_engineering_loop.state import ChangeClass, GraphState, RiskLevel, RoleName


def _write_model_policy(path: Path) -> Path:
    path.write_text(
        """
version: 1
defaults:
  provider: openrouter
  model: minimax/minimax-m3
  tier: cheap
roles:
  systems_engineer:
    provider: openrouter
    model: moonshotai/kimi-k2.6
    tier: mid
  devops_netops:
    provider: openrouter
    model: moonshotai/kimi-k2.6
    tier: mid
  security_auditor:
    provider: anthropic
    model: claude-sonnet-4-6
    tier: strong
  network_architect:
    provider: anthropic
    model: claude-sonnet-4-6
    tier: strong
  finops_integrity:
    provider: openrouter
    model: moonshotai/kimi-k2.6
    tier: mid
  virtual_lab_chaos:
    provider: openrouter
    model: moonshotai/kimi-k2.6
    tier: mid
risk_overrides:
  high:
    min_tier: strong
  critical:
    min_tier: frontier
retry_escalation:
  after_failures: 1
  max_tier: frontier
tier_fallbacks:
  cheap:
    provider: openrouter
    model: minimax/minimax-m3
  mid:
    provider: openrouter
    model: moonshotai/kimi-k2.6
  strong:
    provider: anthropic
    model: claude-sonnet-4-6
  frontier:
    provider: openai
    model: gpt-5.5
""".lstrip(),
        encoding="utf-8",
    )
    return path


def _base_state(
    change_id: str,
    change_class: ChangeClass,
    *,
    policy_path: Path,
    risk_level: RiskLevel = "low",
) -> GraphState:
    return {
        "change_id": change_id,
        "change_class": change_class,
        "risk_level": risk_level,
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
        "model_policy_file": str(policy_path),
    }


def _role_output(final_state: dict[str, object], role: RoleName) -> dict[str, object]:
    outputs = final_state["llm_outputs"]
    assert isinstance(outputs, list)
    for output in outputs:
        if isinstance(output, dict) and output.get("role") == role:
            return output
    raise AssertionError(f"missing llm output for {role}")


def test_model_policy_selects_role_default_and_records_selection(tmp_path: Path) -> None:
    policy_path = _write_model_policy(tmp_path / "model-policy.yml")
    state = _base_state("MODEL_POLICY_GREEN", "app_bugfix", policy_path=policy_path)

    selected = select_model_for_role("systems_engineer", state)

    assert selected.provider == "openrouter"
    assert selected.model == "moonshotai/kimi-k2.6"
    assert selected.tier == "mid"
    assert selected.reason == "role_default"

    final_state = build_graph().invoke(state)
    output = _role_output(dict(final_state), "systems_engineer")
    assert output["model_selection"] == selected.as_dict()


def test_high_risk_app_change_adds_chaos_role_and_strong_model(tmp_path: Path) -> None:
    policy_path = _write_model_policy(tmp_path / "model-policy.yml")
    state = _base_state(
        "HIGH_RISK_APP",
        "app_bugfix",
        policy_path=policy_path,
        risk_level="high",
    )

    selected = select_model_for_role("systems_engineer", state)

    assert selected.provider == "anthropic"
    assert selected.model == "claude-sonnet-4-6"
    assert selected.tier == "strong"
    assert selected.reason == "risk_high"

    final_state = build_graph().invoke(state)
    assert final_state["role_approvals"]["virtual_lab_chaos"] is True
    chaos_output = _role_output(dict(final_state), "virtual_lab_chaos")
    model_selection = chaos_output["model_selection"]
    assert isinstance(model_selection, dict)
    assert model_selection["tier"] == "strong"


def test_retry_escalates_role_to_frontier_tier(tmp_path: Path) -> None:
    policy_path = _write_model_policy(tmp_path / "model-policy.yml")
    state = _base_state("RETRY_ESCALATION", "app_bugfix", policy_path=policy_path)
    state["retry_counters"] = {"llm_systems_engineer": 1}

    selected = select_model_for_role("systems_engineer", state)

    assert selected.provider == "openai"
    assert selected.model == "gpt-5.5"
    assert selected.tier == "frontier"
    assert selected.reason == "retry_escalation_after_1"


def test_routing_change_runs_virtual_lab_chaos_role(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy_path = _write_model_policy(tmp_path / "model-policy.yml")
    state = _base_state("ROUTING_LAB", "routing_bgp_frr", policy_path=policy_path)

    final_state = build_graph().invoke(state)
    output = capsys.readouterr().out

    assert "[Node: Senior Network Architect]" in output
    assert "[Node: Senior Security & Cryptographic Auditor]" in output
    assert "[Node: Virtual Lab & Chaos Simulation Engineer]" in output
    assert final_state["role_approvals"]["network_architect"] is True
    assert final_state["role_approvals"]["security_auditor"] is True
    assert final_state["role_approvals"]["virtual_lab_chaos"] is True
