"""Analysis models — L2 (T069; data-model.md §5, FR-017/FR-018/FR-019).

``RiskFinding`` represents an *ongoing business condition*, not one row per observation cycle
— see the Identity / dedup rules in data-model.md §5 and the Phase 4 planning note in
tasks.md. ``RiskSignalLink`` is the evidence trail (I-1). ``Explanation`` is attached to either
a ``risk_finding`` or a (future, US3) ``recommendation`` via a polymorphic
``subject_type``/``subject_id`` pair — there is deliberately no single FK, since the subject
table varies.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

RISK_TYPES = (
    "likely_shortage",
    "insufficient_until_next_delivery",
    "production_stop_risk",
    "systematic_supplier_delay",
    "quality_degradation",
    "price_anomaly",
)
SEVERITIES = ("low", "med", "high")
RISK_STATUSES = ("open", "recommended", "actioned", "resolved", "dismissed")
TERMINAL_RISK_STATUSES = ("resolved", "dismissed")
DETECTED_BY = ("rule",)
AI_STATUSES = ("pending", "ready", "unavailable")

EXPLANATION_SUBJECT_TYPES = ("risk_finding", "recommendation")
GENERATED_BY = ("ai", "rule")


class RiskFinding(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "risk_finding"

    risk_type: Mapped[str] = enum_column("risk_type", *RISK_TYPES)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("supplier.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    severity: Mapped[str] = enum_column("severity", *SEVERITIES)
    status: Mapped[str] = enum_column("status", *RISK_STATUSES, default="open")
    detected_by: Mapped[str] = enum_column("detected_by", *DETECTED_BY, default="rule")
    ai_status: Mapped[str] = enum_column("ai_status", *AI_STATUSES, default="pending")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Dismissal (Phase 4 decision): persisted directly on risk_finding, no separate table.
    dismissed_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class RiskSignalLink(UUIDPrimaryKey, Timestamps, Base):
    """Evidence: which observation_signal row(s) support a risk_finding (I-1)."""

    __tablename__ = "risk_signal_link"

    risk_finding_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("risk_finding.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_signal_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("observation_signal.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class Explanation(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    """What/why/data-used/factors/confidence for a risk_finding or recommendation.

    (I-2, I-3, FR-018/FR-070). Polymorphic: ``subject_type`` + ``subject_id`` instead of a
    single FK, since the subject table depends on ``subject_type``.
    """

    __tablename__ = "explanation"

    subject_type: Mapped[str] = enum_column("subject_type", *EXPLANATION_SUBJECT_TYPES)
    subject_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    what: Mapped[str] = mapped_column(String, nullable=False)
    why: Mapped[str] = mapped_column(String, nullable=False)
    data_used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    generated_by: Mapped[str] = enum_column("generated_by", *GENERATED_BY, default="ai")
    llm_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String, nullable=True)
