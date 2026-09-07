"""Identity, RBAC and refresh-token models (T011, data-model.md §1).

A user MAY hold several roles; LORM separation-of-duties (author != approver) is enforced in
service logic regardless of roles (FR-065).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import EnterpriseScoped, Timestamps, UUIDPrimaryKey

ROLE_KEYS = ("administrator", "buyer", "approver")

# Full v1 permission catalogue (data-model.md §1).
PERMISSION_KEYS = (
    "datasource.manage",
    "mapping.confirm",
    "domain.read",
    "recommendation.request",
    "approval.act",
    "policy.author",
    "policy.approve",
    "capability.read",
    "capability.promote",
    "audit.read",
    "user.manage",
)


class User(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "user"
    __table_args__ = (UniqueConstraint("enterprise_id", "email", name="uq_user_enterprise_email"),)

    email: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Role(UUIDPrimaryKey, Timestamps, EnterpriseScoped, Base):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("enterprise_id", "key", name="uq_role_enterprise_key"),)

    key: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Permission(UUIDPrimaryKey, Timestamps, Base):
    """Global catalogue — not enterprise-scoped."""

    __tablename__ = "permission"

    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("permission.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("role.id", ondelete="CASCADE"), primary_key=True
    )


class RefreshToken(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "refresh_token"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
