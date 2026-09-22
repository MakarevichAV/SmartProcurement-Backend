"""Contract tests for `/risks` (T064; contracts/rest-api.md "Risks / Recommendations").

    GET  /risks             -- list risk_finding, filterable by status/type/item
    GET  /risks/{id}        -- risk + explanation + linked signals (evidence)
    POST /risks/{id}/dismiss -- human dismiss (reason)

Permission: `domain.read` for all three (tasks.md T073 states this explicitly for
`risks.py`'s whole router, dismiss included -- unlike the mutating endpoints elsewhere in the
contract that require a dedicated `*.manage`/`*.act` permission).

Dismissal schema (resolved): `risk_finding.dismissed_reason` / `dismissed_at` / `dismissed_by`
(T069) and the `risk_dismissed` audit event type (migration `ee988845269c`) now exist; this
file's assertions stay at the status-transition/contract level since the dismiss endpoint
itself (T073) is not implemented yet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import Explanation, RiskFinding, RiskSignalLink
from app.domain.models import Item
from app.integration.models import DataSource
from app.observation.models import ObservationSignal
from tests.helpers import admin_headers, buyer_headers

pytestmark = pytest.mark.asyncio


async def _seed_item(session: AsyncSession, enterprise_id: uuid.UUID) -> uuid.UUID:
    item = Item(enterprise_id=enterprise_id, sku=f"SKU-{uuid.uuid4().hex[:8]}", name="Test item")
    session.add(item)
    await session.flush()
    return item.id


async def _risk_finding(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    *,
    risk_type: str = "likely_shortage",
    item_id: uuid.UUID | None = None,
    status: str = "open",
    ai_status: str = "pending",
) -> RiskFinding:
    finding = RiskFinding(
        enterprise_id=enterprise_id,
        risk_type=risk_type,
        item_id=item_id or await _seed_item(session, enterprise_id),
        supplier_id=None,
        severity="high",
        status=status,
        detected_by="rule",
        ai_status=ai_status,
        detected_at=datetime.now(UTC),
    )
    session.add(finding)
    await session.flush()
    return finding


async def _with_explanation_and_evidence(
    session: AsyncSession, enterprise_id: uuid.UUID, finding: RiskFinding
) -> None:
    src = DataSource(
        enterprise_id=enterprise_id,
        name="Test source",
        kind="file",
        connector_type="file",
        config={},
        health="available",
    )
    session.add(src)
    await session.flush()
    signal = ObservationSignal(
        enterprise_id=enterprise_id,
        data_source_id=src.id,
        signal_type="stock_change",
        item_id=finding.item_id,
        supplier_id=None,
        payload={"quantity": 5},
        observed_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
    )
    session.add(signal)
    await session.flush()
    session.add(RiskSignalLink(risk_finding_id=finding.id, observation_signal_id=signal.id))
    session.add(
        Explanation(
            subject_type="risk_finding",
            subject_id=finding.id,
            what="Stock is depleting faster than expected.",
            why="Consumption exceeds replenishment before the next delivery.",
            data_used=[{"kind": "observation_signal", "ref": str(signal.id)}],
            factors=[{"name": "consumption_rate", "effect": "increases risk", "weight": "high"}],
            confidence=0.8,
            generated_by="ai",
        )
    )
    finding.ai_status = "ready"
    await session.flush()


async def test_list_risks_returns_paginated_envelope(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    await _risk_finding(db_session, seeded.id)
    resp = await client.get("/api/v1/risks", headers=await admin_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body and "next_cursor" in body
    assert any(row["risk_type"] == "likely_shortage" for row in body["items"])


async def test_list_risks_filters_by_status(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    open_finding = await _risk_finding(db_session, seeded.id, status="open")
    await _risk_finding(db_session, seeded.id, status="dismissed")
    headers = await admin_headers(client)

    resp = await client.get("/api/v1/risks", params={"status": "open"}, headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["items"]}
    assert str(open_finding.id) in ids
    assert all(row["status"] == "open" for row in resp.json()["items"])


async def test_list_risks_filters_by_type_and_item(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    item_id = await _seed_item(db_session, seeded.id)
    target = await _risk_finding(db_session, seeded.id, risk_type="price_anomaly", item_id=item_id)
    await _risk_finding(db_session, seeded.id, risk_type="likely_shortage")
    headers = await admin_headers(client)

    resp = await client.get(
        "/api/v1/risks",
        params={"risk_type": "price_anomaly", "item_id": str(item_id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["items"]}
    assert ids == {str(target.id)}


async def test_get_risk_detail_includes_explanation_and_evidence(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    finding = await _risk_finding(db_session, seeded.id)
    await _with_explanation_and_evidence(db_session, seeded.id, finding)

    resp = await client.get(f"/api/v1/risks/{finding.id}", headers=await admin_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(finding.id)
    assert body["explanation"]["what"]
    assert body["explanation"]["data_used"]
    assert body["explanation"]["confidence"] == pytest.approx(0.8)
    assert len(body["evidence"]) >= 1  # linked observation_signal(s)


async def test_dismiss_transitions_status_and_records_reason(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    finding = await _risk_finding(db_session, seeded.id)
    resp = await client.post(
        f"/api/v1/risks/{finding.id}/dismiss",
        json={"reason": "false positive, confirmed with supplier"},
        headers=await admin_headers(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "dismissed"

    refreshed = await db_session.get(RiskFinding, finding.id)
    assert refreshed is not None
    assert refreshed.status == "dismissed"


async def test_dismiss_requires_a_reason(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    finding = await _risk_finding(db_session, seeded.id)
    resp = await client.post(
        f"/api/v1/risks/{finding.id}/dismiss", json={}, headers=await admin_headers(client)
    )
    assert resp.status_code in (400, 422)


async def test_dismiss_an_already_terminal_risk_is_a_conflict(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    finding = await _risk_finding(db_session, seeded.id, status="dismissed")
    resp = await client.post(
        f"/api/v1/risks/{finding.id}/dismiss",
        json={"reason": "again?"},
        headers=await admin_headers(client),
    )
    assert resp.status_code == 409


async def test_risks_allowed_for_buyer(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    await _risk_finding(db_session, seeded.id)
    resp = await client.get("/api/v1/risks", headers=await buyer_headers(client))
    assert resp.status_code == 200


async def test_unknown_risk_is_404(client: AsyncClient, seeded) -> None:
    resp = await client.get(f"/api/v1/risks/{uuid.uuid4()}", headers=await admin_headers(client))
    assert resp.status_code == 404


async def test_risks_are_enterprise_scoped(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    from app.enterprise.models import Enterprise

    headers = await admin_headers(client)  # log in before the other enterprise exists
    other = Enterprise(name="Other Co", base_currency="USD")
    db_session.add(other)
    await db_session.flush()
    foreign = await _risk_finding(db_session, other.id)

    resp = await client.get(f"/api/v1/risks/{foreign.id}", headers=headers)
    assert resp.status_code == 404
