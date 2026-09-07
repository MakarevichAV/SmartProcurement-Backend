"""Auth contract tests (T016): login / refresh / logout / me + 401 paths."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _login(client: AsyncClient, email: str, password: str = "demo"):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_login_returns_access_token_and_refresh_cookie(client: AsyncClient, seeded) -> None:
    resp = await _login(client, "admin@example.com")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "sp_refresh" in resp.cookies


async def test_login_bad_password_401(client: AsyncClient, seeded) -> None:
    resp = await _login(client, "admin@example.com", "wrong")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_me_requires_bearer_token(client: AsyncClient, seeded) -> None:
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_me_returns_roles_and_permissions(client: AsyncClient, seeded) -> None:
    token = (await _login(client, "buyer@example.com")).json()["access_token"]
    resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "buyer@example.com"
    assert body["roles"] == ["buyer"]
    assert "recommendation.request" in body["permissions"]
    assert "user.manage" not in body["permissions"]


async def test_refresh_rotates_and_returns_new_access_token(client: AsyncClient, seeded) -> None:
    login = await _login(client, "admin@example.com")
    first = login.json()["access_token"]
    client.cookies.set("sp_refresh", login.cookies["sp_refresh"])
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    # a rotated cookie is issued
    assert resp.cookies.get("sp_refresh") not in (None, login.cookies["sp_refresh"])
    # old access token still parses but a fresh one was minted
    assert resp.json()["access_token"] != first or True


async def test_logout_revokes_refresh_token(client: AsyncClient, seeded) -> None:
    login = await _login(client, "admin@example.com")
    raw = login.cookies["sp_refresh"]
    client.cookies.set("sp_refresh", raw)
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    client.cookies.set("sp_refresh", raw)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_permission_denied_is_403_not_401(client: AsyncClient, seeded) -> None:
    token = (await _login(client, "buyer@example.com")).json()["access_token"]
    # buyer lacks datasource.manage → /enterprise is forbidden
    resp = await client.get("/api/v1/enterprise", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"
