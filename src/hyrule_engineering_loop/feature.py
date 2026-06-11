"""Feature-intake UX for the Hyrule Engineering Loop."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, cast

from langgraph.checkpoint.memory import MemorySaver

from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.nodes import ALL_ROLES
from hyrule_engineering_loop.preflight import preflight_feature_state
from hyrule_engineering_loop.repo_adapter import discover_hyrule_repositories
from hyrule_engineering_loop.state import ChangeClass, GraphState


class FeatureIntakeError(RuntimeError):
    """Raised when a feature request cannot be converted to graph state."""


class FeaturePreflightError(FeatureIntakeError):
    """Raised when live preflight fails before graph execution."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("feature live preflight failed")
        self.result = result


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower() or "feature"


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_request(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FeatureIntakeError(f"feature request file does not exist: {resolved}")
    return resolved.read_text(encoding="utf-8")


def _parse_mutations(repo_name: str, items: Iterable[str]) -> dict[str, str]:
    mutations: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise FeatureIntakeError(f"--mock-mutation expects PATH=CONTENT, got {item}")
        raw_path, content = item.split("=", 1)
        path = raw_path if ":" in raw_path else f"{repo_name}:{raw_path}"
        mutations[path] = content
    return mutations


def _parse_gate_command(raw_command: list[str] | None) -> list[list[str]]:
    if not raw_command:
        return []
    command = list(raw_command)
    if command and command[0] == "--":
        command = command[1:]
    return [command] if command else []


def _resolve_repo(workspace_root: Path, repo_name: str) -> Path:
    discovered = discover_hyrule_repositories(workspace_root)
    repo_path = discovered.get(repo_name)
    if repo_path is None:
        raise FeatureIntakeError(f"unknown sibling repo: {repo_name}")
    return repo_path


def _failure_retry_count(state: dict[str, Any], node: str, domain: str) -> int:
    counters = state.get("retry_counters", {})
    if not isinstance(counters, dict):
        return 0
    candidates = [node, domain, f"llm_{node}"]
    return max((int(counters.get(candidate, 0)) for candidate in candidates), default=0)


def failure_summary(state: dict[str, Any], *, state_path: Path) -> dict[str, Any] | None:
    """Return a compact next-action summary for failed or paused runs."""
    errors = [error for error in state.get("validation_errors", []) if isinstance(error, dict)]
    if not errors and not state.get("requires_human_signoff", False):
        return None

    last_error = errors[-1] if errors else {}
    node = str(last_error.get("node", "human_signoff"))
    domain = str(last_error.get("domain", "operator"))
    model_selection: dict[str, Any] = {}
    for output in reversed(state.get("llm_outputs", [])):
        if isinstance(output, dict) and output.get("role") == node:
            raw_model = output.get("model_selection")
            if isinstance(raw_model, dict):
                model_selection = dict(raw_model)
            break

    message = str(last_error.get("message", "manual sign-off required"))
    stderr = str(last_error.get("stderr", ""))
    return {
        "last_failing_node": node,
        "domain": domain,
        "retry_count": _failure_retry_count(state, node, domain),
        "model_selection": model_selection,
        "error_excerpt": (stderr or message)[:800],
        "next_operator_command": f"uv run hyrule-engineering-loop trace --state-path {state_path}",
    }


def build_feature_state(
    *,
    change_id: str,
    change_class: ChangeClass,
    workspace_root: Path,
    output_root: Path,
    repo_name: str,
    request_path: Path,
    allowed_paths: list[str],
    source_files: list[str] | None = None,
    mock_mutations: list[str] | None = None,
    plan_path: str | None = None,
    scaffold_plan: bool = True,
    gate_command: list[str] | None = None,
    promotion_base_ref: str = "HEAD",
    model_policy_file: str | None = None,
    live_mode: bool = False,
    dry_live_mode: bool = False,
) -> GraphState:
    """Build a graph state from operator-friendly feature-intake arguments."""
    workspace_root = workspace_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    repo_path = _resolve_repo(workspace_root, repo_name)
    request_text = _read_request(request_path)
    active_plan_path = plan_path or f"docs/engineering-loop/{_slug(change_id)}.md"

    mutations = _parse_mutations(repo_name, mock_mutations or [])
    repo_source_files = [f"{repo_name}:{path}" for path in source_files or []]
    if not repo_source_files:
        repo_source_files = [f"{repo_name}:README.md"]

    state: GraphState = {
        "change_id": change_id,
        "change_class": change_class,
        "risk_level": "low",
        "customer_impact": "none",
        "source_of_truth_files": repo_source_files,
        "proposed_mutations": mutations,
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
        "validation_errors": [],
        "role_approvals": {role: False for role in ALL_ROLES},
        "retry_counters": {},
        "rollback_plan": "Discard the generated feature worktree and branch; no production state was changed.",
        "noc_handoff_metadata": {
            "expected_alerts": [],
            "expected_duration": "none",
            "affected_hosts_services": [],
            "rollback_trigger": "operator rejection, failed gates, or failed post-deploy checks",
            "operator_command_workflow": "hyrule-engineering-loop feature",
        },
        "requires_human_signoff": False,
        "approval_decision": "pending",
        "repo_workspace_root": str(workspace_root),
        "promotion_repo_names": [repo_name],
        "promotion_repositories": {repo_name: str(repo_path)},
        "promotion_enabled": True,
        "promotion_allowed_paths": {repo_name: allowed_paths},
        "promotion_worktree_root": str(output_root / "worktrees"),
        "promotion_branch_prefix": "hyrule-feature",
        "promotion_base_ref": promotion_base_ref,
        "handoff_output_dir": str(output_root / "handoff"),
        "feature_request": request_text,
        "feature_request_path": str(request_path.expanduser().resolve()),
        "feature_target_repo": repo_name,
        "feature_plan_path": active_plan_path,
        "feature_scaffold_plan": scaffold_plan,
        "live_mode": live_mode,
        "dry_live_mode": dry_live_mode,
        "gate_commands": _parse_gate_command(gate_command),
    }
    if model_policy_file is not None:
        state["model_policy_file"] = model_policy_file
    return state


def run_feature_dry_live(
    *,
    change_id: str,
    change_class: str,
    workspace_root: Path,
    output_root: Path,
    repo_name: str,
    request_path: Path,
    allowed_paths: list[str],
    source_files: list[str] | None = None,
    plan_path: str | None = None,
    promotion_base_ref: str = "HEAD",
    model_policy_file: str | None = None,
) -> dict[str, Any]:
    """Build live writer prompt/context/model preview without provider calls."""
    state = build_feature_state(
        change_id=change_id,
        change_class=cast(ChangeClass, change_class),
        workspace_root=workspace_root,
        output_root=output_root,
        repo_name=repo_name,
        request_path=request_path,
        allowed_paths=allowed_paths,
        source_files=source_files,
        mock_mutations=[],
        plan_path=plan_path,
        scaffold_plan=False,
        gate_command=None,
        promotion_base_ref=promotion_base_ref,
        model_policy_file=model_policy_file,
        live_mode=False,
        dry_live_mode=True,
    )
    preflight = preflight_feature_state(state, output_root=output_root, live=False)
    state["preflight_results"] = preflight
    state_path = output_root.expanduser().resolve() / "state" / f"{change_id}.dry-live.json"
    _write_state(state_path, dict(state))
    return {
        "state_path": str(state_path),
        "repo_name": repo_name,
        "dry_live": True,
        "provider_called": False,
        "preflight": preflight,
    }


def run_feature_intake(
    *,
    change_id: str,
    change_class: str,
    workspace_root: Path,
    output_root: Path,
    repo_name: str,
    request_path: Path,
    allowed_paths: list[str],
    source_files: list[str] | None = None,
    mock_mutations: list[str] | None = None,
    plan_path: str | None = None,
    scaffold_plan: bool = True,
    gate_command: list[str] | None = None,
    promotion_base_ref: str = "HEAD",
    model_policy_file: str | None = None,
    live_mode: bool = False,
) -> dict[str, Any]:
    """Run the graph from a human-authored feature request."""
    state = build_feature_state(
        change_id=change_id,
        change_class=cast(ChangeClass, change_class),
        workspace_root=workspace_root,
        output_root=output_root,
        repo_name=repo_name,
        request_path=request_path,
        allowed_paths=allowed_paths,
        source_files=source_files,
        mock_mutations=mock_mutations,
        plan_path=plan_path,
        scaffold_plan=scaffold_plan,
        gate_command=gate_command,
        promotion_base_ref=promotion_base_ref,
        model_policy_file=model_policy_file,
        live_mode=live_mode,
    )
    if live_mode:
        preflight = preflight_feature_state(state, output_root=output_root, live=True)
        state["preflight_results"] = preflight
        if not preflight["ok"]:
            raise FeaturePreflightError(preflight)

    graph = build_graph(checkpointer=MemorySaver())
    if live_mode:
        with _temporary_env({"HYRULE_MOCK_LLM": "0"}):
            final_state = dict(graph.invoke(state, {"configurable": {"thread_id": change_id}}))
    else:
        final_state = dict(graph.invoke(state, {"configurable": {"thread_id": change_id}}))
    state_path = output_root.expanduser().resolve() / "state" / f"{change_id}.json"
    summary = failure_summary(final_state, state_path=state_path)
    if summary is not None:
        final_state["failure_summary"] = summary
    _write_state(state_path, final_state)

    return {
        "state_path": str(state_path),
        "handoff_path": final_state.get("noc_handoff_path"),
        "trace_path": final_state.get("loop_trace_path"),
        "repo_name": repo_name,
        "promotion_count": len(final_state.get("promotion_results", [])),
        "requires_human_signoff": final_state.get("requires_human_signoff", False),
        "policy_status": final_state.get("policy_status", "not_run"),
        "promotion_status": final_state.get("promotion_status", "not_requested"),
        "gate_status": final_state.get("gate_status", "not_run"),
        "diff_preview": final_state.get("diff_preview", []),
        "failure_summary": final_state.get("failure_summary"),
        "live_mode": live_mode,
        "final_state": final_state,
    }


def run_writer_canary(
    *,
    workspace_root: Path,
    output_root: Path,
    repo_name: str,
    change_id: str = "WRITER_CANARY",
    live_mode: bool = False,
    dry_live_mode: bool = True,
    model_policy_file: str | None = None,
) -> dict[str, Any]:
    """Run a controlled docs-only writer canary in live or dry-live mode."""
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    request_path = output_root / "request.md"
    request_path.write_text(
        "Create a docs-only engineering loop writer canary artifact.\n",
        encoding="utf-8",
    )
    if dry_live_mode:
        return run_feature_dry_live(
            change_id=change_id,
            change_class="app_feature",
            workspace_root=workspace_root,
            output_root=output_root,
            repo_name=repo_name,
            request_path=request_path,
            allowed_paths=["docs"],
            source_files=["README.md"],
            model_policy_file=model_policy_file,
        )
    return run_feature_intake(
        change_id=change_id,
        change_class="app_feature",
        workspace_root=workspace_root,
        output_root=output_root,
        repo_name=repo_name,
        request_path=request_path,
        allowed_paths=["docs"],
        source_files=["README.md"],
        scaffold_plan=False,
        model_policy_file=model_policy_file,
        live_mode=live_mode,
    )
