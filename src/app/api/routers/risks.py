"""Risks endpoints (T073; contracts/rest-api.md "Risks / Recommendations").

``GET /risks`` / ``GET /risks/{id}`` / ``POST /risks/{id}/dismiss`` — permission
``domain.read`` for all three (T073's task text states this explicitly for the whole router,
dismiss included; there is no separate mutation permission for a human dismissing a risk).

``GET /risks/{id}``'s ``evidence`` field reuses ``analysis.explain.evidence_payloads`` — the
exact same evidence universe T071 validates a `RiskExplanation` against, so what a human sees
here and what the AI was allowed to cite are provably the same set. For every risk type this
is `observation_signal` rows linked via `risk_signal_link`; for `systematic_supplier_delay`
specifically it also includes `domain_row` (`purchase_order`) evidence, since that risk type
deliberately has no linked signals (T066 doesn't diff `purchase_order` changes into signals).

Recommendation endpoints (``POST /risks/{id}/recommendation``, ``GET /recommendations/{id}``)
are US3 — not implemented here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.explain import evidence_payloads
from app.analysis.models import TERMINAL_RISK_STATUSES, Explanation, RiskFinding
from app.audit.recorder import AuditRecorder
from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import paginate
from app.identity.deps import CurrentUser, require

router = APIRouter(tags=["risks"])

_PERMISSION = "domain.read"


class RiskFindingOut(BaseModel):
    id: str
    risk_type: str
    item_id: str | None
    supplier_id: str | None
    severity: str
    status: str
    ai_status: str
    detected_at: str
    resolved_at: str | None
    dismissed_reason: str | None
    dismissed_at: str | None
    dismissed_by: str | None


class RiskFindingList(BaseModel):
    items: list[RiskFindingOut]
    next_cursor: str | None = None


class ExplanationOut(BaseModel):
    what: str
    why: str
    data_used: list[dict[str, Any]]
    factors: list[dict[str, Any]]
    confidence: float
    generated_by: str
    llm_provider: str | None
    llm_model: str | None


class RiskFindingDetailOut(RiskFindingOut):
    explanation: ExplanationOut | None
    evidence: list[dict[str, Any]]


class DismissIn(BaseModel):
    reason: str = Field(min_length=1)


def _out(finding: RiskFinding) -> RiskFindingOut:
    return RiskFindingOut(
        id=str(finding.id),
        risk_type=finding.risk_type,
        item_id=str(finding.item_id) if finding.item_id else None,
        supplier_id=str(finding.supplier_id) if finding.supplier_id else None,
        severity=finding.severity,
        status=finding.status,
        ai_status=finding.ai_status,
        detected_at=finding.detected_at.isoformat(),
        resolved_at=finding.resolved_at.isoformat() if finding.resolved_at else None,
        dismissed_reason=finding.dismissed_reason,
        dismissed_at=finding.dismissed_at.isoformat() if finding.dismissed_at else None,
        dismissed_by=str(finding.dismissed_by) if finding.dismissed_by else None,
    )


async def _get_finding_or_404(
    session: AsyncSession, enterprise_id: uuid.UUID, risk_id: uuid.UUID
) -> RiskFinding:
    finding = await session.get(RiskFinding, risk_id)
    # A finding from another enterprise is reported as not-found, not forbidden -- consistent
    # with how the rest of the API avoids confirming another enterprise's data exists.
    if finding is None or finding.enterprise_id != enterprise_id:
        raise NotFoundError(f"risk_finding {risk_id} not found")
    return finding


async def _latest_explanation(session: AsyncSession, finding_id: uuid.UUID) -> Explanation | None:
    stmt = (
        select(Explanation)
        .where(Explanation.subject_type == "risk_finding", Explanation.subject_id == finding_id)
        .order_by(Explanation.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _explanation_out(explanation: Explanation) -> ExplanationOut:
    return ExplanationOut(
        what=explanation.what,
        why=explanation.why,
        data_used=explanation.data_used,
        factors=explanation.factors,
        confidence=float(explanation.confidence),
        generated_by=explanation.generated_by,
        llm_provider=explanation.llm_provider,
        llm_model=explanation.llm_model,
    )


@router.get("/risks", response_model=RiskFindingList)
async def list_risks(
    status: str | None = Query(default=None),
    risk_type: str | None = Query(default=None),
    item_id: uuid.UUID | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = Query(default=None),
    user: CurrentUser = Depends(require(_PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> RiskFindingList:
    stmt = select(RiskFinding).where(RiskFinding.enterprise_id == user.enterprise_id)
    if status is not None:
        stmt = stmt.where(RiskFinding.status == status)
    if risk_type is not None:
        stmt = stmt.where(RiskFinding.risk_type == risk_type)
    if item_id is not None:
        stmt = stmt.where(RiskFinding.item_id == item_id)
    stmt = stmt.order_by(RiskFinding.detected_at.desc(), RiskFinding.id)

    rows, next_cursor = await paginate(session, stmt, limit=limit, cursor=cursor)
    return RiskFindingList(items=[_out(r) for r in rows], next_cursor=next_cursor)


@router.get("/risks/{risk_id}", response_model=RiskFindingDetailOut)
async def get_risk(
    risk_id: uuid.UUID,
    user: CurrentUser = Depends(require(_PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> RiskFindingDetailOut:
    finding = await _get_finding_or_404(session, user.enterprise_id, risk_id)
    explanation = await _latest_explanation(session, finding.id)
    evidence = await evidence_payloads(session, finding)

    base = _out(finding)
    return RiskFindingDetailOut(
        **base.model_dump(),
        explanation=_explanation_out(explanation) if explanation is not None else None,
        evidence=evidence,
    )


@router.post("/risks/{risk_id}/dismiss", response_model=RiskFindingOut)
async def dismiss_risk(
    risk_id: uuid.UUID,
    body: DismissIn,
    user: CurrentUser = Depends(require(_PERMISSION)),
    session: AsyncSession = Depends(get_session),
) -> RiskFindingOut:
    finding = await _get_finding_or_404(session, user.enterprise_id, risk_id)
    if finding.status in TERMINAL_RISK_STATUSES:
        raise ConflictError(f"risk_finding {risk_id} is already {finding.status}")

    now = datetime.now(UTC)
    finding.status = "dismissed"
    finding.dismissed_reason = body.reason
    finding.dismissed_at = now
    finding.dismissed_by = user.id
    finding.resolved_at = now  # the only terminal-timestamp column risk_finding has

    await AuditRecorder(session).record(
        enterprise_id=user.enterprise_id,
        event_type="risk_dismissed",
        authorizer={"kind": "human", "user_id": str(user.id)},
        action="risk.dismiss",
        params={"risk_finding_id": str(finding.id), "reason": body.reason},
        outcome={"status": "dismissed"},
    )
    await session.commit()
    return _out(finding)
