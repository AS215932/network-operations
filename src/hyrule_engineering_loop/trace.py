"""Compact execution trace rendering for the engineering loop."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
    if key in {"proposed_mutation_operations"} and isinstance(value, list):
        return [
            {
                "path": item.get("path"),
                "operation": item.get("operation"),
                "source": item.get("source"),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if key in {"diff_preview"} and isinstance(value, list):
        return [
            {
                "repo": item.get("repo"),
                "branch": item.get("branch"),
                "written_files": item.get("written_files", []),
                "diff_chars": len(str(item.get("diff_excerpt", ""))),
                "diff_truncated": item.get("diff_truncated", False),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if key in {"prompt_artifacts"} and isinstance(value, dict):
        return {"roles": sorted(value)}
    if key in {"llm_outputs"} and isinstance(value, list):
        return [
            {
                "role": item.get("role"),
                "approved": item.get("approved"),
                "proposed_mutation_paths": item.get("proposed_mutation_paths", []),
                "source_files": item.get("source_files", []),
                "model_selection": item.get("model_selection", {}),
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


def load_loop_trace(path: str | Path) -> dict[str, Any]:
    """Load a rendered loop trace artifact."""
    resolved = Path(path).expanduser().resolve()
    return cast(dict[str, Any], json.loads(resolved.read_text(encoding="utf-8")))


def summarize_loop_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Return a compact human-facing summary of trace flow and model usage."""
    raw_events = trace.get("events", [])
    events = [event for event in raw_events if isinstance(event, dict)]
    nodes = [str(event.get("node")) for event in events if event.get("node")]
    role_models: list[dict[str, Any]] = []
    validation_errors: list[dict[str, Any]] = []
    mutation_paths: set[str] = set()

    for event in events:
        output = event.get("output")
        if not isinstance(output, dict):
            continue
        llm_outputs = output.get("llm_outputs")
        if isinstance(llm_outputs, list):
            for item in llm_outputs:
                if not isinstance(item, dict):
                    continue
                model_selection = item.get("model_selection")
                role_models.append(
                    {
                        "role": item.get("role"),
                        "approved": item.get("approved"),
                        "model_selection": model_selection if isinstance(model_selection, dict) else {},
                    }
                )
                for path in item.get("proposed_mutation_paths", []):
                    if isinstance(path, str):
                        mutation_paths.add(path)
        errors = output.get("validation_errors")
        if isinstance(errors, list):
            validation_errors.extend(error for error in errors if isinstance(error, dict))
        proposed = output.get("proposed_mutations")
        if isinstance(proposed, dict):
            for path in proposed.get("paths", []):
                if isinstance(path, str):
                    mutation_paths.add(path)

    return {
        "change": trace.get("change", {}),
        "event_count": trace.get("event_count", len(events)),
        "nodes": nodes,
        "role_models": role_models,
        "validation_errors": validation_errors,
        "mutation_paths": sorted(mutation_paths),
    }


def format_loop_trace_summary(trace: dict[str, Any]) -> str:
    """Format a compact trace summary for CLI and Pi display."""
    summary = summarize_loop_trace(trace)
    change = summary["change"] if isinstance(summary["change"], dict) else {}
    lines = [
        f"change_id: {change.get('change_id', 'unknown')}",
        f"change_class: {change.get('change_class', 'unknown')}",
        f"risk_level: {change.get('risk_level', 'unknown')}",
        f"event_count: {summary['event_count']}",
        f"nodes: {' -> '.join(summary['nodes'])}",
    ]
    role_models = summary["role_models"]
    if role_models:
        lines.append("role_models:")
        for item in role_models:
            if not isinstance(item, dict):
                continue
            model_selection = item.get("model_selection")
            model = model_selection if isinstance(model_selection, dict) else {}
            lines.append(
                "  - "
                f"{item.get('role')}: "
                f"{model.get('provider', 'unknown')}/"
                f"{model.get('model', 'unknown')} "
                f"tier={model.get('tier', 'unknown')} "
                f"approved={item.get('approved')}"
            )
    mutation_paths = summary["mutation_paths"]
    if mutation_paths:
        lines.append(f"mutation_paths: {', '.join(mutation_paths)}")
    validation_errors = summary["validation_errors"]
    if validation_errors:
        lines.append("validation_errors:")
        for error in validation_errors:
            if not isinstance(error, dict):
                continue
            lines.append(
                "  - "
                f"{error.get('domain', 'unknown')}: "
                f"{error.get('message', '')}"
            )
    return "\n".join(lines) + "\n"


def write_loop_trace(state: GraphState) -> str | None:
    """Write ``loop_trace.json`` beside the NOC handoff when configured."""
    output_dir = _resolve_trace_dir(state)
    if output_dir is None:
        return None
    path = output_dir / TRACE_FILENAME
    path.write_text(json.dumps(render_loop_trace(state), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
