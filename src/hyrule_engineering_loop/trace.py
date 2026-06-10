"""Compact execution trace rendering for the engineering loop."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState

TRACE_FILENAME = "loop_trace.json"


def _resolve_trace_dir(state: GraphState) -> Path | None:
    raw_dir = state.get("handoff_output_dir") or os.environ.get("HYRULE_HANDOFF_DIR")
    if not raw_dir:
        return None
    path = Path(raw_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _summarize_value(key: str, value: Any) -> Any:
    if key in {"feature_request"}:
        return {"chars": len(str(value))}
    if key in {"proposed_mutations"} and isinstance(value, dict):
        return {"paths": sorted(value), "count": len(value)}
    if key in {"prompt_artifacts"} and isinstance(value, dict):
        return {"roles": sorted(value)}
    if key in {"llm_outputs"} and isinstance(value, list):
        return [
            {
                "role": item.get("role"),
                "approved": item.get("approved"),
                "proposed_mutation_paths": item.get("proposed_mutation_paths", []),
                "source_files": item.get("source_files", []),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if key in {"gate_results"} and isinstance(value, list):
        return [
            {
                "command": item.get("command"),
                "status": item.get("status"),
                "returncode": item.get("returncode"),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if key in {"promotion_results"} and isinstance(value, list):
        return [
            {
                "repo": item.get("repo"),
                "branch": item.get("branch"),
                "worktree_path": item.get("worktree_path"),
                "written_files": item.get("written_files", []),
                "diff_chars": len(str(item.get("diff", ""))),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if key in {"workspace_written_files", "source_of_truth_files"} and isinstance(value, list):
        return list(value)
    if key in {"validation_errors"} and isinstance(value, list):
        return [
            {
                "node": item.get("node"),
                "domain": item.get("domain"),
                "message": item.get("message"),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return str(value)


def compact_update(update: dict[str, Any]) -> dict[str, Any]:
    """Return a trace-safe summary of a graph node update."""
    return {key: _summarize_value(key, value) for key, value in update.items() if key != "trace_events"}


def trace_event(
    *,
    node: str,
    state: GraphState,
    update: dict[str, Any],
    input_keys: list[str],
    role: str | None = None,
) -> dict[str, Any]:
    """Build one compact trace event for a node execution."""
    event: dict[str, Any] = {
        "node": node,
        "timestamp": datetime.now(UTC).isoformat(),
        "input_keys": input_keys,
        "output": compact_update(update),
        "state_before": {
            "validation_error_count": len(state["validation_errors"]),
            "retry_counters": dict(state["retry_counters"]),
            "approval_true": sorted(
                role_name
                for role_name, approved in state["role_approvals"].items()
                if approved
            ),
            "mutation_paths": sorted(state["proposed_mutations"]),
        },
    }
    if role is not None:
        event["role"] = role
    return event


def with_trace(
    node: str,
    state: GraphState,
    update: dict[str, Any],
    *,
    input_keys: list[str],
    role: str | None = None,
) -> dict[str, Any]:
    """Attach one trace event to a node update."""
    return {
        **update,
        "trace_events": [
            trace_event(node=node, state=state, update=update, input_keys=input_keys, role=role)
        ],
    }


def render_loop_trace(state: GraphState) -> dict[str, Any]:
    """Render a compact trace artifact."""
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "change": {
            "change_id": state["change_id"],
            "change_class": state["change_class"],
            "risk_level": state["risk_level"],
            "customer_impact": state["customer_impact"],
        },
        "event_count": len(state.get("trace_events", [])),
        "events": state.get("trace_events", []),
    }


def write_loop_trace(state: GraphState) -> str | None:
    """Write ``loop_trace.json`` beside the NOC handoff when configured."""
    output_dir = _resolve_trace_dir(state)
    if output_dir is None:
        return None
    path = output_dir / TRACE_FILENAME
    path.write_text(json.dumps(render_loop_trace(state), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
