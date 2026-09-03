"""Phase 1 smoke test: the app builds and the health route responds (checkpoint)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_correlation_id_header_present(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers.get("X-Correlation-Id")


async def test_openapi_served(client: AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "Smart Procurement API"
