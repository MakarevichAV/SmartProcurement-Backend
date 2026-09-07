"""AI structured-output wrapper tests (T035).

Valid output parses & validates; invalid / unavailable output triggers the fail-safe path:
an ``ai_unavailable`` observability gap + audit record, then ``LLMUnavailable`` is raised
(FR-016a).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.ai.provider import DeterministicMockProvider, LLMUnavailable
from app.ai.structured import generate_structured
from app.audit.models import AuditRecord
from app.observation.observability import ObservabilityService

pytestmark = pytest.mark.asyncio


class DemoOut(BaseModel):
    answer: int
    note: str


async def _run(db_session, seeded, provider) -> DemoOut:
    return await generate_structured(
        DemoOut,
        session=db_session,
        enterprise_id=seeded.id,
        capability_key="proc.risk.diagnose",
        system="you are a test",
        prompt="give me the answer",
        provider=provider,
        attempts=2,
    )


async def test_valid_output_parses(db_session, seeded) -> None:
    mock = DeterministicMockProvider()
    mock.register("DemoOut", {"answer": 42, "note": "ok"})
    out = await _run(db_session, seeded, mock)
    assert out.answer == 42 and out.note == "ok"


async def test_schema_invalid_output_triggers_failsafe(db_session, seeded) -> None:
    mock = DeterministicMockProvider()
    mock.register("DemoOut", {"answer": "not-an-int"})
    with pytest.raises(LLMUnavailable):
        await _run(db_session, seeded, mock)
    await _assert_failsafe(db_session, seeded)


async def test_provider_failure_triggers_failsafe(db_session, seeded) -> None:
    mock = DeterministicMockProvider()
    mock.register_failure("DemoOut")
    with pytest.raises(LLMUnavailable):
        await _run(db_session, seeded, mock)
    await _assert_failsafe(db_session, seeded)


async def _assert_failsafe(db_session, seeded) -> None:
    gap_open = await ObservabilityService(db_session).has_open_for(
        enterprise_id=seeded.id, scope="capability", scope_ref="proc.risk.diagnose"
    )
    assert gap_open is True

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditRecord)
            .where(AuditRecord.event_type == "ai_unavailable")
        )
    ).scalar_one()
    assert count >= 1
