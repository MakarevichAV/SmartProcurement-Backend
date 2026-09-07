"""Enterprise configuration service (T017)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainRuleError, NotFoundError
from app.enterprise.models import EXECUTION_ADAPTERS, Enterprise


async def get_enterprise(session: AsyncSession, enterprise_id: uuid.UUID) -> Enterprise:
    row = (
        await session.execute(select(Enterprise).where(Enterprise.id == enterprise_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("enterprise not found")
    return row


async def update_enterprise(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    *,
    base_currency: str | None = None,
    active_execution_adapter: str | None = None,
    settings: dict[str, Any] | None = None,
) -> Enterprise:
    ent = await get_enterprise(session, enterprise_id)
    if base_currency is not None:
        if len(base_currency) != 3:
            raise DomainRuleError("base_currency must be a 3-letter ISO code")
        ent.base_currency = base_currency.upper()
    if active_execution_adapter is not None:
        if active_execution_adapter not in EXECUTION_ADAPTERS:
            raise DomainRuleError(f"active_execution_adapter must be one of {EXECUTION_ADAPTERS}")
        ent.active_execution_adapter = active_execution_adapter
    if settings is not None:
        ent.settings = settings
    await session.flush()
    return ent
