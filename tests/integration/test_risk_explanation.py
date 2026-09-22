"""AI output tests for RiskExplanation (T061; contracts/ai-structured-output.md §2, FR-018/
FR-019/FR-070, invariants I-2/I-3).

**Target interface this file specifies for T069/T071** — none of these exist yet, so every
import below is expected to fail until they are written:

    app.ai.schemas.RiskExplanation / EvidenceRef
        -- the module's own docstring already says "later stories add RiskExplanation";
           this pins the exact shape from the contract.
    app.analysis.models.RiskFinding / RiskSignalLink / Explanation  -- data-model.md §5
    app.observation.models.ObservationSignal                        -- data-model.md §4
    app.analysis.explain.explain_risk(session, *, enterprise_id, risk_finding_id, provider=None)
        -- calls ai.structured.generate_structured(RiskExplanation, ...,
           capability_key="proc.risk.diagnose"), then applies the anti-hallucination rule from
           the contract: ``data_used`` entries whose ``ref`` is not one of the risk_finding's
           linked observation_signal ids are dropped (mirrors the existing MappingSuggestion
           precedent of dropping unknown source_field_path); if nothing survives, that's
           treated exactly like empty ``data_used`` -> invalid output -> the same fail-safe
           path as ``generate_structured`` (ai_unavailable gap + audit record), and
           ``risk_finding.ai_status`` is set to ``unavailable``. On success it persists an
           ``Explanation(subject_type="risk_finding", subject_id=risk_finding.id, ...)`` and
           sets ``risk_finding.ai_status = "ready"``.

Note: whether a *partial* hallucination (some refs valid, some not) drops just the bad refs or
invalidates the whole explanation is not fully unambiguous in the contract text ("rejected");
this file encodes the drop-and-keep-valid interpretation, consistent with the existing
MappingSuggestion precedent. Flagged for confirmation alongside T071.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.analysis.explain import explain_risk
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import DeterministicMockProvider, LLMUnavailable
from app.analysis.models import Explanation, RiskFinding, RiskSignalLink
from app.audit.models import AuditRecord
from app.domain.models import Item
from app.integration.models import DataSource
from app.observation.models import ObservationSignal
from app.observation.observability import ObservabilityService

pytestmark = pytest.mark.asyncio


async def _risk_with_signals(
    session: AsyncSession, enterprise_id: uuid.UUID, *, n_signals: int = 2
) -> tuple[RiskFinding, list[ObservationSignal]]:
    item = Item(enterprise_id=enterprise_id, sku=f"SKU-{uuid.uuid4().hex[:8]}", name="Test item")
    src = DataSource(
        enterprise_id=enterprise_id,
        name="Test source",
        kind="file",
        connector_type="file",
        config={},
        health="available",
    )
    session.add_all([item, src])
    await session.flush()
    item_id = item.id

    finding = RiskFinding(
        enterprise_id=enterprise_id,
        risk_type="likely_shortage",
        item_id=item_id,
        supplier_id=None,
        severity="high",
        status="open",
        detected_by="rule",
        ai_status="pending",
        detected_at=datetime.now(UTC),
    )
    session.add(finding)
    await session.flush()

    signals = []
    for _ in range(n_signals):
        sig = ObservationSignal(
            enterprise_id=enterprise_id,
            data_source_id=src.id,
            signal_type="stock_change",
            item_id=item_id,
            supplier_id=None,
            payload={"quantity": 5},
            observed_at=datetime.now(UTC),
            ingested_at=datetime.now(UTC),
        )
        session.add(sig)
        await session.flush()
        session.add(RiskSignalLink(risk_finding_id=finding.id, observation_signal_id=sig.id))
        signals.append(sig)
    await session.flush()
    return finding, signals


def _explanation_payload(data_used: list[dict], *, confidence: float = 0.75) -> dict:
    return {
        "what": "Stock for this item is depleting faster than expected.",
        "why": "Consumption rate exceeds replenishment before the next delivery.",
        "data_used": data_used,
        "factors": [{"name": "consumption_rate", "effect": "increases risk", "weight": "high"}],
        "confidence": confidence,
    }


async def test_valid_explanation_persists_and_marks_ready(db_session: AsyncSession, seeded) -> None:
    finding, signals = await _risk_with_signals(db_session, seeded.id)
    mock = DeterministicMockProvider()
    mock.register(
        "RiskExplanation",
        _explanation_payload([{"kind": "observation_signal", "ref": str(s.id)} for s in signals]),
    )

    explanation = await explain_risk(
        db_session, enterprise_id=seeded.id, risk_finding_id=finding.id, provider=mock
    )

    assert explanation.data_used
    assert {d["ref"] for d in explanation.data_used} == {str(s.id) for s in signals}
    assert explanation.confidence == pytest.approx(0.75)

    refreshed = await db_session.get(RiskFinding, finding.id)
    assert refreshed is not None
    assert refreshed.ai_status == "ready"


async def test_hallucinated_ref_is_dropped_but_valid_refs_are_kept(
    db_session: AsyncSession, seeded
) -> None:
    finding, signals = await _risk_with_signals(db_session, seeded.id, n_signals=1)
    hallucinated_ref = str(uuid.uuid4())  # never linked via risk_signal_link
    mock = DeterministicMockProvider()
    mock.register(
        "RiskExplanation",
        _explanation_payload(
            [
                {"kind": "observation_signal", "ref": str(signals[0].id)},
                {"kind": "observation_signal", "ref": hallucinated_ref},
            ]
        ),
    )

    explanation = await explain_risk(
        db_session, enterprise_id=seeded.id, risk_finding_id=finding.id, provider=mock
    )

    refs = {d["ref"] for d in explanation.data_used}
    assert refs == {str(signals[0].id)}
    assert hallucinated_ref not in refs

    refreshed = await db_session.get(RiskFinding, finding.id)
    assert refreshed is not None
    assert refreshed.ai_status == "ready"


async def test_only_hallucinated_refs_is_treated_as_unavailable(
    db_session: AsyncSession, seeded
) -> None:
    finding, _signals = await _risk_with_signals(db_session, seeded.id, n_signals=1)
    mock = DeterministicMockProvider()
    mock.register(
        "RiskExplanation",
        _explanation_payload([{"kind": "observation_signal", "ref": str(uuid.uuid4())}]),
    )

    with pytest.raises(LLMUnavailable):
        await explain_risk(
            db_session, enterprise_id=seeded.id, risk_finding_id=finding.id, provider=mock
        )

    await _assert_marked_unavailable(db_session, seeded.id, finding.id)


async def test_empty_data_used_is_treated_as_unavailable(db_session: AsyncSession, seeded) -> None:
    finding, _signals = await _risk_with_signals(db_session, seeded.id, n_signals=1)
    mock = DeterministicMockProvider()
    mock.register("RiskExplanation", _explanation_payload([]))

    with pytest.raises(LLMUnavailable):
        await explain_risk(
            db_session, enterprise_id=seeded.id, risk_finding_id=finding.id, provider=mock
        )

    await _assert_marked_unavailable(db_session, seeded.id, finding.id)


async def test_provider_unavailable_marks_risk_finding_unavailable(
    db_session: AsyncSession, seeded
) -> None:
    finding, _signals = await _risk_with_signals(db_session, seeded.id, n_signals=1)
    mock = DeterministicMockProvider()
    mock.register_failure("RiskExplanation")

    with pytest.raises(LLMUnavailable):
        await explain_risk(
            db_session, enterprise_id=seeded.id, risk_finding_id=finding.id, provider=mock
        )

    await _assert_marked_unavailable(db_session, seeded.id, finding.id)


async def _assert_marked_unavailable(
    session: AsyncSession, enterprise_id: uuid.UUID, risk_finding_id: uuid.UUID
) -> None:
    refreshed = await session.get(RiskFinding, risk_finding_id)
    assert refreshed is not None
    assert refreshed.ai_status == "unavailable"

    gap_open = await ObservabilityService(session).has_open_for(
        enterprise_id=enterprise_id, scope="capability", scope_ref="proc.risk.diagnose"
    )
    assert gap_open is True

    count = (
        await session.execute(
            select(func.count())
            .select_from(AuditRecord)
            .where(AuditRecord.event_type == "ai_unavailable")
        )
    ).scalar_one()
    assert count >= 1

    # no Explanation row should have been persisted for a failed attempt
    persisted = (
        await session.execute(
            select(func.count())
            .select_from(Explanation)
            .where(
                Explanation.subject_type == "risk_finding",
                Explanation.subject_id == risk_finding_id,
            )
        )
    ).scalar_one()
    assert persisted == 0
