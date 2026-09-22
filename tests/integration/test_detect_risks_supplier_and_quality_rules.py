"""Focused T070 coverage for the three `detect_risks` orchestration paths that T060 (pure
rule unit tests) and T062 (item-scoped `likely_shortage` pipeline test) don't reach:
`price_anomaly` (per-item aggregate), `systematic_supplier_delay` (per-supplier, across items,
no observation_signal evidence by design), and `quality_degradation` (per item+supplier pair).

Not a formal T-numbered task — added because these three paths query the database differently
from the per-item aggregate loop T062 already exercises, so they carry real, untested
correctness risk (grouping, dedup identity, evidence-link behavior) that the approved T060/T062
tests structurally cannot catch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import RiskFinding, RiskSignalLink
from app.analysis.rules import detect_risks
from app.domain.models import Item, Price, PurchaseOrder, QualityRecord, StockLevel, Supplier
from app.observation.aggregates import recompute_aggregates

pytestmark = pytest.mark.asyncio


async def _item(session: AsyncSession, enterprise_id: uuid.UUID) -> Item:
    item = Item(enterprise_id=enterprise_id, sku=f"SKU-{uuid.uuid4().hex[:8]}", name="x")
    session.add(item)
    await session.flush()
    return item


async def _supplier(session: AsyncSession, enterprise_id: uuid.UUID) -> Supplier:
    supplier = Supplier(enterprise_id=enterprise_id, code=f"SUP-{uuid.uuid4().hex[:8]}", name="x")
    session.add(supplier)
    await session.flush()
    return supplier


async def test_price_anomaly_detected_via_aggregate_and_deduped(
    db_session: AsyncSession, seeded
) -> None:
    item = await _item(db_session, seeded.id)
    supplier = await _supplier(db_session, seeded.id)
    db_session.add(
        StockLevel(enterprise_id=seeded.id, item_id=item.id, quantity=100, min_quantity=10)
    )
    db_session.add(
        Price(
            enterprise_id=seeded.id,
            item_id=item.id,
            supplier_id=supplier.id,
            unit_price=10,
            valid_from=date(2026, 1, 1),
        )
    )
    await db_session.flush()
    db_session.add(
        Price(
            enterprise_id=seeded.id,
            item_id=item.id,
            supplier_id=supplier.id,
            unit_price=12,  # +20% -> above the 10% threshold
            valid_from=date(2026, 2, 1),
        )
    )
    await db_session.flush()

    await recompute_aggregates(db_session, enterprise_id=seeded.id)
    await detect_risks(db_session, enterprise_id=seeded.id)
    # re-run: must not duplicate
    await detect_risks(db_session, enterprise_id=seeded.id)

    rows = (
        (
            await db_session.execute(
                select(RiskFinding).where(
                    RiskFinding.enterprise_id == seeded.id,
                    RiskFinding.risk_type == "price_anomaly",
                    RiskFinding.item_id == item.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].supplier_id is None


async def test_systematic_supplier_delay_is_supplier_scoped_with_no_signal_evidence(
    db_session: AsyncSession, seeded
) -> None:
    supplier = await _supplier(db_session, seeded.id)
    item_a = await _item(db_session, seeded.id)
    item_b = await _item(db_session, seeded.id)  # a second item, same supplier

    now = datetime.now(UTC)
    for item, late_days in ((item_a, 4), (item_b, 5), (item_a, 6)):
        db_session.add(
            PurchaseOrder(
                enterprise_id=seeded.id,
                external_ref=f"PO-{uuid.uuid4().hex[:8]}",
                item_id=item.id,
                supplier_id=supplier.id,
                quantity=10,
                status="received",
                expected_at=now,
                received_at=now + timedelta(days=late_days),
            )
        )
    await db_session.flush()

    await detect_risks(db_session, enterprise_id=seeded.id)

    rows = (
        (
            await db_session.execute(
                select(RiskFinding).where(
                    RiskFinding.enterprise_id == seeded.id,
                    RiskFinding.risk_type == "systematic_supplier_delay",
                    RiskFinding.supplier_id == supplier.id,
                )
            )
        )
        .scalars()
        .all()
    )
    # One finding for the supplier, even though the three late POs span two different items.
    assert len(rows) == 1
    assert rows[0].item_id is None

    # No observation_signal evidence exists for this risk type by design (T066 doesn't diff
    # purchase_order changes into signals) -- documented, not a defect.
    link_count = (
        await db_session.execute(
            select(func.count())
            .select_from(RiskSignalLink)
            .where(RiskSignalLink.risk_finding_id == rows[0].id)
        )
    ).scalar_one()
    assert link_count == 0


async def test_quality_degradation_is_item_and_supplier_scoped(
    db_session: AsyncSession, seeded
) -> None:
    item = await _item(db_session, seeded.id)
    supplier = await _supplier(db_session, seeded.id)
    other_supplier = await _supplier(db_session, seeded.id)

    for defect_rate in (0.08, 0.09, 0.10):
        db_session.add(
            QualityRecord(
                enterprise_id=seeded.id,
                item_id=item.id,
                supplier_id=supplier.id,
                defect_rate=defect_rate,
            )
        )
    # A clean record from a different supplier for the same item must not affect the first.
    db_session.add(
        QualityRecord(
            enterprise_id=seeded.id,
            item_id=item.id,
            supplier_id=other_supplier.id,
            defect_rate=0.01,
        )
    )
    await db_session.flush()

    await detect_risks(db_session, enterprise_id=seeded.id)

    rows = (
        (
            await db_session.execute(
                select(RiskFinding).where(
                    RiskFinding.enterprise_id == seeded.id,
                    RiskFinding.risk_type == "quality_degradation",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].item_id == item.id
    assert rows[0].supplier_id == supplier.id
