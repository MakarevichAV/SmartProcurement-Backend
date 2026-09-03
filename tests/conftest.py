"""Shared pytest fixtures (T009).

Phase 1 provides:
* ``client`` — an in-process async HTTP client bound to the FastAPI app (no DB needed).
* ``mock_llm`` — a ``DeterministicMockProvider`` instance.
* ``db_session`` — a rollback-per-test async session; **skips** if a Postgres instance is not
  reachable at ``DATABASE_URL`` (the dockerised fixture is finalised in Phase 2 with models).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_engine
from app.main import app
from tests.fixtures.llm import DeterministicMockProvider


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def mock_llm() -> DeterministicMockProvider:
    return DeterministicMockProvider()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Rollback-isolated async session; skips when Postgres is unavailable."""
    engine = get_engine()
    try:
        conn = await engine.connect()
    except (SQLAlchemyError, OSError) as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Postgres not reachable at {get_settings().database_url!r}: {exc}")

    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
