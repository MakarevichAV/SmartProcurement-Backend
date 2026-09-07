"""Append-only DB guard (T027).

UPDATE and DELETE on ``audit_record`` (and every table with the ``forbid_mutation`` trigger)
must raise at the database level, independently of application code (FR-063, Constitution X).
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from app.audit.models import AuditRecord
from app.audit.recorder import AuditRecorder

pytestmark = pytest.mark.asyncio


async def _make_record(db_session, seeded) -> AuditRecord:
    rec = await AuditRecorder(db_session).record(
        enterprise_id=seeded.id,
        event_type="mapping_confirmed",
        action="test",
    )
    await db_session.flush()
    return rec


async def test_update_on_audit_record_raises(db_session, seeded) -> None:
    rec = await _make_record(db_session, seeded)
    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(AuditRecord).where(AuditRecord.id == rec.id).values(action="tampered")
        )


async def test_delete_on_audit_record_raises(db_session, seeded) -> None:
    rec = await _make_record(db_session, seeded)
    with pytest.raises(DBAPIError):
        await db_session.execute(delete(AuditRecord).where(AuditRecord.id == rec.id))


async def test_trigger_exists_on_capability_level_event(db_session, seeded) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'capability_level_event'::regclass AND NOT tgisinternal"
            )
        )
    ).scalar_one_or_none()
    assert row == "capability_level_event_append_only"
