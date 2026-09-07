"""Append-only audit writer (T026).

``AuditRecorder.record`` is the single entry point. It asserts the LORM §10.3 / FR-060
required field set is present for L4/L5 events before inserting, so an incomplete audit trail
cannot be written.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import EVENT_TYPES, AuditRecord

# Events that represent an L4/L5 action and therefore need the full §10.3 field set.
_ACTION_EVENTS = frozenset(
    {"l4_approved", "l5_authorized", "dispatched", "execution_result", "verification"}
)
_REQUIRED_FOR_ACTION = ("capability_key", "lorm_level", "authorizer", "action", "evidence_ref")


class AuditIntegrityError(RuntimeError):
    """Raised when a required audit field is missing for an action event."""


class AuditRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        enterprise_id: uuid.UUID,
        event_type: str,
        correlation_id: uuid.UUID | None = None,
        capability_id: uuid.UUID | None = None,
        capability_key: str | None = None,
        lorm_level: str | None = None,
        decision: dict[str, Any] | None = None,
        evidence_ref: dict[str, Any] | None = None,
        authorizer: dict[str, Any] | None = None,
        action: str | None = None,
        params: dict[str, Any] | None = None,
        procurement_action_id: uuid.UUID | None = None,
        outcome: dict[str, Any] | None = None,
        verified: str = "n/a",
    ) -> AuditRecord:
        if event_type not in EVENT_TYPES:
            raise AuditIntegrityError(f"unknown event_type {event_type!r}")

        if event_type in _ACTION_EVENTS:
            local = locals()
            missing = [f for f in _REQUIRED_FOR_ACTION if not local.get(f)]
            if missing:
                raise AuditIntegrityError(
                    f"audit event {event_type!r} missing required field(s): {missing}"
                )

        row = AuditRecord(
            enterprise_id=enterprise_id,
            at=datetime.now(UTC),
            event_type=event_type,
            correlation_id=correlation_id,
            capability_id=capability_id,
            capability_key=capability_key,
            lorm_level=lorm_level,
            decision=decision,
            evidence_ref=evidence_ref,
            authorizer=authorizer,
            action=action,
            params=params,
            procurement_action_id=procurement_action_id,
            outcome=outcome,
            verified=verified,
        )
        self._session.add(row)
        await self._session.flush()
        return row
