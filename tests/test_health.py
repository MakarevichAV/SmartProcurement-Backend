"""Smoke test: the app builds and the health route responds (no DB needed)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def raw_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_health_ok(raw_client: AsyncClient) -> None:
    resp = await raw_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_correlation_id_header_present(raw_client: AsyncClient) -> None:
    resp = await raw_client.get("/health")
    assert resp.headers.get("X-Correlation-Id")


async def test_openapi_served(raw_client: AsyncClient) -> None:
    resp = await raw_client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "Smart Procurement API"
