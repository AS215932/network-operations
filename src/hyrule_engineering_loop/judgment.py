"""Post-diff role judgment for the Hyrule Engineering Loop.

Phase C of the v2 architecture (``docs/engineering-loop/v2-architecture.md``
§3): generation and evaluation are separated. Required roles consult before
implementation and *judge the actual diff* after the authoritative gates,
returning a structured verdict against the task spec's acceptance criteria.
High-risk judgments may run a read-only agentic evaluation pass (the same
``AgentBackend`` with ``read_only=True``) before ruling; any write attempt
from evaluation mode fails the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from hyrule_engineering_loop.backend import (
    AgentBackend,
    BackendConstraints,
    TaskSpec,
    capture_worktree_diff,
)
from hyrule_engineering_loop.llm import HTTPStructuredLLMClient, mock_llm_enabled
from hyrule_engineering_loop.model_policy import ModelSelection
from hyrule_engineering_loop.state import GraphState, RoleName

AGENTIC_EVALUATION_CLASSES = frozenset({"routing_bgp_frr", "firewall_policy"})


class JudgmentFinding(BaseModel):
    """One structured finding tied to the diff and, where possible, a criterion."""

    domain: str = "judgment"
    severity: Literal["blocker", "major", "minor"] = "major"
    path: str | None = None
    message: str
    suggested_remediation: str | None = None


class RoleJudgment(BaseModel):
    """A required role's ruling on the post-gate diff."""

    verdict: Literal["approve", "request_changes"]
    findings: list[JudgmentFinding] = Field(default_factory=list)
    evidence_reviewed: list[str] = Field(default_factory=list)
    notes: str = ""


def agentic_evaluation_required(state: GraphState) -> bool:
    """Read-only agentic evaluation triggers for high risk and routing/firewall."""
    return state["risk_level"] in {"high", "critical"} or str(
        state["change_class"]
    ) in AGENTIC_EVALUATION_CLASSES


def judgment_evidence(state: GraphState) -> dict[str, Any]:
    """Collect the bounded artifact a role rules on: diff stats + gate evidence."""
    spec = state.get("task_spec") or {}
    return {
        "acceptance_criteria": list(spec.get("acceptance_criteria", [])),
        "non_goals": spec.get("non_goals", ""),
        "gate_results": [
            {
                "command": result.get("command"),
                "returncode": result.get("returncode"),
                "stderr": str(result.get("stderr", ""))[:2000],
            }
            for result in state.get("gate_results", [])
            if isinstance(result, dict)
        ],
        "gate_status": state.get("gate_status", "not_run"),
        "backend_runs": [
            {
                "repo": run.get("repo"),
                "status": run.get("status"),
                "changed_paths": run.get("changed_paths", []),
            }
            for run in state.get("backend_results", [])
            if isinstance(run, dict)
        ],
        "proposed_mutation_paths": sorted(state["proposed_mutations"]),
    }


def worktree_diffs_for_judgment(state: GraphState, *, max_chars: int = 12_000) -> dict[str, str]:
    """Capture the current worktree diffs (clipped) for judgment context."""
    diffs: dict[str, str] = {}
    for worktree in state.get("worktree_results") or []:
        repo = str(worktree.get("repo", ""))
        path = Path(str(worktree.get("worktree_path", "")))
        if not path.is_dir():
            continue
        diff, _ = capture_worktree_diff(path)
        diffs[repo] = diff[:max_chars]
    return diffs


def _prior_judgment_count(state: GraphState, role: RoleName) -> int:
    return sum(
        1
        for output in state.get("llm_outputs", [])
        if isinstance(output, dict)
        and output.get("role") == role
        and output.get("phase") == "judgment"
    )


def resolve_mock_judgment(state: GraphState, role: RoleName) -> RoleJudgment | None:
    """Resolve a mocked judgment; list values are consumed sequentially per round."""
    raw: Any = state.get("llm_mock_responses", {}).get(f"judge_{role}")
    if raw is None:
        return None
    if isinstance(raw, list):
        if not raw:
            return None
        index = min(_prior_judgment_count(state, role), len(raw) - 1)
        raw = raw[index]
    return RoleJudgment.model_validate(raw)


def invoke_role_judgment(
    *,
    role: RoleName,
    system_prompt: str,
    state: GraphState,
    model_selection: ModelSelection,
    evidence: dict[str, Any],
    diffs: dict[str, str],
) -> RoleJudgment:
    """Invoke one role judgment: mock override, deterministic default, or live."""
    mock = resolve_mock_judgment(state, role)
    if mock is not None:
        return mock

    if mock_llm_enabled():
        return RoleJudgment(
            verdict="approve",
            evidence_reviewed=[
                "git diff",
                "gate_results",
                *(f"diff:{repo}" for repo in sorted(diffs)),
            ],
            notes=(
                f"Deterministic judgment approval for {role}; "
                f"criteria={len(evidence.get('acceptance_criteria', []))}, "
                f"gate_status={evidence.get('gate_status')}."
            ),
        )

    try:
        client = HTTPStructuredLLMClient.from_env(model_selection)
        return client.invoke_structured(
            node=f"judge_{role}",
            system_prompt=(
                f"{system_prompt}\n\n"
                "You are ruling on the finished diff against the task spec's "
                "acceptance criteria. Return verdict approve only when every "
                "criterion in your domain has evidence; otherwise return "
                "request_changes with findings keyed by path."
            ),
            payload={"evidence": evidence, "diffs": diffs},
            output_model=RoleJudgment,
        )
    except Exception as exc:
        return RoleJudgment(
            verdict="request_changes",
            findings=[
                JudgmentFinding(
                    domain="llm",
                    severity="major",
                    message=f"judgment invocation failed: {exc}",
                )
            ],
            notes="judgment invocation failed; routed as request_changes",
        )


def run_agentic_evaluation(
    *,
    backend: AgentBackend,
    task_spec: TaskSpec,
    worktree: Path,
    constraints: BackendConstraints,
) -> dict[str, Any]:
    """Run a read-only agentic evaluation pass with a write-guard.

    The worktree already carries the implementation diff; the evaluator may
    open files and run read-only inspection, but the worktree state must be
    byte-identical afterwards. Any change is a write violation that fails
    the run — evaluation mode never edits.
    """
    before_diff, before_paths = capture_worktree_diff(worktree)
    result = backend.execute(
        task_spec=task_spec,
        worktree=worktree,
        constraints=BackendConstraints(
            max_iterations=constraints.max_iterations,
            max_wall_clock_seconds=constraints.max_wall_clock_seconds,
            max_cost_usd=constraints.max_cost_usd,
            network_scope=constraints.network_scope,
            read_only=True,
        ),
    )
    after_diff, after_paths = capture_worktree_diff(worktree)
    write_violation = after_diff != before_diff or after_paths != before_paths
    return {
        "status": result.status,
        "notes": result.notes,
        "error": result.error,
        "transcript_path": result.transcript_path,
        "write_violation": write_violation,
        "changed_paths_before": list(before_paths),
        "changed_paths_after": list(after_paths),
    }
