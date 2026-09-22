"""Deterministic aggregate recomputation — the pre-AI guard (T067; research.md §7a).

``recompute_aggregates`` is a **simple, scoped, deterministic** sweep: for each item in scope
(the whole enterprise by default, or a narrower ``item_ids`` set when the caller already knows
which items changed — e.g. the job enqueued from ``sync_source``), it re-derives
``sku_aggregate`` from current ``domain/`` rows. No dirty-tracking table, no cache, no AI call
(Phase 4 decision #1: keep it simple and correct first).

Only domain rows still considered trustworthy (``observability != 'lost'``) feed the
computation — an item with no trustworthy stock data gets **no** aggregate row at all, rather
than a confident-looking row built from data we know is unreliable (FR-015/FR-016). v1
computes at item level only; ``sku_aggregate.warehouse_id`` stays ``NULL`` (Phase 4 decision:
no per-warehouse aggregation yet, matching the v1 risk-identity shape in data-model.md §5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Consumption, Item, Price, PurchaseOrder, StockLevel
from app.observation.models import SkuAggregate

_DAYS_IN_MONTH = Decimal(30)


async def recompute_aggregates(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    item_ids: list[uuid.UUID] | None = None,
) -> list[SkuAggregate]:
    stmt = select(Item.id).where(Item.enterprise_id == enterprise_id)
    if item_ids:
        stmt = stmt.where(Item.id.in_(item_ids))
    ids = (await session.execute(stmt)).scalars().all()

    results: list[SkuAggregate] = []
    for item_id in ids:
        aggregate = await _recompute_one(session, enterprise_id, item_id)
        if aggregate is not None:
            results.append(aggregate)
    return results


async def _recompute_one(
    session: AsyncSession, enterprise_id: uuid.UUID, item_id: uuid.UUID
) -> SkuAggregate | None:
    stock_rows = (
        (
            await session.execute(
                select(StockLevel).where(
                    StockLevel.item_id == item_id, StockLevel.observability != "lost"
                )
            )
        )
        .scalars()
        .all()
    )
    if not stock_rows:
        return None  # no trustworthy stock data -> no confident aggregate (FR-016)

    total_quantity = sum((Decimal(str(r.quantity)) for r in stock_rows), Decimal(0))

    avg_daily_consumption = await _avg_daily_consumption(session, item_id)
    days_of_cover = (
        total_quantity / avg_daily_consumption
        if avg_daily_consumption and avg_daily_consumption > 0
        else None
    )
    next_expected_delivery_at = await _next_expected_delivery(session, item_id)
    last_price, price_trend = await _price_trend(session, item_id)
    avg_supplier_delay_days = await _avg_supplier_delay(session, item_id)

    return await _upsert_aggregate(
        session,
        enterprise_id=enterprise_id,
        item_id=item_id,
        avg_daily_consumption=avg_daily_consumption,
        days_of_cover=days_of_cover,
        next_expected_delivery_at=next_expected_delivery_at,
        last_price=last_price,
        price_trend=price_trend,
        avg_supplier_delay_days=avg_supplier_delay_days,
    )


async def _avg_daily_consumption(session: AsyncSession, item_id: uuid.UUID) -> Decimal | None:
    row = (
        await session.execute(
            select(Consumption)
            .where(Consumption.item_id == item_id, Consumption.observability != "lost")
            .order_by(Consumption.period_end.desc().nulls_last(), Consumption.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    quantity = Decimal(str(row.quantity))
    if row.period_start and row.period_end:
        span = (row.period_end - row.period_start).days
        if span > 0:
            return quantity / Decimal(span)
    # No usable period -> the quantity is treated as a monthly figure (v1 simplification).
    return quantity / _DAYS_IN_MONTH


async def _next_expected_delivery(session: AsyncSession, item_id: uuid.UUID) -> datetime | None:
    return (
        await session.execute(
            select(PurchaseOrder.expected_at)
            .where(
                PurchaseOrder.item_id == item_id,
                PurchaseOrder.status == "open",
                PurchaseOrder.observability != "lost",
                PurchaseOrder.expected_at.is_not(None),
            )
            .order_by(PurchaseOrder.expected_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _price_trend(
    session: AsyncSession, item_id: uuid.UUID
) -> tuple[Decimal | None, Decimal | None]:
    rows = (
        (
            await session.execute(
                select(Price)
                .where(Price.item_id == item_id, Price.observability != "lost")
                .order_by(Price.valid_from.desc().nulls_last(), Price.created_at.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None, None
    newest = Decimal(str(rows[0].unit_price))
    if len(rows) < 2:
        return newest, None
    older = Decimal(str(rows[1].unit_price))
    if older == 0:
        return newest, None
    return newest, (newest - older) / older


async def _avg_supplier_delay(session: AsyncSession, item_id: uuid.UUID) -> Decimal | None:
    rows = (
        (
            await session.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.item_id == item_id,
                    PurchaseOrder.status == "received",
                    PurchaseOrder.observability != "lost",
                    PurchaseOrder.expected_at.is_not(None),
                    PurchaseOrder.received_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    delays: list[Decimal] = []
    for r in rows:
        assert r.expected_at is not None and r.received_at is not None  # filtered in the query
        delays.append(Decimal((r.received_at - r.expected_at).days))
    return sum(delays, Decimal(0)) / Decimal(len(delays))


async def _upsert_aggregate(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    item_id: uuid.UUID,
    avg_daily_consumption: Decimal | None,
    days_of_cover: Decimal | None,
    next_expected_delivery_at: datetime | None,
    last_price: Decimal | None,
    price_trend: Decimal | None,
    avg_supplier_delay_days: Decimal | None,
) -> SkuAggregate:
    existing = (
        await session.execute(
            select(SkuAggregate).where(
                SkuAggregate.item_id == item_id, SkuAggregate.warehouse_id.is_(None)
            )
        )
    ).scalar_one_or_none()

    values = {
        "avg_daily_consumption": avg_daily_consumption,
        "days_of_cover": days_of_cover,
        "next_expected_delivery_at": next_expected_delivery_at,
        "last_price": last_price,
        "price_trend": price_trend,
        "avg_supplier_delay_days": avg_supplier_delay_days,
        "computed_at": datetime.now(UTC),
    }
    if existing is None:
        row = SkuAggregate(
            enterprise_id=enterprise_id, item_id=item_id, warehouse_id=None, **values
        )
        session.add(row)
        await session.flush()
        return row

    for key, value in values.items():
        setattr(existing, key, value)
    await session.flush()
    return existing
