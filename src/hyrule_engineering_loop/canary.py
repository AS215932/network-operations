"""Real sibling-repository canary dry-run harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.nodes import ALL_ROLES
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import GraphState

CANARY_PATH = "docs/engineering-loop-canary.md"


class CanaryDryRunError(RuntimeError):
    """Raised when the sibling repo canary cannot complete safely."""


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _base_state(
    *,
    change_id: str,
    workspace_root: Path,
    output_root: Path,
    repo_name: str,
) -> GraphState:
    canary_content = (
        "# Engineering Loop Canary\n\n"
        f"- change_id: {change_id}\n"
        f"- repo: {repo_name}\n"
        "- mode: dry-run\n"
    )
    return {
        "change_id": change_id,
        "change_class": "app_bugfix",
        "risk_level": "low",
        "customer_impact": "none",
        "source_of_truth_files": ["README.md"],
        "proposed_mutations": {
            f"{repo_name}:{CANARY_PATH}": canary_content,
        },
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
        "validation_errors": [],
        "role_approvals": {role: False for role in ALL_ROLES},
        "retry_counters": {},
        "rollback_plan": "Remove the generated canary worktree and branch; no production state was changed.",
        "noc_handoff_metadata": {
            "expected_alerts": [],
            "expected_duration": "none",
            "affected_hosts_services": [],
            "rollback_trigger": "canary assertion failure",
            "operator_command_workflow": "hyrule-engineering-loop sibling-canary",
        },
        "requires_human_signoff": False,
        "approval_decision": "pending",
        "repo_workspace_root": str(workspace_root),
        "promotion_repo_names": [repo_name],
        "promotion_enabled": True,
        "promotion_allowed_paths": {repo_name: ["docs"]},
        "promotion_worktree_root": str(output_root / "worktrees"),
        "promotion_branch_prefix": "hyrule-canary",
        "handoff_output_dir": str(output_root / "handoff"),
    }


def _assert_canary_state(state: dict[str, Any], *, repo_name: str) -> None:
    if state.get("repo_adapter_status") != "passed":
        raise CanaryDryRunError("repo adapter did not pass")
    if state.get("policy_status") != "passed":
        raise CanaryDryRunError("policy did not pass")
    if state.get("promotion_status") != "passed":
        raise CanaryDryRunError("promotion did not pass")
    if state.get("approval_decision") != "pending":
        raise CanaryDryRunError("canary must stop before approval")
    if "pr_status" in state:
        raise CanaryDryRunError("canary must not publish PR state")

    promotions = state.get("promotion_results", [])
    if len(promotions) != 1:
        raise CanaryDryRunError("canary expected exactly one promotion result")

    promotion = promotions[0]
    if promotion.get("repo") != repo_name:
        raise CanaryDryRunError(f"canary promoted unexpected repo: {promotion.get('repo')}")
    diff = str(promotion.get("diff", ""))
    if CANARY_PATH not in diff:
        raise CanaryDryRunError("canary diff did not include expected docs path")

    worktree_path = Path(str(promotion["worktree_path"]))
    canary_path = worktree_path / CANARY_PATH
    if not canary_path.exists():
        raise CanaryDryRunError(f"canary file was not written: {canary_path}")
    if not state.get("noc_handoff_path"):
        raise CanaryDryRunError("canary did not render NOC handoff")


def run_sibling_repo_canary(
    *,
    workspace_root: Path,
    output_root: Path,
    repo_name: str,
    change_id: str = "SIBLING_CANARY",
    cleanup: bool = True,
) -> dict[str, Any]:
    """Run a docs-only canary against an existing sibling ``hyrule-*`` repo."""
    workspace_root = workspace_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    state_path = output_root / "state" / f"{change_id}.json"

    state = _base_state(
        change_id=change_id,
        workspace_root=workspace_root,
        output_root=output_root,
        repo_name=repo_name,
    )
    graph = build_graph(checkpointer=MemorySaver())
    final_state = dict(graph.invoke(state, {"configurable": {"thread_id": change_id}}))
    _assert_canary_state(final_state, repo_name=repo_name)

    cleanup_performed = False
    if cleanup:
        rollback_promotions(final_state["promotion_results"])
        cleanup_performed = True

    result = {
        **final_state,
        "canary_cleanup_performed": cleanup_performed,
    }
    _write_state(state_path, result)

    return {
        "state_path": str(state_path),
        "workspace_root": str(workspace_root),
        "output_root": str(output_root),
        "repo_name": repo_name,
        "canary_path": CANARY_PATH,
        "handoff_path": result.get("noc_handoff_path"),
        "trace_path": result.get("loop_trace_path"),
        "promotion_results": result.get("promotion_results", []),
        "cleanup_performed": cleanup_performed,
        "final_state": result,
    }
