"""Secret storage seam (T018).

External-source and execution-endpoint credentials are encrypted at rest with Fernet; the
key comes from settings (env / secret manager), never the database. Plaintext is only ever
produced here — never logged, never serialized to an API response, never placed in a prompt
(Constitution §15, FR-068).

``SecretStore`` is an interface so a managed secret manager can replace the DB-backed
implementation later.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.models_secret import Secret


class SecretStore(Protocol):
    async def put(
        self,
        *,
        enterprise_id: uuid.UUID,
        purpose: str,
        plaintext: str,
        created_by: uuid.UUID | None,
    ) -> uuid.UUID: ...

    async def get(self, secret_id: uuid.UUID) -> str: ...


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise AppError("FERNET_KEY is not configured", code="config_error", status_code=500)
    return Fernet(key.encode())


class DbSecretStore:
    """Fernet-encrypted secrets in the ``secret`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(
        self,
        *,
        enterprise_id: uuid.UUID,
        purpose: str,
        plaintext: str,
        created_by: uuid.UUID | None = None,
    ) -> uuid.UUID:
        row = Secret(
            enterprise_id=enterprise_id,
            purpose=purpose,
            ciphertext=_fernet().encrypt(plaintext.encode()),
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def get(self, secret_id: uuid.UUID) -> str:
        row = (
            await self._session.execute(select(Secret).where(Secret.id == secret_id))
        ).scalar_one_or_none()
        if row is None:
            raise AppError("secret not found", code="not_found", status_code=404)
        return _fernet().decrypt(row.ciphertext).decode()
