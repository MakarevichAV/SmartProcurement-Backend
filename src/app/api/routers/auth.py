"""Authentication endpoints (T015).

* ``POST /auth/login``   — email + password → access token in body, refresh token as an
  httpOnly Secure SameSite cookie.
* ``POST /auth/refresh`` — rotates the refresh cookie, returns a fresh access token.
* ``POST /auth/logout``  — revokes the refresh token and clears the cookie.
* ``GET  /me``           — current user + effective permissions + roles.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError
from app.identity.auth_provider import get_auth_provider
from app.identity.deps import CurrentUser, get_active_enterprise_id, get_current_user
from app.identity.security import (
    consume_refresh_token,
    issue_access_token,
    issue_refresh_token,
    revoke_refresh_token,
)
from app.identity.service import effective_permissions, get_user, role_keys

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["auth"])

_REFRESH_COOKIE = "sp_refresh"
_COOKIE_PATH = "/api/v1/auth"


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    id: str
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]


def _set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        _REFRESH_COOKIE,
        raw,
        max_age=settings.jwt_refresh_ttl_seconds,
        httponly=True,
        secure=settings.environment != "local",
        samesite="lax",
        path=_COOKIE_PATH,
    )


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn, response: Response, session: AsyncSession = Depends(get_session)
) -> TokenOut:
    enterprise_id = await get_active_enterprise_id(session)
    authed = await get_auth_provider().authenticate(
        session, enterprise_id=enterprise_id, email=body.email, password=body.password
    )
    if authed is None:
        raise AppError("invalid credentials", code="unauthorized", status_code=401)
    perms = await effective_permissions(session, authed.id)
    access = issue_access_token(authed.id, permissions=perms)
    raw_refresh = await issue_refresh_token(session, authed.id)
    await session.commit()
    _set_refresh_cookie(response, raw_refresh)
    return TokenOut(access_token=access)


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> TokenOut:
    raw = request.cookies.get(_REFRESH_COOKIE)
    if not raw:
        raise AppError("no refresh token", code="unauthorized", status_code=401)
    result = await consume_refresh_token(session, raw)
    if result is None:
        raise AppError("invalid refresh token", code="unauthorized", status_code=401)
    user_id, new_raw = result
    perms = await effective_permissions(session, user_id)
    access = issue_access_token(user_id, permissions=perms)
    await session.commit()
    _set_refresh_cookie(response, new_raw)
    return TokenOut(access_token=access)


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    raw = request.cookies.get(_REFRESH_COOKIE)
    if raw:
        await revoke_refresh_token(session, raw)
        await session.commit()
    response.delete_cookie(_REFRESH_COOKIE, path=_COOKIE_PATH)
    return {"status": "logged_out"}


@me_router.get("/me", response_model=MeOut)
async def me(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    row = await get_user(session, user.id)
    assert row is not None
    return MeOut(
        id=str(row.id),
        email=row.email,
        full_name=row.full_name,
        roles=await role_keys(session, user.id),
        permissions=sorted(user.permissions),
    )
