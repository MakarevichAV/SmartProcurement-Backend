"""Observability-gap open/close service (T028).

A gap is opened when a required input is lost (source unavailable, stale data, AI
unavailable) and closed when it recovers. Open gaps for a capability's inputs block
autonomous action and, from Phase 9, trigger demotion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observation.models import GAP_REASONS, GAP_SCOPES, ObservabilityGap


class ObservabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open(
        self, *, enterprise_id: uuid.UUID, scope: str, scope_ref: str, reason: str
    ) -> ObservabilityGap:
        assert scope in GAP_SCOPES
        assert reason in GAP_REASONS
        existing = await self._current(enterprise_id, scope, scope_ref, reason)
        if existing is not None:
            return existing
        gap = ObservabilityGap(
            enterprise_id=enterprise_id,
            scope=scope,
            scope_ref=scope_ref,
            reason=reason,
            opened_at=datetime.now(UTC),
        )
        self._session.add(gap)
        await self._session.flush()
        return gap

    async def close(
        self, *, enterprise_id: uuid.UUID, scope: str, scope_ref: str, reason: str
    ) -> None:
        gap = await self._current(enterprise_id, scope, scope_ref, reason)
        if gap is not None:
            gap.closed_at = datetime.now(UTC)
            await self._session.flush()

    async def count_open(self, *, enterprise_id: uuid.UUID) -> int:
        """Number of currently-open observability gaps for the enterprise."""
        stmt = (
            select(func.count())
            .select_from(ObservabilityGap)
            .where(
                ObservabilityGap.enterprise_id == enterprise_id,
                ObservabilityGap.closed_at.is_(None),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def has_open_for(self, *, enterprise_id: uuid.UUID, scope: str, scope_ref: str) -> bool:
        stmt = select(ObservabilityGap.id).where(
            ObservabilityGap.enterprise_id == enterprise_id,
            ObservabilityGap.scope == scope,
            ObservabilityGap.scope_ref == scope_ref,
            ObservabilityGap.closed_at.is_(None),
        )
        return (await self._session.execute(stmt)).first() is not None

    async def _current(
        self, enterprise_id: uuid.UUID, scope: str, scope_ref: str, reason: str
    ) -> ObservabilityGap | None:
        stmt = select(ObservabilityGap).where(
            ObservabilityGap.enterprise_id == enterprise_id,
            ObservabilityGap.scope == scope,
            ObservabilityGap.scope_ref == scope_ref,
            ObservabilityGap.reason == reason,
            ObservabilityGap.closed_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
