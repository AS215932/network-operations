"""LangGraph topology for the Hyrule Engineering Loop skeleton."""

from __future__ import annotations

from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from hyrule_engineering_loop.nodes import (
    DOMAIN_TO_ROLE,
    ROLE_NODE_NAMES,
    classification_node,
    devops_netops_node,
    finops_integrity_node,
    gate_execution_node,
    implementation_node,
    network_architect_node,
    package_pr_node,
    required_roles,
    security_auditor_node,
    systems_engineer_node,
    human_signoff_node,
    workspace_cleanup_node,
    workspace_writer_node,
)
from hyrule_engineering_loop.state import GraphState, RoleName

Route = Literal[
    "network_architect",
    "systems_engineer",
    "devops_netops",
    "security_auditor",
    "finops_integrity",
    "package_pr",
    "human_signoff",
]


def role_review_router(state: GraphState) -> list[Route]:
    """Select required role review nodes for the classified change."""
    return [cast(Route, ROLE_NODE_NAMES[role]) for role in required_roles(state["change_class"])]


def _approval_complete(state: GraphState) -> bool:
    approvals = state["role_approvals"]
    return all(approvals.get(role, False) for role in required_roles(state["change_class"]))


def _roles_for_errors(state: GraphState) -> list[RoleName]:
    roles: list[RoleName] = []
    for error in state["validation_errors"]:
        domain = str(error.get("domain", ""))
        role = DOMAIN_TO_ROLE.get(domain)
        if role is not None and role not in roles:
            roles.append(role)
    return roles


def remediation_router(state: GraphState) -> Route | list[Route]:
    """Route gate results to remediation, human sign-off, or PR packaging."""
    if any(count >= 3 for count in state["retry_counters"].values()):
        return "human_signoff"

    if state.get("gate_status") == "failed":
        roles = _roles_for_errors(state)
        if roles:
            return [cast(Route, ROLE_NODE_NAMES[role]) for role in roles]
        return "systems_engineer"

    if _approval_complete(state):
        return "package_pr"

    return role_review_router(state)


def build_graph(
    *,
    checkpointer: Any | None = None,
    interrupt_before: list[str] | None = None,
) -> CompiledStateGraph[GraphState, None, GraphState, GraphState]:
    """Build and compile the Phase 1 Hyrule Engineering Loop graph."""
    graph = StateGraph(GraphState)

    graph.add_node("classification", classification_node)
    graph.add_node("network_architect", network_architect_node)
    graph.add_node("systems_engineer", systems_engineer_node)
    graph.add_node("devops_netops", devops_netops_node)
    graph.add_node("security_auditor", security_auditor_node)
    graph.add_node("finops_integrity", finops_integrity_node)
    graph.add_node("implementation", implementation_node)
    graph.add_node("workspace_writer", workspace_writer_node)
    graph.add_node("gate_execution", gate_execution_node)
    graph.add_node("workspace_cleanup", workspace_cleanup_node)
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
        },
    )

    for role_node in ROLE_NODE_NAMES.values():
        graph.add_edge(role_node, "implementation")

    graph.add_edge("implementation", "workspace_writer")
    graph.add_edge("workspace_writer", "gate_execution")
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
            "package_pr": "package_pr",
            "human_signoff": "human_signoff",
        },
    )
    graph.add_edge("package_pr", END)
    graph.add_edge("human_signoff", END)

    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)
