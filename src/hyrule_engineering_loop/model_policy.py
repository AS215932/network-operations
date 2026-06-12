"""Model routing policy for role-node LLM calls."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from hyrule_engineering_loop.state import GraphState, RiskLevel, RoleName

Tier = Literal["cheap", "mid", "strong", "frontier"]
ModelPolicyNode = RoleName | Literal["implementation_writer"]
TIER_ORDER: tuple[Tier, ...] = ("cheap", "mid", "strong", "frontier")
DEFAULT_MODEL_POLICY_PATH = Path("model-policy.yml")
MODEL_POLICY_ROLES: tuple[RoleName, ...] = (
    "network_architect",
    "systems_engineer",
    "devops_netops",
    "security_auditor",
    "finops_integrity",
    "virtual_lab_chaos",
)
MODEL_POLICY_NODES: tuple[ModelPolicyNode, ...] = (*MODEL_POLICY_ROLES, "implementation_writer")


@dataclass(frozen=True)
class ModelSelection:
    """Resolved model selection for one role invocation."""

    provider: str
    model: str
    tier: Tier
    reason: str
    policy_path: str | None

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "reason": self.reason,
            "policy_path": self.policy_path or "",
        }


def _tier_index(tier: str) -> int:
    if tier not in TIER_ORDER:
        return 0
    return TIER_ORDER.index(tier)


def _normalize_tier(value: Any, fallback: Tier = "cheap") -> Tier:
    if isinstance(value, str) and value in TIER_ORDER:
        return value
    return fallback


def _load_policy(path: str | Path | None = None) -> tuple[dict[str, Any], str | None]:
    configured = path or os.environ.get("HYRULE_MODEL_POLICY_FILE")
    policy_path = Path(configured) if configured is not None else DEFAULT_MODEL_POLICY_PATH
    if not policy_path.exists():
        return (
            {
                "defaults": {
                    "provider": "openrouter",
                    "model": "minimax/minimax-m3",
                    "tier": "cheap",
                },
                "roles": {},
                "risk_overrides": {},
                "retry_escalation": {},
                "tier_fallbacks": {},
            },
            None,
        )

    loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"model policy must be a mapping: {policy_path}")
    return loaded, str(policy_path)


def load_model_policy(path: str | Path | None = None) -> tuple[dict[str, Any], str | None]:
    """Load the configured model policy for operator-facing commands."""
    return _load_policy(path)


def _mapping(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _tier_fallback(policy: dict[str, Any], tier: Tier, defaults: dict[str, Any]) -> dict[str, Any]:
    fallbacks = _mapping(policy.get("tier_fallbacks"))
    return _mapping(fallbacks.get(tier)) or defaults


def _promote_to_min_tier(
    *,
    selection: dict[str, Any],
    min_tier: Tier,
    policy: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    current_tier = _normalize_tier(selection.get("tier"), _normalize_tier(defaults.get("tier")))
    if _tier_index(current_tier) >= _tier_index(min_tier):
        return selection
    fallback = _tier_fallback(policy, min_tier, defaults)
    return {
        **selection,
        "provider": fallback.get("provider", selection.get("provider")),
        "model": fallback.get("model", selection.get("model")),
        "tier": min_tier,
    }


def _retry_failure_count(node: ModelPolicyNode, state: GraphState) -> int:
    counters = state["retry_counters"]
    keys = [node, f"llm_{node}"]
    return max((counters.get(key, 0) for key in keys), default=0)


def _sample_state(
    *,
    risk_level: RiskLevel,
    policy_path: str | None,
    retry_counters: dict[str, int] | None = None,
) -> GraphState:
    state: GraphState = {
        "change_id": "MODEL_POLICY_PREVIEW",
        "change_class": "mixed",
        "risk_level": risk_level,
        "customer_impact": "none",
        "source_of_truth_files": [],
        "proposed_mutations": {},
        "mcp_schema_breaking": False,
        "emulated_lab_verified": "not_applicable",
        "validation_errors": [],
        "role_approvals": {role: False for role in MODEL_POLICY_ROLES},
        "retry_counters": retry_counters or {},
        "rollback_plan": "",
        "noc_handoff_metadata": {},
        "requires_human_signoff": False,
    }
    if policy_path is not None:
        state["model_policy_file"] = policy_path
    return state


def select_model_for_node(node: ModelPolicyNode, state: GraphState) -> ModelSelection:
    """Resolve the configured model for a routed node and current risk/retry state."""
    policy, policy_path = _load_policy(state.get("model_policy_file"))
    defaults = _mapping(policy.get("defaults"))
    roles = _mapping(policy.get("roles"))
    selected = {
        **defaults,
        **_mapping(roles.get(node)),
    }
    reason = "role_default"

    risk_overrides = _mapping(policy.get("risk_overrides"))
    risk_config = _mapping(risk_overrides.get(state["risk_level"]))
    min_tier = risk_config.get("min_tier")
    if isinstance(min_tier, str) and min_tier in TIER_ORDER:
        selected = _promote_to_min_tier(
            selection=selected,
            min_tier=min_tier,
            policy=policy,
            defaults=defaults,
        )
        reason = f"risk_{state['risk_level']}"

    retry_config = _mapping(policy.get("retry_escalation"))
    after_failures = retry_config.get("after_failures", 0)
    if (
        isinstance(after_failures, int)
        and after_failures > 0
        and _retry_failure_count(node, state) >= after_failures
    ):
        max_tier = _normalize_tier(retry_config.get("max_tier"), "frontier")
        selected = _promote_to_min_tier(
            selection=selected,
            min_tier=max_tier,
            policy=policy,
            defaults=defaults,
        )
        reason = f"retry_escalation_after_{after_failures}"

    return ModelSelection(
        provider=str(selected.get("provider", "openrouter")),
        model=str(selected.get("model", "minimax/minimax-m3")),
        tier=_normalize_tier(selected.get("tier"), _normalize_tier(defaults.get("tier"))),
        reason=reason,
        policy_path=policy_path,
    )


def select_model_for_role(role: RoleName, state: GraphState) -> ModelSelection:
    """Resolve the configured model for a senior role and current risk/retry state."""
    return select_model_for_node(role, state)


KNOWN_BACKEND_NAMES: tuple[str, ...] = ("mock", "pi", "claude-code")


@dataclass(frozen=True)
class BackendSelection:
    """Resolved coding-agent backend for one implementation delegation."""

    name: str
    tier: Tier
    reason: str
    command: list[str] | None
    policy_path: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "reason": self.reason,
            "command": list(self.command) if self.command else None,
            "policy_path": self.policy_path or "",
        }


def select_backend_for_state(state: GraphState) -> BackendSelection:
    """Resolve the executor through the same tier/risk/retry escalation as models.

    The implementation-writer model selection already encodes risk minimum
    tiers and retry escalation; the ``backends:`` policy section maps the
    resulting tier onto an executor, so high-risk or repeatedly-failing work
    can move to a stronger harness, not just a stronger model.
    """
    policy, policy_path = _load_policy(state.get("model_policy_file"))
    backends = _mapping(policy.get("backends"))
    writer = select_model_for_node("implementation_writer", state)

    default_name = str(backends.get("default", "mock"))
    tiers = _mapping(backends.get("tiers"))
    raw_tier_name = tiers.get(writer.tier)
    if isinstance(raw_tier_name, str) and raw_tier_name:
        name = raw_tier_name
        reason = f"backend_tier_{writer.tier}({writer.reason})"
    else:
        name = default_name
        reason = f"backend_default({writer.reason})"

    definitions = _mapping(backends.get("definitions"))
    definition = _mapping(definitions.get(name))
    raw_command = definition.get("command")
    command = (
        [str(part) for part in raw_command]
        if isinstance(raw_command, list) and all(isinstance(part, str) for part in raw_command)
        else None
    )
    return BackendSelection(
        name=name,
        tier=writer.tier,
        reason=reason,
        command=command,
        policy_path=policy_path,
    )


def validate_backend_policy(policy: dict[str, Any]) -> list[str]:
    """Return structural errors for the ``backends:`` policy section."""
    errors: list[str] = []
    backends = policy.get("backends")
    if backends is None:
        return errors
    if not isinstance(backends, dict):
        return ["backends must be a mapping"]
    default_name = backends.get("default", "mock")
    if not isinstance(default_name, str) or default_name not in KNOWN_BACKEND_NAMES:
        errors.append(f"unknown default backend: {default_name}")
    tiers = backends.get("tiers", {})
    if not isinstance(tiers, dict):
        errors.append("backends.tiers must be a mapping")
    else:
        for tier, name in tiers.items():
            if tier not in TIER_ORDER:
                errors.append(f"unknown tier in backends.tiers: {tier}")
            if not isinstance(name, str) or name not in KNOWN_BACKEND_NAMES:
                errors.append(f"unknown backend in backends.tiers[{tier}]: {name}")
    definitions = backends.get("definitions", {})
    if not isinstance(definitions, dict):
        errors.append("backends.definitions must be a mapping")
    return errors


def provider_env_names(provider: str) -> dict[str, list[str]]:
    """Return relevant environment variable names for a provider."""
    normalized = provider.lower()
    if normalized == "openrouter":
        return {
            "api_key": ["HYRULE_LLM_API_KEY", "OPENROUTER_API_KEY"],
            "base_url": ["HYRULE_LLM_BASE_URL"],
        }
    if normalized == "openai":
        return {
            "api_key": ["HYRULE_LLM_API_KEY", "OPENAI_API_KEY"],
            "base_url": ["HYRULE_LLM_BASE_URL", "OPENAI_BASE_URL"],
        }
    if normalized == "anthropic":
        return {
            "api_key": ["HYRULE_LLM_API_KEY", "ANTHROPIC_API_KEY"],
            "base_url": ["HYRULE_LLM_BASE_URL"],
        }
    return {
        "api_key": ["HYRULE_LLM_API_KEY"],
        "base_url": ["HYRULE_LLM_BASE_URL"],
    }


def model_policy_snapshot(
    path: str | Path | None = None,
    *,
    risk_level: RiskLevel = "low",
) -> dict[str, Any]:
    """Return a compact, operator-readable view of resolved role models."""
    policy, policy_path = _load_policy(path)
    state = _sample_state(risk_level=risk_level, policy_path=policy_path or (str(path) if path else None))
    selections = [
        select_model_for_node(node, state).as_dict() | {"role": node}
        for node in MODEL_POLICY_NODES
    ]
    return {
        "policy_path": policy_path,
        "risk_level": risk_level,
        "defaults": _mapping(policy.get("defaults")),
        "roles": selections,
        "backend": select_backend_for_state(state).as_dict(),
        "risk_overrides": _mapping(policy.get("risk_overrides")),
        "retry_escalation": _mapping(policy.get("retry_escalation")),
        "tier_fallbacks": _mapping(policy.get("tier_fallbacks")),
    }


def validate_model_policy(
    path: str | Path | None = None,
    *,
    require_keys: bool = False,
) -> dict[str, Any]:
    """Validate model policy structure and configured provider credentials."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        policy, policy_path = _load_policy(path)
    except Exception as exc:
        return {
            "ok": False,
            "policy_path": str(path) if path is not None else None,
            "errors": [str(exc)],
            "warnings": [],
            "providers": {},
        }

    roles = _mapping(policy.get("roles"))
    unknown_roles = sorted(set(roles) - set(MODEL_POLICY_NODES))
    for role in unknown_roles:
        errors.append(f"unknown role in model policy: {role}")

    for section_name in ("defaults", "roles", "risk_overrides", "retry_escalation", "tier_fallbacks"):
        if section_name in policy and not isinstance(policy[section_name], dict):
            errors.append(f"{section_name} must be a mapping")

    errors.extend(validate_backend_policy(policy))

    state = _sample_state(risk_level="low", policy_path=policy_path or (str(path) if path else None))
    selections = [select_model_for_node(node, state) for node in MODEL_POLICY_NODES]
    providers = sorted({selection.provider.lower() for selection in selections})
    provider_status: dict[str, dict[str, Any]] = {}
    for provider in providers:
        env_names = provider_env_names(provider)
        present_api_keys = [name for name in env_names["api_key"] if os.environ.get(name)]
        missing = not present_api_keys
        provider_status[provider] = {
            "api_key_env": env_names["api_key"],
            "base_url_env": env_names["base_url"],
            "api_key_present": not missing,
        }
        if missing:
            message = f"missing API key for provider {provider}: one of {', '.join(env_names['api_key'])}"
            if require_keys:
                errors.append(message)
            else:
                warnings.append(message)

    return {
        "ok": not errors,
        "policy_path": policy_path,
        "errors": errors,
        "warnings": warnings,
        "providers": provider_status,
        "roles": [selection.as_dict() for selection in selections],
    }


def provider_env(selection: ModelSelection) -> tuple[str | None, str | None]:
    """Return ``api_key`` and ``base_url`` defaults for a provider selection."""
    provider = selection.provider.lower()
    if provider == "openrouter":
        return (
            os.environ.get("HYRULE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("HYRULE_LLM_BASE_URL") or "https://openrouter.ai/api/v1",
        )
    if provider == "openai":
        return (
            os.environ.get("HYRULE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            os.environ.get("HYRULE_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        )
    if provider == "anthropic":
        return (
            os.environ.get("HYRULE_LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("HYRULE_LLM_BASE_URL") or "https://api.anthropic.com/v1",
        )
    return (
        os.environ.get("HYRULE_LLM_API_KEY"),
        os.environ.get("HYRULE_LLM_BASE_URL"),
    )
