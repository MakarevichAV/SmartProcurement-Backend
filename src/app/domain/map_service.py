"""L0 Domain Map read service (T054; FR-008..FR-011).

Answers "what exists, where did it come from, is the source still observable" — nothing more.
This is L0: no analysis, no risk, no recommendation.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.pagination import paginate
from app.domain.models import ENTITY_MODELS

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
    return [_serialize(r) for r in rows], next_cursor
