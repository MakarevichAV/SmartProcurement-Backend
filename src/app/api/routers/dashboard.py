"""Dashboard read model (composes existing Phase-3 read services; no new tables).

Communicates the LORM control flow (L2 -> L3 -> L4 -> L5) and the health of the data
behind it. The L2/L3/L4 counters are ``null`` until their subsystems land (US2/US3/US4) —
never a fake ``0``. ``autopilot`` is a real capability-level count; when US5 adds policies
it will be refined to "L5 **and** an active policy" (see ``_autopilot_count``).

Permission ``domain.read`` (buyer / approver / admin). Every underlying service call is
scoped to the caller's enterprise.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.domain.map_service import domain_map
from app.identity.deps import CurrentUser, require
from app.integration import service as integration_service
from app.lorm import capability_service
from app.observation.observability import ObservabilityService

router = APIRouter(tags=["dashboard"])


class DashboardLorm(BaseModel):
    open_risks: int | None
    recommendations: int | None
    approvals: int | None
    autopilot: int


class DashboardDataHealth(BaseModel):
    sources_total: int
    sources_available: int
    sources_unavailable: int
    sources_stale: int
    last_successful_sync: str | None
    canonical_rows: int
    rows_fresh: int
    rows_stale: int
    rows_lost: int
    open_observability_gaps: int


class DashboardOut(BaseModel):
    lorm: DashboardLorm
    data_health: DashboardDataHealth


async def _autopilot_count(session: AsyncSession, enterprise_id: uuid.UUID) -> int:
    """Capabilities currently at L5. Phase-3 approximation of "operating under an approved
    policy" — US5 refines this to require a matching active ``policy``."""
    caps = await capability_service.list_capabilities(session, enterprise_id)
    return sum(1 for c in caps if c.level == "L5")


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    user: CurrentUser = Depends(require("domain.read")),
    session: AsyncSession = Depends(get_session),
) -> DashboardOut:
    sources = await integration_service.list_data_sources(session, user.enterprise_id)
    by_health = {"available": 0, "unavailable": 0, "stale": 0}
    for s in sources:
        by_health[s.health] = by_health.get(s.health, 0) + 1
    last_success = max(
        (s.last_success_at for s in sources if s.last_success_at is not None), default=None
    )

    dmap = await domain_map(session, user.enterprise_id)
    rows_by_state = {"fresh": 0, "stale": 0, "lost": 0}
    canonical_rows = 0
    for entity in dmap["entities"]:
        canonical_rows += entity["count"]
        for state, count in entity["observability"].items():
            rows_by_state[state] = rows_by_state.get(state, 0) + count

    open_gaps = await ObservabilityService(session).count_open(enterprise_id=user.enterprise_id)

    return DashboardOut(
        lorm=DashboardLorm(
            open_risks=None,
            recommendations=None,
            approvals=None,
            autopilot=await _autopilot_count(session, user.enterprise_id),
        ),
        data_health=DashboardDataHealth(
            sources_total=len(sources),
            sources_available=by_health["available"],
            sources_unavailable=by_health["unavailable"],
            sources_stale=by_health["stale"],
            last_successful_sync=last_success.isoformat() if last_success else None,
            canonical_rows=canonical_rows,
            rows_fresh=rows_by_state["fresh"],
            rows_stale=rows_by_state["stale"],
            rows_lost=rows_by_state["lost"],
            open_observability_gaps=open_gaps,
        ),
    )
