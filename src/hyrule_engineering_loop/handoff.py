"""NOC handoff rendering for production monitoring consumers."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.state import GraphState

HANDOFF_FILENAME = "noc_handoff.json"


def resolve_handoff_dir(state: GraphState) -> Path | None:
    """Return the configured handoff directory, if one was designated."""
    raw_dir = state.get("handoff_output_dir") or os.environ.get("HYRULE_HANDOFF_DIR")
    if not raw_dir:
        return None
    path = Path(raw_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"handoff output path is not a directory: {path}")
    return path


def render_noc_handoff(state: GraphState) -> dict[str, Any]:
    """Render the structural handoff consumed by NOC/runtime monitoring."""
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "change": {
            "change_id": state["change_id"],
            "change_class": state["change_class"],
            "risk_level": state["risk_level"],
            "customer_impact": state["customer_impact"],
            "mcp_schema_breaking": state["mcp_schema_breaking"],
            "emulated_lab_verified": state["emulated_lab_verified"],
        },
        "validation": {
            "gate_status": state.get("gate_status", "not_run"),
            "error_count": len(state["validation_errors"]),
            "retry_counters": state["retry_counters"],
            "gate_results": state.get("gate_results", []),
        },
        "roles": {
            "approvals": state["role_approvals"],
            "llm_outputs": state.get("llm_outputs", []),
        },
        "workspace": {
            "written_files": state.get("workspace_written_files", []),
            "cleaned_up": state.get("workspace_cleaned_up", False),
        },
        "rollback": {
            "plan": state["rollback_plan"],
            "requires_human_signoff": state["requires_human_signoff"],
        },
        "noc": state["noc_handoff_metadata"],
    }


def write_noc_handoff(state: GraphState) -> str | None:
    """Write ``noc_handoff.json`` into the configured output directory."""
    output_dir = resolve_handoff_dir(state)
    if output_dir is None:
        return None

    payload = render_noc_handoff(state)
    path = output_dir / HANDOFF_FILENAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
