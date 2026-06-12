"""LangGraph topology for the Hyrule Engineering Loop.

v2 Phase B topology: the branch-backed worktree is created *before*
implementation, the coding-agent backend executes inside it, and the policy
guard validates the resulting diff. The temp-workspace writer is out of the
live flow (it survives only inside ``MockBackend`` scratch runs).
"""

from __future__ import annotations

from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from hyrule_engineering_loop.nodes import (
    DOMAIN_TO_ROLE,
    ROLE_NODE_NAMES,
    classification_node,
    delegate_implementation_node,
    devops_netops_node,
    finops_integrity_node,
    gate_execution_node,
    gate_policy_node,
    network_architect_node,
    package_pr_node,
    policy_node,
    promotion_node,
    repo_adapter_node,
    required_roles_for_state,
    security_auditor_node,
    systems_engineer_node,
    human_signoff_node,
    virtual_lab_chaos_node,
    workspace_cleanup_node,
    worktree_setup_node,
)
from hyrule_engineering_loop.state import GraphState, RoleName

Route = Literal[
    "network_architect",
    "systems_engineer",
    "devops_netops",
    "security_auditor",
    "finops_integrity",
    "virtual_lab_chaos",
    "worktree_setup",
    "pre_gate_policy",
    "delegate_implementation",
    "gate_execution",
    "policy",
    "repo_adapter",
    "promotion",
    "package_pr",
    "human_signoff",
]


def role_review_router(state: GraphState) -> list[Route]:
    """Select required role review nodes for the classified change."""
    return [cast(Route, ROLE_NODE_NAMES[role]) for role in required_roles_for_state(state)]


def _approval_complete(state: GraphState) -> bool:
    approvals = state["role_approvals"]
    return all(approvals.get(role, False) for role in required_roles_for_state(state))


def _roles_for_errors(state: GraphState) -> list[RoleName]:
    roles: list[RoleName] = []
    for error in state["validation_errors"]:
        domain = str(error.get("domain", ""))
        role = DOMAIN_TO_ROLE.get(domain)
        if role is not None and role not in roles:
            roles.append(role)
    return roles


def remediation_router(state: GraphState) -> Route | list[Route]:
    """Route gate results to remediation, human sign-off, or the diff guard."""
    if any(count >= 3 for count in state["retry_counters"].values()):
        return "human_signoff"

    if state.get("policy_status") == "failed":
        return "human_signoff"

    if state.get("gate_status") == "failed":
        roles = _roles_for_errors(state)
        if roles:
            return [cast(Route, ROLE_NODE_NAMES[role]) for role in roles]
        return "systems_engineer"

    if state.get("implementation_writer_status") == "failed":
        return "systems_engineer"

    if _approval_complete(state):
        return "policy"

    return role_review_router(state)


def repo_adapter_router(state: GraphState) -> Route:
    """Route repo adapter outcome to worktree setup or human sign-off."""
    if state.get("repo_adapter_status") == "failed":
        return "human_signoff"
    return "worktree_setup"


def worktree_setup_router(state: GraphState) -> Route:
    """Route worktree setup outcome to gate policy or human sign-off."""
    if state.get("worktree_status") == "failed":
        return "human_signoff"
    return "pre_gate_policy"


def pre_gate_policy_router(state: GraphState) -> Route:
    """Route pre-gate policy outcome to implementation delegation or sign-off."""
    if state.get("policy_status") == "failed":
        return "human_signoff"
    return "delegate_implementation"


def delegate_router(state: GraphState) -> Route:
    """Route backend outcome onward; budget exhaustion goes straight to sign-off."""
    if state.get("implementation_writer_status") == "budget_exhausted":
        return "human_signoff"
    return "gate_execution"


def policy_router(state: GraphState) -> Route:
    """Route diff-guard outcome to promotion capture or human sign-off."""
    if state.get("policy_status") == "failed":
        return "human_signoff"
    return "promotion"


def promotion_router(state: GraphState) -> Route:
    """Route promotion results to package, remediation, or sign-off."""
    if any(count >= 3 for count in state["retry_counters"].values()):
        return "human_signoff"
    if state.get("promotion_status") == "failed":
        return "devops_netops"
    return "package_pr"


def build_graph(
    *,
    checkpointer: Any | None = None,
    interrupt_before: list[str] | None = None,
) -> CompiledStateGraph[GraphState, None, GraphState, GraphState]:
    """Build and compile the Hyrule Engineering Loop graph."""
    graph = StateGraph(GraphState)

    graph.add_node("classification", classification_node)
    graph.add_node("network_architect", network_architect_node)
    graph.add_node("systems_engineer", systems_engineer_node)
    graph.add_node("devops_netops", devops_netops_node)
    graph.add_node("security_auditor", security_auditor_node)
    graph.add_node("finops_integrity", finops_integrity_node)
    graph.add_node("virtual_lab_chaos", virtual_lab_chaos_node)
    graph.add_node("repo_adapter", repo_adapter_node)
    graph.add_node("worktree_setup", worktree_setup_node)
    graph.add_node("pre_gate_policy", gate_policy_node)
    graph.add_node("delegate_implementation", delegate_implementation_node)
    graph.add_node("gate_execution", gate_execution_node)
    graph.add_node("workspace_cleanup", workspace_cleanup_node)
    graph.add_node("policy", policy_node)
    graph.add_node("promotion", promotion_node)
    graph.add_node("package_pr", package_pr_node)
    graph.add_node("human_signoff", human_signoff_node)

    graph.add_edge(START, "classification")
    graph.add_conditional_edges(
        "classification",
        role_review_router,
        {
            "network_architect": "network_architect",
            "systems_engineer": "systems_engineer",
            "devops_netops": "devops_netops",
            "security_auditor": "security_auditor",
            "finops_integrity": "finops_integrity",
            "virtual_lab_chaos": "virtual_lab_chaos",
        },
    )

    for role_node in ROLE_NODE_NAMES.values():
        graph.add_edge(role_node, "repo_adapter")

    graph.add_conditional_edges(
        "repo_adapter",
        repo_adapter_router,
        {
            "worktree_setup": "worktree_setup",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_conditional_edges(
        "worktree_setup",
        worktree_setup_router,
        {
            "pre_gate_policy": "pre_gate_policy",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_conditional_edges(
        "pre_gate_policy",
        pre_gate_policy_router,
        {
            "delegate_implementation": "delegate_implementation",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_conditional_edges(
        "delegate_implementation",
        delegate_router,
        {
            "gate_execution": "gate_execution",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_edge("gate_execution", "workspace_cleanup")
    graph.add_conditional_edges(
        "workspace_cleanup",
        remediation_router,
        {
            "network_architect": "network_architect",
            "systems_engineer": "systems_engineer",
            "devops_netops": "devops_netops",
            "security_auditor": "security_auditor",
            "finops_integrity": "finops_integrity",
            "virtual_lab_chaos": "virtual_lab_chaos",
            "policy": "policy",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_conditional_edges(
        "policy",
        policy_router,
        {
            "promotion": "promotion",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_conditional_edges(
        "promotion",
        promotion_router,
        {
            "devops_netops": "devops_netops",
            "package_pr": "package_pr",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_edge("package_pr", END)
    graph.add_edge("human_signoff", END)

    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)
