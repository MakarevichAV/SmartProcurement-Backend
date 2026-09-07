"""Capability seed (T021).

Fixed initial levels for the 8 procurement capabilities. Story tests must promote to the
level they need — never rely on these defaults implicitly.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.lorm.models import Capability

# key -> (level, l5_allowed)
SEED_LEVELS: dict[str, tuple[str, bool]] = {
    "proc.inventory.observe": ("L0", True),
    "proc.demand.observe": ("L1", True),
    "proc.risk.diagnose": ("L2", True),
    "proc.order.recommend": ("L3", True),
    "proc.po.create": ("L3", True),
    "proc.replenish.routine": ("L4", True),
    "proc.supplier.add": ("L4", False),
    "proc.payment.release": ("L3", False),
}

DEFAULT_TOLERANCES = {"price_pct": 10, "qty_short_pct": 10, "late_days": 3}
DEFAULT_UNCERTAINTY_THRESHOLD = 0.3


async def seed_capabilities(session: AsyncSession, enterprise_id: uuid.UUID) -> int:
    """Create any missing capabilities for the enterprise. Returns the number created."""
    existing = set(
        (
            await session.execute(
                select(Capability.key).where(Capability.enterprise_id == enterprise_id)
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for key, (level, l5_allowed) in SEED_LEVELS.items():
        if key in existing:
            continue
        session.add(
            Capability(
                enterprise_id=enterprise_id,
                key=key,
                level=level,
                l5_allowed=l5_allowed,
                verification_tolerances=dict(DEFAULT_TOLERANCES),
                uncertainty_threshold=DEFAULT_UNCERTAINTY_THRESHOLD,
            )
        )
        created += 1
    await session.flush()
    return created
