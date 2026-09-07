"""AuthProvider seam (T014).

The local implementation authenticates against ``user.password_hash``. An OIDC/SSO provider
can replace it later without touching route or RBAC code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.security import verify_password
from app.identity.service import get_user_by_email


@dataclass(frozen=True)
class AuthenticatedUser:
    id: uuid.UUID
    enterprise_id: uuid.UUID


class AuthProvider(Protocol):
    async def authenticate(
        self, session: AsyncSession, *, enterprise_id: uuid.UUID, email: str, password: str
    ) -> AuthenticatedUser | None: ...


class LocalAuthProvider:
    async def authenticate(
        self, session: AsyncSession, *, enterprise_id: uuid.UUID, email: str, password: str
    ) -> AuthenticatedUser | None:
        user = await get_user_by_email(session, enterprise_id, email)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return AuthenticatedUser(id=user.id, enterprise_id=user.enterprise_id)


def get_auth_provider() -> AuthProvider:
    return LocalAuthProvider()
