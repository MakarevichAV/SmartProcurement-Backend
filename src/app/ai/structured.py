"""Typed LLM output with a fail-safe path (T034; FR-016a).

``generate_structured`` calls the provider, parses the JSON, validates it against the
Pydantic ``output_schema``, and retries with exponential backoff. On persistent failure
(unavailable / timeout / invalid) it:

1. opens an ``ai_unavailable`` observability gap for the affected capability,
2. writes an ``ai_unavailable`` audit record,
3. raises ``LLMUnavailable`` so the caller marks the risk/recommendation "human-required".

Secrets are never placed in ``system`` / ``prompt`` / ``context`` (FR-068) — callers pass
plain domain data only.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider, LLMUnavailable, get_provider
from app.audit.recorder import AuditRecorder
from app.observation.observability import ObservabilityService

TModel = TypeVar("TModel", bound=BaseModel)

_DEFAULT_ATTEMPTS = 3
_DEFAULT_TIMEOUT_S = 30.0
_BACKOFF_BASE_S = 0.5


async def generate_structured(
    output_schema: type[TModel],
    *,
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    capability_key: str,
    system: str,
    prompt: str,
    provider: LLMProvider | None = None,
    attempts: int = _DEFAULT_ATTEMPTS,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    correlation_id: uuid.UUID | None = None,
) -> TModel:
    provider = provider or get_provider()
    schema = output_schema.model_json_schema()
    last_error = "unknown"

    for attempt in range(attempts):
        try:
            raw = await asyncio.wait_for(
                provider.generate(
                    system=system, prompt=prompt, json_schema=schema, timeout_s=timeout_s
                ),
                timeout=timeout_s,
            )
            data = json.loads(raw)
            return output_schema.model_validate(data)
        except (LLMUnavailable, TimeoutError) as exc:
            last_error = f"provider error: {exc!r}"
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = f"invalid structured output: {exc!r}"
        if attempt < attempts - 1:
            await asyncio.sleep(_BACKOFF_BASE_S * (2**attempt))

    # --- fail-safe path ---
    await ObservabilityService(session).open(
        enterprise_id=enterprise_id,
        scope="capability",
        scope_ref=capability_key,
        reason="ai_unavailable",
    )
    await AuditRecorder(session).record(
        enterprise_id=enterprise_id,
        event_type="ai_unavailable",
        correlation_id=correlation_id,
        capability_key=capability_key,
        action=f"generate_structured:{output_schema.__name__}",
        outcome={"error": last_error, "attempts": attempts},
    )
    raise LLMUnavailable(
        f"AI unavailable for {capability_key} ({output_schema.__name__}): {last_error}"
    )
