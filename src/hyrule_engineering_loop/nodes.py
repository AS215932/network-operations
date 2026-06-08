"""Graph nodes for the Hyrule Engineering Loop runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from hyrule_engineering_loop.gate_runner import run_gate_commands
from hyrule_engineering_loop.handoff import write_noc_handoff
from hyrule_engineering_loop.llm import invoke_role_review
from hyrule_engineering_loop.prompts import load_role_prompts
from hyrule_engineering_loop.state import ChangeClass, GraphState, RoleApprovals, RoleName
from hyrule_engineering_loop.workspace import cleanup_workspace, write_mutations_to_workspace

StateUpdate = dict[str, Any]

ALL_ROLES: tuple[RoleName, ...] = (
    "network_architect",
    "systems_engineer",
    "devops_netops",
    "security_auditor",
    "finops_integrity",
)

ROLE_NODE_NAMES: dict[RoleName, str] = {
    "network_architect": "network_architect",
    "systems_engineer": "systems_engineer",
    "devops_netops": "devops_netops",
    "security_auditor": "security_auditor",
    "finops_integrity": "finops_integrity",
}

DOMAIN_TO_ROLE: dict[str, RoleName] = {
    "network": "network_architect",
    "routing": "network_architect",
    "firewall": "network_architect",
    "systems": "systems_engineer",
    "runtime": "systems_engineer",
    "devops": "devops_netops",
    "ci": "devops_netops",
    "security": "security_auditor",
    "secret": "security_auditor",
    "finops": "finops_integrity",
    "billing": "finops_integrity",
}


def required_roles(change_class: ChangeClass) -> tuple[RoleName, ...]:
    """Return the senior roles required for a change class."""
    if change_class in {"app_feature", "app_bugfix", "frontend"}:
        return ("systems_engineer", "devops_netops")
    if change_class == "cloud_api":
        return ("systems_engineer", "devops_netops", "finops_integrity")
    if change_class == "mcp_diagnostic_tooling":
        return ("systems_engineer", "devops_netops")
    if change_class == "noc_runtime":
        return ("systems_engineer", "devops_netops", "security_auditor")
    if change_class in {"infra_ansible", "dns", "monitoring_logging"}:
        return ("systems_engineer", "devops_netops")
    if change_class in {"routing_bgp_frr", "firewall_policy"}:
        return ("network_architect", "security_auditor")
    if change_class == "vault_secret_plane":
        return ("security_auditor", "devops_netops")
    if change_class == "mixed":
        return ALL_ROLES
    return ("systems_engineer", "devops_netops")


def _read_source_context(paths: Iterable[str]) -> dict[str, str]:
    base = Path.cwd().resolve()
    context: dict[str, str] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            context[raw_path] = "[skipped: unsafe source path]"
            continue

        resolved = (base / path).resolve()
        if not resolved.is_relative_to(base):
            context[raw_path] = "[skipped: outside workspace]"
            continue
        if not resolved.exists():
            context[raw_path] = "[missing]"
            continue
        if not resolved.is_file():
            context[raw_path] = "[skipped: not a file]"
            continue
        context[raw_path] = resolved.read_text(encoding="utf-8")
    return context


def _role_review_update(role: RoleName, state: GraphState) -> StateUpdate:
    if role not in required_roles(state["change_class"]):
        return {}

    prompts = load_role_prompts()
    system_prompt = prompts[role]
    source_context = _read_source_context(state["source_of_truth_files"])
    review = invoke_role_review(
        role=role,
        system_prompt=system_prompt,
        source_context=source_context,
        state=state,
    )

    update: RoleApprovals = {role: review.approved}
    errors: list[dict[str, Any]] = []
    errors.extend(review.validation_errors)
    if "MERGE_ERRORS" in state["change_id"]:
        errors.append(
            {
                "node": role,
                "domain": role,
                "message": f"Reducer merge probe from {role}",
            }
        )

    mutations = {mutation.path: mutation.content for mutation in review.proposed_mutations}
    result: StateUpdate = {
        "role_approvals": update,
        "prompt_artifacts": {role: system_prompt},
        "llm_outputs": [
            {
                "role": role,
                "approved": review.approved,
                "notes": review.notes,
                "proposed_mutation_paths": list(mutations),
                "source_files": list(source_context),
            }
        ],
    }
    if mutations:
        result["proposed_mutations"] = mutations
    if errors:
        result["validation_errors"] = errors
        result["retry_counters"] = _increment_counter(state["retry_counters"], f"llm_{role}")
    return result


def _increment_counter(counters: dict[str, int], key: str) -> dict[str, int]:
    return {key: 1}


def _reset_required_approvals(state: GraphState, roles: Iterable[RoleName]) -> RoleApprovals:
    approvals = dict(state["role_approvals"])
    for role in roles:
        approvals[role] = False
    return approvals


def classification_node(state: GraphState) -> StateUpdate:
    print("[Node: Change Classifier] Classifying change and loading source-of-truth context...")
    roles = required_roles(state["change_class"])
    source_files = list(state["source_of_truth_files"])

    if state["change_class"] == "firewall_policy" and "docs/network-flows.md" not in source_files:
        source_files.append("docs/network-flows.md")
    if state["change_class"] in {"routing_bgp_frr", "mixed"} and "docs/architecture.md" not in source_files:
        source_files.append("docs/architecture.md")

    return {
        "role_approvals": _reset_required_approvals(state, roles),
        "source_of_truth_files": source_files,
    }


def network_architect_node(state: GraphState) -> StateUpdate:
    print("[Node: Senior Network Architect] Reviewing routing topology...")
    return _role_review_update("network_architect", state)


def systems_engineer_node(state: GraphState) -> StateUpdate:
    print("[Node: Senior Systems Engineer] Reviewing host and runtime behavior...")
    return _role_review_update("systems_engineer", state)


def devops_netops_node(state: GraphState) -> StateUpdate:
    print("[Node: Senior DevOps/NetOps Engineer] Reviewing CI, deploy, and rollback gates...")
    return _role_review_update("devops_netops", state)


def security_auditor_node(state: GraphState) -> StateUpdate:
    print("[Node: Senior Security & Cryptographic Auditor] Reviewing security boundaries...")
    return _role_review_update("security_auditor", state)


def finops_integrity_node(state: GraphState) -> StateUpdate:
    print("[Node: FinOps & Billing Integrity Engineer] Reviewing payment and quota integrity...")
    return _role_review_update("finops_integrity", state)


def implementation_node(state: GraphState) -> StateUpdate:
    print("[Node: Implementation Tranche] Preparing deterministic mock implementation tranche...")
    mutations = dict(state["proposed_mutations"])
    mutations.setdefault("hyrule-infra", f"mock diff for {state['change_id']}")
    return {"proposed_mutations": mutations}


def workspace_writer_node(state: GraphState) -> StateUpdate:
    print("[Node: Workspace Writer] Applying proposed mutations to temporary workspace...")
    root, written = write_mutations_to_workspace(state["proposed_mutations"])
    return {
        "workspace_root": str(root),
        "workspace_written_files": written,
        "workspace_cleaned_up": False,
    }


def gate_execution_node(state: GraphState) -> StateUpdate:
    print("[Node: Gate Execution] Running deterministic validation gates...")
    if "FAIL_GATES" not in state["change_id"]:
        commands = state.get("gate_commands", [])
        if not commands:
            return {"gate_status": "passed"}

        results, errors = run_gate_commands(commands, cwd=state.get("workspace_root"))
        if errors:
            return {
                "gate_results": results,
                "validation_errors": errors,
                "retry_counters": _increment_counter(state["retry_counters"], "ci"),
                "gate_status": "failed",
            }
        return {"gate_results": results, "gate_status": "passed"}

    domain = "security"
    error = {
        "node": "gate_execution",
        "domain": domain,
        "message": "Mock gate failure requested by change_id",
    }
    return {
        "validation_errors": [error],
        "retry_counters": _increment_counter(state["retry_counters"], domain),
        "role_approvals": {"security_auditor": False},
        "gate_status": "failed",
    }


def workspace_cleanup_node(state: GraphState) -> StateUpdate:
    print("[Node: Workspace Cleanup] Removing temporary workspace...")
    return {"workspace_cleaned_up": cleanup_workspace(state.get("workspace_root"))}


def package_pr_node(state: GraphState) -> StateUpdate:
    print("[Node: PR Packaging] Producing PR summary, rollout notes, and NOC handoff...")
    noc_handoff = dict(state["noc_handoff_metadata"])
    noc_handoff.setdefault("status", "ready_for_pr_signoff")
    noc_handoff.setdefault("post_deploy_checks", ["review graph state", "run documented gates"])
    rollback_plan = state["rollback_plan"] or "Revert the tranche and rerun validation gates."
    handoff_state: GraphState = {
        **state,
        "rollback_plan": rollback_plan,
        "noc_handoff_metadata": noc_handoff,
    }
    handoff_path = write_noc_handoff(handoff_state)
    result: StateUpdate = {
        "rollback_plan": rollback_plan,
        "noc_handoff_metadata": noc_handoff,
    }
    if handoff_path is not None:
        result["noc_handoff_path"] = handoff_path
    return result


def human_signoff_node(state: GraphState) -> StateUpdate:
    print("[Node: Human Sign-off] Circuit breaker reached; pausing for operator review...")
    return {"requires_human_signoff": True}
