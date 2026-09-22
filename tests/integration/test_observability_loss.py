"""Observability-loss integration tests (T063; FR-015/FR-016).

Three stages, from already-implemented Phase 3 behavior through to new Phase 4 behavior:

1. An unavailable source opens a ``source_unavailable`` gap -- this already exists
   (``check_connection``, Phase 3/T050) and is pinned here as a regression guard / grounding
   fact, not new Phase-4 work.
2. New (T067): ``recompute_aggregates`` must not produce a fresh-looking ``sku_aggregate`` for
   an item whose only supporting domain data comes from a currently-unavailable source --
   computing a confident aggregate from data we know is no longer trustworthy would itself be
   "treating it as autonomous-grade" input.
3. New (T070): ``detect_risks`` must not raise a risk claim for an item with no (or no fresh)
   aggregate -- no output is produced with unreliable evidence, rather than a downgraded-
   confidence output. This is the L1/L2 translation of FR-016 ("MUST NOT выдавать автономное
   действие как достоверное, если ... отсутствуют критически необходимые данные"); it
   deliberately stops there -- no recommendation, enforcement, or L3+ behavior is exercised or
   required to make this test pass.
"""

from __future__ import annotations

import uuid

import pytest
from app.analysis.models import RiskFinding
from app.analysis.rules import detect_risks
from app.observation.aggregates import recompute_aggregates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Item
from app.integration.models import DataSource
from app.integration.service import check_connection, create_data_source
from app.observation.models import SkuAggregate
from app.observation.observability import ObservabilityService

pytestmark = pytest.mark.asyncio


async def _unavailable_source(db_session: AsyncSession, enterprise_id: uuid.UUID) -> DataSource:
    src = await create_data_source(
        db_session,
        enterprise_id=enterprise_id,
        name="No file uploaded yet",
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True},  # no "content" -> test_connection fails
        credential=None,
        created_by=uuid.uuid4(),
    )
    check = await check_connection(db_session, enterprise_id=enterprise_id, data_source_id=src.id)
    assert check.health == "unavailable"
    return src


async def test_unavailable_source_opens_a_source_gap(db_session: AsyncSession, seeded) -> None:
    """Already-implemented Phase 3 behavior (T050) -- grounds the rest of this file."""
    src = await _unavailable_source(db_session, seeded.id)

    gap_open = await ObservabilityService(db_session).has_open_for(
        enterprise_id=seeded.id, scope="source", scope_ref=str(src.id)
    )
    assert gap_open is True


async def test_recompute_aggregates_skips_items_from_an_unavailable_source(
    db_session: AsyncSession, seeded
) -> None:
    src = await _unavailable_source(db_session, seeded.id)
    item = Item(
        enterprise_id=seeded.id,
        sku="SKU-STALE-1",
        name="Item behind a dead source",
        source_provenance={"data_source_id": str(src.id)},
        observability="lost",
    )
    db_session.add(item)
    await db_session.flush()

    await recompute_aggregates(db_session, enterprise_id=seeded.id)

    aggregate = (
        await db_session.execute(select(SkuAggregate).where(SkuAggregate.item_id == item.id))
    ).scalar_one_or_none()
    assert aggregate is None  # no confident aggregate computed from data we can't trust


async def test_detect_risks_does_not_claim_a_risk_without_a_fresh_aggregate(
    db_session: AsyncSession, seeded
) -> None:
    src = await _unavailable_source(db_session, seeded.id)
    item = Item(
        enterprise_id=seeded.id,
        sku="SKU-STALE-2",
        name="Item behind a dead source",
        source_provenance={"data_source_id": str(src.id)},
        observability="lost",
    )
    db_session.add(item)
    await db_session.flush()

    await recompute_aggregates(db_session, enterprise_id=seeded.id)  # no-op for this item
    await detect_risks(db_session, enterprise_id=seeded.id)

    count = (
        await db_session.execute(
            select(func.count()).select_from(RiskFinding).where(RiskFinding.item_id == item.id)
        )
    ).scalar_one()
    assert count == 0  # no autonomous-grade output from data we know is unreliable
