"""L0 Domain Map read service (T054; FR-008..FR-011).

Answers "what exists, where did it come from, is the source still observable" — nothing more.
This is L0: no analysis, no risk, no recommendation.
"""

from __future__ import annotations

import contextlib
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.pagination import paginate
from app.domain.models import ENTITY_MODELS
from app.integration import service as integration_service

# Domain masters that carry a human-readable identifier and are worth resolving FKs to.
_LABELABLE = ("item", "warehouse", "supplier")

# Static L0 relationship graph (structural, not analytical).
_RELATIONSHIPS = [
    {"from": "item", "to": "supplier", "via": "item_supplier", "kind": "approved-supplier"},
    {"from": "stock_level", "to": "item", "via": "item_id", "kind": "belongs-to"},
    {"from": "stock_level", "to": "warehouse", "via": "warehouse_id", "kind": "located-in"},
    {"from": "price", "to": "item", "via": "item_id", "kind": "priced"},
    {"from": "price", "to": "supplier", "via": "supplier_id", "kind": "quoted-by"},
    {"from": "lead_time", "to": "supplier", "via": "supplier_id", "kind": "quoted-by"},
    {"from": "purchase_order", "to": "item", "via": "item_id", "kind": "orders"},
    {"from": "purchase_order", "to": "supplier", "via": "supplier_id", "kind": "ordered-from"},
    {"from": "consumption", "to": "item", "via": "item_id", "kind": "consumes"},
    {"from": "production_demand", "to": "item", "via": "item_id", "kind": "requires"},
    {"from": "quality_record", "to": "supplier", "via": "supplier_id", "kind": "rates"},
]


def _serialize(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in sa_inspect(row.__class__).columns:
        value = getattr(row, col.key)
        if isinstance(value, uuid.UUID):
            out[col.key] = str(value)
        elif isinstance(value, datetime | date):
            out[col.key] = value.isoformat()
        elif isinstance(value, Decimal):
            out[col.key] = float(value)
        else:
            out[col.key] = value
    return out


def _resolve_model(entity: str) -> type[Any]:
    model = ENTITY_MODELS.get(entity.replace("-", "_"))
    if model is None:
        raise NotFoundError(f"unknown domain entity {entity!r}")
    return model


def _referenceable_fk_columns(model: type[Any]) -> dict[str, str]:
    """``{fk_column: target_entity}`` for FKs that point at a labelable master.

    Derived from SQLAlchemy metadata — no per-entity hard-coding.
    """
    out: dict[str, str] = {}
    for col in sa_inspect(model).columns:
        for fk in col.foreign_keys:
            target = fk.column.table.name
            if target in _LABELABLE:
                out[col.key] = target
    return out


def _reference_payload(entity: str, obj: Any) -> dict[str, Any]:
    """A compact business identity for a referenced row: a display ``label`` plus the raw
    id and the natural-key fields (secondary/technical). Not a copy of the row's data."""
    if entity == "item":
        label = f"{obj.sku} — {obj.name}" if obj.name else obj.sku
        return {
            "entity": "item",
            "id": str(obj.id),
            "label": label,
            "sku": obj.sku,
            "name": obj.name,
        }
    # warehouse / supplier: code (— name)
    label = f"{obj.code} — {obj.name}" if obj.name else obj.code
    return {"entity": entity, "id": str(obj.id), "label": label, "code": obj.code, "name": obj.name}


async def _attach_references(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    model: type[Any],
    rows: list[Any],
    serialized: list[dict[str, Any]],
) -> None:
    fk_cols = _referenceable_fk_columns(model)
    if not fk_cols:
        return

    ids_by_entity: dict[str, set[uuid.UUID]] = defaultdict(set)
    for row in rows:
        for col, target in fk_cols.items():
            value = getattr(row, col)
            if value is not None:
                ids_by_entity[target].add(value)

    loaded: dict[str, dict[uuid.UUID, Any]] = {}
    for target, ids in ids_by_entity.items():
        target_model = ENTITY_MODELS[target]
        objs = (
            (
                await session.execute(
                    select(target_model).where(
                        target_model.enterprise_id == enterprise_id,
                        target_model.id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        loaded[target] = {obj.id: obj for obj in objs}

    for row, out in zip(rows, serialized, strict=True):
        refs: dict[str, Any] = {}
        for col, target in fk_cols.items():
            value = getattr(row, col)
            obj = loaded.get(target, {}).get(value) if value is not None else None
            if obj is not None:
                refs[col] = _reference_payload(target, obj)
        out["references"] = refs


async def _attach_provenance(
    session: AsyncSession, enterprise_id: uuid.UUID, serialized: list[dict[str, Any]]
) -> None:
    def _as_uuid(raw: Any) -> uuid.UUID | None:
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            return uuid.UUID(str(raw))
        return None

    wanted = {
        u
        for out in serialized
        if (u := _as_uuid((out.get("source_provenance") or {}).get("data_source_id"))) is not None
    }
    names = await integration_service.get_source_names(session, enterprise_id, wanted)

    for out in serialized:
        sp = out.get("source_provenance") or {}
        raw_id = sp.get("data_source_id")
        ds_uuid = _as_uuid(raw_id)
        fields = [
            f for f in (p.strip() for p in (sp.get("source_field_path") or "").split(",")) if f
        ]
        out["provenance"] = {
            "data_source_id": str(raw_id) if raw_id else None,
            "data_source_name": names.get(ds_uuid) if ds_uuid else None,
            "source_fields": fields,
            "fetched_at": sp.get("fetched_at"),
        }


async def domain_map(session: AsyncSession, enterprise_id: uuid.UUID) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    for name, model in ENTITY_MODELS.items():
        obs_counts = {
            state: count
            for state, count in (
                await session.execute(
                    select(model.observability, func.count())
                    .where(model.enterprise_id == enterprise_id)
                    .group_by(model.observability)
                )
            ).all()
        }
        source_ids = sorted(
            {
                s
                for (s,) in (
                    await session.execute(
                        select(model.source_provenance["data_source_id"].astext)
                        .where(model.enterprise_id == enterprise_id)
                        .distinct()
                    )
                ).all()
                if s
            }
        )
        entities.append(
            {
                "entity": name,
                "count": sum(obs_counts.values()),
                "observability": obs_counts,
                "sources": source_ids,
            }
        )
    return {"entities": entities, "relationships": _RELATIONSHIPS}


async def domain_entity_rows(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    entity: str,
    *,
    limit: int | None = None,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    model = _resolve_model(entity)
    stmt = (
        select(model)
        .where(model.enterprise_id == enterprise_id)
        .order_by(model.created_at, model.id)
    )
    rows, next_cursor = await paginate(session, stmt, limit=limit, cursor=cursor)
    serialized = [_serialize(r) for r in rows]
    await _attach_references(session, enterprise_id, model, rows, serialized)
    await _attach_provenance(session, enterprise_id, serialized)
    return serialized, next_cursor
