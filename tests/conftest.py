"""Shared pytest fixtures (T009, extended in Phase 2).

* ``db_session`` — a function-scoped async session bound to an outer transaction that is
  rolled back after each test (``join_transaction_mode="create_savepoint"`` so service-level
  ``commit()`` calls only release savepoints).
* ``client`` — an in-process async HTTP client with the ``get_session`` dependency overridden
  to the test's ``db_session``.
* ``seeded`` — runs the demo seed inside the test transaction; yields the ``Enterprise``.
* ``mock_llm`` — a ``DeterministicMockProvider``.

Every fixture that needs the database ``pytest.skip``s when Postgres is unreachable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.models_registry
from app.ai.provider import DeterministicMockProvider
from app.core.config import get_settings
from app.core.db import get_session
from app.main import app


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # A per-test engine with NullPool avoids "future attached to a different loop":
    # pytest-asyncio gives each test its own event loop.
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except (SQLAlchemyError, OSError) as exc:  # pragma: no cover - env-dependent
        await engine.dispose()
        pytest.skip(f"Postgres not reachable at {get_settings().database_url!r}: {exc}")

    trans = await conn.begin()
    session = AsyncSession(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    from app.seed import run_demo_seed

    await run_demo_seed(db_session, password="demo")
    from sqlalchemy import select

    from app.enterprise.models import Enterprise

    return (await db_session.execute(select(Enterprise))).scalar_one()


@pytest.fixture
def mock_llm() -> DeterministicMockProvider:
    return DeterministicMockProvider()
