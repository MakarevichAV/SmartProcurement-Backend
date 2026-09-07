"""Encrypted-secret storage model (T018, data-model.md §2).

Holds only ciphertext (Fernet). Never serialized by any API response; the plaintext is only
ever produced inside the backend by ``SecretStore`` (Constitution §15, FR-068).
"""

from __future__ import annotations

from sqlalchemy import LargeBinary
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import EnterpriseScoped, Timestamps, UUIDPrimaryKey, enum_column

SECRET_PURPOSES = ("source_credential", "execution_endpoint")


class Secret(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "secret"

    purpose: Mapped[str] = enum_column("purpose", *SECRET_PURPOSES)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[str | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
