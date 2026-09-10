"""Contract tests for `/domain/map` and `/domain/{entity}` (T041).

The L0 map exposes, per entity, where the data came from (`source_provenance`) and whether
the source is still observable (`observability`) — data-model.md §3, FR-008..FR-011.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Item, Supplier
from tests.helpers import admin_headers, buyer_headers

pytestmark = pytest.mark.asyncio


async def _seed_rows(session: AsyncSession, enterprise_id: uuid.UUID) -> None:
    prov = {
        "data_source_id": str(uuid.uuid4()),
        "source_field_path": "sku",
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    session.add(
        Item(
            enterprise_id=enterprise_id,
            sku="A-1",
            name="Widget",
            category="hardware",
            unit="ea",
            is_active=True,
            source_provenance=prov,
            observability="fresh",
        )
    )
    session.add(
        Supplier(
            enterprise_id=enterprise_id,
            code="ACME",
            name="Acme Corp",
            is_approved=True,
            notes="",
            source_provenance=prov,
            observability="fresh",
        )
    )
    await session.flush()


async def test_domain_map_summary_has_provenance_and_observability(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    await _seed_rows(db_session, seeded.id)
    headers = await admin_headers(client)
    resp = await client.get("/api/v1/domain/map", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entities = {e["entity"]: e for e in body["entities"]}
    assert entities["item"]["count"] == 1
    assert entities["supplier"]["count"] == 1
    # per-entity provenance + observability summary present
    assert "sources" in entities["item"]
    assert "observability" in entities["item"]
    assert "relationships" in body


async def test_domain_entity_rows_carry_provenance(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    await _seed_rows(db_session, seeded.id)
    headers = await admin_headers(client)
    resp = await client.get("/api/v1/domain/item", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["sku"] == "A-1"
    assert body["items"][0]["source_provenance"]["source_field_path"] == "sku"
    assert body["items"][0]["observability"] == "fresh"


async def test_domain_read_allowed_for_buyer(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    await _seed_rows(db_session, seeded.id)
    headers = await buyer_headers(client)  # buyer has domain.read
    assert (await client.get("/api/v1/domain/map", headers=headers)).status_code == 200


async def test_domain_unknown_entity_is_404(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    resp = await client.get("/api/v1/domain/not-an-entity", headers=headers)
    assert resp.status_code == 404
