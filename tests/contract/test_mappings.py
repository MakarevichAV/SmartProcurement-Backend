"""Contract tests for `/mappings` (T040).

create / `/confirm` / `/reject` / `/retire` / PATCH and `/data-sources/{id}/mappings`
per contracts/rest-api.md. Only `confirmed` mappings feed sync (FR-004/FR-005); every
lifecycle transition writes a `mapping_change_event` (data-model.md §2).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.helpers import admin_headers, buyer_headers

pytestmark = pytest.mark.asyncio

_CSV = "sku,name\nA-1,Widget\n"


async def _source(client: AsyncClient, headers: dict) -> str:
    resp = await client.post(
        "/api/v1/data-sources",
        json={
            "name": "src",
            "kind": "file",
            "connector_type": "file",
            "config": {"format": "csv", "has_header": True, "content": _CSV},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


async def _mapping(client: AsyncClient, headers: dict, source_id: str, **over) -> dict:
    body = {
        "data_source_id": source_id,
        "source_field_path": "sku",
        "canonical_entity": "item",
        "canonical_attribute": "sku",
    }
    body.update(over)
    resp = await client.post("/api/v1/mappings", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_create_mapping_is_suggested(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    m = await _mapping(client, headers, source_id)
    assert m["status"] == "suggested"
    assert m["confirmed_by"] is None


async def test_list_source_mappings(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    await _mapping(client, headers, source_id)
    await _mapping(client, headers, source_id, source_field_path="name", canonical_attribute="name")
    resp = await client.get(f"/api/v1/data-sources/{source_id}/mappings", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


async def test_confirm_sets_status_and_writes_event(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    m = await _mapping(client, headers, source_id)
    resp = await client.post(f"/api/v1/mappings/{m['id']}/confirm", headers=headers)
    assert resp.status_code == 200, resp.text
    confirmed = resp.json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_by"] is not None

    hist = await client.get(f"/api/v1/mappings/{m['id']}/history", headers=headers)
    assert hist.status_code == 200
    actions = [e["action"] for e in hist.json()["items"]]
    assert "confirmed" in actions


async def test_reject_and_retire(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    m1 = await _mapping(client, headers, source_id)
    r = await client.post(f"/api/v1/mappings/{m1['id']}/reject", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    m2 = await _mapping(
        client, headers, source_id, source_field_path="name", canonical_attribute="name"
    )
    await client.post(f"/api/v1/mappings/{m2['id']}/confirm", headers=headers)
    ret = await client.post(f"/api/v1/mappings/{m2['id']}/retire", headers=headers)
    assert ret.status_code == 200
    assert ret.json()["status"] == "retired"


async def test_patch_edits_confirmed_mapping_and_logs(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    m = await _mapping(client, headers, source_id)
    await client.post(f"/api/v1/mappings/{m['id']}/confirm", headers=headers)
    resp = await client.patch(
        f"/api/v1/mappings/{m['id']}",
        json={"transform": {"trim": True}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["transform"] == {"trim": True}
    hist = await client.get(f"/api/v1/mappings/{m['id']}/history", headers=headers)
    assert "edited" in [e["action"] for e in hist.json()["items"]]


async def test_confirm_requires_mapping_confirm_permission(client: AsyncClient, seeded) -> None:
    admin = await admin_headers(client)
    source_id = await _source(client, admin)
    m = await _mapping(client, admin, source_id)
    buyer = await buyer_headers(client)  # buyer lacks mapping.confirm
    resp = await client.post(f"/api/v1/mappings/{m['id']}/confirm", headers=buyer)
    assert resp.status_code == 403
