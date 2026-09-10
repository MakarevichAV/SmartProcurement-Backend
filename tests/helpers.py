"""Shared test helpers (auth token acquisition for contract tests)."""

from __future__ import annotations

from httpx import AsyncClient


async def login(client: AsyncClient, email: str, password: str = "demo") -> str:
    """Log in via the real auth endpoint and return the bearer access token."""
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def admin_headers(client: AsyncClient) -> dict[str, str]:
    return auth_headers(await login(client, "admin@example.com"))


async def buyer_headers(client: AsyncClient) -> dict[str, str]:
    return auth_headers(await login(client, "buyer@example.com"))
