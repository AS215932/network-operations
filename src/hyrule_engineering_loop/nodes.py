"""Graph nodes for the Hyrule Engineering Loop runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, cast

from hyrule_engineering_loop.backend import (
    constraints_from_state,
    create_backend,
    task_spec_from_state,
)
from hyrule_engineering_loop.gate_runner import run_gate_commands, select_gate_commands_for_mutations
from hyrule_engineering_loop.handoff import write_noc_handoff
from hyrule_engineering_loop.judgment import (
    agentic_evaluation_required,
    invoke_role_judgment,
    judgment_evidence,
    run_agentic_evaluation,
    worktree_diffs_for_judgment,
)
from hyrule_engineering_loop.llm import RoleReviewOutput, invoke_role_review, mock_llm_enabled
from hyrule_engineering_loop.memory import (
    detect_failure_patterns,
    memory_context_for_state,
    resolve_memory_write_root,
    write_journal_entry,
    write_lesson_proposal,
)
from hyrule_engineering_loop.model_policy import (
    select_backend_for_state,
    select_model_for_node,
    select_model_for_role,
)
from hyrule_engineering_loop.task_spec import (
    DEFAULT_BUDGET,
    TaskSpecError,
    parse_task_spec_text,
    render_task_spec,
    write_task_spec,
)
from hyrule_engineering_loop.policy import validate_gate_commands_for_state, validate_graph_state
from hyrule_engineering_loop.prompts import load_role_prompts
from hyrule_engineering_loop.promotion import (
    PromotionError,
    capture_worktree_results,
    diff_preview_from_results,
    setup_worktrees_for_state,
)
from hyrule_engineering_loop.repo_adapter import (
    RepoAdapterError,
    build_repo_context_bundle,
    resolve_repositories_for_state,
)
from hyrule_engineering_loop.state import ChangeClass, GraphState, RoleApprovals, RoleName
from hyrule_engineering_loop.trace import trace_event, with_trace, write_loop_trace
from hyrule_engineering_loop.workspace import cleanup_workspace

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

ROLE_PROMPT_FILES_HINT: dict[RoleName, str] = {
    "network_architect": "role-network-architect/SKILL.md",
    "systems_engineer": "role-systems-engineer/SKILL.md",
    "devops_netops": "role-devops-netops/SKILL.md",
    "security_auditor": "role-security-auditor/SKILL.md",
    "finops_integrity": "role-finops-integrity/SKILL.md",
    "virtual_lab_chaos": "role-virtual-lab-chaos/SKILL.md",
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
    operations = [
        {
            "path": mutation.path,
            "content": mutation.content,
            "operation": mutation.operation,
            "source": role,
        }
        for mutation in review.proposed_mutations
    ]
    result: StateUpdate = {
        "role_approvals": update,
        "prompt_artifacts": {role: system_prompt},
        "llm_outputs": [
            {
                "role": role,
                "phase": "consult",
                "approved": review.approved,
                "notes": review.notes,
                "proposed_mutation_paths": list(mutations),
                "source_files": list(source_context),
                "model_selection": model_selection.as_dict(),
            }
        ],
    }
    if state.get("task_spec_required", False) and not any(
        isinstance(entry, dict) and entry.get("role") == role
        for entry in state.get("role_constraints", [])
    ):
        consult_mock = state.get("llm_mock_responses", {}).get(f"consult_{role}")
        if isinstance(consult_mock, dict):
            constraints = [str(item) for item in consult_mock.get("constraints", [])]
            extra_criteria = [str(item) for item in consult_mock.get("acceptance_criteria", [])]
        else:
            constraints = [
                f"The exit criteria in skills/{ROLE_PROMPT_FILES_HINT.get(role, role)} "
                "apply to the final diff."
            ]
            extra_criteria = []
        result["role_constraints"] = [
            {
                "role": role,
                "constraints": constraints,
                "acceptance_criteria": extra_criteria,
            }
        ]
    if mutations:
        result["proposed_mutations"] = mutations
        result["proposed_mutation_operations"] = operations
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


def planner_node(state: GraphState) -> StateUpdate:
    print("[Node: Planner] Expanding intake into a task-spec sprint contract...")
    if not state.get("task_spec_required", False):
        return with_trace("planner", state, {}, input_keys=["task_spec_required"])

    update: StateUpdate = {}
    spec = dict(state.get("task_spec") or {})
    if not spec:
        request = state.get("feature_request", "")
        intent = next(
            (line.strip() for line in request.splitlines() if line.strip()),
            "(no request text supplied)",
        )[:300]
        spec = {
            "change_id": state["change_id"],
            "change_class": str(state["change_class"]),
            "risk_level": str(state["risk_level"]),
            "customer_impact": str(state["customer_impact"]),
            "repos": {
                repo: list(paths)
                for repo, paths in state.get("promotion_allowed_paths", {}).items()
            },
            "required_roles": list(required_roles_for_state(state)),
            "gates": [" ".join(command) for command in state.get("gate_commands", [])]
            or ["auto-selected from changed paths"],
            "budget": dict(state.get("backend_budget") or DEFAULT_BUDGET),
            "intake_source": "operator",
            "intent": intent,
            "acceptance_criteria": [
                "The request is implemented within the allowed paths of each target repo.",
                "All selected gates pass in the branch-backed worktree.",
                "The diff introduces no secret material or denied content patterns.",
            ],
            "non_goals": "Anything outside the allowed paths; unrelated refactors.",
            "rollback_sketch": state.get("rollback_plan")
            or "Discard the generated worktree and branch; no production state changes.",
        }
        mock = state.get("llm_mock_responses", {}).get("planner")
        if isinstance(mock, dict):
            spec.update(mock)

    try:
        parsed = parse_task_spec_text(render_task_spec(spec))
    except TaskSpecError as exc:
        update = cast(StateUpdate, {
            "requires_human_signoff": True,
            "validation_errors": [
                {
                    "node": "planner",
                    "domain": "devops",
                    "message": f"task spec is structurally invalid: {exc}",
                }
            ],
            "retry_counters": _increment_counter(state["retry_counters"], "planner"),
        })
        return with_trace(
            "planner", state, update, input_keys=["feature_request", "task_spec", "change_class"]
        )

    update["task_spec"] = parsed
    if not state.get("backend_budget"):
        update["backend_budget"] = dict(parsed.get("budget", DEFAULT_BUDGET))
    spec_path = state.get("task_spec_path")
    if spec_path:
        update["task_spec_path"] = write_task_spec(parsed, spec_path)
    # Lessons and journal tail are part of the planner's context: record
    # what memory exists for the target repos so the trace shows it and a
    # live planner can consume the full text later.
    update["memory_context"] = memory_context_for_state(
        cast(GraphState, {**state, "task_spec": parsed})
    )
    return with_trace(
        "planner",
        state,
        update,
        input_keys=["feature_request", "task_spec", "change_class", "risk_level", "memory_dir"],
    )


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


def _scaffold_feature_response(state: GraphState) -> RoleReviewOutput | None:
    if not state.get("feature_request") or not state.get("feature_scaffold_plan", False):
        return None
    target_repo = state.get("feature_target_repo")
    plan_path = state.get("feature_plan_path")
    if not target_repo or not plan_path:
        return None

    findings = state.get("remediation_findings") or []
    content = (
        "# Engineering Loop Feature Intake\n\n"
        f"- change_id: {state['change_id']}\n"
        f"- repo: {target_repo}\n"
        "- status: scaffolded\n\n"
        "## Request\n\n"
        f"{state['feature_request'].rstrip()}\n"
    )
    if findings:
        addressed = "\n".join(
            f"- {finding.get('message', '')}" for finding in findings if isinstance(finding, dict)
        )
        content += f"\n## Remediation round\n\nAddressed judgment findings:\n\n{addressed}\n"
    return RoleReviewOutput.model_validate(
        {
            "approved": True,
            "notes": "Deterministic implementation writer scaffolded the feature request.",
            "proposed_mutations": [
                {
                    "path": f"{target_repo}:{plan_path}",
                    "content": content,
                    # The scaffold file already exists in the worktree on a
                    # remediation round, so the revised tranche replaces it.
                    "operation": "replace" if findings else "create",
                }
            ],
        }
    )


def _implementation_writer_response(
    state: GraphState,
    *,
    repo_context: dict[str, Any],
) -> tuple[RoleReviewOutput | None, dict[str, Any] | None]:
    if state["proposed_mutations"] and not state.get("remediation_findings"):
        # Mutations already resolved and no judgment findings to address.
        return None, None
    has_mock = "implementation_writer" in state.get("llm_mock_responses", {})
    if mock_llm_enabled() and not has_mock:
        return _scaffold_feature_response(state), None
    if not state.get("feature_request") and not has_mock:
        return None, None

    prompts = load_role_prompts()
    system_prompt = prompts["implementation_writer"]
    model_selection = select_model_for_node("implementation_writer", state)
    writer_state = cast(GraphState, {**state, "repo_context_bundle": repo_context})
    source_context = {
        "feature_request": state.get("feature_request", ""),
        "repo_context_bundle": json.dumps(repo_context, sort_keys=True),
    }
    review = invoke_role_review(
        role="implementation_writer",
        system_prompt=system_prompt,
        source_context=source_context,
        state=writer_state,
        model_selection=model_selection,
    )
    metadata = {
        "prompt_artifacts": {"implementation_writer": system_prompt},
        "llm_outputs": [
            {
                "role": "implementation_writer",
                "approved": review.approved,
                "notes": review.notes,
                "proposed_mutation_paths": [mutation.path for mutation in review.proposed_mutations],
                "source_files": ["feature_request", "repo_context_bundle"],
                "model_selection": model_selection.as_dict(),
            }
        ],
    }
    return review, metadata


def _mutation_operations_from_writer(review: RoleReviewOutput, *, source: str) -> list[dict[str, Any]]:
    return [
        {
            "path": mutation.path,
            "content": mutation.content,
            "operation": mutation.operation,
            "source": source,
        }
        for mutation in review.proposed_mutations
    ]


def worktree_setup_node(state: GraphState) -> StateUpdate:
    print("[Node: Worktree Setup] Creating branch-backed worktrees before implementation...")
    if not state.get("promotion_enabled", False):
        update = cast(StateUpdate, {"worktree_status": "not_requested"})
        return with_trace("worktree_setup", state, update, input_keys=["promotion_enabled"])

    try:
        results = setup_worktrees_for_state(state)
    except PromotionError as exc:
        update = cast(StateUpdate, {
            "worktree_status": "failed",
            "requires_human_signoff": True,
            "validation_errors": [
                {
                    "node": "worktree_setup",
                    "domain": "devops",
                    "message": str(exc),
                }
            ],
            "retry_counters": _increment_counter(state["retry_counters"], "worktree_setup"),
        })
        return with_trace(
            "worktree_setup",
            state,
            update,
            input_keys=["promotion_repositories", "promotion_worktree_root", "promotion_base_ref"],
        )

    update = cast(StateUpdate, {
        "worktree_status": "passed",
        "worktree_results": results,
    })
    return with_trace(
        "worktree_setup",
        state,
        update,
        input_keys=["promotion_repositories", "promotion_worktree_root", "promotion_base_ref"],
    )


def delegate_implementation_node(state: GraphState) -> StateUpdate:
    print("[Node: Implementation Delegation] Executing coding-agent backend in guarded worktree...")
    if state.get("task_spec_required", False) and not state.get("task_spec"):
        refusal_update = cast(StateUpdate, {
            "implementation_writer_status": "refused",
            "requires_human_signoff": True,
            "validation_errors": [
                {
                    "node": "delegate_implementation",
                    "domain": "devops",
                    "message": "a task spec is required for this run and none was produced",
                }
            ],
            "retry_counters": _increment_counter(state["retry_counters"], "backend"),
        })
        return with_trace(
            "delegate_implementation",
            state,
            refusal_update,
            input_keys=["task_spec_required", "task_spec", "change_id"],
        )

    # Keep the spec document current: record the role consult notes so the
    # file is the complete sprint contract the backend executes against.
    spec_dict = state.get("task_spec")
    spec_path = state.get("task_spec_path")
    if spec_dict and spec_path:
        write_task_spec(spec_dict, spec_path, role_constraints=state.get("role_constraints", []))

    repo_context = build_repo_context_bundle(state)
    selection = select_backend_for_state(state)

    mutations = dict(state["proposed_mutations"])
    new_operations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    retry_key: str | None = None
    writer_status = "not_required"

    if selection.name == "mock":
        writer, writer_metadata = _implementation_writer_response(state, repo_context=repo_context)
    else:
        writer, writer_metadata = None, None
    if writer is not None:
        for mutation in writer.proposed_mutations:
            mutations[mutation.path] = mutation.content
        new_operations.extend(_mutation_operations_from_writer(writer, source="implementation_writer"))
        writer_status = "complete"
        if not writer.approved or writer.validation_errors:
            errors.extend(
                writer.validation_errors
                or [
                    {
                        "node": "implementation_writer",
                        "domain": "llm",
                        "message": "implementation writer did not approve the generated tranche",
                    }
                ]
            )
            retry_key = "llm_implementation_writer"
            writer_status = "failed"
    elif selection.name == "mock" and not mutations and not state.get("feature_request"):
        mutations["hyrule-infra"] = f"mock diff for {state['change_id']}"
        new_operations.append(
            {
                "path": "hyrule-infra",
                "content": mutations["hyrule-infra"],
                "operation": "create",
                "source": "legacy_mock",
            }
        )

    update: StateUpdate = {
        "proposed_mutations": mutations,
        "repo_context_bundle": repo_context,
        "backend_name": selection.name,
        "implementation_writer_status": writer_status,
    }
    if writer_metadata:
        update.update(writer_metadata)

    all_operations = [*state.get("proposed_mutation_operations", []), *new_operations]
    backend_runs: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    requires_signoff = False

    if writer_status != "failed":
        spec = task_spec_from_state(state)
        constraints = constraints_from_state(state)
        worktrees = state.get("worktree_results") or []
        if worktrees:
            for worktree in worktrees:
                repo = str(worktree.get("repo", ""))
                backend = create_backend(
                    selection.name,
                    command=selection.command,
                    mutations=mutations,
                    operations=all_operations,
                    repo=repo,
                )
                result = backend.execute(
                    task_spec=spec,
                    worktree=Path(str(worktree.get("worktree_path", ""))),
                    constraints=constraints,
                )
                backend_runs.append({"repo": repo, **result.as_dict()})
                changed_paths.extend(result.changed_paths)
                if result.status == "budget_exhausted":
                    writer_status = "budget_exhausted"
                    requires_signoff = True
                    errors.append(
                        {
                            "node": "delegate_implementation",
                            "domain": "devops",
                            "message": f"backend budget exhausted for {repo}: {result.notes}",
                        }
                    )
                    retry_key = retry_key or "backend"
                elif result.status == "failed":
                    writer_status = "failed"
                    errors.append(
                        {
                            "node": "delegate_implementation",
                            "domain": "devops",
                            "message": result.error or f"backend {selection.name} failed for {repo}",
                        }
                    )
                    retry_key = retry_key or "backend"
        elif selection.name == "mock":
            backend = create_backend(
                "mock", mutations=mutations, operations=all_operations, repo=None
            )
            result = backend.execute(task_spec=spec, worktree=None, constraints=constraints)
            backend_runs.append({"repo": None, **result.as_dict()})
            changed_paths.extend(result.changed_paths)
            if result.workspace_root is not None:
                update["workspace_root"] = result.workspace_root
                update["workspace_written_files"] = list(result.changed_paths)
                update["workspace_cleaned_up"] = False
            if result.status == "budget_exhausted":
                writer_status = "budget_exhausted"
                requires_signoff = True
                errors.append(
                    {
                        "node": "delegate_implementation",
                        "domain": "devops",
                        "message": f"backend budget exhausted: {result.notes}",
                    }
                )
                retry_key = retry_key or "backend"
            elif result.status == "failed":
                writer_status = "failed"
                errors.append(
                    {
                        "node": "delegate_implementation",
                        "domain": "devops",
                        "message": result.error or "mock backend failed",
                    }
                )
                retry_key = retry_key or "backend"
        else:
            writer_status = "failed"
            errors.append(
                {
                    "node": "delegate_implementation",
                    "domain": "devops",
                    "message": f"backend {selection.name} requires promotion-enabled worktrees",
                }
            )
            retry_key = retry_key or "backend"

    update["implementation_writer_status"] = writer_status
    if requires_signoff:
        update["requires_human_signoff"] = True
    if backend_runs:
        update["backend_results"] = backend_runs
    if new_operations:
        update["proposed_mutation_operations"] = new_operations
    if errors:
        update["validation_errors"] = errors
        update["retry_counters"] = _increment_counter(
            state["retry_counters"], retry_key or "backend"
        )
    if not state.get("gate_commands") and (changed_paths or mutations):
        update["gate_commands"] = select_gate_commands_for_mutations(
            changed_paths or list(mutations)
        )
    return with_trace(
        "delegate_implementation",
        state,
        update,
        input_keys=[
            "proposed_mutations",
            "feature_request",
            "worktree_results",
            "backend_budget",
            "change_id",
        ],
    )


def gate_execution_node(state: GraphState) -> StateUpdate:
    print("[Node: Gate Execution] Running deterministic validation gates...")
    if "FAIL_GATES" not in state["change_id"]:
        commands = state.get("gate_commands", [])
        if not commands:
            update = cast(StateUpdate, {"gate_status": "passed"})
            return with_trace("gate_execution", state, update, input_keys=["gate_commands", "workspace_root"])

        violations = validate_gate_commands_for_state(state)
        if violations:
            update = cast(StateUpdate, {
                "policy_status": "failed",
                "requires_human_signoff": True,
                "validation_errors": [
                    {
                        "node": "gate_policy",
                        "domain": "security",
                        "message": violation,
                    }
                    for violation in violations
                ],
                "retry_counters": _increment_counter(state["retry_counters"], "policy"),
            })
            return with_trace("gate_execution", state, update, input_keys=["gate_commands", "workspace_root"])

        cwds: list[str | None] = [
            str(worktree.get("worktree_path"))
            for worktree in state.get("worktree_results") or []
            if worktree.get("worktree_path")
        ] or [state.get("workspace_root")]
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for cwd in cwds:
            cwd_results, cwd_errors = run_gate_commands(commands, cwd=cwd)
            results.extend(cwd_results)
            errors.extend(cwd_errors)
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


def gate_policy_node(state: GraphState) -> StateUpdate:
    print("[Node: Gate Policy] Enforcing validation command policy...")
    violations = validate_gate_commands_for_state(state)
    if not violations:
        update = cast(StateUpdate, {"policy_status": "passed"})
        return with_trace("gate_policy", state, update, input_keys=["gate_commands", "policy_file"])

    update = cast(StateUpdate, {
        "policy_status": "failed",
        "requires_human_signoff": True,
        "validation_errors": [
            {
                "node": "gate_policy",
                "domain": "security",
                "message": violation,
            }
            for violation in violations
        ],
        "retry_counters": _increment_counter(state["retry_counters"], "policy"),
    })
    return with_trace("gate_policy", state, update, input_keys=["gate_commands", "policy_file"])


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


def role_judgment_node(state: GraphState) -> StateUpdate:
    print("[Node: Role Judgment] Required roles ruling on the captured diff...")
    required = required_roles_for_state(state)
    prompts = load_role_prompts()
    evidence = judgment_evidence(state)
    diffs = worktree_diffs_for_judgment(state)
    spec = task_spec_from_state(state)
    constraints = constraints_from_state(state)
    selection = select_backend_for_state(state)

    judgment_entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    # Read-only agentic evaluation for high/critical risk and routing/firewall
    # classes: the evaluator may inspect the worktree but never modify it.
    write_violation = False
    if agentic_evaluation_required(state) and state.get("worktree_results"):
        for worktree in state.get("worktree_results") or []:
            backend = create_backend(selection.name, command=selection.command)
            evaluation = run_agentic_evaluation(
                backend=backend,
                task_spec=spec,
                worktree=Path(str(worktree.get("worktree_path", ""))),
                constraints=constraints,
            )
            judgment_entries.append(
                {"phase": "evaluation", "repo": worktree.get("repo"), **evaluation}
            )
            if evaluation["write_violation"]:
                write_violation = True

    if write_violation:
        violation_update = cast(StateUpdate, {
            "judgment_results": judgment_entries,
            # Approvals granted at consult time do not survive a violated
            # evaluation; the run must stop at human sign-off.
            "role_approvals": {role: False for role in required},
            "requires_human_signoff": True,
            "validation_errors": [
                {
                    "node": "role_judgment",
                    "domain": "security",
                    "message": "read-only agentic evaluation attempted to modify the worktree",
                }
            ],
            "retry_counters": _increment_counter(state["retry_counters"], "judgment"),
        })
        return with_trace(
            "role_judgment",
            state,
            violation_update,
            input_keys=["task_spec", "worktree_results", "gate_results"],
        )

    approvals: RoleApprovals = {}
    llm_entries: list[dict[str, Any]] = []
    findings_for_backend: list[dict[str, Any]] = []
    for role in required:
        model_selection = select_model_for_role(role, state)
        judgment = invoke_role_judgment(
            role=role,
            system_prompt=prompts[role],
            state=state,
            model_selection=model_selection,
            evidence=evidence,
            diffs=diffs,
        )
        approved = judgment.verdict == "approve"
        approvals[role] = approved
        findings = [finding.model_dump() for finding in judgment.findings]
        judgment_entries.append(
            {
                "phase": "judgment",
                "role": role,
                "verdict": judgment.verdict,
                "findings": findings,
                "evidence_reviewed": list(judgment.evidence_reviewed),
            }
        )
        llm_entries.append(
            {
                "role": role,
                "phase": "judgment",
                "approved": approved,
                "notes": judgment.notes,
                "evidence_reviewed": list(judgment.evidence_reviewed),
                "proposed_mutation_paths": [],
                "source_files": [],
                "model_selection": model_selection.as_dict(),
            }
        )
        events.append(
            trace_event(
                node="role_judgment",
                state=state,
                update={
                    "verdict": judgment.verdict,
                    "finding_count": len(findings),
                    "evidence_reviewed": list(judgment.evidence_reviewed),
                },
                input_keys=["task_spec", "worktree_results", "gate_results"],
                role=role,
            )
        )
        if not approved:
            findings_for_backend.extend(findings)
            errors.append(
                {
                    "node": "role_judgment",
                    "domain": str(findings[0].get("domain", "judgment")) if findings else "judgment",
                    "message": f"{role} requested changes on the diff"
                    + (f": {findings[0].get('message')}" if findings else ""),
                }
            )

    update = cast(StateUpdate, {
        "role_approvals": approvals,
        "judgment_results": judgment_entries,
        "llm_outputs": llm_entries,
        "remediation_findings": findings_for_backend,
    })
    if errors:
        update["validation_errors"] = errors
        update["retry_counters"] = _increment_counter(state["retry_counters"], "judgment")
    return {**update, "trace_events": events}


def promotion_node(state: GraphState) -> StateUpdate:
    print("[Node: Promotion] Capturing validated worktree diffs for human review...")
    if not state.get("promotion_enabled", False):
        update = cast(StateUpdate, {"promotion_status": "not_requested"})
        return with_trace("promotion", state, update, input_keys=["promotion_enabled"])

    try:
        results = capture_worktree_results(state)
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
            input_keys=["worktree_results", "promotion_allowed_paths", "proposed_mutations"],
        )

    update = cast(StateUpdate, {
        "promotion_status": "passed",
        "promotion_results": results,
        "diff_preview": diff_preview_from_results(results),
        "requires_human_signoff": bool(results),
    })
    return with_trace(
        "promotion",
        state,
        update,
        input_keys=["worktree_results", "promotion_allowed_paths", "proposed_mutations"],
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


def reflection_node(state: GraphState) -> StateUpdate:
    print("[Node: Reflection] Distilling the run into journal notes and lesson proposals...")
    patterns = detect_failure_patterns(state)
    root = resolve_memory_write_root(state)
    if root is None:
        update = cast(StateUpdate, {
            "reflection_results": {
                "written": False,
                "reason": "memory_dir not configured",
                "failure_patterns": len(patterns),
            }
        })
        return with_trace("reflection", state, update, input_keys=["memory_dir", "retry_counters"])

    journal_path = write_journal_entry(state, root, patterns)
    proposal_path = write_lesson_proposal(state, root, patterns)
    reflection: dict[str, Any] = {
        "written": True,
        "journal_path": journal_path,
        "failure_patterns": len(patterns),
    }
    if proposal_path is not None:
        reflection["lesson_proposal_path"] = proposal_path
    update = cast(StateUpdate, {"reflection_results": reflection})

    event = trace_event(
        node="reflection",
        state=state,
        update=update,
        input_keys=["memory_dir", "retry_counters", "gate_results", "judgment_results"],
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


def human_signoff_node(state: GraphState) -> StateUpdate:
    triage_required = bool(state["validation_errors"]) or any(
        state.get(key) == "failed"
        for key in (
            "gate_status",
            "implementation_writer_status",
            "policy_status",
            "promotion_status",
            "repo_adapter_status",
        )
    )
    if triage_required:
        print("[Node: Human Sign-off] Operator triage required; pausing graph execution...")
    else:
        print("[Node: Human Sign-off] Validated change ready for operator review...")
    update = cast(StateUpdate, {
        "requires_human_signoff": True,
        "signoff_status": "needs_operator_triage" if triage_required else "ready_for_review",
    })
    event = trace_event(
        node="human_signoff",
        state=state,
        update=update,
        input_keys=["validation_errors", "retry_counters", "requires_human_signoff", "signoff_status"],
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
