"""Reusable ORM mixins and helpers (T007).

Conventions (data-model.md): every table has a uuid PK + created/updated timestamps; every
non-global table is enterprise-scoped; append-only tables carry the ``AppendOnly`` marker and
are additionally protected by a DB trigger (T027, Phase 2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column


class UUIDPrimaryKey:
    """Adds a ``id`` uuid primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class Timestamps:
    """Adds ``created_at`` / ``updated_at`` (server-side defaults)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class EnterpriseScoped:
    """Adds a non-null ``enterprise_id`` FK for multi-enterprise readiness."""

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("enterprise.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class AppendOnly:
    """Marker for tables that must never be UPDATE-d or DELETE-d.

    Enforcement is twofold: application code refuses mutation, and a Postgres
    ``forbid_mutation()`` trigger is attached in the table's migration (T027).
    """

    __append_only__ = True


def enum_column(
    name: str, *values: str, nullable: bool = False, default: str | None = None
) -> MappedColumn[Any]:
    """A ``text`` column constrained to a fixed value set via a CHECK constraint.

    Portable alternative to native enums; new values only require a migration.
    """
    allowed = ", ".join(f"'{v}'" for v in values)
    return mapped_column(
        String,
        CheckConstraint(f"{name} IN ({allowed})", name=f"ck_{name}"),
        nullable=nullable,
        default=default,
    )
