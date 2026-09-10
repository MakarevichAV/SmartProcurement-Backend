"""Field-mapping lifecycle service (T052; data-model.md §2, FR-004/FR-005/FR-006).

States: ``suggested`` → ``confirmed`` → (``retired``) ; any → ``rejected``. Every transition
and edit appends an (append-only) ``mapping_change_event`` with a before/after snapshot. Only
``confirmed`` mappings are visible to sync.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, DomainRuleError, NotFoundError
from app.integration.models import (
    CANONICAL_ENTITIES,
    DataSource,
    FieldMapping,
    MappingChangeEvent,
)

_UNSET: Any = object()


def _snapshot(m: FieldMapping) -> dict[str, Any]:
    return {
        "source_field_path": m.source_field_path,
        "canonical_entity": m.canonical_entity,
        "canonical_attribute": m.canonical_attribute,
        "transform": m.transform,
        "status": m.status,
        "ai_confidence": float(m.ai_confidence) if m.ai_confidence is not None else None,
    }


async def _owned_source(
    session: AsyncSession, enterprise_id: uuid.UUID, data_source_id: uuid.UUID
) -> DataSource:
    ds = (
        await session.execute(
            select(DataSource).where(
                DataSource.id == data_source_id, DataSource.enterprise_id == enterprise_id
            )
        )
    ).scalar_one_or_none()
    if ds is None:
        raise NotFoundError("data source not found")
    return ds


async def get_mapping(
    session: AsyncSession, enterprise_id: uuid.UUID, mapping_id: uuid.UUID
) -> FieldMapping:
    row = (
        await session.execute(
            select(FieldMapping)
            .join(DataSource, DataSource.id == FieldMapping.data_source_id)
            .where(FieldMapping.id == mapping_id, DataSource.enterprise_id == enterprise_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("mapping not found")
    return row


async def list_source_mappings(
    session: AsyncSession, enterprise_id: uuid.UUID, data_source_id: uuid.UUID
) -> list[FieldMapping]:
    await _owned_source(session, enterprise_id, data_source_id)
    return list(
        (
            await session.execute(
                select(FieldMapping)
                .where(FieldMapping.data_source_id == data_source_id)
                .order_by(FieldMapping.created_at)
            )
        )
        .scalars()
        .all()
    )


async def confirmed_mappings(
    session: AsyncSession, data_source_id: uuid.UUID
) -> list[FieldMapping]:
    return list(
        (
            await session.execute(
                select(FieldMapping).where(
                    FieldMapping.data_source_id == data_source_id,
                    FieldMapping.status == "confirmed",
                )
            )
        )
        .scalars()
        .all()
    )


async def mapping_history(
    session: AsyncSession, enterprise_id: uuid.UUID, mapping_id: uuid.UUID
) -> list[MappingChangeEvent]:
    await get_mapping(session, enterprise_id, mapping_id)
    return list(
        (
            await session.execute(
                select(MappingChangeEvent)
                .where(MappingChangeEvent.field_mapping_id == mapping_id)
                .order_by(MappingChangeEvent.at)
            )
        )
        .scalars()
        .all()
    )


def _event(
    mapping: FieldMapping,
    action: str,
    *,
    actor_id: uuid.UUID | None,
    before: dict[str, Any] | None,
) -> MappingChangeEvent:
    return MappingChangeEvent(
        field_mapping_id=mapping.id,
        action=action,
        actor_id=actor_id,
        before=before,
        after=_snapshot(mapping),
        at=datetime.now(UTC),
    )


async def create_mapping(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    source_field_path: str,
    canonical_entity: str,
    canonical_attribute: str,
    transform: dict[str, Any] | None = None,
    actor_id: uuid.UUID | None = None,
) -> FieldMapping:
    await _owned_source(session, enterprise_id, data_source_id)
    if canonical_entity not in CANONICAL_ENTITIES:
        raise DomainRuleError(f"canonical_entity must be one of {CANONICAL_ENTITIES}")
    mapping = FieldMapping(
        data_source_id=data_source_id,
        source_field_path=source_field_path,
        canonical_entity=canonical_entity,
        canonical_attribute=canonical_attribute,
        transform=transform,
        status="suggested",
    )
    session.add(mapping)
    await session.flush()
    session.add(_event(mapping, "suggested", actor_id=actor_id, before=None))
    await session.flush()
    return mapping


async def _apply_confirm(
    session: AsyncSession, mapping: FieldMapping, *, actor_id: uuid.UUID
) -> None:
    """The single ``suggested/rejected/confirmed → confirmed`` transition: sets the fields
    and appends the append-only ``mapping_change_event``. Callers do the eligibility checks."""
    before = _snapshot(mapping)
    mapping.status = "confirmed"
    mapping.confirmed_by = actor_id
    mapping.confirmed_at = datetime.now(UTC)
    await session.flush()
    session.add(_event(mapping, "confirmed", actor_id=actor_id, before=before))
    await session.flush()


async def confirm_mapping(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    mapping_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> FieldMapping:
    mapping = await get_mapping(session, enterprise_id, mapping_id)
    if mapping.status == "retired":
        raise ConflictError("a retired mapping cannot be confirmed")
    await _apply_confirm(session, mapping, actor_id=actor_id)
    return mapping


@dataclass
class BulkConfirmResult:
    data_source_id: str
    requested: list[str]
    confirmed: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


async def confirm_mappings_bulk(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    mapping_ids: list[uuid.UUID],
    actor_id: uuid.UUID,
) -> BulkConfirmResult:
    """Confirm every mapping in ``mapping_ids`` that belongs to ``data_source_id`` and is
    currently ``suggested``. Ineligible ids (wrong/foreign data source, not found, or a
    non-``suggested`` status) are left untouched and returned in ``failed``. Uses the same
    per-mapping lifecycle as :func:`confirm_mapping` (``_apply_confirm``)."""
    await _owned_source(session, enterprise_id, data_source_id)

    requested = list(dict.fromkeys(mapping_ids))  # de-dupe, preserve order
    candidates = {
        m.id: m
        for m in (
            await session.execute(
                select(FieldMapping)
                .join(DataSource, DataSource.id == FieldMapping.data_source_id)
                .where(
                    FieldMapping.id.in_(requested),
                    FieldMapping.data_source_id == data_source_id,
                    DataSource.enterprise_id == enterprise_id,
                )
            )
        )
        .scalars()
        .all()
    }

    result = BulkConfirmResult(
        data_source_id=str(data_source_id), requested=[str(x) for x in requested]
    )
    for mid in requested:
        mapping = candidates.get(mid)
        if mapping is None:
            result.failed.append(
                {"mapping_id": str(mid), "reason": "not found for this data source"}
            )
            continue
        if mapping.status != "suggested":
            result.failed.append(
                {"mapping_id": str(mid), "reason": f"status is {mapping.status!r}, not 'suggested'"}
            )
            continue
        await _apply_confirm(session, mapping, actor_id=actor_id)
        result.confirmed.append(str(mid))
    return result


async def reject_mapping(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    mapping_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> FieldMapping:
    mapping = await get_mapping(session, enterprise_id, mapping_id)
    before = _snapshot(mapping)
    mapping.status = "rejected"
    mapping.confirmed_by = None
    mapping.confirmed_at = None
    await session.flush()
    session.add(_event(mapping, "rejected", actor_id=actor_id, before=before))
    await session.flush()
    return mapping


async def retire_mapping(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    mapping_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> FieldMapping:
    mapping = await get_mapping(session, enterprise_id, mapping_id)
    before = _snapshot(mapping)
    mapping.status = "retired"
    await session.flush()
    session.add(_event(mapping, "retired", actor_id=actor_id, before=before))
    await session.flush()
    return mapping


async def edit_mapping(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    mapping_id: uuid.UUID,
    actor_id: uuid.UUID,
    canonical_entity: str | None = None,
    canonical_attribute: str | None = None,
    transform: Any = _UNSET,
) -> FieldMapping:
    mapping = await get_mapping(session, enterprise_id, mapping_id)
    before = _snapshot(mapping)
    if canonical_entity is not None:
        if canonical_entity not in CANONICAL_ENTITIES:
            raise DomainRuleError(f"canonical_entity must be one of {CANONICAL_ENTITIES}")
        mapping.canonical_entity = canonical_entity
    if canonical_attribute is not None:
        mapping.canonical_attribute = canonical_attribute
    if transform is not _UNSET:
        mapping.transform = transform
    await session.flush()
    session.add(_event(mapping, "edited", actor_id=actor_id, before=before))
    await session.flush()
    return mapping
