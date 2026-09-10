"""Data-source, schema-discovery and field-mapping models (T044; data-model.md §2).

A ``data_source`` is a swappable connection to enterprise data. ``describe_schema()``
persists ``source_field`` rows; AI proposes ``field_mapping`` rows (``status=suggested``)
that a human confirms. Only ``confirmed`` mappings feed sync (FR-004/FR-005). Every mapping
lifecycle transition is recorded in the append-only ``mapping_change_event`` (T027).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import AppendOnly, EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

SOURCE_KINDS = ("erp", "mes", "wms", "db", "api", "file", "other")
CONNECTOR_TYPES = ("rest", "file", "sql")
SOURCE_HEALTH = ("available", "unavailable", "stale")

CANONICAL_ENTITIES = (
    "item",
    "warehouse",
    "stock_level",
    "supplier",
    "item_supplier",
    "price",
    "lead_time",
    "purchase_order",
    "consumption",
    "production_demand",
    "quality_record",
)

MAPPING_STATUSES = ("suggested", "confirmed", "rejected", "retired")
MAPPING_ACTIONS = ("suggested", "confirmed", "edited", "rejected", "retired")

DEFAULT_OBSERVATION_INTERVAL_SECONDS = 900


class DataSource(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "data_source"

    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = enum_column("kind", *SOURCE_KINDS)
    connector_type: Mapped[str] = enum_column("connector_type", *CONNECTOR_TYPES)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    credential_ref: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("secret.id", ondelete="SET NULL"), nullable=True
    )
    observation_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_OBSERVATION_INTERVAL_SECONDS
    )
    health: Mapped[str] = enum_column("health", *SOURCE_HEALTH, default="unavailable")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)


class SourceField(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "source_field"
    __table_args__ = (
        UniqueConstraint("data_source_id", "path", name="uq_source_field_source_path"),
    )

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("data_source.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    inferred_type: Mapped[str] = mapped_column(String, nullable=False, default="string")
    sample_values: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class FieldMapping(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "field_mapping"

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("data_source.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_field_path: Mapped[str] = mapped_column(String, nullable=False)
    canonical_entity: Mapped[str] = enum_column("canonical_entity", *CANONICAL_ENTITIES)
    canonical_attribute: Mapped[str] = mapped_column(String, nullable=False)
    transform: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = enum_column("status", *MAPPING_STATUSES, default="suggested")
    ai_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MappingChangeEvent(UUIDPrimaryKey, Timestamps, AppendOnly, Base):
    __tablename__ = "mapping_change_event"

    field_mapping_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("field_mapping.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = enum_column("action", *MAPPING_ACTIONS)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
