"""Observability-gap model (T028; data-model.md §4).

Records a lost/degraded input for a source, a domain entity, or a capability. Mutable
lifecycle: ``closed_at`` is set when the gap clears. Feeds demotion (FR-015/FR-016a; Phase 9).
Signal / aggregate models arrive with US2 (Phase 4).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

GAP_SCOPES = ("source", "entity", "capability")
GAP_REASONS = ("source_unavailable", "stale_data", "ai_unavailable")


class ObservabilityGap(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "observability_gap"

    scope: Mapped[str] = enum_column("scope", *GAP_SCOPES)
    scope_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    reason: Mapped[str] = enum_column("reason", *GAP_REASONS)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
