"""Password hashing and JWT / refresh-token services (T013).

* Passwords: Argon2id via ``argon2-cffi``.
* Access tokens: short-lived signed JWTs (HS256).
* Refresh tokens: opaque random strings; only their SHA-256 hash is stored, so a DB leak
  does not yield usable tokens. Rotated on every refresh, revocable individually.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.identity.models import RefreshToken

_ph = PasswordHasher()
_ALGO = "HS256"


def hash_password(plaintext: str) -> str:
    return _ph.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False


def issue_access_token(user_id: uuid.UUID, *, permissions: list[str]) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "perms": permissions,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_access_ttl_seconds)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])


def _hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID) -> str:
    settings = get_settings()
    raw = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_refresh(raw),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
        )
    )
    await session.flush()
    return raw


async def consume_refresh_token(session: AsyncSession, raw: str) -> tuple[uuid.UUID, str] | None:
    """Validate + rotate. Returns ``(user_id, new_raw_token)`` or ``None`` if invalid."""
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(raw))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at <= datetime.now(UTC):
        return None
    row.revoked_at = datetime.now(UTC)
    new_raw = await issue_refresh_token(session, row.user_id)
    return row.user_id, new_raw


async def revoke_refresh_token(session: AsyncSession, raw: str) -> None:
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(raw))
        )
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
