"""Promotion-core tests (T025).

The minimal capability service is the only writer that raises ``capability.level``:
one level at a time, human-only, and never to L5 for a capped capability.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import ConflictError, DomainRuleError
from app.lorm import capability_service as svc

pytestmark = pytest.mark.asyncio


async def test_one_level_promotion_applies_and_records_event(db_session, seeded) -> None:
    ent_id = seeded.id
    actor = uuid.uuid4()
    req = await svc.create_promotion_request(
        db_session,
        enterprise_id=ent_id,
        key="proc.po.create",  # seeded at L3
        proposed_to_level="L4",
        rationale="test",
        created_by=actor,
    )
    cap = await svc.approve_promotion(
        db_session, enterprise_id=ent_id, request_id=req.id, decided_by=actor
    )
    assert cap.level == "L4"

    history = await svc.level_history(db_session, cap.id)
    assert len(history) == 1
    ev = history[0]
    assert (ev.from_level, ev.to_level, ev.direction, ev.trigger) == (
        "L3",
        "L4",
        "promotion",
        "human",
    )
    assert ev.actor_id == actor


async def test_two_level_jump_is_rejected(db_session, seeded) -> None:
    with pytest.raises(DomainRuleError):
        await svc.create_promotion_request(
            db_session,
            enterprise_id=seeded.id,
            key="proc.risk.diagnose",  # seeded at L2
            proposed_to_level="L4",
            rationale="",
            created_by=uuid.uuid4(),
        )


async def test_l5_forbidden_for_capped_capability(db_session, seeded) -> None:
    with pytest.raises(DomainRuleError):
        await svc.create_promotion_request(
            db_session,
            enterprise_id=seeded.id,
            key="proc.supplier.add",  # seeded at L4, l5_allowed=false
            proposed_to_level="L5",
            rationale="",
            created_by=uuid.uuid4(),
        )


async def test_level_unchanged_without_approval(db_session, seeded) -> None:
    cap_before = await svc.get_capability(db_session, seeded.id, "proc.po.create")
    await svc.create_promotion_request(
        db_session,
        enterprise_id=seeded.id,
        key="proc.po.create",
        proposed_to_level="L4",
        rationale="",
        created_by=uuid.uuid4(),
    )
    cap_after = await svc.get_capability(db_session, seeded.id, "proc.po.create")
    assert cap_after.level == cap_before.level == "L3"
    assert await svc.level_history(db_session, cap_after.id) == []


async def test_double_approve_conflicts(db_session, seeded) -> None:
    actor = uuid.uuid4()
    req = await svc.create_promotion_request(
        db_session,
        enterprise_id=seeded.id,
        key="proc.po.create",
        proposed_to_level="L4",
        rationale="",
        created_by=actor,
    )
    await svc.approve_promotion(
        db_session, enterprise_id=seeded.id, request_id=req.id, decided_by=actor
    )
    with pytest.raises(ConflictError):
        await svc.approve_promotion(
            db_session, enterprise_id=seeded.id, request_id=req.id, decided_by=actor
        )
