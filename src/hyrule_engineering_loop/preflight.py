"""Preflight checks for live and dry-live feature writer runs."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from hyrule_engineering_loop.backend import (
    SubprocessBackend,
    assemble_backend_prompt,
    constraints_from_state,
    create_backend,
    env_hygiene_violations,
    scrubbed_backend_env,
    task_spec_from_state,
)
from hyrule_engineering_loop.model_policy import (
    ModelPolicyNode,
    ModelSelection,
    provider_env,
    provider_env_names,
    select_backend_for_state,
    select_model_for_node,
    validate_model_policy,
)
from hyrule_engineering_loop.nodes import required_roles_for_state
from hyrule_engineering_loop.prompts import load_role_prompts
from hyrule_engineering_loop.repo_adapter import build_repo_context_bundle, verify_repository
from hyrule_engineering_loop.state import GraphState


def _check(name: str, ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "message": message, **extra}


def _check_writable(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".preflight-", dir=path, delete=True):
            pass
    except Exception as exc:
        return _check("output_writable", False, str(exc), path=str(path))
    return _check("output_writable", True, "writable", path=str(path))


def _selected_models(state: GraphState) -> list[tuple[ModelPolicyNode, ModelSelection]]:
    nodes: list[ModelPolicyNode] = [*required_roles_for_state(state), "implementation_writer"]
    return [(node, select_model_for_node(node, state)) for node in nodes]


def preflight_feature_state(
    state: GraphState,
    *,
    output_root: Path,
    live: bool,
) -> dict[str, Any]:
    """Run no-network preflight checks and build the dry-live preview bundle."""
    checks: list[dict[str, Any]] = []

    policy = validate_model_policy(state.get("model_policy_file"), require_keys=False)
    checks.append(_check("model_policy", bool(policy["ok"]), "valid" if policy["ok"] else "invalid"))

    allowed = state.get("promotion_allowed_paths", {})
    allowed_ok = all(bool(paths) for paths in allowed.values()) and bool(allowed)
    checks.append(_check("allowed_paths", allowed_ok, "configured" if allowed_ok else "missing allowed paths"))

    for repo_name, raw_repo_path in state.get("promotion_repositories", {}).items():
        try:
            verified = verify_repository(raw_repo_path, base_ref=state.get("promotion_base_ref", "HEAD"))
        except Exception as exc:
            checks.append(_check("repo_clean", False, str(exc), repo=repo_name, path=str(raw_repo_path)))
        else:
            checks.append(
                _check(
                    "repo_clean",
                    True,
                    "clean attached worktree",
                    repo=repo_name,
                    path=str(verified.path),
                    branch=verified.branch,
                )
            )

    output_root = output_root.expanduser().resolve()
    checks.append(_check_writable(output_root / "handoff"))
    checks.append(_check_writable(output_root / "state"))

    selections = _selected_models(state)
    provider_checks: list[dict[str, Any]] = []
    for node, selection in selections:
        api_key, base_url = provider_env(selection)
        env_names = provider_env_names(selection.provider)
        provider_checks.append(
            {
                "role": node,
                "model_selection": selection.as_dict(),
                "api_key_present": bool(api_key),
                "api_key_env": env_names["api_key"],
                "base_url": base_url,
            }
        )
        if live:
            checks.append(
                _check(
                    "provider_key",
                    bool(api_key),
                    "configured" if api_key else "missing provider API key",
                    provider=selection.provider,
                    model=selection.model,
                    env=env_names["api_key"],
                )
            )

    backend_selection = select_backend_for_state(state)
    backend_env = scrubbed_backend_env()
    leaked = env_hygiene_violations(backend_env)
    checks.append(
        _check(
            "backend_env_hygiene",
            not leaked,
            "scrubbed" if not leaked else f"credential-like vars leaked: {', '.join(leaked)}",
        )
    )
    backend_spec = task_spec_from_state(state)
    backend_constraints = constraints_from_state(state)
    backend_prompt = assemble_backend_prompt(backend_spec, backend_constraints)
    backend_instance = create_backend(backend_selection.name, command=backend_selection.command)
    command_preview: list[str] | None = None
    if isinstance(backend_instance, SubprocessBackend):
        command_preview = backend_instance.build_command(
            prompt="<assembled prompt>", constraints=backend_constraints
        )

    prompt = load_role_prompts()["implementation_writer"]
    repo_context = build_repo_context_bundle(state)
    ok = all(bool(check["ok"]) for check in checks)
    return {
        "ok": ok,
        "live": live,
        "provider_called": False,
        "checks": checks,
        "model_policy": policy,
        "model_selections": [
            selection.as_dict() | {"role": node}
            for node, selection in selections
        ],
        "provider_checks": provider_checks,
        "backend": {
            "selection": backend_selection.as_dict(),
            "prompt_chars": len(backend_prompt),
            "command_preview": command_preview,
            "env_allowlisted_count": len(backend_env),
            "max_iterations": backend_constraints.max_iterations,
            "max_wall_clock_seconds": backend_constraints.max_wall_clock_seconds,
            "lessons_injected": {
                repo: len(text) for repo, text in backend_spec.lessons.items()
            },
            "journal_tail_chars": len(backend_spec.journal_tail),
        },
        "implementation_writer": {
            "prompt_chars": len(prompt),
            "repo_context": repo_context,
            "output_schema": "RoleReviewOutput with create/replace FileMutation entries",
        },
        "next_operator_command": "fix preflight failures" if not ok else "rerun with --live to call the provider",
    }
