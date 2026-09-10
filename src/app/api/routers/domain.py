"""L0 Domain Map endpoints (T055; contracts/rest-api.md "L0 Domain Map").

Read-only, permission ``domain.read``. Shows what exists, where it came from
(``source_provenance``) and whether the source is still observable (``observability``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.domain import map_service
from app.identity.deps import CurrentUser, require

router = APIRouter(tags=["domain"])

_READ = "domain.read"


class EntityRows(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


@router.get("/domain/map")
async def get_domain_map(
    user: CurrentUser = Depends(require(_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await map_service.domain_map(session, user.enterprise_id)


@router.get("/domain/{entity}", response_model=EntityRows)
async def get_domain_entity(
    entity: str,
    user: CurrentUser = Depends(require(_READ)),
    session: AsyncSession = Depends(get_session),
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> EntityRows:
    items, next_cursor = await map_service.domain_entity_rows(
        session, user.enterprise_id, entity, limit=limit, cursor=cursor
    )
    return EntityRows(items=items, next_cursor=next_cursor)
