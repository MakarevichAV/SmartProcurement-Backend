"""Service-level tests for bulk field-mapping confirmation.

Focus: enterprise isolation, that the shared lifecycle (status + confirmed_by/at +
append-only ``mapping_change_event``) is reused, and the structured result shape.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.integration.mapping_service import confirm_mappings_bulk
from app.integration.models import DataSource, FieldMapping, MappingChangeEvent

pytestmark = pytest.mark.asyncio


async def _source(session: AsyncSession, enterprise_id: uuid.UUID, name: str = "src") -> DataSource:
    ds = DataSource(
        enterprise_id=enterprise_id,
        name=name,
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True, "content": "sku\nA-1\n"},
        health="unavailable",
    )
    session.add(ds)
    await session.flush()
    return ds


async def _mapping(
    session: AsyncSession, ds: DataSource, path: str, *, status: str = "suggested"
) -> FieldMapping:
    m = FieldMapping(
        data_source_id=ds.id,
        source_field_path=path,
        canonical_entity="item",
        canonical_attribute=path,
        status=status,
    )
    session.add(m)
    await session.flush()
    return m


async def test_bulk_confirm_reuses_the_confirm_lifecycle(db_session: AsyncSession, seeded) -> None:
    ds = await _source(db_session, seeded.id)
    ms = [await _mapping(db_session, ds, f"f{i}") for i in range(4)]
    actor = uuid.uuid4()

    result = await confirm_mappings_bulk(
        db_session,
        enterprise_id=seeded.id,
        data_source_id=ds.id,
        mapping_ids=[m.id for m in ms],
        actor_id=actor,
    )

    assert sorted(result.confirmed) == sorted(str(m.id) for m in ms)
    assert result.failed == []
    for m in ms:
        await db_session.refresh(m)
        assert m.status == "confirmed"
        assert m.confirmed_by == actor
        assert m.confirmed_at is not None
        events = (
            (
                await db_session.execute(
                    select(MappingChangeEvent).where(
                        MappingChangeEvent.field_mapping_id == m.id,
                        MappingChangeEvent.action == "confirmed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].actor_id == actor
        assert events[0].before["status"] == "suggested"


async def test_bulk_confirm_skips_non_suggested_and_reports_them(
    db_session: AsyncSession, seeded
) -> None:
    ds = await _source(db_session, seeded.id)
    m_sug = await _mapping(db_session, ds, "sug")
    m_conf = await _mapping(db_session, ds, "conf", status="confirmed")
    m_rej = await _mapping(db_session, ds, "rej", status="rejected")
    m_ret = await _mapping(db_session, ds, "ret", status="retired")
    bogus = uuid.uuid4()

    result = await confirm_mappings_bulk(
        db_session,
        enterprise_id=seeded.id,
        data_source_id=ds.id,
        mapping_ids=[m_sug.id, m_conf.id, m_rej.id, m_ret.id, bogus],
        actor_id=uuid.uuid4(),
    )

    assert result.confirmed == [str(m_sug.id)]
    assert {f["mapping_id"] for f in result.failed} == {
        str(m_conf.id),
        str(m_rej.id),
        str(m_ret.id),
        str(bogus),
    }
    assert all(f["reason"] for f in result.failed)
    assert len(result.requested) == 5

    for m, expected in ((m_conf, "confirmed"), (m_rej, "rejected"), (m_ret, "retired")):
        await db_session.refresh(m)
        assert m.status == expected
    # no change events were written for the untouched ones
    ev_count = (
        await db_session.execute(
            select(func.count())
            .select_from(MappingChangeEvent)
            .where(MappingChangeEvent.field_mapping_id.in_([m_conf.id, m_rej.id, m_ret.id]))
        )
    ).scalar_one()
    assert ev_count == 0


async def test_bulk_confirm_enforces_enterprise_isolation(db_session: AsyncSession, seeded) -> None:
    # a second enterprise with its own source + suggested mapping
    from app.enterprise.models import Enterprise

    other = Enterprise(name="Other Co", base_currency="USD")
    db_session.add(other)
    await db_session.flush()
    other_ds = await _source(db_session, other.id, "other-src")
    other_m = await _mapping(db_session, other_ds, "foreign")

    my_ds = await _source(db_session, seeded.id, "mine")
    my_m = await _mapping(db_session, my_ds, "mine")

    # (a) foreign mapping id passed with my source -> reported failed, left untouched
    result = await confirm_mappings_bulk(
        db_session,
        enterprise_id=seeded.id,
        data_source_id=my_ds.id,
        mapping_ids=[my_m.id, other_m.id],
        actor_id=uuid.uuid4(),
    )
    assert result.confirmed == [str(my_m.id)]
    assert [f["mapping_id"] for f in result.failed] == [str(other_m.id)]
    await db_session.refresh(other_m)
    assert other_m.status == "suggested"

    # (b) foreign source id -> 404, nothing touched
    with pytest.raises(NotFoundError):
        await confirm_mappings_bulk(
            db_session,
            enterprise_id=seeded.id,
            data_source_id=other_ds.id,
            mapping_ids=[other_m.id],
            actor_id=uuid.uuid4(),
        )
    await db_session.refresh(other_m)
    assert other_m.status == "suggested"
