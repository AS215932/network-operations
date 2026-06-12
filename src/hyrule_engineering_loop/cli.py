"""Operator CLI for the Hyrule Engineering Loop skeleton."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.memory import MemorySaver

from hyrule_engineering_loop.canary import CanaryDryRunError, run_sibling_repo_canary
from hyrule_engineering_loop.feature import (
    FeatureIntakeError,
    FeaturePreflightError,
    run_backend_canary,
    run_feature_dry_live,
    run_feature_intake,
)
from hyrule_engineering_loop.graph import build_graph
from hyrule_engineering_loop.memory import list_memory
from hyrule_engineering_loop.model_policy import (
    model_policy_snapshot,
    validate_model_policy,
)
from hyrule_engineering_loop.nodes import ALL_ROLES
from hyrule_engineering_loop.operator_harness import OperatorHarnessError, run_operator_dry_run
from hyrule_engineering_loop.pr import PRBoundaryError, publish_promoted_worktrees
from hyrule_engineering_loop.promotion import rollback_promotions
from hyrule_engineering_loop.state import ChangeClass, GraphState
from hyrule_engineering_loop.trace import (
    format_loop_trace_summary,
    load_loop_trace,
    summarize_loop_trace,
)

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


def _write_state_file(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _model_summary_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for output in state.get("llm_outputs", []):
        if not isinstance(output, dict):
            continue
        model_selection = output.get("model_selection")
        summary.append(
            {
                "role": output.get("role"),
                "approved": output.get("approved"),
                "model_selection": model_selection if isinstance(model_selection, dict) else {},
            }
        )
    return summary


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
    if args.model_policy:
        state["model_policy_file"] = args.model_policy
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
    _write_state_file(path, state)
    print(f"[CLI] approved state artifact: {path}")
    return 0


def state_approve_command(args: argparse.Namespace) -> int:
    path = Path(args.state_path).expanduser().resolve()
    state = _read_state(path)
    state["approval_decision"] = "approved"
    state["requires_human_signoff"] = False
    _write_state_file(path, state)
    print(json.dumps({"state_path": str(path), "approval_decision": "approved"}, indent=2, sort_keys=True))
    return 0


def state_cleanup_command(args: argparse.Namespace) -> int:
    path = Path(args.state_path).expanduser().resolve()
    state = _read_state(path)
    promotions = list(state.get("promotion_results", []))
    promoted_worktrees = {str(item.get("worktree_path")) for item in promotions}
    promotions.extend(
        item
        for item in state.get("worktree_results", [])
        if str(item.get("worktree_path")) not in promoted_worktrees
    )
    rollback_promotions(promotions)
    state["promotion_cleanup_performed"] = True
    _write_state_file(path, state)
    print(
        json.dumps(
            {
                "state_path": str(path),
                "promotion_cleanup_performed": True,
                "promotion_count": len(promotions),
            },
            indent=2,
            sort_keys=True,
        )
    )
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
        "trace_path": result["trace_path"],
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
        "trace_path": result["trace_path"],
        "repo_name": result["repo_name"],
        "canary_path": result["canary_path"],
        "cleanup_performed": result["cleanup_performed"],
        "promotion_count": len(result["promotion_results"]),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def feature_command(args: argparse.Namespace) -> int:
    if args.live and args.dry_live:
        print("[CLI] --live and --dry-live are mutually exclusive")
        return 1
    if args.dry_live:
        try:
            result = run_feature_dry_live(
                change_id=args.change_id,
                change_class=args.change_class,
                workspace_root=Path(args.workspace_root),
                output_root=Path(args.output_root),
                repo_name=args.repo,
                request_path=Path(args.request),
                allowed_paths=args.allow,
                source_files=args.source,
                plan_path=args.plan_path,
                promotion_base_ref=args.base_ref,
                model_policy_file=args.model_policy,
                memory_dir=args.memory_dir,
            )
        except FeatureIntakeError as exc:
            print(f"[CLI] feature dry-live failed: {exc}")
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    try:
        result = run_feature_intake(
            change_id=args.change_id,
            change_class=args.change_class,
            workspace_root=Path(args.workspace_root),
            output_root=Path(args.output_root),
            repo_name=args.repo,
            request_path=Path(args.request),
            allowed_paths=args.allow,
            source_files=args.source,
            mock_mutations=args.mock_mutation,
            plan_path=args.plan_path,
            scaffold_plan=not args.no_scaffold_plan,
            gate_command=args.gate_command,
            promotion_base_ref=args.base_ref,
            model_policy_file=args.model_policy,
            live_mode=args.live,
            task_spec=Path(args.task_spec) if args.task_spec else None,
            memory_dir=args.memory_dir,
        )
    except FeaturePreflightError as exc:
        print(json.dumps({"preflight": exc.result, "live_mode": args.live}, indent=2, sort_keys=True))
        return 1
    except FeatureIntakeError as exc:
        print(f"[CLI] feature intake failed: {exc}")
        return 1

    summary = {
        "state_path": result["state_path"],
        "handoff_path": result["handoff_path"],
        "trace_path": result["trace_path"],
        "task_spec_path": result.get("task_spec_path"),
        "reflection": result.get("reflection"),
        "repo_name": result["repo_name"],
        "promotion_count": result["promotion_count"],
        "requires_human_signoff": result["requires_human_signoff"],
        "policy_status": result["policy_status"],
        "promotion_status": result["promotion_status"],
        "gate_status": result["gate_status"],
        "model_summary": _model_summary_from_state(result["final_state"]),
        "diff_preview": result["diff_preview"],
        "signoff_status": result.get("signoff_status"),
        "live_mode": result["live_mode"],
    }
    if result.get("failure_summary") is not None:
        summary["failure_summary"] = result["failure_summary"]
    if result.get("signoff_summary") is not None:
        summary["signoff_summary"] = result["signoff_summary"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def backend_canary_command(args: argparse.Namespace) -> int:
    if args.live and args.dry_live:
        print("[CLI] --live and --dry-live are mutually exclusive")
        return 1
    try:
        result = run_backend_canary(
            workspace_root=Path(args.workspace_root),
            output_root=Path(args.output_root),
            repo_name=args.repo_name,
            change_id=args.change_id,
            live_mode=args.live,
            dry_live_mode=args.dry_live or not args.live,
            model_policy_file=args.model_policy,
        )
    except FeaturePreflightError as exc:
        print(json.dumps({"preflight": exc.result, "live_mode": args.live}, indent=2, sort_keys=True))
        return 1
    except FeatureIntakeError as exc:
        print(f"[CLI] backend canary failed: {exc}")
        return 1

    final_state = result.get("final_state", {})
    summary = {
        "state_path": result["state_path"],
        "repo_name": result["repo_name"],
        "dry_live": result.get("dry_live", False),
        "live_mode": result.get("live_mode", args.live),
        "provider_called": not result.get("dry_live", False),
        "preflight": result.get("preflight"),
        "trace_path": result.get("trace_path"),
        "diff_preview": result.get("diff_preview", []),
        "model_summary": _model_summary_from_state(final_state if isinstance(final_state, dict) else {}),
    }
    if result.get("failure_summary") is not None:
        summary["failure_summary"] = result["failure_summary"]
    if result.get("signoff_summary") is not None:
        summary["signoff_summary"] = result["signoff_summary"]
    if result.get("signoff_status") is not None:
        summary["signoff_status"] = result["signoff_status"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def lessons_command(args: argparse.Namespace) -> int:
    """Review lessons and pending proposals; merging stays a human git action."""
    import os

    raw_root = args.memory_dir or os.environ.get("HYRULE_MEMORY_DIR")
    if raw_root:
        root = Path(raw_root).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "memory"
    summary = list_memory(root)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"memory root: {summary['root']}")
    print(f"journal entries: {summary['journal_count']}")
    print("lessons:")
    for item in summary["lessons"] or [{"name": "(none)", "path": "", "chars": 0}]:
        print(f"  - {item['name']}: {item['chars']} chars {item['path']}")
    print("pending proposals (review, then merge into memory/lessons/ by hand):")
    if not summary["proposals"]:
        print("  - (none)")
    for item in summary["proposals"]:
        print(f"  - {item['name']}: {item['path']}")
        excerpt = str(item.get("excerpt", "")).strip()
        if excerpt:
            for line in excerpt.splitlines()[:6]:
                print(f"      {line}")
    return 0


def models_show_command(args: argparse.Namespace) -> int:
    snapshot = model_policy_snapshot(args.model_policy, risk_level=args.risk_level)
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return 0

    print(f"policy_path: {snapshot['policy_path'] or 'default fallback'}")
    print(f"risk_level: {snapshot['risk_level']}")
    print("roles:")
    for item in snapshot["roles"]:
        print(
            "  - "
            f"{item['role']}: "
            f"{item['provider']}/{item['model']} "
            f"tier={item['tier']} "
            f"reason={item['reason']}"
        )
    backend = snapshot.get("backend", {})
    if backend:
        print(
            "backend: "
            f"{backend.get('name', 'mock')} "
            f"tier={backend.get('tier', 'unknown')} "
            f"reason={backend.get('reason', 'unknown')}"
        )
    return 0


def models_validate_command(args: argparse.Namespace) -> int:
    result = validate_model_policy(args.model_policy, require_keys=args.require_keys)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"ok: {result['ok']}")
        print(f"policy_path: {result['policy_path'] or 'default fallback'}")
        for provider, provider_status in result["providers"].items():
            print(
                "provider: "
                f"{provider} "
                f"api_key_present={provider_status['api_key_present']} "
                f"api_key_env={','.join(provider_status['api_key_env'])}"
            )
        for warning in result["warnings"]:
            print(f"warning: {warning}")
        for error in result["errors"]:
            print(f"error: {error}")
    return 0 if result["ok"] else 1


def trace_command(args: argparse.Namespace) -> int:
    if args.trace_path:
        trace_path = Path(args.trace_path).expanduser().resolve()
    elif args.state_path:
        state = _read_state(Path(args.state_path).expanduser().resolve())
        raw_trace_path = state.get("loop_trace_path")
        if not isinstance(raw_trace_path, str):
            print("[CLI] state artifact has no loop_trace_path")
            return 1
        trace_path = Path(raw_trace_path).expanduser().resolve()
    elif args.change_id:
        state = _read_state(_state_path(Path(args.state_dir), args.change_id))
        raw_trace_path = state.get("loop_trace_path")
        if not isinstance(raw_trace_path, str):
            print("[CLI] state artifact has no loop_trace_path")
            return 1
        trace_path = Path(raw_trace_path).expanduser().resolve()
    else:
        print("[CLI] trace requires change_id, --state-path, or --trace-path")
        return 1

    trace = load_loop_trace(trace_path)
    if args.json:
        print(json.dumps(summarize_loop_trace(trace), indent=2, sort_keys=True))
    else:
        print(format_loop_trace_summary(trace), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Hyrule Engineering Loop skeleton")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the graph and persist final state")
    run_parser.add_argument("change_id")
    run_parser.add_argument("change_class")
    run_parser.add_argument("--policy-file")
    run_parser.add_argument("--model-policy")
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
    dry_run_parser.add_argument("--model-policy")
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

    state_approve_parser = subparsers.add_parser("state-approve", help="approve a state artifact by path")
    state_approve_parser.add_argument("--state-path", required=True)
    state_approve_parser.set_defaults(func=state_approve_command)

    state_cleanup_parser = subparsers.add_parser("state-cleanup", help="rollback promoted worktrees from a state artifact")
    state_cleanup_parser.add_argument("--state-path", required=True)
    state_cleanup_parser.set_defaults(func=state_cleanup_command)

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

    backend_canary_parser = subparsers.add_parser(
        "backend-canary",
        help="run a live or dry-live docs-only coding-agent backend canary",
    )
    backend_canary_parser.add_argument("--workspace-root", required=True)
    backend_canary_parser.add_argument("--repo-name", required=True)
    backend_canary_parser.add_argument("--output-root", required=True)
    backend_canary_parser.add_argument("--change-id", default="BACKEND_CANARY")
    backend_canary_parser.add_argument("--model-policy")
    backend_canary_parser.add_argument("--live", action="store_true")
    backend_canary_parser.add_argument("--dry-live", action="store_true")
    backend_canary_parser.set_defaults(func=backend_canary_command)

    writer_canary_parser = subparsers.add_parser(
        "writer-canary",
        help="deprecated alias for backend-canary",
    )
    writer_canary_parser.add_argument("--workspace-root", required=True)
    writer_canary_parser.add_argument("--repo-name", required=True)
    writer_canary_parser.add_argument("--output-root", required=True)
    writer_canary_parser.add_argument("--change-id", default="WRITER_CANARY")
    writer_canary_parser.add_argument("--model-policy")
    writer_canary_parser.add_argument("--live", action="store_true")
    writer_canary_parser.add_argument("--dry-live", action="store_true")
    writer_canary_parser.set_defaults(func=backend_canary_command)

    feature_parser = subparsers.add_parser(
        "feature",
        help="run the engineering loop from a human feature request file",
    )
    feature_parser.add_argument("change_id")
    feature_parser.add_argument("--request", required=True)
    feature_parser.add_argument("--repo", required=True)
    feature_parser.add_argument("--workspace-root", required=True)
    feature_parser.add_argument("--output-root", required=True)
    feature_parser.add_argument("--change-class", default="app_feature")
    feature_parser.add_argument("--allow", action="append", required=True)
    feature_parser.add_argument("--source", action="append", default=[])
    feature_parser.add_argument("--mock-mutation", action="append", default=[])
    feature_parser.add_argument("--plan-path")
    feature_parser.add_argument("--task-spec")
    feature_parser.add_argument("--memory-dir")
    feature_parser.add_argument("--no-scaffold-plan", action="store_true")
    feature_parser.add_argument("--base-ref", default="HEAD")
    feature_parser.add_argument("--model-policy")
    feature_parser.add_argument("--live", action="store_true")
    feature_parser.add_argument("--dry-live", action="store_true")
    feature_parser.add_argument("--gate-command", nargs=argparse.REMAINDER)
    feature_parser.set_defaults(func=feature_command)

    lessons_parser = subparsers.add_parser(
        "lessons",
        help="review memory lessons and pending lesson proposals",
    )
    lessons_parser.add_argument("--memory-dir")
    lessons_parser.add_argument("--json", action="store_true")
    lessons_parser.set_defaults(func=lessons_command)

    models_parser = subparsers.add_parser("models", help="inspect model routing policy")
    models_subparsers = models_parser.add_subparsers(dest="models_command", required=True)

    models_show_parser = models_subparsers.add_parser("show", help="show resolved role model routing")
    models_show_parser.add_argument("--model-policy")
    models_show_parser.add_argument(
        "--risk-level",
        choices=["low", "medium", "high", "critical"],
        default="low",
    )
    models_show_parser.add_argument("--json", action="store_true")
    models_show_parser.set_defaults(func=models_show_command)

    models_validate_parser = models_subparsers.add_parser("validate", help="validate model policy and provider env")
    models_validate_parser.add_argument("--model-policy")
    models_validate_parser.add_argument("--require-keys", action="store_true")
    models_validate_parser.add_argument("--json", action="store_true")
    models_validate_parser.set_defaults(func=models_validate_command)

    trace_parser = subparsers.add_parser("trace", help="print a compact loop trace summary")
    trace_parser.add_argument("change_id", nargs="?")
    trace_parser.add_argument("--state-path")
    trace_parser.add_argument("--trace-path")
    trace_parser.add_argument("--json", action="store_true")
    trace_parser.set_defaults(func=trace_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
