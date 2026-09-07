"""Minimal capability level + promotion service (T023).

This module is the **only** code path that raises ``capability.level``. It enforces the
non-negotiable LORM rules:

* promotion is human-only and **exactly one level** at a time (FR-051, SPEC §6.2, I-8);
* a capability with ``l5_allowed = false`` can never be promoted to L5 (FR-049);
* every change writes an append-only ``capability_level_event``.

AI-originated suggestions, the L4→L5 approver ≠ policy-author rule and trust-record gating
are added in US6.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, DomainRuleError, NotFoundError
from app.lorm.models import LEVELS, Capability, CapabilityLevelEvent, PromotionRequest

_LEVEL_INDEX = {lvl: i for i, lvl in enumerate(LEVELS)}


async def get_capability(session: AsyncSession, enterprise_id: uuid.UUID, key: str) -> Capability:
    row = (
        await session.execute(
            select(Capability).where(
                Capability.enterprise_id == enterprise_id, Capability.key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"capability {key!r} not found")
    return row


async def list_capabilities(session: AsyncSession, enterprise_id: uuid.UUID) -> list[Capability]:
    return list(
        (
            await session.execute(
                select(Capability)
                .where(Capability.enterprise_id == enterprise_id)
                .order_by(Capability.key)
            )
        )
        .scalars()
        .all()
    )


async def level_history(
    session: AsyncSession, capability_id: uuid.UUID
) -> list[CapabilityLevelEvent]:
    return list(
        (
            await session.execute(
                select(CapabilityLevelEvent)
                .where(CapabilityLevelEvent.capability_id == capability_id)
                .order_by(CapabilityLevelEvent.at)
            )
        )
        .scalars()
        .all()
    )


async def create_promotion_request(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    key: str,
    proposed_to_level: str,
    rationale: str,
    created_by: uuid.UUID,
) -> PromotionRequest:
    cap = await get_capability(session, enterprise_id, key)
    _validate_target(cap, proposed_to_level)
    req = PromotionRequest(
        enterprise_id=enterprise_id,
        capability_id=cap.id,
        proposed_to_level=proposed_to_level,
        rationale=rationale,
        evidence={},
        origin="human",
        status="pending",
    )
    session.add(req)
    await session.flush()
    return req


async def get_promotion_request(
    session: AsyncSession, enterprise_id: uuid.UUID, request_id: uuid.UUID
) -> PromotionRequest:
    row = (
        await session.execute(
            select(PromotionRequest).where(
                PromotionRequest.id == request_id,
                PromotionRequest.enterprise_id == enterprise_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("promotion request not found")
    return row


async def approve_promotion(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    request_id: uuid.UUID,
    decided_by: uuid.UUID,
) -> Capability:
    req = await get_promotion_request(session, enterprise_id, request_id)
    if req.status != "pending":
        raise ConflictError(f"promotion request is already {req.status}")
    cap = await get_capability_by_id(session, req.capability_id)
    _validate_target(cap, req.proposed_to_level)  # re-check against current level

    from_level = cap.level
    cap.level = req.proposed_to_level
    req.status = "approved"
    req.decided_by = decided_by
    req.decided_at = datetime.now(UTC)
    session.add(
        CapabilityLevelEvent(
            capability_id=cap.id,
            direction="promotion",
            from_level=from_level,
            to_level=cap.level,
            reason=req.rationale or "promotion approved",
            trigger="human",
            actor_id=decided_by,
            at=datetime.now(UTC),
        )
    )
    await session.flush()
    return cap


async def reject_promotion(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    request_id: uuid.UUID,
    decided_by: uuid.UUID,
) -> PromotionRequest:
    req = await get_promotion_request(session, enterprise_id, request_id)
    if req.status != "pending":
        raise ConflictError(f"promotion request is already {req.status}")
    req.status = "rejected"
    req.decided_by = decided_by
    req.decided_at = datetime.now(UTC)
    await session.flush()
    return req


async def get_capability_by_id(session: AsyncSession, capability_id: uuid.UUID) -> Capability:
    row = (
        await session.execute(select(Capability).where(Capability.id == capability_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("capability not found")
    return row


def _validate_target(cap: Capability, target: str) -> None:
    if target not in _LEVEL_INDEX:
        raise DomainRuleError(f"unknown level {target!r}")
    delta = _LEVEL_INDEX[target] - _LEVEL_INDEX[cap.level]
    if delta != 1:
        raise DomainRuleError(
            "promotion must raise the level by exactly one step "
            f"(from {cap.level} to {LEVELS[_LEVEL_INDEX[cap.level] + 1]})"
        )
    if target == "L5" and not cap.l5_allowed:
        raise DomainRuleError(f"capability {cap.key!r} may never reach L5 (l5_allowed = false)")
