"""FastAPI dependency for the LLM provider (T033 support).

Endpoints that trigger an AI call depend on ``get_llm_provider`` so tests can override it
with a ``DeterministicMockProvider``. Job handlers call ``get_provider()`` directly.
"""

from __future__ import annotations

from app.ai.provider import LLMProvider, get_provider


def get_llm_provider() -> LLMProvider:
    return get_provider()
