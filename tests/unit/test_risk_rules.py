"""Unit tests for the six deterministic risk rules (T060; data-model.md §5, FR-017/FR-019).

Pure, DB-free tests: each rule is a plain function over a small snapshot of already-computed
data (the pre-AI aggregation guard, research.md §7a) and returns a ``RiskCandidate`` or
``None``. No LLM, no session, no job queue — dedup/persistence (identity tuples approved for
Phase 4) is exercised separately in ``tests/integration/test_observation_loop.py`` once
``detect_risks`` assembles these candidates into ``risk_finding`` rows.

**Target interface this file specifies for T070** (``backend/src/app/analysis/rules.py`` does
not exist yet — every import below is expected to fail until it is written):

    AggregateSnapshot        -- mirrors the future `sku_aggregate` row (data-model.md §4)
    ProductionDemandSnapshot -- mirrors the future `production_demand` row (data-model.md §3)
    RiskCandidate            -- risk_type / item_id / supplier_id / severity / reason

    evaluate_likely_shortage(aggregate, *, lead_time_days) -> RiskCandidate | None
    evaluate_insufficient_until_next_delivery(aggregate, *, now) -> RiskCandidate | None
    evaluate_production_stop_risk(aggregate, demands, *, now) -> RiskCandidate | None
    evaluate_systematic_supplier_delay(supplier_id, delay_samples, *,
                                        threshold_days=3.0, min_samples=3) -> RiskCandidate | None
    evaluate_price_anomaly(aggregate, *, threshold_pct=10.0) -> RiskCandidate | None
    evaluate_quality_degradation(item_id, supplier_id, defect_samples, *,
                                  threshold_defect_rate=0.05) -> RiskCandidate | None

Identity carried by each candidate matches the approved Phase 4 dedup table
(tasks.md Phase 4 Note / data-model.md §5): ``likely_shortage``/``insufficient_until_next_
delivery``/``production_stop_risk``/``price_anomaly`` -> item_id only;
``systematic_supplier_delay`` -> supplier_id only; ``quality_degradation`` -> item_id +
supplier_id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.analysis.rules import (
    AggregateSnapshot,
    ProductionDemandSnapshot,
    evaluate_insufficient_until_next_delivery,
    evaluate_likely_shortage,
    evaluate_price_anomaly,
    evaluate_production_stop_risk,
    evaluate_quality_degradation,
    evaluate_systematic_supplier_delay,
)

_NOW = datetime(2026, 9, 22, tzinfo=UTC)


def _aggregate(**overrides: object) -> AggregateSnapshot:
    defaults: dict[str, object] = dict(
        item_id=uuid.uuid4(),
        warehouse_id=None,
        avg_daily_consumption=10.0,
        days_of_cover=30.0,
        next_expected_delivery_at=None,
        last_price=100.0,
        price_trend=0.0,
        avg_supplier_delay_days=0.0,
        computed_at=_NOW,
    )
    defaults.update(overrides)
    return AggregateSnapshot(**defaults)  # type: ignore[arg-type]


# --- likely_shortage -----------------------------------------------------------------------


def test_likely_shortage_fires_when_cover_below_lead_time() -> None:
    agg = _aggregate(days_of_cover=3.0)
    candidate = evaluate_likely_shortage(agg, lead_time_days=7.0)
    assert candidate is not None
    assert candidate.risk_type == "likely_shortage"
    assert candidate.item_id == agg.item_id
    assert candidate.supplier_id is None
    assert candidate.severity in ("low", "med", "high")


def test_likely_shortage_does_not_fire_when_cover_sufficient() -> None:
    agg = _aggregate(days_of_cover=20.0)
    assert evaluate_likely_shortage(agg, lead_time_days=7.0) is None


def test_likely_shortage_severity_rises_with_more_exceeded_margin() -> None:
    mild = evaluate_likely_shortage(_aggregate(days_of_cover=6.9), lead_time_days=7.0)
    severe = evaluate_likely_shortage(_aggregate(days_of_cover=0.5), lead_time_days=7.0)
    assert mild is not None and severe is not None
    severity_rank = {"low": 0, "med": 1, "high": 2}
    assert severity_rank[severe.severity] >= severity_rank[mild.severity]


# --- insufficient_until_next_delivery -------------------------------------------------------


def test_insufficient_until_next_delivery_fires_before_the_open_po_arrives() -> None:
    agg = _aggregate(days_of_cover=2.0, next_expected_delivery_at=_NOW + timedelta(days=10))
    candidate = evaluate_insufficient_until_next_delivery(agg, now=_NOW)
    assert candidate is not None
    assert candidate.risk_type == "insufficient_until_next_delivery"
    assert candidate.item_id == agg.item_id
    assert candidate.supplier_id is None


def test_insufficient_until_next_delivery_does_not_fire_when_delivery_unknown() -> None:
    agg = _aggregate(days_of_cover=1.0, next_expected_delivery_at=None)
    assert evaluate_insufficient_until_next_delivery(agg, now=_NOW) is None


def test_insufficient_until_next_delivery_does_not_fire_when_cover_outlasts_delivery() -> None:
    agg = _aggregate(days_of_cover=30.0, next_expected_delivery_at=_NOW + timedelta(days=10))
    assert evaluate_insufficient_until_next_delivery(agg, now=_NOW) is None


# --- production_stop_risk (production_demand is optional, FR-017 + Phase 4 decision) --------


def test_production_stop_risk_fires_when_demand_precedes_projected_cover() -> None:
    agg = _aggregate(days_of_cover=5.0)
    demands = [
        ProductionDemandSnapshot(
            item_id=agg.item_id, quantity=50, need_by=_NOW + timedelta(days=10)
        )
    ]
    candidate = evaluate_production_stop_risk(agg, demands, now=_NOW)
    assert candidate is not None
    assert candidate.risk_type == "production_stop_risk"
    assert candidate.item_id == agg.item_id
    assert candidate.supplier_id is None


def test_production_stop_risk_does_not_fire_without_production_demand() -> None:
    """Explicit case (Phase 4 decision #2): no applicable production_demand -> never fires.

    This MUST NOT be an error and MUST NOT manufacture a risk from absent data.
    """
    agg = _aggregate(days_of_cover=1.0)  # would otherwise look shortage-prone
    assert evaluate_production_stop_risk(agg, [], now=_NOW) is None


def test_production_stop_risk_does_not_fire_when_demand_is_comfortably_covered() -> None:
    agg = _aggregate(days_of_cover=30.0)
    demands = [
        ProductionDemandSnapshot(item_id=agg.item_id, quantity=50, need_by=_NOW + timedelta(days=5))
    ]
    assert evaluate_production_stop_risk(agg, demands, now=_NOW) is None


# --- systematic_supplier_delay (supplier-only identity, Phase 4 decision) -------------------


def test_systematic_supplier_delay_fires_on_a_recurring_pattern() -> None:
    supplier_id = uuid.uuid4()
    candidate = evaluate_systematic_supplier_delay(supplier_id, [4.0, 5.0, 6.0])
    assert candidate is not None
    assert candidate.risk_type == "systematic_supplier_delay"
    assert candidate.supplier_id == supplier_id
    assert candidate.item_id is None  # identity is supplier-only in v1


def test_systematic_supplier_delay_does_not_fire_below_min_samples() -> None:
    # High average delay but too few observations to call it "systematic".
    assert evaluate_systematic_supplier_delay(uuid.uuid4(), [10.0, 12.0]) is None


def test_systematic_supplier_delay_does_not_fire_when_mostly_on_time() -> None:
    assert evaluate_systematic_supplier_delay(uuid.uuid4(), [0.0, 1.0, 0.5, 1.0]) is None


# --- price_anomaly (item-only identity) ------------------------------------------------------


def test_price_anomaly_fires_on_a_large_increase() -> None:
    agg = _aggregate(price_trend=0.25)
    candidate = evaluate_price_anomaly(agg)
    assert candidate is not None
    assert candidate.risk_type == "price_anomaly"
    assert candidate.item_id == agg.item_id
    assert candidate.supplier_id is None


def test_price_anomaly_fires_on_a_large_decrease_too() -> None:
    agg = _aggregate(price_trend=-0.30)
    assert evaluate_price_anomaly(agg) is not None


def test_price_anomaly_does_not_fire_on_a_small_move() -> None:
    agg = _aggregate(price_trend=0.03)
    assert evaluate_price_anomaly(agg) is None


# --- quality_degradation (item + supplier identity, Phase 4 decision) -----------------------


def test_quality_degradation_fires_on_elevated_defect_rate() -> None:
    item_id, supplier_id = uuid.uuid4(), uuid.uuid4()
    candidate = evaluate_quality_degradation(item_id, supplier_id, [0.08, 0.09, 0.1])
    assert candidate is not None
    assert candidate.risk_type == "quality_degradation"
    assert candidate.item_id == item_id
    assert candidate.supplier_id == supplier_id


def test_quality_degradation_does_not_fire_within_tolerance() -> None:
    assert evaluate_quality_degradation(uuid.uuid4(), uuid.uuid4(), [0.01, 0.02]) is None


def test_quality_degradation_does_not_fire_without_samples() -> None:
    assert evaluate_quality_degradation(uuid.uuid4(), uuid.uuid4(), []) is None
