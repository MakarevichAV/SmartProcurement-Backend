"""AI explanation generation for an already-detected risk (T071; contracts/ai-structured-
output.md §2, FR-018/FR-070, invariants I-2/I-3).

``explain_risk`` explains a ``risk_finding`` that T070 already created — it never decides
whether the risk exists, and never changes ``risk_type``/``severity``/``status``. It also
never recommends a procurement action, selects a supplier, suggests a quantity, or approves/
executes anything; the system/user prompt says so explicitly, and the output schema
(``RiskExplanation``) has no field capable of expressing any of that.

**Evidence grounding** (Phase 4 decision, contracts/ai-structured-output.md §2): a `data_used`
entry is valid only if it resolves to evidence genuinely tied to the finding. For every risk
type except `systematic_supplier_delay` that means an `observation_signal` linked via
`risk_signal_link`. `systematic_supplier_delay` deliberately has zero `risk_signal_link` rows
(T066 doesn't diff `purchase_order` into signals) — its genuine evidence is the same
`purchase_order` rows T070's `_supplier_delay_samples` used to detect it in the first place, so
this module additionally accepts `domain_row`-kind refs that resolve to one of those exact
rows. This uses the `EvidenceRef.kind` values the contract already defines
(`observation_signal`/`sku_aggregate`/`domain_row`); no schema or T070 change.

Invalid/hallucinated refs are discarded individually, not treated as an all-or-nothing
rejection; the `Explanation` is persisted only if at least one valid ref survives. Any failure
path — provider/schema failure (handled by `generate_structured` itself) or "no valid evidence
survived grounding" (handled here, mirroring the same fail-safe) — leaves the `risk_finding`
itself untouched except for `ai_status`, never undoes T070's detection.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider, LLMUnavailable
from app.ai.schemas import RiskExplanation
from app.ai.structured import generate_structured
from app.analysis.models import Explanation, RiskFinding, RiskSignalLink
from app.audit.recorder import AuditRecorder
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.domain.models import PurchaseOrder
from app.observation.models import ObservationSignal
from app.observation.observability import ObservabilityService

_CAPABILITY_KEY = "proc.risk.diagnose"

_SYSTEM_PROMPT = (
    "You explain a procurement risk that a deterministic rule has ALREADY detected. You do "
    "not decide whether the risk exists, and you MUST NOT change its type or severity. You "
    "MUST NOT recommend a procurement action, select a supplier, suggest an order quantity, "
    "or approve or execute anything -- that is out of scope entirely. Using only the evidence "
    "provided, explain what was detected, why it is a problem, the factors that contributed, "
    "and how confident you are that the evidence supports this explanation. Every entry in "
    "data_used must be copied verbatim (kind and ref) from the evidence list given to you -- "
    "never invent a kind or ref that was not provided."
)


async def explain_risk(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    risk_finding_id: uuid.UUID,
    provider: LLMProvider | None = None,
) -> Explanation:
    finding = await session.get(RiskFinding, risk_finding_id)
    if finding is None or finding.enterprise_id != enterprise_id:
        raise NotFoundError("risk_finding not found")

    evidence = await _evidence_payloads(session, finding)
    valid_refs = {(e["kind"], e["ref"]) for e in evidence}

    prompt = json.dumps(
        {
            "risk_type": finding.risk_type,
            "severity": finding.severity,
            "item_id": str(finding.item_id) if finding.item_id else None,
            "supplier_id": str(finding.supplier_id) if finding.supplier_id else None,
            "evidence": evidence,
        }
    )

    try:
        parsed = await generate_structured(
            RiskExplanation,
            session=session,
            enterprise_id=enterprise_id,
            capability_key=_CAPABILITY_KEY,
            system=_SYSTEM_PROMPT,
            prompt=prompt,
            provider=provider,
        )
    except LLMUnavailable:
        finding.ai_status = "unavailable"
        await session.flush()
        raise

    kept = [d for d in parsed.data_used if (d.kind, d.ref) in valid_refs]
    if not kept:
        await _mark_unavailable(
            session,
            enterprise_id=enterprise_id,
            reason="no valid evidence remained after grounding validation "
            "(all data_used refs were hallucinated or empty)",
        )
        finding.ai_status = "unavailable"
        await session.flush()
        raise LLMUnavailable(
            f"AI unavailable for {_CAPABILITY_KEY} (RiskExplanation): "
            "no valid evidence after grounding"
        )

    settings = get_settings()
    explanation = Explanation(
        enterprise_id=enterprise_id,
        subject_type="risk_finding",
        subject_id=finding.id,
        what=parsed.what,
        why=parsed.why,
        data_used=[{"kind": d.kind, "ref": d.ref} for d in kept],
        factors=[{"name": f.name, "effect": f.effect, "weight": f.weight} for f in parsed.factors],
        confidence=parsed.confidence,
        generated_by="ai",
        llm_provider=provider.name if provider is not None else None,
        llm_model=(
            settings.anthropic_model
            if (provider is None or provider.name == "anthropic")
            else provider.name
        ),
    )
    session.add(explanation)
    finding.ai_status = "ready"
    await session.flush()
    return explanation


async def _mark_unavailable(
    session: AsyncSession, *, enterprise_id: uuid.UUID, reason: str
) -> None:
    """Replicates ``generate_structured``'s fail-safe for a failure it can't see: the raw
    output parsed fine, but nothing in ``data_used`` survived grounding validation."""
    await ObservabilityService(session).open(
        enterprise_id=enterprise_id,
        scope="capability",
        scope_ref=_CAPABILITY_KEY,
        reason="ai_unavailable",
    )
    await AuditRecorder(session).record(
        enterprise_id=enterprise_id,
        event_type="ai_unavailable",
        capability_key=_CAPABILITY_KEY,
        action="generate_structured:RiskExplanation",
        outcome={"error": reason},
    )


async def _evidence_payloads(session: AsyncSession, finding: RiskFinding) -> list[dict[str, Any]]:
    """The evidence universe a `RiskExplanation` may legitimately cite for this finding --
    doubles as both the prompt content and the grounding-validation allow-list."""
    signals = (
        (
            await session.execute(
                select(ObservationSignal)
                .join(RiskSignalLink, RiskSignalLink.observation_signal_id == ObservationSignal.id)
                .where(RiskSignalLink.risk_finding_id == finding.id)
            )
        )
        .scalars()
        .all()
    )
    evidence: list[dict[str, Any]] = [
        {
            "kind": "observation_signal",
            "ref": str(s.id),
            "signal_type": s.signal_type,
            "payload": s.payload,
            "observed_at": s.observed_at.isoformat(),
        }
        for s in signals
    ]

    if finding.risk_type == "systematic_supplier_delay" and finding.supplier_id is not None:
        pos = (
            (
                await session.execute(
                    select(PurchaseOrder).where(
                        PurchaseOrder.enterprise_id == finding.enterprise_id,
                        PurchaseOrder.supplier_id == finding.supplier_id,
                        PurchaseOrder.status == "received",
                        PurchaseOrder.observability != "lost",
                        PurchaseOrder.expected_at.is_not(None),
                        PurchaseOrder.received_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for po in pos:
            assert po.expected_at is not None and po.received_at is not None
            evidence.append(
                {
                    "kind": "domain_row",
                    "ref": str(po.id),
                    "entity": "purchase_order",
                    "expected_at": po.expected_at.isoformat(),
                    "received_at": po.received_at.isoformat(),
                    "late_days": (po.received_at - po.expected_at).days,
                }
            )

    return evidence
