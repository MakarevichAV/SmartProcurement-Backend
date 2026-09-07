"""Enterprise config endpoints (T017)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.enterprise.models import Enterprise
from app.enterprise.service import get_enterprise, update_enterprise
from app.identity.deps import CurrentUser, require

router = APIRouter(prefix="/enterprise", tags=["enterprise"])


class EnterpriseOut(BaseModel):
    id: str
    name: str
    base_currency: str
    active_execution_adapter: str
    settings: dict[str, Any]


class EnterprisePatch(BaseModel):
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    active_execution_adapter: str | None = None
    settings: dict[str, Any] | None = None


def _to_out(ent: Enterprise) -> EnterpriseOut:
    return EnterpriseOut(
        id=str(ent.id),
        name=ent.name,
        base_currency=ent.base_currency,
        active_execution_adapter=ent.active_execution_adapter,
        settings=ent.settings,
    )


@router.get("", response_model=EnterpriseOut)
async def read_enterprise(
    user: CurrentUser = Depends(require("datasource.manage")),
    session: AsyncSession = Depends(get_session),
) -> EnterpriseOut:
    return _to_out(await get_enterprise(session, user.enterprise_id))


@router.patch("", response_model=EnterpriseOut)
async def patch_enterprise(
    body: EnterprisePatch,
    user: CurrentUser = Depends(require("datasource.manage")),
    session: AsyncSession = Depends(get_session),
) -> EnterpriseOut:
    ent = await update_enterprise(
        session,
        user.enterprise_id,
        base_currency=body.base_currency,
        active_execution_adapter=body.active_execution_adapter,
        settings=body.settings,
    )
    await session.commit()
    return _to_out(ent)
