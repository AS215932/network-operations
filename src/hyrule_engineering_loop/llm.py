"""Structured LLM invocation layer for role review hydration.

The default client is deterministic so tests and local scaffolding need no API
keys. Production model adapters should implement ``StructuredLLMClient`` and
return the same Pydantic schema.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

from hyrule_engineering_loop.model_policy import ModelPolicyNode, ModelSelection, provider_env
from hyrule_engineering_loop.state import GraphState

ModelT = TypeVar("ModelT", bound=BaseModel)


class FileMutation(BaseModel):
    """A proposed file content mutation relative to a workspace root."""

    path: str = Field(min_length=1)
    content: str
    operation: Literal["create", "replace"] = "create"


class RoleReviewOutput(BaseModel):
    """Structured role review output consumed by LangGraph nodes."""

    approved: bool
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    proposed_mutations: list[FileMutation] = Field(default_factory=list)
    notes: str = ""


class StructuredLLMClient(Protocol):
    """Provider interface for structured role review invocation."""

    def invoke_role_review(
        self,
        *,
        role: ModelPolicyNode,
        system_prompt: str,
        source_context: dict[str, str],
        state: GraphState,
    ) -> RoleReviewOutput:
        """Return a structured role review output."""


class LLMInvocationError(RuntimeError):
    """Raised when live inference fails before producing structured output."""


class DeterministicStructuredLLMClient:
    """Safe default client for local graph execution without live inference."""

    def invoke_role_review(
        self,
        *,
        role: ModelPolicyNode,
        system_prompt: str,
        source_context: dict[str, str],
        state: GraphState,
    ) -> RoleReviewOutput:
        mock = state.get("llm_mock_responses", {}).get(role)
        if mock is not None:
            return RoleReviewOutput.model_validate(mock)

        return RoleReviewOutput(
            approved=True,
            notes=(
                f"Deterministic approval for {role}; prompt chars={len(system_prompt)}, "
                f"source files={len(source_context)}."
            ),
        )


class HTTPStructuredLLMClient:
    """Small OpenAI-compatible structured-output client.

    This intentionally uses the standard library so the loop can instantiate
    live inference from environment variables without forcing a specific SDK
    into the runtime skeleton.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider.lower()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @classmethod
    def from_env(cls, selection: ModelSelection | None = None) -> "HTTPStructuredLLMClient":
        api_key, selected_base_url = provider_env(selection) if selection else (None, None)
        api_key = api_key or os.environ.get("HYRULE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMInvocationError(
                "HYRULE_MOCK_LLM=0 requires HYRULE_LLM_API_KEY or the selected provider API key"
            )

        return cls(
            api_key=api_key,
            base_url=selected_base_url
            or os.environ.get("HYRULE_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1",
            model=os.environ.get("HYRULE_LLM_MODEL") or (selection.model if selection else "gpt-4.1-mini"),
            provider=selection.provider if selection else os.environ.get("HYRULE_LLM_PROVIDER", "openai"),
            timeout_seconds=float(os.environ.get("HYRULE_LLM_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.environ.get("HYRULE_LLM_MAX_RETRIES", "2")),
        )

    def invoke_role_review(
        self,
        *,
        role: ModelPolicyNode,
        system_prompt: str,
        source_context: dict[str, str],
        state: GraphState,
    ) -> RoleReviewOutput:
        return self.invoke_structured(
            node=role,
            system_prompt=system_prompt,
            payload={
                "role": role,
                "state": state,
                "source_context": source_context,
            },
            output_model=RoleReviewOutput,
        )

    def invoke_structured(
        self,
        *,
        node: str,
        system_prompt: str,
        payload: dict[str, Any],
        output_model: type[ModelT],
    ) -> ModelT:
        """Invoke the provider and validate an arbitrary structured schema."""
        user_payload = json.dumps(
            {
                **payload,
                "node": node,
                "output_schema": output_model.model_json_schema(),
            },
            sort_keys=True,
            default=str,
        )
        if self.provider == "anthropic":
            http_payload = {
                "model": self.model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Return only JSON matching the output_schema. "
                            f"No markdown fences.\n\n{user_payload}"
                        ),
                    }
                ],
            }
        else:
            http_payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_payload,
                    },
                ],
                "response_format": {"type": "json_object"},
            }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return output_model.model_validate_json(self._post(http_payload))
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0, 0.25 * (2**attempt)))

        raise LLMInvocationError(str(last_error) if last_error else "LLM invocation failed")

    def _post(self, payload: dict[str, Any]) -> str:
        if self.provider == "anthropic":
            url = f"{self.base_url}/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
                "Content-Type": "application/json",
            }
        else:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMInvocationError(f"HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise LLMInvocationError(str(exc.reason)) from exc

        decoded = json.loads(raw)
        if self.provider == "anthropic":
            return str(decoded["content"][0]["text"])
        return str(decoded["choices"][0]["message"]["content"])


def mock_llm_enabled() -> bool:
    return os.environ.get("HYRULE_MOCK_LLM", "1").lower() not in {"0", "false", "no"}


def default_llm_client(selection: ModelSelection | None = None) -> StructuredLLMClient:
    """Return the configured LLM client, defaulting to deterministic mock mode."""
    if mock_llm_enabled():
        return DeterministicStructuredLLMClient()
    return HTTPStructuredLLMClient.from_env(selection)


def role_api_error(role: ModelPolicyNode, message: str) -> RoleReviewOutput:
    """Convert provider failures into graph-consumable validation errors."""
    return RoleReviewOutput(
        approved=False,
        validation_errors=[
            {
                "node": role,
                "domain": "llm",
                "message": message,
            }
        ],
        notes="LLM invocation failed; routed as validation error.",
    )


def invoke_role_review(
    *,
    role: ModelPolicyNode,
    system_prompt: str,
    source_context: dict[str, str],
    state: GraphState,
    model_selection: ModelSelection | None = None,
    client: StructuredLLMClient | None = None,
) -> RoleReviewOutput:
    """Invoke a role review and validate structured output."""
    mock = state.get("llm_mock_responses", {}).get(role)
    if mock is not None:
        return RoleReviewOutput.model_validate(mock)

    try:
        active_client = client or default_llm_client(model_selection)
        return active_client.invoke_role_review(
            role=role,
            system_prompt=system_prompt,
            source_context=source_context,
            state=state,
        )
    except Exception as exc:
        return role_api_error(role, str(exc))
