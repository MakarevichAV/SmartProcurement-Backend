"""Deterministic LLM double for the default test suite (T009).

The real ``LLMProvider`` protocol and ``DeterministicMockProvider`` implementation live in
``app/ai/provider.py`` (Phase 2, T033). This module is the test-side registry: keyed canned
responses so LLM-dependent tests never touch a network or an API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeterministicMockProvider:
    """Returns pre-registered structured payloads by ``(task, key)``.

    ``generate_structured`` mirrors the Phase 2 provider contract closely enough for
    Phase 1 wiring checks; it is fleshed out alongside T033.
    """

    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def register(self, task: str, key: str, payload: Any) -> None:
        self.responses[(task, key)] = payload

    async def generate_structured(self, *, task: str, key: str = "default", **kwargs: Any) -> Any:
        self.calls.append({"task": task, "key": key, **kwargs})
        try:
            return self.responses[(task, key)]
        except KeyError as exc:  # pragma: no cover - guard for un-registered fixtures
            raise AssertionError(
                f"No deterministic response registered for task={task!r} key={key!r}"
            ) from exc
