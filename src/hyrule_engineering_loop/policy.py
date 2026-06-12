"""Policy guards for bounded engineering-loop mutation and publication."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

import yaml

from hyrule_engineering_loop.state import GraphState
from hyrule_engineering_loop.workspace import _safe_relative_path

DEFAULT_POLICY_PATH = Path("engineering-loop-policy.yml")


class PolicyViolation(RuntimeError):
    """Raised when graph state violates engineering-loop policy."""


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the policy file, returning defaults when absent."""
    configured_path = path or os.environ.get("HYRULE_POLICY_FILE")
    policy_path = Path(configured_path) if configured_path is not None else DEFAULT_POLICY_PATH
    if not policy_path.exists():
        return {"version": 1, "defaults": {}, "repos": {}}
    loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise PolicyViolation(f"policy file must contain a mapping: {policy_path}")
    return loaded


def _defaults(policy: dict[str, Any]) -> dict[str, Any]:
    defaults = policy.get("defaults", {})
    if not isinstance(defaults, dict):
        raise PolicyViolation("policy defaults must be a mapping")
    return defaults


def _repo_policy(policy: dict[str, Any], repo: str) -> dict[str, Any]:
    repos = policy.get("repos", {})
    if not isinstance(repos, dict):
        raise PolicyViolation("policy repos must be a mapping")
    specific = repos.get(repo, {})
    if not isinstance(specific, dict):
        raise PolicyViolation(f"policy for repo {repo} must be a mapping")
    return specific


def _list_value(config: dict[str, Any], key: str, fallback: list[str] | None = None) -> list[str]:
    raw = config.get(key, fallback or [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise PolicyViolation(f"policy key {key} must be a list of strings")
    return list(raw)


def _int_value(config: dict[str, Any], key: str, fallback: int) -> int:
    raw = config.get(key, fallback)
    if not isinstance(raw, int):
        raise PolicyViolation(f"policy key {key} must be an integer")
    return raw


def _iter_mutations(state: GraphState) -> list[tuple[str | None, Path, str]]:
    mutations: list[tuple[str | None, Path, str]] = []
    for raw_key, content in state["proposed_mutations"].items():
        repo: str | None = None
        raw_path = raw_key
        if ":" in raw_key:
            repo, raw_path = raw_key.split(":", 1)
        mutations.append((repo, _safe_relative_path(raw_path), content))
    return mutations


def _glob_match(path: Path, patterns: list[str]) -> str | None:
    path_text = path.as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(path_text, pattern):
            return pattern
    return None


def _validate_mutations(state: GraphState, policy: dict[str, Any]) -> list[str]:
    defaults = _defaults(policy)
    mutations = _iter_mutations(state)
    violations: list[str] = []

    max_changed_files = _int_value(defaults, "max_changed_files", 20)
    if len(mutations) > max_changed_files:
        violations.append(f"changed file count exceeds policy limit: {len(mutations)} > {max_changed_files}")

    for repo, path, content in mutations:
        repo_config = _repo_policy(policy, repo) if repo else {}
        denied_globs = _list_value(
            repo_config,
            "denied_path_globs",
            _list_value(defaults, "denied_path_globs"),
        )
        denied_match = _glob_match(path, denied_globs)
        if denied_match:
            violations.append(f"mutation path {path.as_posix()} denied by pattern {denied_match}")

        max_file_bytes = _int_value(repo_config, "max_file_bytes", _int_value(defaults, "max_file_bytes", 1048576))
        size = len(content.encode("utf-8"))
        if size > max_file_bytes:
            violations.append(f"mutation file {path.as_posix()} exceeds size limit: {size} > {max_file_bytes}")

        for pattern in _list_value(
            repo_config,
            "denied_content_patterns",
            _list_value(defaults, "denied_content_patterns"),
        ):
            if re.search(pattern, content):
                violations.append(f"mutation content for {path.as_posix()} denied by pattern {pattern}")

        if repo and state.get("promotion_enabled", False):
            allowed = state.get("promotion_allowed_paths", {}).get(repo, [])
            if not allowed:
                violations.append(f"promotion repo {repo} has no allowed path prefixes")

    return violations


def _validate_gate_commands(state: GraphState, policy: dict[str, Any]) -> list[str]:
    defaults = _defaults(policy)
    allowed = _list_value(defaults, "allowed_gate_commands", [])
    if not allowed:
        return []

    violations: list[str] = []
    for command in state.get("gate_commands", []):
        if not command:
            violations.append("gate command cannot be empty")
            continue
        name = Path(command[0]).name
        if name not in allowed:
            violations.append(f"gate command not allowlisted: {name}")
    return violations


def validate_gate_commands_for_state(state: GraphState, policy: dict[str, Any] | None = None) -> list[str]:
    """Return policy violations for gate commands before executing them."""
    active_policy = policy or load_policy(state.get("policy_file"))
    return _validate_gate_commands(state, active_policy)


def _validate_promotion(state: GraphState, policy: dict[str, Any]) -> list[str]:
    defaults = _defaults(policy)
    violations: list[str] = []

    branch_prefix = state.get("promotion_branch_prefix", "hyrule-loop")
    for protected in _list_value(defaults, "protected_branch_prefixes", []):
        if branch_prefix == protected or branch_prefix.startswith(f"{protected}/"):
            violations.append(f"promotion branch prefix targets protected branch namespace: {branch_prefix}")

    repos = state.get("promotion_repositories", {})
    for repo, repo_path in repos.items():
        repo_config = _repo_policy(policy, repo)
        allowed_roots = _list_value(repo_config, "allowed_repo_roots", [])
        if allowed_roots:
            resolved = Path(repo_path).expanduser().resolve()
            if not any(resolved == Path(root).expanduser().resolve() for root in allowed_roots):
                violations.append(f"promotion repo root not allowlisted for {repo}: {resolved}")

    return violations


def validate_pr_remote(state: dict[str, Any], *, remote: str, policy: dict[str, Any] | None = None) -> None:
    """Validate a PR publication remote against policy."""
    active_policy = policy or load_policy()
    allowed = _list_value(_defaults(active_policy), "allowed_pr_remotes", ["origin"])
    if remote not in allowed:
        raise PolicyViolation(f"PR remote not allowlisted: {remote}")


def _validate_handoff_path(state: GraphState, policy: dict[str, Any]) -> list[str]:
    raw_dir = state.get("handoff_output_dir") or os.environ.get("HYRULE_HANDOFF_DIR")
    if not raw_dir:
        return []

    defaults = _defaults(policy)
    allowed_dirs = _list_value(defaults, "allowed_handoff_dirs", [])
    if not allowed_dirs:
        return []

    target = Path(raw_dir).expanduser().resolve()
    for raw_allowed in allowed_dirs:
        allowed = Path(raw_allowed).expanduser().resolve()
        if target == allowed or target.is_relative_to(allowed):
            return []
    return [f"handoff output directory is not allowlisted: {target}"]


def validate_graph_state(state: GraphState, policy: dict[str, Any] | None = None) -> list[str]:
    """Return policy violations for graph state."""
    active_policy = policy or load_policy(state.get("policy_file"))
    violations: list[str] = []
    violations.extend(_validate_mutations(state, active_policy))
    violations.extend(_validate_gate_commands(state, active_policy))
    violations.extend(_validate_promotion(state, active_policy))
    violations.extend(_validate_handoff_path(state, active_policy))
    return violations
