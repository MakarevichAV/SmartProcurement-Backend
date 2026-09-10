"""Canonical procurement domain — the L0 map (T045; data-model.md §3).

These are the entities Smart Procurement reasons about, normalized away from any specific
ERP (Constitution IX). Every row carries:

* ``source_provenance`` — ``{data_source_id, source_field_path, fetched_at}`` so the L0 map
  can show where a fact came from (FR-010);
* ``observability`` — ``fresh`` | ``stale`` | ``lost`` so the map can show gaps (FR-011).

**v1 has no vector columns and does not require ``pgvector``** — unstructured text
(``supplier.notes``, ``quality_record.note``) is plain ``text`` only (research.md §13).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

OBSERVABILITY_STATES = ("fresh", "stale", "lost")
PO_STATUSES = ("open", "received", "cancelled")
PO_ORIGINS = ("external", "smart_procurement")

_QTY = Numeric(18, 4)


class Provenanced:
    """Adds ``source_provenance`` + ``observability`` to a canonical domain row."""

    source_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observability: Mapped[str] = enum_column(
        "observability", *OBSERVABILITY_STATES, default="fresh"
    )


def _item_fk() -> Any:
    return mapped_column(
        PgUUID(as_uuid=True), ForeignKey("item.id", ondelete="CASCADE"), nullable=False, index=True
    )


def _supplier_fk(nullable: bool = False) -> Any:
    return mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("supplier.id", ondelete="CASCADE"),
        nullable=nullable,
        index=True,
    )


def _warehouse_fk(nullable: bool = False) -> Any:
    return mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("warehouse.id", ondelete="CASCADE"),
        nullable=nullable,
        index=True,
    )


class Item(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "item"
    __table_args__ = (UniqueConstraint("enterprise_id", "sku", name="uq_item_enterprise_sku"),)

    sku: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Warehouse(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "warehouse"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "code", name="uq_warehouse_enterprise_code"),
    )

    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    location: Mapped[str | None] = mapped_column(String, nullable=True)


class Supplier(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "supplier"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "code", name="uq_supplier_enterprise_code"),
    )

    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")


class StockLevel(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "stock_level"

    item_id: Mapped[uuid.UUID] = _item_fk()
    warehouse_id: Mapped[uuid.UUID | None] = _warehouse_fk(nullable=True)
    quantity: Mapped[float] = mapped_column(_QTY, nullable=False, default=0)
    min_quantity: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ItemSupplier(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "item_supplier"

    item_id: Mapped[uuid.UUID] = _item_fk()
    supplier_id: Mapped[uuid.UUID] = _supplier_fk()
    preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Price(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "price"

    item_id: Mapped[uuid.UUID] = _item_fk()
    supplier_id: Mapped[uuid.UUID] = _supplier_fk()
    unit_price: Mapped[float] = mapped_column(_QTY, nullable=False, default=0)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class LeadTime(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "lead_time"

    item_id: Mapped[uuid.UUID] = _item_fk()
    supplier_id: Mapped[uuid.UUID] = _supplier_fk()
    days: Mapped[float] = mapped_column(_QTY, nullable=False, default=0)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PurchaseOrder(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "purchase_order"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "external_ref", name="uq_po_enterprise_external_ref"),
    )

    external_ref: Mapped[str] = mapped_column(String, nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_id: Mapped[uuid.UUID | None] = _supplier_fk(nullable=True)
    quantity: Mapped[float] = mapped_column(_QTY, nullable=False, default=0)
    unit_price: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    status: Mapped[str] = enum_column("status", *PO_STATUSES, default="open")
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_quantity: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    origin: Mapped[str] = enum_column("origin", *PO_ORIGINS, default="external")
    # FK to procurement_action is added in US3 when that table exists (data-model.md §3/§6).
    procurement_action_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )


class Consumption(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "consumption"

    item_id: Mapped[uuid.UUID] = _item_fk()
    warehouse_id: Mapped[uuid.UUID | None] = _warehouse_fk(nullable=True)
    quantity: Mapped[float] = mapped_column(_QTY, nullable=False, default=0)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)


class ProductionDemand(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "production_demand"

    item_id: Mapped[uuid.UUID] = _item_fk()
    quantity: Mapped[float] = mapped_column(_QTY, nullable=False, default=0)
    need_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)


class QualityRecord(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Provenanced, Base):
    __tablename__ = "quality_record"

    item_id: Mapped[uuid.UUID] = _item_fk()
    supplier_id: Mapped[uuid.UUID | None] = _supplier_fk(nullable=True)
    defect_rate: Mapped[float | None] = mapped_column(_QTY, nullable=True)
    note: Mapped[str] = mapped_column(String, nullable=False, default="")
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# entity key -> ORM class, for the domain-map read service and sync upsert.
# ``type[Any]`` (not ``type[Base]``) so callers can access mapped columns dynamically.
ENTITY_MODELS: dict[str, type[Any]] = {
    "item": Item,
    "warehouse": Warehouse,
    "supplier": Supplier,
    "stock_level": StockLevel,
    "item_supplier": ItemSupplier,
    "price": Price,
    "lead_time": LeadTime,
    "purchase_order": PurchaseOrder,
    "consumption": Consumption,
    "production_demand": ProductionDemand,
    "quality_record": QualityRecord,
}
