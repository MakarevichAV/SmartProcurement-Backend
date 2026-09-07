"""Append-only audit record (T026; data-model.md §9).

Superset of the LORM SPEC §10.3 minimum field set plus spec FR-060/FR-061. INSERT/SELECT
only — mutation is blocked by the ``forbid_mutation()`` DB trigger (T027) and by the
``AppendOnly`` marker.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import AppendOnly, EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

EVENT_TYPES = (
    "recommendation",
    "l4_prepared",
    "l4_approved",
    "l4_rejected",
    "l5_authorized",
    "dispatched",
    "execution_result",
    "verification",
    "promotion",
    "demotion",
    "recovery",
    "ai_unavailable",
    "policy_approved",
    "policy_revoked",
    "mapping_confirmed",
    "enforcement",
)
VERIFIED_STATES = ("verified", "failed", "unverifiable", "pending", "n/a")


class AuditRecord(UUIDPrimaryKey, Timestamps, EnterpriseScoped, AppendOnly, Base):
    __tablename__ = "audit_record"

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    capability_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    capability_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    lorm_level: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = enum_column("event_type", *EVENT_TYPES)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    authorizer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    procurement_action_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    verified: Mapped[str] = enum_column("verified", *VERIFIED_STATES, default="n/a")
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, index=True
    )
