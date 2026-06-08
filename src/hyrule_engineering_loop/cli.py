"""Operator CLI for the Hyrule Engineering Loop skeleton."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.memory import MemorySaver

from hyrule_engineering_loop.canary import CanaryDryRunError, run_sibling_repo_canary
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.nodes import ALL_ROLES
from hyrule_engineering_loop.operator_harness import OperatorHarnessError, run_operator_dry_run
from hyrule_engineering_loop.pr import PRBoundaryError, publish_promoted_worktrees
from hyrule_engineering_loop.state import ChangeClass, GraphState

DEFAULT_STATE_DIR = Path(".engineering-loop-state")


def _default_state(change_id: str, change_class: ChangeClass) -> GraphState:
    return {
        "change_id": change_id,
        "change_class": change_class,
        "risk_level": "low",
        "customer_impact": "none",
        "source_of_truth_files": [],
        "proposed_mutations": {},
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
        "validation_errors": [],
        "role_approvals": {role: False for role in ALL_ROLES},
        "retry_counters": {},
        "rollback_plan": "",
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
        "approval_decision": "pending",
    }


def _state_path(state_dir: Path, change_id: str) -> Path:
    return state_dir / f"{change_id}.json"


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_state(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _parse_key_value(items: list[str] | None, *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{option} expects NAME=VALUE, got {item}")
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def _parse_repo_paths(items: list[str] | None, *, option: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{option} expects REPO=PATH_PREFIX, got {item}")
        repo, prefix = item.split("=", 1)
        parsed.setdefault(repo, []).append(prefix)
    return parsed


def _parse_mutations(items: list[str] | None, *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{option} expects PATH=CONTENT, got {item}")
        path, content = item.split("=", 1)
        parsed[path] = content
    return parsed


def run_command(args: argparse.Namespace) -> int:
    change_class = cast(ChangeClass, args.change_class)
    state = _default_state(args.change_id, change_class)
    if getattr(args, "repo_workspace_root", None):
        state["repo_workspace_root"] = args.repo_workspace_root
    if getattr(args, "promotion_repo_name", None):
        state["promotion_repo_names"] = list(args.promotion_repo_name)
    if getattr(args, "promotion_base_ref", None):
        state["promotion_base_ref"] = args.promotion_base_ref
    if getattr(args, "mutation", None):
        state["proposed_mutations"] = _parse_mutations(args.mutation, option="--mutation")
    if args.policy_file:
        state["policy_file"] = args.policy_file
    if args.handoff_dir:
        state["handoff_output_dir"] = args.handoff_dir
    if args.gate_command:
        gate_command = list(args.gate_command)
        if gate_command and gate_command[0] == "--":
            gate_command = gate_command[1:]
        state["gate_commands"] = [gate_command]
    if args.promotion_enabled:
        state["promotion_enabled"] = True
        state["promotion_repositories"] = _parse_key_value(
            args.promotion_repo,
            option="--promotion-repo",
        )
        state["promotion_allowed_paths"] = _parse_repo_paths(
            args.promotion_allow,
            option="--promotion-allow",
        )
        if args.promotion_worktree_root:
            state["promotion_worktree_root"] = args.promotion_worktree_root
        if args.promotion_branch_prefix:
            state["promotion_branch_prefix"] = args.promotion_branch_prefix

    graph = build_graph(
        checkpointer=MemorySaver(),
        interrupt_before=["human_signoff"] if args.interrupt_before_signoff else None,
    )
    final_state = graph.invoke(state, {"configurable": {"thread_id": args.change_id}})
    path = _state_path(Path(args.state_dir), args.change_id)
    _write_state(path, dict(final_state))
    print(f"[CLI] wrote state artifact: {path}")
    return 0


def dry_run_command(args: argparse.Namespace) -> int:
    args.promotion_enabled = True
    args.interrupt_before_signoff = True
    return run_command(args)


def show_command(args: argparse.Namespace) -> int:
    path = _state_path(Path(args.state_dir), args.change_id)
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def approve_command(args: argparse.Namespace) -> int:
    path = _state_path(Path(args.state_dir), args.change_id)
    state = _read_state(path)
    state["approval_decision"] = "approved"
    state["requires_human_signoff"] = False
    _write_state(path, state)
    print(f"[CLI] approved state artifact: {path}")
    return 0


def pr_command(args: argparse.Namespace) -> int:
    path = _state_path(Path(args.state_dir), args.change_id)
    state = _read_state(path)
    if args.policy_file:
        state["policy_file"] = args.policy_file
    try:
        pr_results = publish_promoted_worktrees(
            state,
            remote=args.remote,
            commit_message=args.commit_message,
            pr_title=args.title,
            pr_body=args.body,
            pr_labels=args.label,
            pr_reviewers=args.reviewer,
            create_github_pr=args.create_github_pr,
        )
    except PRBoundaryError as exc:
        state["pr_status"] = "failed"
        state["pr_results"] = [
            {
                "error": str(exc),
            }
        ]
        _write_state(path, state)
        print(f"[CLI] PR boundary refused: {exc}")
        return 1

    state["pr_status"] = "pushed"
    state["pr_results"] = pr_results
    state["pr_remote"] = args.remote
    state["commit_message"] = args.commit_message
    state["pr_title"] = args.title
    state["pr_body"] = args.body
    state["pr_labels"] = args.label
    state["pr_reviewers"] = args.reviewer
    state["pr_create_github"] = args.create_github_pr
    _write_state(path, state)
    print(f"[CLI] published {len(pr_results)} promoted worktree(s)")
    return 0


def operator_dry_run_command(args: argparse.Namespace) -> int:
    try:
        result = run_operator_dry_run(
            root=Path(args.root),
            change_id=args.change_id,
            mock_github_pr_url=args.mock_github_pr_url,
            labels=args.label,
            reviewers=args.reviewer,
        )
    except (OperatorHarnessError, PRBoundaryError) as exc:
        print(f"[CLI] operator dry-run failed: {exc}")
        return 1

    summary = {
        "state_path": result["state_path"],
        "handoff_path": result["handoff_path"],
        "remote_path": result["remote_path"],
        "branch": result["branch"],
        "remote_commit": result["remote_commit"],
        "github_pr": result["pr_results"][0]["github_pr"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def sibling_canary_command(args: argparse.Namespace) -> int:
    try:
        result = run_sibling_repo_canary(
            workspace_root=Path(args.workspace_root),
            output_root=Path(args.output_root),
            repo_name=args.repo_name,
            change_id=args.change_id,
            cleanup=not args.keep_worktree,
        )
    except CanaryDryRunError as exc:
        print(f"[CLI] sibling canary failed: {exc}")
        return 1

    summary = {
        "state_path": result["state_path"],
        "handoff_path": result["handoff_path"],
        "repo_name": result["repo_name"],
        "canary_path": result["canary_path"],
        "cleanup_performed": result["cleanup_performed"],
        "promotion_count": len(result["promotion_results"]),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Hyrule Engineering Loop skeleton")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the graph and persist final state")
    run_parser.add_argument("change_id")
    run_parser.add_argument("change_class")
    run_parser.add_argument("--policy-file")
    run_parser.add_argument("--handoff-dir")
    run_parser.add_argument("--promotion-enabled", action="store_true")
    run_parser.add_argument("--repo-workspace-root")
    run_parser.add_argument("--promotion-repo-name", action="append")
    run_parser.add_argument("--promotion-base-ref")
    run_parser.add_argument("--promotion-repo", action="append")
    run_parser.add_argument("--promotion-allow", action="append")
    run_parser.add_argument("--promotion-worktree-root")
    run_parser.add_argument("--promotion-branch-prefix")
    run_parser.add_argument("--mutation", action="append")
    run_parser.add_argument("--gate-command", nargs=argparse.REMAINDER)
    run_parser.add_argument("--no-interrupt-before-signoff", action="store_false", dest="interrupt_before_signoff")
    run_parser.set_defaults(func=run_command, interrupt_before_signoff=True)

    dry_run_parser = subparsers.add_parser(
        "dry-run",
        help="run graph, policy, promotion, and handoff without approval or PR publication",
    )
    dry_run_parser.add_argument("change_id")
    dry_run_parser.add_argument("change_class")
    dry_run_parser.add_argument("--policy-file")
    dry_run_parser.add_argument("--handoff-dir")
    dry_run_parser.add_argument("--repo-workspace-root")
    dry_run_parser.add_argument("--promotion-repo-name", action="append")
    dry_run_parser.add_argument("--promotion-base-ref")
    dry_run_parser.add_argument("--promotion-repo", action="append")
    dry_run_parser.add_argument("--promotion-allow", action="append")
    dry_run_parser.add_argument("--promotion-worktree-root")
    dry_run_parser.add_argument("--promotion-branch-prefix")
    dry_run_parser.add_argument("--mutation", action="append")
    dry_run_parser.add_argument("--gate-command", nargs=argparse.REMAINDER)
    dry_run_parser.set_defaults(func=dry_run_command, promotion_enabled=True, interrupt_before_signoff=True)

    show_parser = subparsers.add_parser("show", help="print a persisted state artifact")
    show_parser.add_argument("change_id")
    show_parser.set_defaults(func=show_command)

    approve_parser = subparsers.add_parser("approve", help="record manual approval in a state artifact")
    approve_parser.add_argument("change_id")
    approve_parser.set_defaults(func=approve_command)

    pr_parser = subparsers.add_parser("pr", help="commit and push approved promoted worktrees")
    pr_parser.add_argument("change_id")
    pr_parser.add_argument("--policy-file")
    pr_parser.add_argument("--remote", default="origin")
    pr_parser.add_argument("--commit-message", required=True)
    pr_parser.add_argument("--title", required=True)
    pr_parser.add_argument("--body", required=True)
    pr_parser.add_argument("--label", action="append", default=[])
    pr_parser.add_argument("--reviewer", action="append", default=[])
    pr_parser.add_argument("--create-github-pr", action="store_true")
    pr_parser.set_defaults(func=pr_command)

    operator_parser = subparsers.add_parser(
        "operator-dry-run",
        help="run an offline end-to-end operator harness with a disposable repo",
    )
    operator_parser.add_argument("--root", required=True)
    operator_parser.add_argument("--change-id", default="OPERATOR_DRY_RUN")
    operator_parser.add_argument(
        "--mock-github-pr-url",
        default="https://github.example.invalid/hyrule/demo/pull/1",
    )
    operator_parser.add_argument("--label", action="append", default=[])
    operator_parser.add_argument("--reviewer", action="append", default=[])
    operator_parser.set_defaults(func=operator_dry_run_command)

    canary_parser = subparsers.add_parser(
        "sibling-canary",
        help="run a docs-only dry-run against a real sibling hyrule-* repo",
    )
    canary_parser.add_argument("--workspace-root", required=True)
    canary_parser.add_argument("--repo-name", required=True)
    canary_parser.add_argument("--output-root", required=True)
    canary_parser.add_argument("--change-id", default="SIBLING_CANARY")
    canary_parser.add_argument("--keep-worktree", action="store_true")
    canary_parser.set_defaults(func=sibling_canary_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
