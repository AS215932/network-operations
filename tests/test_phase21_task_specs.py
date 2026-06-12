"""Phase C (v2): task specs as sprint contracts + two-phase role review."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from hyrule_engineering_loop.backend import (
    AgentRunResult,
    BackendConstraints,
    CostReport,
    TaskSpec,
)
from hyrule_engineering_loop.cli import main
from hyrule_engineering_loop.feature import build_feature_state
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.judgment import run_agentic_evaluation
from hyrule_engineering_loop.nodes import required_roles_for_state
from hyrule_engineering_loop.prompts import load_role_prompts
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import GraphState
from hyrule_engineering_loop.task_spec import TaskSpecError, parse_task_spec_text, render_task_spec


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


def _feature_state(tmp_path: Path, change_id: str, *, scaffold: bool = True) -> GraphState:
    workspace_root = tmp_path / "workspace"
    if not (workspace_root / "hyrule-cloud").exists():
        _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Add a task-spec driven tranche.\n", encoding="utf-8")
    return build_feature_state(
        change_id=change_id,
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=tmp_path / "feature-output",
        repo_name="hyrule-cloud",
        request_path=request_path,
        allowed_paths=["docs"],
        source_files=["README.md"],
        scaffold_plan=scaffold,
    )


# --- task spec parser -------------------------------------------------------


def test_task_spec_round_trip_and_validation() -> None:
    spec = {
        "change_id": "SPEC_RT",
        "change_class": "app_feature",
        "risk_level": "medium",
        "customer_impact": "none",
        "repos": {"hyrule-cloud": ["docs/", "tests/"]},
        "required_roles": ["systems_engineer", "devops_netops"],
        "gates": ["pytest -q"],
        "budget": {"max_iterations": 9, "max_wall_clock_minutes": 10},
        "intake_source": "issue:AS215932/network-operations#192",
        "intent": "Round-trip the sprint contract.",
        "acceptance_criteria": ["The parser round-trips this document."],
        "non_goals": "Anything else.",
        "rollback_sketch": "Discard the worktree.",
    }
    parsed = parse_task_spec_text(render_task_spec(spec))

    assert parsed["change_id"] == "SPEC_RT"
    assert parsed["repos"] == {"hyrule-cloud": ["docs/", "tests/"]}
    assert parsed["acceptance_criteria"][0] == "The parser round-trips this document."
    assert parsed["budget"]["max_iterations"] == 9
    assert parsed["intake_source"] == "issue:AS215932/network-operations#192"


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"acceptance_criteria": []}, "acceptance criterion"),
        ({"repos": {}}, "at least one repo"),
        ({"repos": {"demo": []}}, "allowed_paths"),
        ({"intent": ""}, "intent"),
    ],
)
def test_task_spec_structural_refusals(mutation: dict[str, Any], match: str) -> None:
    spec: dict[str, Any] = {
        "change_id": "SPEC_BAD",
        "repos": {"demo": ["docs/"]},
        "intent": "Intent.",
        "acceptance_criteria": ["One criterion."],
    }
    spec.update(mutation)
    with pytest.raises(TaskSpecError, match=match):
        parse_task_spec_text(render_task_spec(spec))


def test_feature_refuses_invalid_supplied_task_spec(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Spec refusal.\n", encoding="utf-8")
    bad_spec = tmp_path / "bad-spec.md"
    bad_spec.write_text("# not a task spec\n", encoding="utf-8")

    exit_code = main(
        [
            "feature",
            "SPEC_REFUSED",
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
            "--task-spec",
            str(bad_spec),
        ]
    )

    assert exit_code == 1
    assert "task spec refused" in capsys.readouterr().out


def test_planner_failure_routes_to_human_signoff(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "PLANNER_BAD")
    # Force a structurally invalid spec through the planner mock override.
    state["llm_mock_responses"] = {"planner": {"acceptance_criteria": []}}

    final_state = dict(build_graph().invoke(state))

    assert final_state["requires_human_signoff"] is True
    assert "task_spec" not in final_state
    assert any(
        "task spec is structurally invalid" in str(error.get("message"))
        for error in final_state["validation_errors"]
    )
    nodes = [event["node"] for event in final_state["trace_events"]]
    assert "delegate_implementation" not in nodes


# --- AC2: consult + judgment recorded ---------------------------------------


def test_spec_records_consults_and_trace_shows_both_phases(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "TWO_PHASE")
    state["llm_mock_responses"] = {
        "consult_systems_engineer": {
            "constraints": ["Daemons changed by this diff need health checks."],
            "acceptance_criteria": ["No unstructured logging is introduced."],
        }
    }

    final_state = dict(build_graph().invoke(state))

    spec_path = Path(str(final_state["task_spec_path"]))
    assert spec_path.exists()
    spec_text = spec_path.read_text(encoding="utf-8")
    assert "## Role consult notes" in spec_text
    assert "### systems_engineer" in spec_text
    assert "health checks" in spec_text
    assert "No unstructured logging is introduced." in spec_text

    outputs = final_state["llm_outputs"]
    consult_roles = {o["role"] for o in outputs if o.get("phase") == "consult"}
    judgment_roles = {o["role"] for o in outputs if o.get("phase") == "judgment"}
    assert {"systems_engineer", "devops_netops"} <= consult_roles
    assert {"systems_engineer", "devops_netops"} <= judgment_roles

    events = final_state["trace_events"]
    assert any(e["node"] == "systems_engineer" for e in events)
    assert any(e["node"] == "role_judgment" and e.get("role") == "systems_engineer" for e in events)

    assert final_state["promotion_status"] == "passed"
    rollback_promotions(final_state["promotion_results"])


# --- AC3: findings feed the backend and the diff changes ---------------------


def test_judgment_findings_change_diff_on_retry(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "JUDGE_RETRY")
    state["llm_mock_responses"] = {
        "judge_systems_engineer": [
            {
                "verdict": "request_changes",
                "findings": [
                    {
                        "domain": "systems",
                        "severity": "major",
                        "path": "docs/engineering-loop/judge_retry.md",
                        "message": "The scaffold must state its remediation round.",
                    }
                ],
            },
            {"verdict": "approve"},
        ],
    }

    final_state = dict(build_graph().invoke(state))

    verdicts = [
        entry["verdict"]
        for entry in final_state["judgment_results"]
        if entry.get("role") == "systems_engineer"
    ]
    assert verdicts == ["request_changes", "approve"]
    assert final_state["retry_counters"]["judgment"] == 1
    assert final_state["promotion_status"] == "passed"
    diff = final_state["promotion_results"][0]["diff"]
    assert "Remediation round" in diff
    assert "remediation round" in diff.lower()

    rollback_promotions(final_state["promotion_results"])


# --- AC4: role matrix byte-identical to v1 -----------------------------------

V1_ROLE_MATRIX: dict[str, tuple[str, ...]] = {
    "app_feature": ("systems_engineer", "devops_netops"),
    "app_bugfix": ("systems_engineer", "devops_netops"),
    "frontend": ("systems_engineer", "devops_netops"),
    "cloud_api": ("systems_engineer", "devops_netops", "finops_integrity"),
    "mcp_diagnostic_tooling": ("systems_engineer", "devops_netops"),
    "noc_runtime": ("systems_engineer", "devops_netops", "security_auditor", "virtual_lab_chaos"),
    "infra_ansible": ("systems_engineer", "devops_netops", "virtual_lab_chaos"),
    "dns": ("systems_engineer", "devops_netops"),
    "monitoring_logging": ("systems_engineer", "devops_netops"),
    "routing_bgp_frr": ("network_architect", "security_auditor", "virtual_lab_chaos"),
    "firewall_policy": ("network_architect", "security_auditor", "virtual_lab_chaos"),
    "vault_secret_plane": ("security_auditor", "devops_netops"),
    "mixed": (
        "network_architect",
        "systems_engineer",
        "devops_netops",
        "security_auditor",
        "finops_integrity",
        "virtual_lab_chaos",
    ),
}


@pytest.mark.parametrize("change_class", sorted(V1_ROLE_MATRIX))
@pytest.mark.parametrize("risk_level", ["low", "medium", "high", "critical"])
def test_role_matrix_is_byte_identical_to_v1(change_class: str, risk_level: str) -> None:
    state = cast(
        GraphState,
        {
            "change_id": "MATRIX",
            "change_class": change_class,
            "risk_level": risk_level,
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
        },
    )

    expected = list(V1_ROLE_MATRIX[change_class])
    if risk_level in {"high", "critical"} and "virtual_lab_chaos" not in expected:
        expected.append("virtual_lab_chaos")

    assert list(required_roles_for_state(state)) == expected


# --- AC5: read-only evaluation write-guard -----------------------------------


class _WritingStubBackend:
    """Misbehaving evaluator: writes into the worktree despite read_only."""

    name = "stub"

    def execute(
        self,
        *,
        task_spec: TaskSpec,
        worktree: Path | None,
        constraints: BackendConstraints,
    ) -> AgentRunResult:
        assert worktree is not None
        (worktree / "evaluation-escape.txt").write_text("oops\n", encoding="utf-8")
        return AgentRunResult(
            status="completed",
            diff="",
            changed_paths=(),
            transcript_path=None,
            gate_evidence=(),
            iterations=1,
            wall_clock_seconds=0.0,
            cost=CostReport(),
            backend="stub",
            notes="pretending to be read-only",
        )


def test_agentic_evaluation_write_guard_detects_writes(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    _init_repo(repo)
    spec = TaskSpec(
        change_id="EVAL_GUARD",
        change_class="routing_bgp_frr",
        risk_level="high",
        request="evaluate",
        allowed_paths={"demo": ("docs",)},
    )

    evaluation = run_agentic_evaluation(
        backend=_WritingStubBackend(),
        task_spec=spec,
        worktree=repo,
        constraints=BackendConstraints(),
    )

    assert evaluation["write_violation"] is True
    assert "evaluation-escape.txt" in evaluation["changed_paths_after"]


def test_write_violation_fails_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _feature_state(tmp_path, "EVAL_VIOLATION")
    state["risk_level"] = "high"

    def _violating_evaluation(**_: Any) -> dict[str, Any]:
        return {
            "status": "completed",
            "notes": "",
            "error": None,
            "transcript_path": None,
            "write_violation": True,
            "changed_paths_before": [],
            "changed_paths_after": ["evaluation-escape.txt"],
        }

    monkeypatch.setattr(
        "hyrule_engineering_loop.nodes.run_agentic_evaluation", _violating_evaluation
    )

    final_state = dict(build_graph().invoke(state))

    assert final_state["requires_human_signoff"] is True
    assert "promotion_status" not in final_state
    assert any(
        "read-only agentic evaluation attempted to modify the worktree"
        in str(error.get("message"))
        for error in final_state["validation_errors"]
    )


def test_high_risk_judgment_runs_read_only_evaluation(tmp_path: Path) -> None:
    state = _feature_state(tmp_path, "EVAL_CLEAN")
    state["risk_level"] = "high"

    final_state = dict(build_graph().invoke(state))

    evaluations = [
        entry
        for entry in final_state["judgment_results"]
        if entry.get("phase") == "evaluation"
    ]
    assert evaluations and evaluations[0]["write_violation"] is False
    assert final_state["promotion_status"] == "passed"

    rollback_promotions(final_state["promotion_results"])


# --- prompt rebind ------------------------------------------------------------


def test_role_prompts_load_from_skills_tree() -> None:
    prompts = load_role_prompts()
    assert "role-network-architect" in prompts["network_architect"]
    assert "role-virtual-lab-chaos" in prompts["virtual_lab_chaos"]
    assert "implementation-tranche" in prompts["implementation_writer"]
    assert "Anti-rationalization" in prompts["network_architect"]


def test_feature_summary_reports_task_spec_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    _init_repo(workspace_root / "hyrule-cloud")
    request_path = tmp_path / "request.md"
    request_path.write_text("Report the spec path.\n", encoding="utf-8")

    assert (
        main(
            [
                "feature",
                "SPEC_SUMMARY",
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

    output = capsys.readouterr().out
    payload = cast(dict[str, Any], json.loads(output[output.index("{") :]))
    spec_path = Path(str(payload["task_spec_path"]))
    assert spec_path.exists()
    parsed = parse_task_spec_text(spec_path.read_text(encoding="utf-8"))
    assert parsed["change_id"] == "SPEC_SUMMARY"

    state = json.loads(Path(str(payload["state_path"])).read_text(encoding="utf-8"))
    rollback_promotions(state["promotion_results"])
