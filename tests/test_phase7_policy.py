from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.policy import PolicyViolation, validate_graph_state, validate_pr_remote
from hyrule_engineering_loop.state import GraphState


def _write_policy(path: Path, overrides: dict[str, Any] | None = None) -> Path:
    policy: dict[str, Any] = {
        "version": 1,
        "defaults": {
            "max_changed_files": 3,
            "max_file_bytes": 100,
            "denied_path_globs": [".env", "*.key", "secrets/**", "**/secrets/**"],
            "denied_content_patterns": ["PRIVATE KEY", "(?i)token\\s*="],
            "allowed_gate_commands": ["python"],
            "protected_branch_prefixes": ["main", "prod"],
            "allowed_pr_remotes": ["origin"],
            "allowed_handoff_dirs": [str(path.parent)],
        },
        "repos": {},
    }
    if overrides:
        policy.update(overrides)
    path.write_text(yaml.safe_dump(policy, sort_keys=True), encoding="utf-8")
    return path


def _base_state(policy_path: Path) -> GraphState:
    return {
        "change_id": "POLICY",
        "change_class": "app_bugfix",
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
        "policy_file": str(policy_path),
    }


def test_policy_denies_secret_paths_and_content(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "policy.yml")
    state = _base_state(policy_path)
    state["proposed_mutations"] = {
        "demo:secrets/token.yml": "token = 'abc'\n",
        "demo:docs/key.txt": "-----BEGIN PRIVATE KEY-----\n",
    }

    violations = validate_graph_state(state)

    assert any("denied by pattern secrets/**" in violation for violation in violations)
    assert any("denied by pattern PRIVATE KEY" in violation for violation in violations)
    assert any("denied by pattern (?i)token" in violation for violation in violations)


def test_policy_denies_too_many_files_and_gate_command(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "policy.yml")
    state = _base_state(policy_path)
    state["proposed_mutations"] = {
        "demo:docs/a.txt": "a",
        "demo:docs/b.txt": "b",
        "demo:docs/c.txt": "c",
        "demo:docs/d.txt": "d",
    }
    state["gate_commands"] = [["bash", "-lc", "echo no"]]

    violations = validate_graph_state(state)

    assert any("changed file count exceeds" in violation for violation in violations)
    assert any("gate command not allowlisted: bash" in violation for violation in violations)


def test_policy_denies_protected_branch_prefix_and_remote(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "policy.yml")
    state = _base_state(policy_path)
    state["promotion_enabled"] = True
    state["promotion_branch_prefix"] = "main/generated"
    state["promotion_repositories"] = {"demo": str(tmp_path / "demo")}
    state["promotion_allowed_paths"] = {"demo": ["docs"]}
    state["proposed_mutations"] = {"demo:docs/a.txt": "ok\n"}

    violations = validate_graph_state(state)

    assert any("protected branch namespace" in violation for violation in violations)
    with pytest.raises(PolicyViolation, match="PR remote not allowlisted"):
        validate_pr_remote({"policy_file": str(policy_path)}, remote="upstream")


def test_policy_node_stops_graph_before_promotion(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "policy.yml")
    state = _base_state(policy_path)
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [{"path": "demo:.env", "content": "TOKEN=x\n"}],
        },
        "devops_netops": {"approved": True},
    }

    final_state = build_graph().invoke(state)

    assert final_state["policy_status"] == "failed"
    assert final_state["requires_human_signoff"] is True
    assert "promotion_status" not in final_state


def test_policy_passes_safe_graph_state(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "policy.yml")
    state = _base_state(policy_path)
    state["llm_mock_responses"] = {
        "systems_engineer": {
            "approved": True,
            "proposed_mutations": [{"path": "docs/safe.txt", "content": "safe\n"}],
        },
        "devops_netops": {"approved": True},
    }
    state["gate_commands"] = [["python", "-c", "print('ok')"]]

    final_state = build_graph().invoke(state)

    assert final_state["policy_status"] == "passed"
    assert final_state["promotion_status"] == "not_requested"
    assert final_state["requires_human_signoff"] is False


def test_policy_denies_non_allowlisted_repo_root(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "policy.yml",
        {
            "repos": {
                "demo": {
                    "allowed_repo_roots": [str(tmp_path / "allowed-demo")],
                }
            }
        },
    )
    actual_repo = tmp_path / "actual-demo"
    subprocess.run(["git", "init", str(actual_repo)], check=True, capture_output=True, text=True)
    state = _base_state(policy_path)
    state["promotion_enabled"] = True
    state["promotion_repositories"] = {"demo": str(actual_repo)}
    state["promotion_allowed_paths"] = {"demo": ["docs"]}
    state["proposed_mutations"] = {"demo:docs/a.txt": "ok\n"}

    violations = validate_graph_state(state)

    assert any("repo root not allowlisted" in violation for violation in violations)
