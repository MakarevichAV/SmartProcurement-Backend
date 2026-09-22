"""Contract tests for `GET /api/v1/dashboard`.

A thin Phase-3 read model: the L2/L3/L4 LORM counters are ``null`` (their subsystems do
not exist yet — never a fake ``0``); L5/autopilot is a real capability-level count; the
data-health block is aggregated from current data sources + canonical rows + observability
gaps. Everything enterprise-scoped, permission ``domain.read``.

**Phase 4 additions (T064)**: once US2 lands, ``lorm.open_risks`` becomes a real count of
non-terminal ``risk_finding`` rows (replacing the Phase-3 ``null`` placeholder) — this is the
one LORM counter Phase 4 is allowed to fill in, per the strict L1/L2 boundary
(``recommendations``/``approvals`` stay ``null`` until US3/US4). T072's task text also lists
"AI-unavailable items" as part of the aggregation service; the contract doesn't name an exact
response key for it, so this file infers ``data_health["ai_unavailable_items"]`` (a data-trust
concept, grouped with the rest of ``data_health``) — flagged as an inferred, not
contract-specified, field name for T072/T073 to confirm or rename. T072's text also mentions
"pending-approval count", "active policies", and "recent executions", which are US4/US5
concepts with no backing data in Phase 4; this file does not test them, consistent with
keeping Phase 4 strictly L1/L2.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Item
from app.integration.models import DataSource
from app.lorm.models import Capability
from app.observation.models import ObservabilityGap
from tests.helpers import admin_headers, buyer_headers

pytestmark = pytest.mark.asyncio


async def _get(client: AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/v1/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_lorm_placeholders_are_null_not_zero(client: AsyncClient, seeded) -> None:
    body = await _get(client, await admin_headers(client))
    lorm = body["lorm"]
    # Phase 4 (T072): open_risks is no longer a null placeholder -- it's a real count now
    # (see test_open_risks_becomes_a_real_count_once_a_risk_finding_exists below for the
    # counting behavior itself). recommendations/approvals stay null until US3/US4.
    assert isinstance(lorm["open_risks"], int)
    assert lorm["recommendations"] is None
    assert lorm["approvals"] is None
    assert isinstance(lorm["autopilot"], int)  # real count, 0 by seed (no L5)
    assert set(body["data_health"]) == {
        "sources_total",
        "sources_available",
        "sources_unavailable",
        "sources_stale",
        "last_successful_sync",
        "canonical_rows",
        "rows_fresh",
        "rows_stale",
        "rows_lost",
        "open_observability_gaps",
        "ai_unavailable_items",  # Phase 4 (T072)
    }


async def test_autopilot_counts_l5_capabilities(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    headers = await admin_headers(client)
    before = (await _get(client, headers))["lorm"]["autopilot"]

    cap = (
        await db_session.execute(
            select(Capability).where(
                Capability.enterprise_id == seeded.id,
                Capability.key == "proc.replenish.routine",
            )
        )
    ).scalar_one()
    cap.level = "L5"
    await db_session.flush()

    assert (await _get(client, headers))["lorm"]["autopilot"] == before + 1


async def test_data_health_reflects_a_new_source_and_its_rows(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    headers = await admin_headers(client)
    base = (await _get(client, headers))["data_health"]

    synced_at = datetime.now(UTC) + timedelta(days=1)  # guaranteed to be the max
    ds = DataSource(
        enterprise_id=seeded.id,
        name="Dash source",
        kind="file",
        connector_type="file",
        config={},
        health="available",
        last_success_at=synced_at,
    )
    db_session.add(ds)
    await db_session.flush()
    for i in range(3):
        db_session.add(
            Item(
                enterprise_id=seeded.id,
                sku=f"DASH-{uuid.uuid4().hex[:6]}-{i}",
                name="x",
                source_provenance={"data_source_id": str(ds.id)},
                observability="fresh",
            )
        )
    await db_session.flush()

    now = (await _get(client, headers))["data_health"]
    assert now["sources_total"] == base["sources_total"] + 1
    assert now["sources_available"] == base["sources_available"] + 1
    assert now["canonical_rows"] == base["canonical_rows"] + 3
    assert now["rows_fresh"] == base["rows_fresh"] + 3
    assert now["last_successful_sync"] == synced_at.isoformat()


async def test_data_health_counts_unavailable_source_and_open_gap(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    headers = await admin_headers(client)
    base = (await _get(client, headers))["data_health"]

    db_session.add(
        DataSource(
            enterprise_id=seeded.id,
            name="Down source",
            kind="file",
            connector_type="file",
            config={},
            health="unavailable",
        )
    )
    db_session.add(
        ObservabilityGap(
            enterprise_id=seeded.id,
            scope="source",
            scope_ref=str(uuid.uuid4()),
            reason="source_unavailable",
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    now = (await _get(client, headers))["data_health"]
    assert now["sources_unavailable"] == base["sources_unavailable"] + 1
    assert now["open_observability_gaps"] == base["open_observability_gaps"] + 1


async def test_dashboard_allowed_for_buyer(client: AsyncClient, seeded) -> None:
    assert (
        await client.get("/api/v1/dashboard", headers=await buyer_headers(client))
    ).status_code == 200


async def test_dashboard_is_enterprise_scoped(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    from app.enterprise.models import Enterprise

    headers = await admin_headers(client)  # log in before a second enterprise exists
    base = (await _get(client, headers))["data_health"]

    other = Enterprise(name="Other Co", base_currency="USD")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        DataSource(
            enterprise_id=other.id,
            name="Foreign",
            kind="file",
            connector_type="file",
            config={},
            health="available",
        )
    )
    db_session.add(
        ObservabilityGap(
            enterprise_id=other.id,
            scope="source",
            scope_ref=str(uuid.uuid4()),
            reason="source_unavailable",
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    now = (await _get(client, headers))["data_health"]
    assert now["sources_total"] == base["sources_total"]
    assert now["open_observability_gaps"] == base["open_observability_gaps"]


async def test_open_risks_becomes_a_real_count_once_a_risk_finding_exists(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    # Local import: app.analysis.models is Phase 4 (T069), not yet implemented. Keeping this
    # import inside the test (rather than module-level) lets the rest of this file's existing
    # Phase-3 tests keep collecting/passing before T069 lands.
    from app.analysis.models import RiskFinding

    headers = await admin_headers(client)
    before = (await _get(client, headers))["lorm"]["open_risks"]

    item = Item(enterprise_id=seeded.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", name="x")
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        RiskFinding(
            enterprise_id=seeded.id,
            risk_type="likely_shortage",
            item_id=item.id,
            supplier_id=None,
            severity="high",
            status="open",
            detected_by="rule",
            ai_status="pending",
            detected_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    after = (await _get(client, headers))["lorm"]["open_risks"]
    assert isinstance(after, int)  # no longer the Phase-3 null placeholder
    assert after == (before or 0) + 1


async def test_open_risks_excludes_terminal_findings(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    from app.analysis.models import RiskFinding

    headers = await admin_headers(client)
    before = (await _get(client, headers))["lorm"]["open_risks"]

    item = Item(enterprise_id=seeded.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", name="x")
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        RiskFinding(
            enterprise_id=seeded.id,
            risk_type="likely_shortage",
            item_id=item.id,
            supplier_id=None,
            severity="low",
            status="dismissed",
            detected_by="rule",
            ai_status="pending",
            detected_at=datetime.now(UTC),
            resolved_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    after = (await _get(client, headers))["lorm"]["open_risks"]
    assert after == (before or 0)  # dismissed findings aren't "open"


async def test_data_health_counts_ai_unavailable_risk_findings(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    from app.analysis.models import RiskFinding

    headers = await admin_headers(client)
    base = (await _get(client, headers))["data_health"]

    item = Item(enterprise_id=seeded.id, sku=f"SKU-{uuid.uuid4().hex[:8]}", name="x")
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        RiskFinding(
            enterprise_id=seeded.id,
            risk_type="price_anomaly",
            item_id=item.id,
            supplier_id=None,
            severity="med",
            status="open",
            detected_by="rule",
            ai_status="unavailable",
            detected_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    now = (await _get(client, headers))["data_health"]
    assert now["ai_unavailable_items"] == base.get("ai_unavailable_items", 0) + 1
