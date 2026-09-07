"""FastAPI auth / RBAC dependencies (T014).

* ``get_current_user`` — decodes the bearer access token, loads the user, attaches effective
  permissions.
* ``require(permission)`` — a dependency factory that 403s when the permission is absent.

The backend is the only security boundary: every state-changing route declares a permission
here (FR-066/FR-067).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError, PermissionDeniedError
from app.enterprise.models import Enterprise
from app.identity.security import decode_access_token
from app.identity.service import effective_permissions, get_user

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    permissions: frozenset[str]

    def has(self, permission: str) -> bool:
        return permission in self.permissions


async def get_active_enterprise_id(session: AsyncSession) -> uuid.UUID:
    """The single active enterprise for this deployment (v1)."""
    rows = (await session.execute(select(Enterprise.id).limit(2))).scalars().all()
    if not rows:
        raise AppError("no enterprise configured", code="config_error", status_code=500)
    if len(rows) > 1:
        raise AppError(
            "multiple enterprises present; v1 expects exactly one",
            code="config_error",
            status_code=500,
        )
    return rows[0]


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("missing bearer token", code="unauthorized", status_code=401)
    try:
        payload = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise AppError("invalid or expired token", code="unauthorized", status_code=401) from exc
    if payload.get("typ") != "access":
        raise AppError("wrong token type", code="unauthorized", status_code=401)

    user_id = uuid.UUID(payload["sub"])
    user = await get_user(session, user_id)
    if user is None or not user.is_active:
        raise AppError("user not found or inactive", code="unauthorized", status_code=401)

    perms = await effective_permissions(session, user_id)
    return CurrentUser(id=user.id, enterprise_id=user.enterprise_id, permissions=frozenset(perms))


def require(permission: str) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(permission):
            raise PermissionDeniedError(f"missing permission: {permission}")
        return user

    return _dep
