"""Observation models (T028 ObservabilityGap; T065 ObservationSignal + SkuAggregate).

data-model.md §4. ``ObservationSignal`` is append-only evidence of a meaningful canonical
change (FR-014); ``SkuAggregate`` is the deterministic, pre-AI rolling-aggregate row consumed
by risk detection (research.md §7a). **v1 decision (Phase 4)**: ``observation_signal`` is a
normal indexed table, not physically partitioned — see data-model.md §4 for the full rationale.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

GAP_SCOPES = ("source", "entity", "capability")
GAP_REASONS = ("source_unavailable", "stale_data", "ai_unavailable")

SIGNAL_TYPES = (
    "stock_change",
    "consumption_rate",
    "reorder_point_near",
    "demand_change",
    "supplier_delay",
    "lead_time_change",
    "price_change",
    "quality_issue",
    "other",
)

_QTY = Numeric(18, 4)


class ObservabilityGap(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "observability_gap"

    scope: Mapped[str] = enum_column("scope", *GAP_SCOPES)
    scope_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    reason: Mapped[str] = enum_column("reason", *GAP_REASONS)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ObservationSignal(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    """Append-only evidence of a meaningful canonical-data change (T066, FR-014).

    A normal indexed table in v1 — **not** physically partitioned; see the module docstring
    and data-model.md §4 for the rationale. Marked append-only in its migration via
    ``attach_append_only`` (T027 pattern); the app layer never updates or deletes a row.
    """

    __tablename__ = "observation_signal"
    __table_args__ = (
        Index(
            "ix_observation_signal_enterprise_item_observed",
            "enterprise_id",
            "item_id",
            "observed_at",
        ),
        Index(
            "ix_observation_signal_enterprise_supplier_observed",
            "enterprise_id",
            "supplier_id",
            "observed_at",
        ),
        Index(
            "ix_observation_signal_enterprise_type_observed",
            "enterprise_id",
            "signal_type",
            "observed_at",
        ),
    )

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("data_source.id", ondelete="RESTRICT"), nullable=False
    )
    signal_type: Mapped[str] = enum_column("signal_type", *SIGNAL_TYPES)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("supplier.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkuAggregate(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    """Deterministic per-item[/warehouse] rolling aggregate (T067; research.md §7a).

    Upserted by ``recompute_aggregates``; never touched by an LLM call. Numeric fields are
    nullable — ``None`` means "not enough trustworthy domain data to compute this", which is
    deliberately distinct from a confident ``0``.
    """

    __tablename__ = "sku_aggregate"
    __table_args__ = (
        UniqueConstraint("item_id", "warehouse_id", name="uq_sku_aggregate_item_warehouse"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("warehouse.id", ondelete="CASCADE"), nullable=True
    )
    avg_daily_consumption: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    days_of_cover: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    next_expected_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_price: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    price_trend: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    avg_supplier_delay_days: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
