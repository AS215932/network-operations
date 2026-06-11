"""Graph nodes for the Hyrule Engineering Loop runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, cast

from hyrule_engineering_loop.gate_runner import run_gate_commands
from hyrule_engineering_loop.handoff import write_noc_handoff
from hyrule_engineering_loop.llm import invoke_role_review
from hyrule_engineering_loop.model_policy import select_model_for_role
from hyrule_engineering_loop.policy import validate_graph_state
from hyrule_engineering_loop.prompts import load_role_prompts
from hyrule_engineering_loop.promotion import PromotionError, promote_mutations
from hyrule_engineering_loop.repo_adapter import RepoAdapterError, resolve_repositories_for_state
from hyrule_engineering_loop.state import ChangeClass, GraphState, RoleApprovals, RoleName
from hyrule_engineering_loop.trace import trace_event, with_trace, write_loop_trace
from hyrule_engineering_loop.workspace import cleanup_workspace, write_mutations_to_workspace

StateUpdate = dict[str, Any]

ALL_ROLES: tuple[RoleName, ...] = (
    "network_architect",
    "systems_engineer",
    "devops_netops",
    "security_auditor",
    "finops_integrity",
    "virtual_lab_chaos",
)

ROLE_NODE_NAMES: dict[RoleName, str] = {
    "network_architect": "network_architect",
    "systems_engineer": "systems_engineer",
    "devops_netops": "devops_netops",
    "security_auditor": "security_auditor",
    "finops_integrity": "finops_integrity",
    "virtual_lab_chaos": "virtual_lab_chaos",
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
    "lab": "virtual_lab_chaos",
    "chaos": "virtual_lab_chaos",
    "emulation": "virtual_lab_chaos",
    "rollback": "virtual_lab_chaos",
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
        return ("systems_engineer", "devops_netops", "security_auditor", "virtual_lab_chaos")
    if change_class == "infra_ansible":
        return ("systems_engineer", "devops_netops", "virtual_lab_chaos")
    if change_class in {"dns", "monitoring_logging"}:
        return ("systems_engineer", "devops_netops")
    if change_class in {"routing_bgp_frr", "firewall_policy"}:
        return ("network_architect", "security_auditor", "virtual_lab_chaos")
    if change_class == "vault_secret_plane":
        return ("security_auditor", "devops_netops")
    if change_class == "mixed":
        return ALL_ROLES
    return ("systems_engineer", "devops_netops")


def required_roles_for_state(state: GraphState) -> tuple[RoleName, ...]:
    """Return required roles after change class and risk-level expansion."""
    roles = list(required_roles(state["change_class"]))
    if state["risk_level"] in {"high", "critical"} and "virtual_lab_chaos" not in roles:
        roles.append("virtual_lab_chaos")
    return tuple(roles)


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


def _read_repo_source_context(paths: Iterable[str], state: GraphState) -> dict[str, str]:
    base = Path.cwd().resolve()
    repos = {
        name: Path(path).expanduser().resolve()
        for name, path in state.get("promotion_repositories", {}).items()
    }
    context: dict[str, str] = {}
    if state.get("feature_request"):
        request_key = state.get("feature_request_path", "feature_request")
        context[request_key] = state["feature_request"]

    for raw_path in paths:
        repo_root: Path | None = None
        source_path = raw_path
        if ":" in raw_path:
            repo_name, source_path = raw_path.split(":", 1)
            repo_root = repos.get(repo_name)
            if repo_root is None:
                context[raw_path] = "[skipped: unknown repo]"
                continue

        path = Path(source_path)
        if path.is_absolute() or ".." in path.parts:
            context[raw_path] = "[skipped: unsafe source path]"
            continue

        root = repo_root or base
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
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
    if role not in required_roles_for_state(state):
        return {}

    prompts = load_role_prompts()
    system_prompt = prompts[role]
    source_context = _read_repo_source_context(state["source_of_truth_files"], state)
    model_selection = select_model_for_role(role, state)
    review = invoke_role_review(
        role=role,
        system_prompt=system_prompt,
        source_context=source_context,
        state=state,
        model_selection=model_selection,
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
                "model_selection": model_selection.as_dict(),
            }
        ],
    }
    if mutations:
        result["proposed_mutations"] = mutations
    if errors:
        result["validation_errors"] = errors
        result["retry_counters"] = _increment_counter(state["retry_counters"], f"llm_{role}")
    return with_trace(
        ROLE_NODE_NAMES[role],
        state,
        result,
        input_keys=["source_of_truth_files", "feature_request", "proposed_mutations"],
        role=role,
    )


def _increment_counter(counters: dict[str, int], key: str) -> dict[str, int]:
    return {key: 1}


def _reset_required_approvals(state: GraphState, roles: Iterable[RoleName]) -> RoleApprovals:
    approvals = dict(state["role_approvals"])
    for role in roles:
        approvals[role] = False
    return approvals


def classification_node(state: GraphState) -> StateUpdate:
    print("[Node: Change Classifier] Classifying change and loading source-of-truth context...")
    roles = required_roles_for_state(state)
    source_files = list(state["source_of_truth_files"])

    if state["change_class"] == "firewall_policy" and "docs/network-flows.md" not in source_files:
        source_files.append("docs/network-flows.md")
    if state["change_class"] in {"routing_bgp_frr", "mixed"} and "docs/architecture.md" not in source_files:
        source_files.append("docs/architecture.md")

    update = cast(StateUpdate, {
        "role_approvals": _reset_required_approvals(state, roles),
        "source_of_truth_files": source_files,
    })
    return with_trace(
        "classification",
        state,
        update,
        input_keys=["change_class", "source_of_truth_files"],
    )


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


def virtual_lab_chaos_node(state: GraphState) -> StateUpdate:
    print("[Node: Virtual Lab & Chaos Simulation Engineer] Reviewing lab proof and rollback behavior...")
    return _role_review_update("virtual_lab_chaos", state)


def implementation_node(state: GraphState) -> StateUpdate:
    print("[Node: Implementation Tranche] Preparing deterministic mock implementation tranche...")
    mutations = dict(state["proposed_mutations"])
    mutations.setdefault("hyrule-infra", f"mock diff for {state['change_id']}")
    update = {"proposed_mutations": mutations}
    return with_trace(
        "implementation",
        state,
        update,
        input_keys=["proposed_mutations", "change_id"],
    )


def workspace_writer_node(state: GraphState) -> StateUpdate:
    print("[Node: Workspace Writer] Applying proposed mutations to temporary workspace...")
    root, written = write_mutations_to_workspace(state["proposed_mutations"])
    update = {
        "workspace_root": str(root),
        "workspace_written_files": written,
        "workspace_cleaned_up": False,
    }
    return with_trace("workspace_writer", state, update, input_keys=["proposed_mutations"])


def gate_execution_node(state: GraphState) -> StateUpdate:
    print("[Node: Gate Execution] Running deterministic validation gates...")
    if "FAIL_GATES" not in state["change_id"]:
        commands = state.get("gate_commands", [])
        if not commands:
            update = cast(StateUpdate, {"gate_status": "passed"})
            return with_trace("gate_execution", state, update, input_keys=["gate_commands", "workspace_root"])

        results, errors = run_gate_commands(commands, cwd=state.get("workspace_root"))
        if errors:
            update = cast(StateUpdate, {
                "gate_results": results,
                "validation_errors": errors,
                "retry_counters": _increment_counter(state["retry_counters"], "ci"),
                "gate_status": "failed",
            })
            return with_trace("gate_execution", state, update, input_keys=["gate_commands", "workspace_root"])
        update = cast(StateUpdate, {"gate_results": results, "gate_status": "passed"})
        return with_trace("gate_execution", state, update, input_keys=["gate_commands", "workspace_root"])

    domain = "security"
    error = {
        "node": "gate_execution",
        "domain": domain,
        "message": "Mock gate failure requested by change_id",
    }
    update = cast(StateUpdate, {
        "validation_errors": [error],
        "retry_counters": _increment_counter(state["retry_counters"], domain),
        "role_approvals": {"security_auditor": False},
        "gate_status": "failed",
    })
    return with_trace("gate_execution", state, update, input_keys=["change_id", "gate_commands", "workspace_root"])


def workspace_cleanup_node(state: GraphState) -> StateUpdate:
    print("[Node: Workspace Cleanup] Removing temporary workspace...")
    update = cast(StateUpdate, {"workspace_cleaned_up": cleanup_workspace(state.get("workspace_root"))})
    return with_trace("workspace_cleanup", state, update, input_keys=["workspace_root"])


def repo_adapter_node(state: GraphState) -> StateUpdate:
    print("[Node: Repo Adapter] Verifying promotion target repositories...")
    if not state.get("promotion_enabled", False):
        return {"repo_adapter_status": "not_run"}

    try:
        repositories, results = resolve_repositories_for_state(state)
    except RepoAdapterError as exc:
        update = cast(StateUpdate, {
            "repo_adapter_status": "failed",
            "requires_human_signoff": True,
            "validation_errors": [
                {
                    "node": "repo_adapter",
                    "domain": "devops",
                    "message": str(exc),
                }
            ],
            "retry_counters": _increment_counter(state["retry_counters"], "repo_adapter"),
        })
        return with_trace(
            "repo_adapter",
            state,
            update,
            input_keys=["repo_workspace_root", "promotion_repo_names", "promotion_repositories"],
        )

    update = cast(StateUpdate, {
        "repo_adapter_status": "passed",
        "promotion_repositories": repositories,
        "repo_adapter_results": results,
    })
    return with_trace(
        "repo_adapter",
        state,
        update,
        input_keys=["repo_workspace_root", "promotion_repo_names", "promotion_repositories"],
    )


def policy_node(state: GraphState) -> StateUpdate:
    print("[Node: Policy] Enforcing mutation and publication policy...")
    violations = validate_graph_state(state)
    if not violations:
        update = cast(StateUpdate, {"policy_status": "passed"})
        return with_trace("policy", state, update, input_keys=["proposed_mutations", "gate_commands", "policy_file"])

    update = cast(StateUpdate, {
        "policy_status": "failed",
        "requires_human_signoff": True,
        "validation_errors": [
            {
                "node": "policy",
                "domain": "security",
                "message": violation,
            }
            for violation in violations
        ],
        "retry_counters": _increment_counter(state["retry_counters"], "policy"),
    })
    return with_trace("policy", state, update, input_keys=["proposed_mutations", "gate_commands", "policy_file"])


def promotion_node(state: GraphState) -> StateUpdate:
    print("[Node: Promotion] Promoting validated mutations to branch-backed worktrees...")
    if not state.get("promotion_enabled", False):
        update = cast(StateUpdate, {"promotion_status": "not_requested"})
        return with_trace("promotion", state, update, input_keys=["promotion_enabled"])

    try:
        results = promote_mutations(state)
    except PromotionError as exc:
        update = cast(StateUpdate, {
            "promotion_status": "failed",
            "validation_errors": [
                {
                    "node": "promotion",
                    "domain": "devops",
                    "message": str(exc),
                }
            ],
            "retry_counters": _increment_counter(state["retry_counters"], "promotion"),
        })
        return with_trace(
            "promotion",
            state,
            update,
            input_keys=["promotion_repositories", "promotion_allowed_paths", "proposed_mutations"],
        )

    update = cast(StateUpdate, {
        "promotion_status": "passed",
        "promotion_results": results,
        "requires_human_signoff": bool(results),
    })
    return with_trace(
        "promotion",
        state,
        update,
        input_keys=["promotion_repositories", "promotion_allowed_paths", "proposed_mutations"],
    )


def package_pr_node(state: GraphState) -> StateUpdate:
    print("[Node: PR Packaging] Producing PR summary, rollout notes, and NOC handoff...")
    noc_handoff = dict(state["noc_handoff_metadata"])
    noc_handoff.setdefault("status", "ready_for_pr_signoff")
    noc_handoff.setdefault("post_deploy_checks", ["review graph state", "run documented gates"])
    rollback_plan = state["rollback_plan"] or "Revert the tranche and rerun validation gates."
    handoff_state = cast(GraphState, {
        **state,
        "rollback_plan": rollback_plan,
        "noc_handoff_metadata": noc_handoff,
    })
    handoff_path = write_noc_handoff(handoff_state)
    result: StateUpdate = {
        "rollback_plan": rollback_plan,
        "noc_handoff_metadata": noc_handoff,
    }
    if handoff_path is not None:
        result["noc_handoff_path"] = handoff_path
    event = trace_event(
        node="package_pr",
        state=state,
        update=result,
        input_keys=["rollback_plan", "noc_handoff_metadata", "promotion_results"],
    )
    trace_state = cast(GraphState, {
        **handoff_state,
        **result,
        "trace_events": [*state.get("trace_events", []), event],
    })
    trace_path = write_loop_trace(trace_state)
    result["trace_events"] = [event]
    if trace_path is not None:
        result["loop_trace_path"] = trace_path
    return result


def human_signoff_node(state: GraphState) -> StateUpdate:
    print("[Node: Human Sign-off] Circuit breaker reached; pausing for operator review...")
    update = cast(StateUpdate, {"requires_human_signoff": True})
    event = trace_event(
        node="human_signoff",
        state=state,
        update=update,
        input_keys=["validation_errors", "retry_counters", "requires_human_signoff"],
    )
    trace_state = cast(GraphState, {
        **state,
        **update,
        "trace_events": [*state.get("trace_events", []), event],
    })
    trace_path = write_loop_trace(trace_state)
    result: StateUpdate = {**update, "trace_events": [event]}
    if trace_path is not None:
        result["loop_trace_path"] = trace_path
    return result
