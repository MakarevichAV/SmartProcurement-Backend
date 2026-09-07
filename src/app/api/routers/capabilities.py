"""Capability + minimal promotion endpoints (T024).

US6 extends the payloads (AI suggestions, trust record). The promotion flow here is the
minimal one US4/US5 need to raise a capability's level via the API.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.identity.deps import CurrentUser, require
from app.lorm import capability_service as svc
from app.lorm.models import PromotionRequest

router = APIRouter(tags=["capabilities"])


class CapabilityOut(BaseModel):
    key: str
    level: str
    l5_allowed: bool
    verification_tolerances: dict[str, Any]
    uncertainty_threshold: float


class LevelEventOut(BaseModel):
    direction: str
    from_level: str
    to_level: str
    reason: str
    trigger: str
    actor_id: str | None
    at: str


class PromotionRequestOut(BaseModel):
    id: str
    capability_key: str | None = None
    proposed_to_level: str
    rationale: str
    origin: str
    status: str
    decided_by: str | None
    decided_at: str | None


class PromotionRequestIn(BaseModel):
    proposed_to_level: str
    rationale: str = ""


@router.get("/capabilities", response_model=list[CapabilityOut])
async def list_capabilities(
    user: CurrentUser = Depends(require("capability.read")),
    session: AsyncSession = Depends(get_session),
) -> list[CapabilityOut]:
    caps = await svc.list_capabilities(session, user.enterprise_id)
    return [
        CapabilityOut(
            key=c.key,
            level=c.level,
            l5_allowed=c.l5_allowed,
            verification_tolerances=c.verification_tolerances,
            uncertainty_threshold=float(c.uncertainty_threshold),
        )
        for c in caps
    ]


@router.get("/capabilities/{key}/history", response_model=list[LevelEventOut])
async def capability_history(
    key: str,
    user: CurrentUser = Depends(require("capability.read")),
    session: AsyncSession = Depends(get_session),
) -> list[LevelEventOut]:
    cap = await svc.get_capability(session, user.enterprise_id, key)
    events = await svc.level_history(session, cap.id)
    return [
        LevelEventOut(
            direction=e.direction,
            from_level=e.from_level,
            to_level=e.to_level,
            reason=e.reason,
            trigger=e.trigger,
            actor_id=str(e.actor_id) if e.actor_id else None,
            at=e.at.isoformat(),
        )
        for e in events
    ]


@router.post("/capabilities/{key}/promotion-requests", response_model=PromotionRequestOut)
async def create_promotion_request(
    key: str,
    body: PromotionRequestIn,
    user: CurrentUser = Depends(require("capability.promote")),
    session: AsyncSession = Depends(get_session),
) -> PromotionRequestOut:
    req = await svc.create_promotion_request(
        session,
        enterprise_id=user.enterprise_id,
        key=key,
        proposed_to_level=body.proposed_to_level,
        rationale=body.rationale,
        created_by=user.id,
    )
    await session.commit()
    return _req_out(req, key)


@router.post("/promotion-requests/{request_id}/approve", response_model=CapabilityOut)
async def approve_promotion(
    request_id: uuid.UUID,
    user: CurrentUser = Depends(require("capability.promote")),
    session: AsyncSession = Depends(get_session),
) -> CapabilityOut:
    cap = await svc.approve_promotion(
        session, enterprise_id=user.enterprise_id, request_id=request_id, decided_by=user.id
    )
    await session.commit()
    return CapabilityOut(
        key=cap.key,
        level=cap.level,
        l5_allowed=cap.l5_allowed,
        verification_tolerances=cap.verification_tolerances,
        uncertainty_threshold=float(cap.uncertainty_threshold),
    )


@router.post("/promotion-requests/{request_id}/reject", response_model=PromotionRequestOut)
async def reject_promotion(
    request_id: uuid.UUID,
    user: CurrentUser = Depends(require("capability.promote")),
    session: AsyncSession = Depends(get_session),
) -> PromotionRequestOut:
    req = await svc.reject_promotion(
        session, enterprise_id=user.enterprise_id, request_id=request_id, decided_by=user.id
    )
    await session.commit()
    return _req_out(req, None)


def _req_out(req: PromotionRequest, key: str | None) -> PromotionRequestOut:
    return PromotionRequestOut(
        id=str(req.id),
        capability_key=key,
        proposed_to_level=req.proposed_to_level,
        rationale=req.rationale,
        origin=req.origin,
        status=req.status,
        decided_by=str(req.decided_by) if req.decided_by else None,
        decided_at=req.decided_at.isoformat() if req.decided_at else None,
    )
