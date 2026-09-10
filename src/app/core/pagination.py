"""Minimal cursor pagination for list endpoints (contracts/rest-api.md §Pagination).

v1 uses an opaque offset cursor — adequate at the documented scale (≤10 sources, ≤10k SKUs).
Keyset pagination is a Polish-phase refinement (tasks.md T166).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        offset = int(cursor)
    except ValueError:
        return 0
    return max(offset, 0)


def clamp_limit(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


async def paginate(
    session: AsyncSession,
    stmt: Select[Any],
    *,
    limit: int | None,
    cursor: str | None,
) -> tuple[list[Any], str | None]:
    """Return ``(rows, next_cursor)`` for a SELECT of ORM rows."""
    lim = clamp_limit(limit)
    offset = decode_cursor(cursor)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list((await session.execute(stmt.offset(offset).limit(lim))).scalars().all())
    next_cursor = str(offset + lim) if offset + lim < total else None
    return rows, next_cursor
