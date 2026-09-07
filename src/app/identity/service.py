"""Identity read/query helpers used by auth and RBAC (T014 support)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import Permission, Role, RolePermission, User, UserRole


async def get_user_by_email(
    session: AsyncSession, enterprise_id: uuid.UUID, email: str
) -> User | None:
    return (
        await session.execute(
            select(User).where(User.enterprise_id == enterprise_id, User.email == email)
        )
    ).scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def effective_permissions(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Distinct permission keys granted through any of the user's roles."""
    stmt = (
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .distinct()
    )
    return sorted((await session.execute(stmt)).scalars().all())


async def role_keys(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    stmt = (
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return sorted((await session.execute(stmt)).scalars().all())
