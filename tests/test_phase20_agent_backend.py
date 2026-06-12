"""Phase B (v2): AgentBackend, worktree-first execution, diff policy guard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from hyrule_engineering_loop.backend import (
    BackendConstraints,
    ClaudeCodeBackend,
    PiBackend,
    TaskSpec,
    assemble_backend_prompt,
    env_hygiene_violations,
    scrubbed_backend_env,
)
from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.feature import build_feature_state
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.model_policy import select_backend_for_state, validate_model_policy
from hyrule_engineering_loop.promotion import rollback_promotions
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


def _summary_from_stdout(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output[output.index("{") :]))


def _feature_state(tmp_path: Path, change_id: str, *, allow: list[str]) -> GraphState:
    workspace_root = tmp_path / "workspace"
    if not (workspace_root / "hyrule-cloud").exists():
        _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Phase 20 backend test request.\n", encoding="utf-8")
    return build_feature_state(
        change_id=change_id,
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "feature-output",
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=allow,
        source_files=["README.md"],
        scaffold_plan=False,
    )


def test_env_hygiene_scrubs_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_TOKEN", "supersecret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/agent.sock")
    monkeypatch.setenv("HYRULE_LLM_API_KEY", "sk-nope")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-nope")

    env = scrubbed_backend_env()

    assert "VAULT_TOKEN" not in env
    assert "SSH_AUTH_SOCK" not in env
    assert "HYRULE_LLM_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "PATH" in env
    assert env_hygiene_violations(env) == []


def test_subprocess_backend_command_assembly_and_refusals(tmp_path: Path) -> None:
    spec = TaskSpec(
        change_id="CMD_ASSEMBLY",
        change_class="app_feature",
        risk_level="low",
        request="do the thing",
        allowed_paths={"hyrule-cloud": ("docs",)},
    )
    constraints = BackendConstraints(max_iterations=7)

    command = ClaudeCodeBackend().build_command(
        prompt=assemble_backend_prompt(spec, constraints), constraints=constraints
    )
    assert command[0] == "claude"
    assert "-p" in command
    assert "--output-format" in command and "json" in command
    assert command[command.index("--max-turns") + 1] == "7"
    assert "CMD_ASSEMBLY" in command[command.index("-p") + 1]

    refused = PiBackend().execute(task_spec=spec, worktree=None, constraints=constraints)
    assert refused.status == "failed"
    assert "requires a branch-backed worktree" in str(refused.error)

    read_only = PiBackend().execute(
        task_spec=spec, worktree=tmp_path, constraints=BackendConstraints(read_only=True)
    )
    assert read_only.status == "failed"


def test_backend_selection_follows_tier_escalation(tmp_path: Path) -> None:
    policy_path = tmp_path / "model-policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "version: 1",
                "defaults: {provider: openrouter, model: m, tier: cheap}",
                "roles:",
                "  implementation_writer: {provider: openrouter, model: m, tier: mid}",
                "risk_overrides:",
                "  high: {min_tier: strong}",
                "tier_fallbacks:",
                "  strong: {provider: anthropic, model: claude-sonnet-4-6}",
                "backends:",
                "  default: mock",
                "  tiers: {strong: claude-code}",
                "  definitions:",
                "    claude-code:",
                "      command: [claude, -p, '{prompt}']",
                "",
            ]
        ),
        encoding="utf-8",
    )

    def _state(risk: str) -> GraphState:
        return cast(
            GraphState,
            {
                "change_id": "BACKEND_SELECT",
                "change_class": "app_feature",
                "risk_level": risk,
                "customer_impact": "none",
                "source_of_truth_files": [],
                "proposed_mutations": {},
                "mcp_schema_breaking": False,
                "emulated_lab_verified": "not_applicable",
                "validation_errors": [],
                "role_approvals": {},
                "retry_counters": {},
                "rollback_plan": "",
                "noc_handoff_metadata": {},
                "requires_human_signoff": False,
                "model_policy_file": str(policy_path),
            },
        )

    low = select_backend_for_state(_state("low"))
    assert low.name == "mock"
    assert low.tier == "mid"

    high = select_backend_for_state(_state("high"))
    assert high.name == "claude-code"
    assert high.tier == "strong"
    assert high.command == ["claude", "-p", "{prompt}"]

    bad_policy = tmp_path / "bad-policy.yml"
    bad_policy.write_text("version: 1\nbackends: {default: warp}\n", encoding="utf-8")
    result = validate_model_policy(bad_policy)
    assert result["ok"] is False
    assert any("unknown default backend" in error for error in result["errors"])


def test_budget_exhaustion_routes_to_human_signoff(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "BUDGET_EXHAUSTED", allow=["docs"])
    state["backend_budget"] = {"max_iterations": 0}

    final_state = dict(build_graph().invoke(state))

    assert final_state["implementation_writer_status"] == "budget_exhausted"
    assert final_state["requires_human_signoff"] is True
    assert final_state["signoff_status"] == "needs_operator_triage"
    assert any(
        "budget exhausted" in str(error.get("message", ""))
        for error in final_state["validation_errors"]
    )
    backend_runs = final_state["backend_results"]
    assert backend_runs[0]["status"] == "budget_exhausted"
    nodes = [event["node"] for event in final_state["trace_events"]]
    assert "delegate_implementation" in nodes
    assert "gate_execution" not in nodes

    rollback_promotions(final_state["worktree_results"])


def test_policy_guard_rejects_diff_outside_allowed_paths(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "GUARD_SCOPE", allow=["docs"])
    state["llm_mock_responses"] = {
        "implementation_writer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "hyrule-cloud:src/evil.py",
                    "content": "print('out of scope')\n",
                    "operation": "create",
                }
            ],
        }
    }

    final_state = dict(build_graph().invoke(state))

    assert final_state["policy_status"] == "failed"
    assert final_state["requires_human_signoff"] is True
    assert "promotion_status" not in final_state
    assert any(
        "worktree path not allowlisted for hyrule-cloud: src/evil.py" in str(error.get("message"))
        for error in final_state["validation_errors"]
    )

    rollback_promotions(final_state["worktree_results"])


def test_policy_guard_rejects_secret_bearing_diff(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "GUARD_SECRET", allow=["docs"])
    state["llm_mock_responses"] = {
        "implementation_writer": {
            "approved": True,
            "proposed_mutations": [
                {
                    "path": "hyrule-cloud:docs/leak.md",
                    "content": 'api_key = "definitely-not-a-secret"\n',
                    "operation": "create",
                }
            ],
        }
    }

    final_state = dict(build_graph().invoke(state))

    assert final_state["policy_status"] == "failed"
    assert any(
        "denied by pattern" in str(error.get("message"))
        for error in final_state["validation_errors"]
    )

    rollback_promotions(final_state["worktree_results"])


def test_policy_guard_enforces_changed_file_cap(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "version: 1",
                "defaults:",
                "  max_changed_files: 1",
                "  max_file_bytes: 1048576",
                "  denied_path_globs: []",
                "  denied_content_patterns: []",
                "  allowed_gate_commands: [python, python3]",
                "repos: {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    state = _feature_state(tmp_path, "GUARD_CAP", allow=["docs"])
    state["policy_file"] = str(policy_path)
    state["llm_mock_responses"] = {
        "implementation_writer": {
            "approved": True,
            "proposed_mutations": [
                {"path": "hyrule-cloud:docs/a.md", "content": "a\n", "operation": "create"},
                {"path": "hyrule-cloud:docs/b.md", "content": "b\n", "operation": "create"},
            ],
        }
    }

    final_state = dict(build_graph().invoke(state))

    assert final_state["policy_status"] == "failed"
    assert any(
        "changed file count exceeds policy limit" in str(error.get("message"))
        for error in final_state["validation_errors"]
    )

    rollback_promotions(final_state["worktree_results"])


def test_backend_canary_dry_live_assembles_without_execution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")

    assert (
        main(
            [
                "backend-canary",
                "--workspace-root",
                str(workspace_root),
                "--repo-name",
                "hyrule-cloud",
                "--output-root",
                str(tmp_path / "canary-output"),
                "--dry-live",
            ]
        )
        == 0
    )

    payload = _summary_from_stdout(capsys.readouterr().out)
    assert payload["dry_live"] is True
    assert payload["provider_called"] is False

    preflight = cast(dict[str, Any], payload["preflight"])
    backend = cast(dict[str, Any], preflight["backend"])
    assert cast(dict[str, Any], backend["selection"])["name"] == "mock"
    assert int(backend["prompt_chars"]) > 0
    assert any(
        check["name"] == "backend_env_hygiene" and check["ok"]
        for check in cast(list[dict[str, Any]], preflight["checks"])
    )
