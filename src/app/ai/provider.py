"""LLM provider seam (T033).

``LLMProvider.generate`` takes a JSON schema and returns raw text the caller parses/validates.
Two v1 implementations:

* ``AnthropicProvider`` — real, uses the ``anthropic`` SDK; model id from settings.
* ``DeterministicMockProvider`` — fixture-driven, used by the default test suite (no network,
  no API key).

Structured validation, retries and the fail-safe path live in ``ai/structured.py`` (T034).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.core.config import get_settings


class LLMUnavailable(RuntimeError):
    """The provider could not produce a usable response."""


class LLMProvider(Protocol):
    name: str

    async def generate(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], timeout_s: float
    ) -> str: ...


class AnthropicProvider:
    name = "anthropic"

    async def generate(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], timeout_s: float
    ) -> str:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not configured")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("anthropic SDK not installed") from exc

        client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=timeout_s)
        instruction = (
            f"{system}\n\nRespond with a single JSON object that validates against this schema:\n"
            f"{json.dumps(json_schema)}"
        )
        try:
            msg = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=2048,
                system=instruction,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise LLMUnavailable(f"anthropic call failed: {exc!r}") from exc
        parts = [
            text for block in msg.content if (text := getattr(block, "text", None)) is not None
        ]
        if not parts:
            raise LLMUnavailable("anthropic returned no text content")
        return "".join(parts)


class DeterministicMockProvider:
    """Returns canned JSON keyed by ``(task, key)``; raises for unregistered keys."""

    name = "mock"

    def __init__(self) -> None:
        self._responses: dict[tuple[str, str], str] = {}
        self.calls: list[dict[str, Any]] = []

    def register(self, task: str, payload: Any, *, key: str = "default") -> None:
        self._responses[(task, key)] = payload if isinstance(payload, str) else json.dumps(payload)

    def register_failure(self, task: str, *, key: str = "default") -> None:
        self._responses[(task, key)] = "__FAIL__"

    async def generate(
        self, *, system: str, prompt: str, json_schema: dict[str, Any], timeout_s: float
    ) -> str:
        task = json_schema.get("title", "unknown")
        key = _fixture_key(prompt)
        self.calls.append({"task": task, "key": key, "prompt": prompt})
        raw = self._responses.get((task, key)) or self._responses.get((task, "default"))
        if raw is None:
            # Demo fallback: registered fixtures always win; only used when none was set
            # (e.g. the quickstart flow with LLM_PROVIDER=mock). See ai/mock_demo.py.
            if task == "MappingSuggestionSet":
                from app.ai.mock_demo import mapping_suggestions_json

                return mapping_suggestions_json(prompt)
            raise LLMUnavailable(f"no mock response for task={task!r} key={key!r}")
        if raw == "__FAIL__":
            raise LLMUnavailable(f"mock forced failure for task={task!r}")
        return raw


def _fixture_key(prompt: str) -> str:
    # A stable short key so fixtures can be registered per input without hashing noise.
    return prompt.strip().splitlines()[0][:64] if prompt.strip() else "default"


def get_provider() -> LLMProvider:
    return (
        AnthropicProvider()
        if get_settings().llm_provider == "anthropic"
        else DeterministicMockProvider()
    )
