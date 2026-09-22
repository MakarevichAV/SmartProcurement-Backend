"""Source synchronisation — the L0 data path (T053) + signal diffing (T066).

``sync_source`` fetches a batch from the connector, maps each raw record through the
data source's **confirmed** ``field_mapping`` rows (FR-004/FR-005), and upserts canonical
``domain/`` rows, each stamped with ``source_provenance`` and ``observability='fresh'``
(FR-007/FR-013).

For a fixed set of "signal-worthy" attributes (`_SIGNAL_WATCH`), an **update** that actually
changes the value emits an append-only ``observation_signal`` row (FR-014) — the first sync
that creates a row is a baseline, not a signal. If any signals were produced, a
``recompute_aggregates`` job is enqueued in the same transaction (data-model.md §10), scoped
to the touched items (Phase 4 decision: simple, scoped recompute — no dirty-tracking table).

Out of scope for v1 (deliberate simplification, not a spec requirement): ``purchase_order``
changes are not diffed into signals here — a `supplier_delay` signal needs lateness
computation (actual vs. expected), not a plain value diff, and none of the Phase 4 risk rules
need a pre-materialized `supplier_delay` signal type (T070 reads `purchase_order` history
directly for `systematic_supplier_delay`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UpstreamError
from app.domain.models import (
    Consumption,
    Item,
    ItemSupplier,
    LeadTime,
    Price,
    ProductionDemand,
    PurchaseOrder,
    QualityRecord,
    StockLevel,
    Supplier,
    Warehouse,
)
from app.integration.connectors.base import ConnectorError
from app.integration.connectors.registry import build_connector
from app.integration.mapping_service import confirmed_mappings
from app.integration.models import DataSource, FieldMapping
from app.integration.service import load_credential
from app.jobs import queue as job_queue
from app.observation.models import ObservationSignal
from app.observation.observability import ObservabilityService

# entity -> (watched attribute, signal_type emitted when it changes on an UPDATE)
_SIGNAL_WATCH: dict[str, tuple[str, str]] = {
    "stock_level": ("quantity", "stock_change"),
    "price": ("unit_price", "price_change"),
    "lead_time": ("days", "lead_time_change"),
    "quality_record": ("defect_rate", "quality_issue"),
    "production_demand": ("quantity", "demand_change"),
}


@dataclass
class EntitySpec:
    model: type[Any]  # ORM class; columns are accessed dynamically
    identity: list[str]  # column names (post ref-resolution) forming the upsert key
    refs: dict[str, tuple[str, str]] = field(default_factory=dict)  # in_attr -> (entity, nat_key)


# Masters first so dependents can resolve their foreign keys.
_ENTITY_ORDER = (
    "item",
    "warehouse",
    "supplier",
    "item_supplier",
    "stock_level",
    "price",
    "lead_time",
    "consumption",
    "production_demand",
    "quality_record",
    "purchase_order",
)

_ITEM_REF = {"item_sku": ("item", "sku")}
_SUP_REF = {"supplier_code": ("supplier", "code")}
_WH_REF = {"warehouse_code": ("warehouse", "code")}

_SPECS: dict[str, EntitySpec] = {
    "item": EntitySpec(Item, ["sku"]),
    "warehouse": EntitySpec(Warehouse, ["code"]),
    "supplier": EntitySpec(Supplier, ["code"]),
    "item_supplier": EntitySpec(
        ItemSupplier, ["item_id", "supplier_id"], {**_ITEM_REF, **_SUP_REF}
    ),
    "stock_level": EntitySpec(StockLevel, ["item_id", "warehouse_id"], {**_ITEM_REF, **_WH_REF}),
    "price": EntitySpec(Price, ["item_id", "supplier_id"], {**_ITEM_REF, **_SUP_REF}),
    "lead_time": EntitySpec(LeadTime, ["item_id", "supplier_id"], {**_ITEM_REF, **_SUP_REF}),
    "consumption": EntitySpec(Consumption, ["item_id", "warehouse_id"], {**_ITEM_REF, **_WH_REF}),
    "production_demand": EntitySpec(ProductionDemand, ["item_id", "need_by"], {**_ITEM_REF}),
    "quality_record": EntitySpec(
        QualityRecord, ["item_id", "supplier_id", "as_of"], {**_ITEM_REF, **_SUP_REF}
    ),
    "purchase_order": EntitySpec(PurchaseOrder, ["external_ref"], {**_ITEM_REF, **_SUP_REF}),
}

_MANAGED_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "enterprise_id",
    "source_provenance",
    "observability",
}


@dataclass
class SyncResult:
    fetched_at: datetime
    records_seen: int = 0
    upserts: dict[str, int] = field(default_factory=dict)
    signals_created: int = 0


async def sync_source(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    since: datetime | None = None,
) -> SyncResult:
    ds = (
        await session.execute(
            select(DataSource).where(
                DataSource.id == data_source_id, DataSource.enterprise_id == enterprise_id
            )
        )
    ).scalar_one_or_none()
    if ds is None:
        raise UpstreamError("data source not found")

    credential = await load_credential(session, ds)
    obs = ObservabilityService(session)
    try:
        connector = build_connector(ds.connector_type, dict(ds.config), credential)
        batch = await connector.fetch(since)
    except ConnectorError as exc:
        ds.health = "unavailable"
        ds.last_error = str(exc)
        ds.last_check_at = datetime.now(UTC)
        await obs.open(
            enterprise_id=enterprise_id,
            scope="source",
            scope_ref=str(ds.id),
            reason="source_unavailable",
        )
        await session.flush()
        raise UpstreamError(f"sync failed for data source {ds.name!r}: {exc}") from exc

    grouped: dict[str, list[FieldMapping]] = {}
    for m in await confirmed_mappings(session, ds.id):
        grouped.setdefault(m.canonical_entity, []).append(m)

    result = SyncResult(fetched_at=batch.fetched_at, records_seen=len(batch.records))
    natural_cache: dict[tuple[str, str], uuid.UUID] = {}
    touched_item_ids: set[uuid.UUID] = set()

    for entity in _ENTITY_ORDER:
        mappings = grouped.get(entity)
        if not mappings:
            continue
        spec = _SPECS[entity]
        col_types = _column_types(spec.model)
        paths = sorted({m.source_field_path for m in mappings})
        for record in batch.records:
            attrs = _extract(record, mappings)
            if not attrs:
                continue
            resolved = await _resolve_refs(session, enterprise_id, spec, attrs, natural_cache)
            if resolved is None:
                continue
            if any(
                k not in resolved or resolved[k] in (None, "") for k in _required_identity(spec)
            ):
                continue
            provenance = {
                "data_source_id": str(ds.id),
                "source_field_path": ",".join(paths),
                "fetched_at": batch.fetched_at.isoformat(),
            }
            created, signal = await _upsert(
                session,
                enterprise_id,
                entity,
                spec,
                col_types,
                resolved,
                provenance,
                data_source_id=ds.id,
                observed_at=batch.fetched_at,
            )
            result.upserts[entity] = result.upserts.get(entity, 0) + (1 if created else 0)
            if signal is not None:
                result.signals_created += 1
                if signal.item_id is not None:
                    touched_item_ids.add(signal.item_id)

    ds.health = "available"
    ds.last_error = None
    ds.last_success_at = batch.fetched_at
    ds.last_check_at = datetime.now(UTC)
    await obs.close(
        enterprise_id=enterprise_id,
        scope="source",
        scope_ref=str(ds.id),
        reason="source_unavailable",
    )

    if result.signals_created:
        await job_queue.enqueue(
            session,
            kind="recompute_aggregates",
            payload={
                "enterprise_id": str(enterprise_id),
                "item_ids": [str(i) for i in touched_item_ids],
            },
            enterprise_id=enterprise_id,
        )

    await session.flush()
    return result


# -- normalisation helpers ---------------------------------------------------


def _column_types(model: type[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for col in sa_inspect(model).columns:
        if col.key not in _MANAGED_COLUMNS:
            result[col.key] = col.type
    return result


def _required_identity(spec: EntitySpec) -> list[str]:
    # identity columns that must be non-null (nullable FK identity cols may legitimately be None)
    optional = {"warehouse_id"}
    return [c for c in spec.identity if c not in optional]


def _extract(record: dict[str, Any], mappings: list[FieldMapping]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in mappings:
        if m.source_field_path not in record:
            continue
        value = record[m.source_field_path]
        if isinstance(m.transform, dict):
            value = _apply_transform(value, m.transform)
        if value in (None, ""):
            continue
        out[m.canonical_attribute] = value
    return out


def _apply_transform(value: Any, transform: dict[str, Any]) -> Any:
    if value is None:
        return None
    text = str(value)
    if transform.get("trim"):
        text = text.strip()
    if transform.get("upper"):
        text = text.upper()
    if transform.get("lower"):
        text = text.lower()
    mult = transform.get("multiply")
    if mult is not None:
        try:
            return Decimal(text) * Decimal(str(mult))
        except (InvalidOperation, ValueError):
            return text
    return text


async def _resolve_refs(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    spec: EntitySpec,
    attrs: dict[str, Any],
    cache: dict[tuple[str, str], uuid.UUID],
) -> dict[str, Any] | None:
    resolved = {k: v for k, v in attrs.items() if k not in spec.refs}
    for in_attr, (target_entity, nat_key) in spec.refs.items():
        raw = attrs.get(in_attr)
        fk_col = f"{target_entity}_id" if target_entity != "warehouse" else "warehouse_id"
        if raw in (None, ""):
            continue
        key = (target_entity, str(raw))
        found = cache.get(key)
        if found is None:
            target_model = _SPECS[target_entity].model
            found = (
                await session.execute(
                    select(target_model.id).where(
                        target_model.enterprise_id == enterprise_id,
                        getattr(target_model, nat_key) == str(raw),
                    )
                )
            ).scalar_one_or_none()
            if found is None:
                # a dependent row referencing an unknown master — skip it this cycle
                if fk_col in _required_identity(spec):
                    return None
                continue
            cache[key] = found
        resolved[fk_col] = found
    return resolved


async def _upsert(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    entity: str,
    spec: EntitySpec,
    col_types: dict[str, Any],
    resolved: dict[str, Any],
    provenance: dict[str, Any],
    *,
    data_source_id: uuid.UUID,
    observed_at: datetime,
) -> tuple[bool, ObservationSignal | None]:
    coerced: dict[str, Any] = {}
    for key, value in resolved.items():
        if key.endswith("_id"):
            coerced[key] = value
        elif key in col_types:
            coerced[key] = _coerce(value, col_types[key])
    for id_col in spec.identity:
        coerced.setdefault(id_col, None)

    conds = [spec.model.enterprise_id == enterprise_id]
    for id_col in spec.identity:
        col = getattr(spec.model, id_col)
        val = coerced.get(id_col)
        conds.append(col.is_(None) if val is None else col == val)

    existing = (await session.execute(select(spec.model).where(*conds))).scalar_one_or_none()
    if existing is None:
        row = spec.model(enterprise_id=enterprise_id, **coerced)
        row.source_provenance = provenance
        row.observability = "fresh"
        session.add(row)
        await session.flush()
        return True, None  # a first-seen row is a baseline, not a signal (FR-014)

    signal = await _diff_signal(
        session,
        enterprise_id,
        entity,
        existing,
        coerced,
        resolved,
        data_source_id=data_source_id,
        observed_at=observed_at,
    )

    for key, value in coerced.items():
        if key in spec.identity:
            continue
        setattr(existing, key, value)
    existing.source_provenance = provenance
    existing.observability = "fresh"
    await session.flush()
    return True, signal


async def _diff_signal(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    entity: str,
    existing: Any,
    coerced: dict[str, Any],
    resolved: dict[str, Any],
    *,
    data_source_id: uuid.UUID,
    observed_at: datetime,
) -> ObservationSignal | None:
    """An UPDATE that actually changes a watched attribute emits one signal (FR-014)."""
    watch = _SIGNAL_WATCH.get(entity)
    if watch is None:
        return None
    attr, signal_type = watch
    if attr not in coerced:
        return None  # this attribute wasn't part of the incoming record at all
    old_value, new_value = getattr(existing, attr, None), coerced[attr]
    if old_value == new_value:
        return None

    signal = ObservationSignal(
        enterprise_id=enterprise_id,
        data_source_id=data_source_id,
        signal_type=signal_type,
        item_id=resolved.get("item_id"),
        supplier_id=resolved.get("supplier_id"),
        payload={
            "entity": entity,
            "attribute": attr,
            "old": str(old_value) if old_value is not None else None,
            "new": str(new_value) if new_value is not None else None,
        },
        observed_at=observed_at,
        ingested_at=datetime.now(UTC),
    )
    session.add(signal)
    await session.flush()
    return signal


def _coerce(value: Any, col_type: Any) -> Any:
    py = getattr(col_type, "python_type", str)
    try:
        if py in (int, float) or py.__name__ == "Decimal":
            return Decimal(str(value))
        if py is bool:
            return str(value).strip().lower() in ("1", "true", "yes", "y", "t")
        if py is date:
            return _parse_date(str(value))
        if py is datetime:
            return _parse_datetime(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return str(value)


def _parse_date(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _parse_datetime(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        d = _parse_date(text)
        return datetime(d.year, d.month, d.day, tzinfo=UTC) if d else None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
