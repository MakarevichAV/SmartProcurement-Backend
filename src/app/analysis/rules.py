"""Deterministic risk rules + `detect_risks` orchestrator (T070; data-model.md §5, FR-017/
FR-019). No LLM call decides whether a risk exists — every rule below is a pure function over
already-computed, deterministic inputs (`sku_aggregate`, `production_demand`, `purchase_order`,
`quality_record`); `detect_risks` is the only DB-aware piece, and it only assembles those
inputs, dedupes, and links evidence.

**Thresholds** (systematic_supplier_delay / price_anomaly / quality_degradation) are a
confirmed product decision — none of the three is derivable from spec.md/data-model.md/
research.md/contracts (only `capability.verification_tolerances`, FR-055a, exists, and it is
explicitly scoped to *post-execution verification*, not L2 detection):

* systematic_supplier_delay: avg lateness > 3 days over >= 3 received purchase orders.
* price_anomaly: |price_trend| >= 10%.
* quality_degradation: avg defect_rate > 5%.

`likely_shortage` / `insufficient_until_next_delivery` / `production_stop_risk` need no such
constant — each is a relational comparison between two already-computed dynamic quantities
(days_of_cover vs. lead time / next delivery / production need-by), matching quickstart.md §4.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import RiskFinding, RiskSignalLink
from app.domain.models import LeadTime, ProductionDemand, PurchaseOrder, QualityRecord
from app.observation.models import ObservationSignal, SkuAggregate

_SYSTEMATIC_DELAY_THRESHOLD_DAYS = Decimal(3)
_SYSTEMATIC_DELAY_MIN_SAMPLES = 3
_PRICE_ANOMALY_THRESHOLD_PCT = Decimal("0.10")
_QUALITY_DEGRADATION_THRESHOLD_RATE = Decimal("0.05")


def _dec(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _severity_from_ratio(ratio: Decimal, *, high: Decimal, med: Decimal) -> str:
    if ratio >= high:
        return "high"
    if ratio >= med:
        return "med"
    return "low"


# --- pure inputs (no DB) ---------------------------------------------------------------------


@dataclass
class AggregateSnapshot:
    """Mirrors a `sku_aggregate` row (data-model.md §4)."""

    item_id: uuid.UUID
    warehouse_id: uuid.UUID | None
    avg_daily_consumption: object
    days_of_cover: object
    next_expected_delivery_at: datetime | None
    last_price: object
    price_trend: object
    avg_supplier_delay_days: object
    computed_at: datetime


@dataclass
class ProductionDemandSnapshot:
    """Mirrors a `production_demand` row (data-model.md §3)."""

    item_id: uuid.UUID
    quantity: object
    need_by: datetime | date


@dataclass
class RiskCandidate:
    risk_type: str
    item_id: uuid.UUID | None
    supplier_id: uuid.UUID | None
    severity: str
    reason: str = ""


# --- pure rules --------------------------------------------------------------------------


def evaluate_likely_shortage(
    aggregate: AggregateSnapshot, *, lead_time_days: object
) -> RiskCandidate | None:
    days_of_cover = _dec(aggregate.days_of_cover)
    lead_time = _dec(lead_time_days)
    if days_of_cover is None or lead_time is None or lead_time <= 0:
        return None
    if days_of_cover >= lead_time:
        return None
    ratio = days_of_cover / lead_time
    severity = _severity_from_ratio(1 - ratio, high=Decimal("0.75"), med=Decimal("0.25"))
    return RiskCandidate(
        risk_type="likely_shortage",
        item_id=aggregate.item_id,
        supplier_id=None,
        severity=severity,
        reason=f"days_of_cover ({days_of_cover}) < lead_time_days ({lead_time})",
    )


def evaluate_insufficient_until_next_delivery(
    aggregate: AggregateSnapshot, *, now: datetime
) -> RiskCandidate | None:
    days_of_cover = _dec(aggregate.days_of_cover)
    if days_of_cover is None or aggregate.next_expected_delivery_at is None:
        return None
    days_until_delivery = Decimal((aggregate.next_expected_delivery_at - now).days)
    if days_until_delivery <= 0 or days_of_cover >= days_until_delivery:
        return None
    ratio = days_of_cover / days_until_delivery
    severity = _severity_from_ratio(1 - ratio, high=Decimal("0.75"), med=Decimal("0.25"))
    return RiskCandidate(
        risk_type="insufficient_until_next_delivery",
        item_id=aggregate.item_id,
        supplier_id=None,
        severity=severity,
        reason=(
            f"days_of_cover ({days_of_cover}) < days until next delivery "
            f"({days_until_delivery})"
        ),
    )


def evaluate_production_stop_risk(
    aggregate: AggregateSnapshot,
    demands: list[ProductionDemandSnapshot],
    *,
    now: datetime,
) -> RiskCandidate | None:
    # Explicit Phase 4 decision: no applicable production_demand -> never fires. This is not
    # an error and must not manufacture a risk from absent data.
    if not demands:
        return None
    days_of_cover = _dec(aggregate.days_of_cover)
    if days_of_cover is None:
        return None

    today = _as_date(now)
    soonest_days_until_need: Decimal | None = None
    for demand in demands:
        if demand.item_id != aggregate.item_id:
            continue
        need_by = _as_date(demand.need_by)
        days_until_need = Decimal((need_by - today).days)
        if soonest_days_until_need is None or days_until_need < soonest_days_until_need:
            soonest_days_until_need = days_until_need

    if soonest_days_until_need is None or soonest_days_until_need <= 0:
        return None
    if days_of_cover >= soonest_days_until_need:
        return None
    ratio = days_of_cover / soonest_days_until_need
    severity = _severity_from_ratio(1 - ratio, high=Decimal("0.75"), med=Decimal("0.25"))
    return RiskCandidate(
        risk_type="production_stop_risk",
        item_id=aggregate.item_id,
        supplier_id=None,
        severity=severity,
        reason=(
            f"days_of_cover ({days_of_cover}) < days until production need "
            f"({soonest_days_until_need})"
        ),
    )


def evaluate_systematic_supplier_delay(
    supplier_id: uuid.UUID,
    delay_samples: Sequence[object],
    *,
    threshold_days: object = _SYSTEMATIC_DELAY_THRESHOLD_DAYS,
    min_samples: int = _SYSTEMATIC_DELAY_MIN_SAMPLES,
) -> RiskCandidate | None:
    if len(delay_samples) < min_samples:
        return None
    threshold = _dec(threshold_days)
    assert threshold is not None
    values = [d for v in delay_samples if (d := _dec(v)) is not None]
    if not values:
        return None
    avg_delay = sum(values, Decimal(0)) / Decimal(len(values))
    if avg_delay <= threshold:
        return None
    ratio = avg_delay / threshold
    severity = _severity_from_ratio(ratio, high=Decimal(2), med=Decimal("1.5"))
    return RiskCandidate(
        risk_type="systematic_supplier_delay",
        item_id=None,
        supplier_id=supplier_id,
        severity=severity,
        reason=f"avg delay {avg_delay} days over {len(values)} deliveries > {threshold} days",
    )


def evaluate_price_anomaly(
    aggregate: AggregateSnapshot, *, threshold_pct: object = _PRICE_ANOMALY_THRESHOLD_PCT
) -> RiskCandidate | None:
    price_trend = _dec(aggregate.price_trend)
    if price_trend is None:
        return None
    threshold = _dec(threshold_pct)
    assert threshold is not None
    magnitude = abs(price_trend)
    if magnitude < threshold:
        return None
    ratio = magnitude / threshold
    severity = _severity_from_ratio(ratio, high=Decimal(2), med=Decimal("1.5"))
    return RiskCandidate(
        risk_type="price_anomaly",
        item_id=aggregate.item_id,
        supplier_id=None,
        severity=severity,
        reason=f"price_trend {price_trend} exceeds {threshold} threshold",
    )


def evaluate_quality_degradation(
    item_id: uuid.UUID,
    supplier_id: uuid.UUID,
    defect_samples: Sequence[object],
    *,
    threshold_defect_rate: object = _QUALITY_DEGRADATION_THRESHOLD_RATE,
) -> RiskCandidate | None:
    if not defect_samples:
        return None
    threshold = _dec(threshold_defect_rate)
    assert threshold is not None
    values = [d for v in defect_samples if (d := _dec(v)) is not None]
    if not values:
        return None
    avg_defect_rate = sum(values, Decimal(0)) / Decimal(len(values))
    if avg_defect_rate <= threshold:
        return None
    ratio = avg_defect_rate / threshold
    severity = _severity_from_ratio(ratio, high=Decimal(2), med=Decimal("1.5"))
    return RiskCandidate(
        risk_type="quality_degradation",
        item_id=item_id,
        supplier_id=supplier_id,
        severity=severity,
        reason=f"avg defect_rate {avg_defect_rate} exceeds {threshold} threshold",
    )


# --- detect_risks orchestrator (T070; DB-aware) -----------------------------------------------

# Which observation_signal types are relevant evidence for each risk_type. T066 deliberately
# does not diff purchase_order changes into signals (documented simplification), so
# systematic_supplier_delay has no observation_signal evidence to link — its evidence is the
# underlying purchase_order history itself, queried directly below; risk_signal_link stays
# empty for it rather than linking unrelated signals or fabricating a signal type that does
# not exist (Phase 4 decision: follow the documented semantics, don't invent evidence).
_EVIDENCE_SIGNAL_TYPES: dict[str, tuple[str, ...]] = {
    "likely_shortage": ("stock_change",),
    "insufficient_until_next_delivery": ("stock_change",),
    "production_stop_risk": ("stock_change", "demand_change"),
    "price_anomaly": ("price_change",),
    "quality_degradation": ("quality_issue",),
    "systematic_supplier_delay": (),
}


async def detect_risks(session: AsyncSession, *, enterprise_id: uuid.UUID) -> list[RiskFinding]:
    """Run all six deterministic rules over current aggregate/domain state for an enterprise.

    Dedupes against non-terminal findings per the approved identity table (data-model.md §5):
    a matching open condition gets new evidence linked instead of a duplicate row; a
    resolved/dismissed finding may be superseded by a new one on genuine recurrence.
    """
    now = datetime.now(UTC)
    findings: list[RiskFinding] = []

    aggregates = (
        (
            await session.execute(
                select(SkuAggregate).where(SkuAggregate.enterprise_id == enterprise_id)
            )
        )
        .scalars()
        .all()
    )

    for agg in aggregates:
        snapshot = AggregateSnapshot(
            item_id=agg.item_id,
            warehouse_id=agg.warehouse_id,
            avg_daily_consumption=agg.avg_daily_consumption,
            days_of_cover=agg.days_of_cover,
            next_expected_delivery_at=agg.next_expected_delivery_at,
            last_price=agg.last_price,
            price_trend=agg.price_trend,
            avg_supplier_delay_days=agg.avg_supplier_delay_days,
            computed_at=agg.computed_at,
        )

        lead_time_days = await _latest_lead_time_days(session, agg.item_id)
        if lead_time_days is not None:
            candidate = evaluate_likely_shortage(snapshot, lead_time_days=lead_time_days)
            if candidate is not None:
                findings.append(
                    await _apply(session, enterprise_id, candidate, item_id=agg.item_id)
                )

        candidate = evaluate_insufficient_until_next_delivery(snapshot, now=now)
        if candidate is not None:
            findings.append(await _apply(session, enterprise_id, candidate, item_id=agg.item_id))

        demands = await _production_demands(session, agg.item_id)
        candidate = evaluate_production_stop_risk(snapshot, demands, now=now)
        if candidate is not None:
            findings.append(await _apply(session, enterprise_id, candidate, item_id=agg.item_id))

        candidate = evaluate_price_anomaly(snapshot)
        if candidate is not None:
            findings.append(await _apply(session, enterprise_id, candidate, item_id=agg.item_id))

    for supplier_id, delay_samples in await _supplier_delay_samples(session, enterprise_id):
        candidate = evaluate_systematic_supplier_delay(supplier_id, delay_samples)
        if candidate is not None:
            findings.append(
                await _apply(session, enterprise_id, candidate, supplier_id=supplier_id)
            )

    for (item_id, supplier_id), defect_samples in await _quality_samples(session, enterprise_id):
        candidate = evaluate_quality_degradation(item_id, supplier_id, defect_samples)
        if candidate is not None:
            findings.append(
                await _apply(
                    session, enterprise_id, candidate, item_id=item_id, supplier_id=supplier_id
                )
            )

    return findings


async def _apply(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    candidate: RiskCandidate,
    *,
    item_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
) -> RiskFinding:
    finding = await _find_non_terminal(session, enterprise_id, candidate)
    if finding is None:
        finding = RiskFinding(
            enterprise_id=enterprise_id,
            risk_type=candidate.risk_type,
            item_id=candidate.item_id,
            supplier_id=candidate.supplier_id,
            severity=candidate.severity,
            status="open",
            detected_by="rule",
            ai_status="pending",
            detected_at=datetime.now(UTC),
        )
        session.add(finding)
        await session.flush()

    signal_types = _EVIDENCE_SIGNAL_TYPES[candidate.risk_type]
    if signal_types:
        await _link_new_evidence(
            session, finding, item_id=item_id, supplier_id=supplier_id, signal_types=signal_types
        )
    return finding


async def _find_non_terminal(
    session: AsyncSession, enterprise_id: uuid.UUID, candidate: RiskCandidate
) -> RiskFinding | None:
    conds = [
        RiskFinding.enterprise_id == enterprise_id,
        RiskFinding.risk_type == candidate.risk_type,
        RiskFinding.status.not_in(("resolved", "dismissed")),
    ]
    conds.append(
        RiskFinding.item_id.is_(None)
        if candidate.item_id is None
        else RiskFinding.item_id == candidate.item_id
    )
    conds.append(
        RiskFinding.supplier_id.is_(None)
        if candidate.supplier_id is None
        else RiskFinding.supplier_id == candidate.supplier_id
    )
    return (await session.execute(select(RiskFinding).where(*conds))).scalar_one_or_none()


async def _link_new_evidence(
    session: AsyncSession,
    finding: RiskFinding,
    *,
    item_id: uuid.UUID | None,
    supplier_id: uuid.UUID | None,
    signal_types: tuple[str, ...],
) -> None:
    already_linked = select(RiskSignalLink.observation_signal_id).where(
        RiskSignalLink.risk_finding_id == finding.id
    )
    conds = [
        ObservationSignal.enterprise_id == finding.enterprise_id,
        ObservationSignal.signal_type.in_(signal_types),
        ObservationSignal.id.not_in(already_linked),
    ]
    if item_id is not None:
        conds.append(ObservationSignal.item_id == item_id)
    if supplier_id is not None:
        conds.append(ObservationSignal.supplier_id == supplier_id)

    new_signal_ids = (
        (await session.execute(select(ObservationSignal.id).where(*conds))).scalars().all()
    )
    if not new_signal_ids:
        return
    for signal_id in new_signal_ids:
        session.add(RiskSignalLink(risk_finding_id=finding.id, observation_signal_id=signal_id))
    await session.flush()


async def _latest_lead_time_days(session: AsyncSession, item_id: uuid.UUID) -> float | None:
    return (
        await session.execute(
            select(LeadTime.days)
            .where(LeadTime.item_id == item_id, LeadTime.observability != "lost")
            .order_by(LeadTime.as_of.desc().nulls_last(), LeadTime.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _production_demands(
    session: AsyncSession, item_id: uuid.UUID
) -> list[ProductionDemandSnapshot]:
    rows = (
        (
            await session.execute(
                select(ProductionDemand).where(
                    ProductionDemand.item_id == item_id,
                    ProductionDemand.observability != "lost",
                    ProductionDemand.need_by.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        ProductionDemandSnapshot(item_id=r.item_id, quantity=r.quantity, need_by=r.need_by)
        for r in rows
        if r.need_by is not None
    ]


async def _supplier_delay_samples(
    session: AsyncSession, enterprise_id: uuid.UUID
) -> list[tuple[uuid.UUID, list[Decimal]]]:
    rows = (
        (
            await session.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.enterprise_id == enterprise_id,
                    PurchaseOrder.status == "received",
                    PurchaseOrder.observability != "lost",
                    PurchaseOrder.supplier_id.is_not(None),
                    PurchaseOrder.expected_at.is_not(None),
                    PurchaseOrder.received_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_supplier: dict[uuid.UUID, list[Decimal]] = {}
    for r in rows:
        assert r.supplier_id is not None and r.expected_at is not None and r.received_at is not None
        by_supplier.setdefault(r.supplier_id, []).append(
            Decimal((r.received_at - r.expected_at).days)
        )
    return list(by_supplier.items())


async def _quality_samples(
    session: AsyncSession, enterprise_id: uuid.UUID
) -> list[tuple[tuple[uuid.UUID, uuid.UUID], list[Decimal]]]:
    rows = (
        (
            await session.execute(
                select(QualityRecord).where(
                    QualityRecord.enterprise_id == enterprise_id,
                    QualityRecord.observability != "lost",
                    QualityRecord.supplier_id.is_not(None),
                    QualityRecord.defect_rate.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_pair: dict[tuple[uuid.UUID, uuid.UUID], list[Decimal]] = {}
    for r in rows:
        assert r.supplier_id is not None and r.defect_rate is not None
        by_pair.setdefault((r.item_id, r.supplier_id), []).append(Decimal(str(r.defect_rate)))
    return list(by_pair.items())
