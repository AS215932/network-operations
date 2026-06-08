"""Shared LangGraph state for the Hyrule Engineering Loop."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Literal, NotRequired, TypedDict

ChangeClass = Literal[
    "app_feature",
    "app_bugfix",
    "frontend",
    "cloud_api",
    "mcp_diagnostic_tooling",
    "noc_runtime",
    "infra_ansible",
    "routing_bgp_frr",
    "firewall_policy",
    "dns",
    "vault_secret_plane",
    "monitoring_logging",
    "mixed",
]

RiskLevel = Literal["low", "medium", "high", "critical"]
CustomerImpact = Literal["none", "possible", "expected"]
LabVerification = Literal["not_applicable", "pending", "passed", "failed"]
GateStatus = Literal["not_run", "passed", "failed"]
PromotionStatus = Literal["not_requested", "passed", "failed"]
PRStatus = Literal["not_requested", "pushed", "failed"]

RoleName = Literal[
    "network_architect",
    "systems_engineer",
    "devops_netops",
    "security_auditor",
    "finops_integrity",
]

RoleApprovals = Dict[RoleName, bool]


def merge_string_map(left: Dict[str, str] | None, right: Dict[str, str] | None) -> Dict[str, str]:
    """Merge parallel string maps without dropping sibling branch writes."""
    merged: Dict[str, str] = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


def merge_role_approvals(left: RoleApprovals | None, right: RoleApprovals | None) -> RoleApprovals:
    """Merge parallel role approval updates without dropping sibling branches."""
    merged: RoleApprovals = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


def merge_retry_counters(
    left: Dict[str, int] | None,
    right: Dict[str, int] | None,
) -> Dict[str, int]:
    """Merge retry counters from parallel branches by summing matching keys."""
    merged: Dict[str, int] = {}
    if left:
        merged.update(left)
    if right:
        for key, value in right.items():
            merged[key] = merged.get(key, 0) + value
    return merged


class GraphState(TypedDict):
    """Centralized state passed through the engineering-loop graph."""

    change_id: str
    change_class: ChangeClass
    risk_level: RiskLevel
    customer_impact: CustomerImpact

    source_of_truth_files: List[str]
    proposed_mutations: Annotated[Dict[str, str], merge_string_map]

    mcp_schema_breaking: bool
    emulated_lab_verified: LabVerification

    validation_errors: Annotated[List[Dict[str, Any]], operator.add]
    role_approvals: Annotated[RoleApprovals, merge_role_approvals]
    retry_counters: Annotated[Dict[str, int], merge_retry_counters]

    rollback_plan: str
    noc_handoff_metadata: Dict[str, Any]
    requires_human_signoff: bool

    gate_commands: NotRequired[List[List[str]]]
    gate_results: NotRequired[Annotated[List[Dict[str, Any]], operator.add]]
    gate_status: NotRequired[GateStatus]
    prompt_artifacts: NotRequired[Annotated[Dict[str, str], merge_string_map]]
    approval_decision: NotRequired[Literal["pending", "approved", "rejected"]]
    llm_mock_responses: NotRequired[Dict[str, Dict[str, Any]]]
    llm_outputs: NotRequired[Annotated[List[Dict[str, Any]], operator.add]]
    workspace_root: NotRequired[str]
    workspace_written_files: NotRequired[List[str]]
    workspace_cleaned_up: NotRequired[bool]
    handoff_output_dir: NotRequired[str]
    noc_handoff_path: NotRequired[str]
    promotion_enabled: NotRequired[bool]
    promotion_repositories: NotRequired[Dict[str, str]]
    promotion_allowed_paths: NotRequired[Dict[str, List[str]]]
    promotion_worktree_root: NotRequired[str]
    promotion_branch_prefix: NotRequired[str]
    promotion_status: NotRequired[PromotionStatus]
    promotion_results: NotRequired[Annotated[List[Dict[str, Any]], operator.add]]
    pr_enabled: NotRequired[bool]
    pr_status: NotRequired[PRStatus]
    pr_remote: NotRequired[str]
    pr_create_github: NotRequired[bool]
    commit_message: NotRequired[str]
    pr_title: NotRequired[str]
    pr_body: NotRequired[str]
    pr_results: NotRequired[Annotated[List[Dict[str, Any]], operator.add]]
