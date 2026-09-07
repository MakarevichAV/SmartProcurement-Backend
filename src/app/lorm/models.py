"""LORM capability, level-history and promotion-request models (T020, T022; data-model.md §7).

``capability.level`` is raised only by ``capability_service`` (human, one step) and lowered
only by ``demotion`` logic (automatic, one step; Phase 9). ``capability_level_event`` is
append-only and DB-trigger-protected (T027).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import AppendOnly, EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")

CAPABILITY_KEYS = (
    "proc.inventory.observe",
    "proc.demand.observe",
    "proc.risk.diagnose",
    "proc.order.recommend",
    "proc.po.create",
    "proc.replenish.routine",
    "proc.supplier.add",
    "proc.payment.release",
)

LEVEL_CHANGE_DIRECTIONS = ("promotion", "demotion")
LEVEL_CHANGE_TRIGGERS = (
    "human",
    "incident",
    "rollback",
    "verification_failure",
    "observability_loss",
    "uncertainty",
    "policy_expiry",
    "policy_revocation",
)
PROMOTION_ORIGINS = ("human", "ai")
PROMOTION_STATUSES = ("pending", "approved", "rejected")


class Capability(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "capability"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "key", name="uq_capability_enterprise_key"),
    )

    key: Mapped[str] = enum_column("key", *CAPABILITY_KEYS)
    level: Mapped[str] = enum_column("level", *LEVELS)
    l5_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verification_tolerances: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"price_pct": 10, "qty_short_pct": 10, "late_days": 3},
    )
    uncertainty_threshold: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.3)


class CapabilityLevelEvent(UUIDPrimaryKey, Timestamps, AppendOnly, Base):
    __tablename__ = "capability_level_event"

    capability_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = enum_column("direction", *LEVEL_CHANGE_DIRECTIONS)
    from_level: Mapped[str] = enum_column("from_level", *LEVELS)
    to_level: Mapped[str] = enum_column("to_level", *LEVELS)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    trigger: Mapped[str] = enum_column("trigger", *LEVEL_CHANGE_TRIGGERS)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromotionRequest(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "promotion_request"

    capability_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("capability.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposed_to_level: Mapped[str] = enum_column("proposed_to_level", *LEVELS)
    rationale: Mapped[str] = mapped_column(String, nullable=False, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    origin: Mapped[str] = enum_column("origin", *PROMOTION_ORIGINS)
    status: Mapped[str] = enum_column("status", *PROMOTION_STATUSES, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
