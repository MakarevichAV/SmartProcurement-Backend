"""Demo seed CLI (T036): ``python -m app.seed --demo``.

Creates one enterprise, the full permission catalogue mapped to the three roles, users
``admin`` / ``buyer`` / ``approver`` (password ``demo`` unless overridden), and the capability
seed (T021). Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models_registry  # noqa: F401
from app.core.db import get_sessionmaker
from app.enterprise.models import Enterprise
from app.identity.models import (
    PERMISSION_KEYS,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.identity.security import hash_password
from app.lorm.seed import seed_capabilities

# role key -> granted permission keys
ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "administrator": PERMISSION_KEYS,  # everything
    "buyer": (
        "domain.read",
        "recommendation.request",
        "approval.act",
        "policy.author",
        "capability.read",
    ),
    "approver": (
        "domain.read",
        "approval.act",
        "policy.approve",
        "capability.read",
        "capability.promote",
        "audit.read",
    ),
}

DEMO_USERS = [
    ("admin@example.com", "Demo Admin", "administrator"),
    ("buyer@example.com", "Demo Buyer", "buyer"),
    ("approver@example.com", "Demo Approver", "approver"),
]


async def _get_or_create_enterprise(session: AsyncSession) -> Enterprise:
    ent = (await session.execute(select(Enterprise).limit(1))).scalar_one_or_none()
    if ent is None:
        ent = Enterprise(name="Demo Enterprise", base_currency="USD")
        session.add(ent)
        await session.flush()
    return ent


async def _seed_permissions(session: AsyncSession) -> dict[str, Permission]:
    existing = {p.key: p for p in (await session.execute(select(Permission))).scalars().all()}
    for key in PERMISSION_KEYS:
        if key not in existing:
            p = Permission(key=key, description=key)
            session.add(p)
            existing[key] = p
    await session.flush()
    return existing


async def run_demo_seed(session: AsyncSession, password: str) -> dict[str, int]:
    ent = await _get_or_create_enterprise(session)
    perms = await _seed_permissions(session)

    roles: dict[str, Role] = {
        r.key: r
        for r in (await session.execute(select(Role).where(Role.enterprise_id == ent.id)))
        .scalars()
        .all()
    }
    for key, grant_keys in ROLE_GRANTS.items():
        role = roles.get(key)
        if role is None:
            role = Role(enterprise_id=ent.id, key=key, name=key.capitalize())
            session.add(role)
            await session.flush()
            roles[key] = role
        have = set(
            (
                await session.execute(
                    select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
                )
            )
            .scalars()
            .all()
        )
        for gk in grant_keys:
            pid = perms[gk].id
            if pid not in have:
                session.add(RolePermission(role_id=role.id, permission_id=pid))

    users_created = 0
    for email, name, role_key in DEMO_USERS:
        user = (
            await session.execute(
                select(User).where(User.enterprise_id == ent.id, User.email == email)
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(
                enterprise_id=ent.id,
                email=email,
                full_name=name,
                password_hash=hash_password(password),
                is_active=True,
            )
            session.add(user)
            await session.flush()
            users_created += 1
        link = (
            await session.execute(
                select(UserRole).where(
                    UserRole.user_id == user.id, UserRole.role_id == roles[role_key].id
                )
            )
        ).scalar_one_or_none()
        if link is None:
            session.add(UserRole(user_id=user.id, role_id=roles[role_key].id))

    caps_created = await seed_capabilities(session, ent.id)
    await session.commit()
    return {
        "permissions": len(perms),
        "roles": len(roles),
        "users_created": users_created,
        "capabilities_created": caps_created,
    }


async def _main(password: str) -> None:
    async with get_sessionmaker()() as session:
        result = await run_demo_seed(session, password)
    print(f"[seed] {result}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.seed")
    parser.add_argument("--demo", action="store_true", help="seed the demo enterprise")
    args = parser.parse_args()
    if not args.demo:
        parser.error("nothing to do; pass --demo")
    password = os.environ.get("SEED_PASSWORD", "demo")
    asyncio.run(_main(password))


if __name__ == "__main__":
    main()
