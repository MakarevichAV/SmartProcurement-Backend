"""Field-mapping endpoints (T055; contracts/rest-api.md "Data Sources / Mappings").

Creating a raw mapping is part of source setup (``datasource.manage``); confirming / rejecting
/ retiring / editing a mapping is the human authority step (``mapping.confirm``). Only
``confirmed`` mappings ever feed sync (FR-004/FR-005).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.data_sources import MappingOut, mapping_out
from app.core.db import get_session
from app.identity.deps import CurrentUser, require
from app.integration import mapping_service
from app.integration.mapping_service import _UNSET

router = APIRouter(tags=["mappings"])

_MANAGE = "datasource.manage"
_CONFIRM = "mapping.confirm"


class MappingIn(BaseModel):
    data_source_id: uuid.UUID
    source_field_path: str
    canonical_entity: str
    canonical_attribute: str
    transform: dict[str, Any] | None = None


class MappingPatch(BaseModel):
    canonical_entity: str | None = None
    canonical_attribute: str | None = None
    transform: dict[str, Any] | None = None
    model_config = {"extra": "forbid"}


class MappingList(BaseModel):
    items: list[MappingOut]
    next_cursor: str | None = None


class BulkConfirmIn(BaseModel):
    data_source_id: uuid.UUID
    mapping_ids: list[uuid.UUID]


class BulkConfirmFailure(BaseModel):
    mapping_id: str
    reason: str


class BulkConfirmOut(BaseModel):
    data_source_id: str
    requested: list[str]
    confirmed: list[str]
    failed: list[BulkConfirmFailure]


class ChangeEventOut(BaseModel):
    action: str
    actor_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    at: str


class ChangeEventList(BaseModel):
    items: list[ChangeEventOut]


@router.get("/data-sources/{data_source_id}/mappings", response_model=MappingList)
async def list_mappings(
    data_source_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> MappingList:
    rows = await mapping_service.list_source_mappings(session, user.enterprise_id, data_source_id)
    return MappingList(items=[mapping_out(m) for m in rows])


@router.post("/mappings", response_model=MappingOut)
async def create_mapping(
    body: MappingIn,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> MappingOut:
    m = await mapping_service.create_mapping(
        session,
        enterprise_id=user.enterprise_id,
        data_source_id=body.data_source_id,
        source_field_path=body.source_field_path,
        canonical_entity=body.canonical_entity,
        canonical_attribute=body.canonical_attribute,
        transform=body.transform,
        actor_id=user.id,
    )
    await session.commit()
    return mapping_out(m)


@router.post("/mappings/bulk-confirm", response_model=BulkConfirmOut)
async def bulk_confirm(
    body: BulkConfirmIn,
    user: CurrentUser = Depends(require(_CONFIRM)),
    session: AsyncSession = Depends(get_session),
) -> BulkConfirmOut:
    """Confirm every eligible (belongs-to-source + ``suggested``) mapping in one request.
    Ineligible ids are reported in ``failed`` and left untouched (partial-failure safe)."""
    result = await mapping_service.confirm_mappings_bulk(
        session,
        enterprise_id=user.enterprise_id,
        data_source_id=body.data_source_id,
        mapping_ids=body.mapping_ids,
        actor_id=user.id,
    )
    await session.commit()
    return BulkConfirmOut(
        data_source_id=result.data_source_id,
        requested=result.requested,
        confirmed=result.confirmed,
        failed=[BulkConfirmFailure(**f) for f in result.failed],
    )


@router.post("/mappings/{mapping_id}/confirm", response_model=MappingOut)
async def confirm(
    mapping_id: uuid.UUID,
    user: CurrentUser = Depends(require(_CONFIRM)),
    session: AsyncSession = Depends(get_session),
) -> MappingOut:
    m = await mapping_service.confirm_mapping(
        session, enterprise_id=user.enterprise_id, mapping_id=mapping_id, actor_id=user.id
    )
    await session.commit()
    return mapping_out(m)


@router.post("/mappings/{mapping_id}/reject", response_model=MappingOut)
async def reject(
    mapping_id: uuid.UUID,
    user: CurrentUser = Depends(require(_CONFIRM)),
    session: AsyncSession = Depends(get_session),
) -> MappingOut:
    m = await mapping_service.reject_mapping(
        session, enterprise_id=user.enterprise_id, mapping_id=mapping_id, actor_id=user.id
    )
    await session.commit()
    return mapping_out(m)


@router.post("/mappings/{mapping_id}/retire", response_model=MappingOut)
async def retire(
    mapping_id: uuid.UUID,
    user: CurrentUser = Depends(require(_CONFIRM)),
    session: AsyncSession = Depends(get_session),
) -> MappingOut:
    m = await mapping_service.retire_mapping(
        session, enterprise_id=user.enterprise_id, mapping_id=mapping_id, actor_id=user.id
    )
    await session.commit()
    return mapping_out(m)


@router.patch("/mappings/{mapping_id}", response_model=MappingOut)
async def edit(
    mapping_id: uuid.UUID,
    body: MappingPatch,
    user: CurrentUser = Depends(require(_CONFIRM)),
    session: AsyncSession = Depends(get_session),
) -> MappingOut:
    fields = body.model_dump(exclude_unset=True)
    m = await mapping_service.edit_mapping(
        session,
        enterprise_id=user.enterprise_id,
        mapping_id=mapping_id,
        actor_id=user.id,
        canonical_entity=fields.get("canonical_entity"),
        canonical_attribute=fields.get("canonical_attribute"),
        transform=fields["transform"] if "transform" in fields else _UNSET,
    )
    await session.commit()
    return mapping_out(m)


@router.get("/mappings/{mapping_id}/history", response_model=ChangeEventList)
async def history(
    mapping_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> ChangeEventList:
    events = await mapping_service.mapping_history(session, user.enterprise_id, mapping_id)
    return ChangeEventList(
        items=[
            ChangeEventOut(
                action=e.action,
                actor_id=str(e.actor_id) if e.actor_id else None,
                before=e.before,
                after=e.after,
                at=e.at.isoformat(),
            )
            for e in events
        ]
    )
