"""Observation-loop integration test (T062): sync -> signals -> aggregates -> risk, end to end.

Drives the real US1 onboarding path (same services as test_onboarding_journey.py) through a
single file data source, then exercises the Phase 4 pipeline on top of it:

    sync_source (extended, T066)  -> observation_signal rows (FR-014)
    recompute_aggregates (T067)   -> sku_aggregate rows (research.md §7a, deterministic)
    detect_risks (T070)           -> risk_finding + risk_signal_link, deduped by identity

**Target interface this file specifies for T066/T067/T069/T070** (none exist yet, so every
Phase-4 import below is expected to fail until they are written):

    app.observation.models.ObservationSignal, SkuAggregate
    app.observation.aggregates.recompute_aggregates(session, *, enterprise_id) -> list[...]
        -- simple deterministic recompute scoped to the enterprise (Phase 4 decision #1: no
           dirty-tracking, no new cache/queue infra).
    app.analysis.models.RiskFinding, RiskSignalLink
    app.analysis.rules.detect_risks(session, *, enterprise_id) -> list[RiskFinding]
        -- the DB-aware orchestrator: runs the pure rule functions from
           tests/unit/test_risk_rules.py over current aggregates/domain rows, and applies the
           approved Phase 4 dedup identity (enterprise_id + risk_type + item_id, non-terminal
           only) — a repeat condition links new evidence to the existing finding via
           risk_signal_link instead of creating a duplicate row; a resolved/dismissed finding
           may be superseded by a new row on recurrence.

``sync_source`` itself is unchanged (already implemented, Phase 3) — only its *internal*
signal-diffing behavior is new in T066; the call signature used here already exists today.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.analysis.models import RiskFinding, RiskSignalLink
from app.analysis.rules import detect_risks
from app.observation.aggregates import recompute_aggregates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import DeterministicMockProvider
from app.domain.models import Item, StockLevel
from app.integration.mapping_ai import suggest_mappings_for_source
from app.integration.mapping_service import confirm_mapping
from app.integration.models import DataSource, FieldMapping
from app.integration.service import create_data_source, describe_and_store
from app.integration.sync import sync_source
from app.observation.models import ObservationSignal

pytestmark = pytest.mark.asyncio

_ITEM_SKU = "SKU-SHORTAGE-1"
_LEAD_TIME_DAYS = 7
_MONTHLY_CONSUMPTION = 300  # -> 10/day


def _csv(on_hand_qty: int) -> str:
    header = (
        "sku,name,on_hand_qty,min_qty,warehouse_code,supplier_code,supplier_name,"
        "supplier_approved,unit_price,currency,lead_time_days,monthly_consumption"
    )
    row = (
        f"{_ITEM_SKU},Test Widget,{on_hand_qty},100,WH-1,SUP-1,Test Supplier,"
        f"true,10.00,USD,{_LEAD_TIME_DAYS},{_MONTHLY_CONSUMPTION}"
    )
    return f"{header}\n{row}\n"


async def _setup_confirmed_source(db_session: AsyncSession, enterprise_id: uuid.UUID) -> DataSource:
    src = await create_data_source(
        db_session,
        enterprise_id=enterprise_id,
        name="Shortage demo source",
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True, "content": _csv(1000)},
        credential=None,
        created_by=uuid.uuid4(),
    )
    await describe_and_store(db_session, enterprise_id=enterprise_id, data_source_id=src.id)

    # No fixture registered -> DeterministicMockProvider falls back to the existing
    # name-based mock_demo heuristic (app.ai.mock_demo), which already recognizes every
    # column used here (see app/ai/mock_demo.py _RULES).
    await suggest_mappings_for_source(
        db_session,
        enterprise_id=enterprise_id,
        data_source_id=src.id,
        provider=DeterministicMockProvider(),
    )
    mappings = (
        (
            await db_session.execute(
                select(FieldMapping).where(FieldMapping.data_source_id == src.id)
            )
        )
        .scalars()
        .all()
    )
    assert mappings, "mock_demo heuristic should have suggested mappings for every known column"
    for m in mappings:
        await confirm_mapping(
            db_session, enterprise_id=enterprise_id, mapping_id=m.id, actor_id=uuid.uuid4()
        )
    return src


async def _sync_with_stock(
    db_session: AsyncSession, enterprise_id: uuid.UUID, src: DataSource, on_hand_qty: int
) -> None:
    src.config = {**src.config, "content": _csv(on_hand_qty)}
    await db_session.flush()
    await sync_source(db_session, enterprise_id=enterprise_id, data_source_id=src.id)


async def _item_id(db_session: AsyncSession, enterprise_id: uuid.UUID) -> uuid.UUID:
    item = (
        await db_session.execute(
            select(Item).where(Item.enterprise_id == enterprise_id, Item.sku == _ITEM_SKU)
        )
    ).scalar_one()
    return item.id


async def _signal_count(db_session: AsyncSession, item_id: uuid.UUID) -> int:
    return (
        await db_session.execute(
            select(func.count())
            .select_from(ObservationSignal)
            .where(ObservationSignal.item_id == item_id)
        )
    ).scalar_one()


async def _non_terminal_findings(
    db_session: AsyncSession, enterprise_id: uuid.UUID, item_id: uuid.UUID
) -> list[RiskFinding]:
    return list(
        (
            await db_session.execute(
                select(RiskFinding).where(
                    RiskFinding.enterprise_id == enterprise_id,
                    RiskFinding.risk_type == "likely_shortage",
                    RiskFinding.item_id == item_id,
                    RiskFinding.status.not_in(("resolved", "dismissed")),
                )
            )
        )
        .scalars()
        .all()
    )


async def test_stock_drop_produces_signal_then_aggregate_then_risk_before_stockout(
    db_session: AsyncSession, seeded
) -> None:
    src = await _setup_confirmed_source(db_session, seeded.id)
    await sync_source(db_session, enterprise_id=seeded.id, data_source_id=src.id)  # baseline
    item_id = await _item_id(db_session, seeded.id)

    baseline_signals = await _signal_count(db_session, item_id)
    assert baseline_signals == 0  # first sync establishes the baseline, nothing to diff against

    # Stock drops to 40 units: days_of_cover = 40 / 10/day = 4 < lead_time_days (7) -> shortage.
    await _sync_with_stock(db_session, seeded.id, src, on_hand_qty=40)
    assert await _signal_count(db_session, item_id) > baseline_signals

    await recompute_aggregates(db_session, enterprise_id=seeded.id)
    findings = await detect_risks(db_session, enterprise_id=seeded.id)

    matching = [f for f in findings if f.risk_type == "likely_shortage" and f.item_id == item_id]
    assert len(matching) == 1
    finding = matching[0]
    assert finding.status not in ("resolved", "dismissed")

    # Evidence: the triggering signal(s) are linked, not just implied.
    links = (
        await db_session.execute(
            select(func.count())
            .select_from(RiskSignalLink)
            .where(RiskSignalLink.risk_finding_id == finding.id)
        )
    ).scalar_one()
    assert links >= 1

    # Stock hasn't hit zero yet -- the risk fired *before* the modelled stock-out (SC-002).
    stock_row = (
        await db_session.execute(select(StockLevel).where(StockLevel.item_id == item_id))
    ).scalar_one()
    assert stock_row.quantity == 40


async def test_repeated_evidence_links_to_existing_open_finding_not_a_duplicate(
    db_session: AsyncSession, seeded
) -> None:
    src = await _setup_confirmed_source(db_session, seeded.id)
    await sync_source(db_session, enterprise_id=seeded.id, data_source_id=src.id)
    item_id = await _item_id(db_session, seeded.id)

    await _sync_with_stock(db_session, seeded.id, src, on_hand_qty=40)
    await recompute_aggregates(db_session, enterprise_id=seeded.id)
    await detect_risks(db_session, enterprise_id=seeded.id)

    first_open = await _non_terminal_findings(db_session, seeded.id, item_id)
    assert len(first_open) == 1
    finding_id = first_open[0].id
    links_before = (
        await db_session.execute(
            select(func.count())
            .select_from(RiskSignalLink)
            .where(RiskSignalLink.risk_finding_id == finding_id)
        )
    ).scalar_one()

    # Stock drops further -- a new observation_signal, same underlying (still-open) condition.
    await _sync_with_stock(db_session, seeded.id, src, on_hand_qty=20)
    await recompute_aggregates(db_session, enterprise_id=seeded.id)
    await detect_risks(db_session, enterprise_id=seeded.id)

    still_open = await _non_terminal_findings(db_session, seeded.id, item_id)
    assert len(still_open) == 1  # no duplicate risk_finding row
    assert still_open[0].id == finding_id  # same identity, same row

    links_after = (
        await db_session.execute(
            select(func.count())
            .select_from(RiskSignalLink)
            .where(RiskSignalLink.risk_finding_id == finding_id)
        )
    ).scalar_one()
    assert links_after > links_before  # new evidence was linked, not discarded


async def test_recurrence_after_resolution_creates_a_new_finding(
    db_session: AsyncSession, seeded
) -> None:
    src = await _setup_confirmed_source(db_session, seeded.id)
    await sync_source(db_session, enterprise_id=seeded.id, data_source_id=src.id)
    item_id = await _item_id(db_session, seeded.id)

    await _sync_with_stock(db_session, seeded.id, src, on_hand_qty=40)
    await recompute_aggregates(db_session, enterprise_id=seeded.id)
    await detect_risks(db_session, enterprise_id=seeded.id)

    open_findings = await _non_terminal_findings(db_session, seeded.id, item_id)
    assert len(open_findings) == 1
    resolved_id = open_findings[0].id
    open_findings[0].status = "resolved"
    open_findings[0].resolved_at = datetime.now(UTC)
    await db_session.flush()

    # A genuinely new occurrence of the same condition.
    await _sync_with_stock(db_session, seeded.id, src, on_hand_qty=15)
    await recompute_aggregates(db_session, enterprise_id=seeded.id)
    await detect_risks(db_session, enterprise_id=seeded.id)

    open_again = await _non_terminal_findings(db_session, seeded.id, item_id)
    assert len(open_again) == 1
    assert open_again[0].id != resolved_id  # a new row, not a reopened one

    resolved_row = await db_session.get(RiskFinding, resolved_id)
    assert resolved_row is not None
    assert resolved_row.status == "resolved"  # untouched


async def test_aggregate_recompute_is_deterministic_for_the_same_inputs(
    db_session: AsyncSession, seeded
) -> None:
    src = await _setup_confirmed_source(db_session, seeded.id)
    await sync_source(db_session, enterprise_id=seeded.id, data_source_id=src.id)
    await _sync_with_stock(db_session, seeded.id, src, on_hand_qty=250)

    first = await recompute_aggregates(db_session, enterprise_id=seeded.id)
    second = await recompute_aggregates(db_session, enterprise_id=seeded.id)

    def _by_item(rows: object) -> dict:
        return {row.item_id: (row.avg_daily_consumption, row.days_of_cover) for row in rows}

    assert _by_item(first) == _by_item(second)  # same inputs -> same deterministic output
