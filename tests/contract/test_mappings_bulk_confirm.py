"""Contract tests for `POST /mappings/bulk-confirm` (bulk field-mapping confirmation).

One request confirms every eligible (belongs-to-source + status=``suggested``) mapping in
the list; already confirmed / rejected / retired ones are untouched and reported back.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.helpers import admin_headers, buyer_headers

pytestmark = pytest.mark.asyncio

_CSV = "sku,name,qty\nA-1,Widget,5\n"


async def _source(client: AsyncClient, headers: dict, name: str = "src") -> str:
    resp = await client.post(
        "/api/v1/data-sources",
        json={
            "name": name,
            "kind": "file",
            "connector_type": "file",
            "config": {"format": "csv", "has_header": True, "content": _CSV},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


async def _mapping(client: AsyncClient, headers: dict, source_id: str, path: str, attr: str) -> str:
    resp = await client.post(
        "/api/v1/mappings",
        json={
            "data_source_id": source_id,
            "source_field_path": path,
            "canonical_entity": "item",
            "canonical_attribute": attr,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


async def _statuses(client: AsyncClient, headers: dict, source_id: str) -> dict[str, str]:
    resp = await client.get(f"/api/v1/data-sources/{source_id}/mappings", headers=headers)
    assert resp.status_code == 200
    return {m["id"]: m["status"] for m in resp.json()["items"]}


async def test_bulk_confirm_confirms_all_suggested_in_one_request(
    client: AsyncClient, seeded
) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    ids = [await _mapping(client, headers, source_id, f"c{i}", f"attr{i}") for i in range(6)]

    resp = await client.post(
        "/api/v1/mappings/bulk-confirm",
        json={"data_source_id": source_id, "mapping_ids": ids},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data_source_id"] == source_id
    assert sorted(body["requested"]) == sorted(ids)
    assert sorted(body["confirmed"]) == sorted(ids)
    assert body["failed"] == []

    assert set((await _statuses(client, headers, source_id)).values()) == {"confirmed"}

    # lifecycle preserved: each confirm wrote a mapping_change_event
    hist = await client.get(f"/api/v1/mappings/{ids[0]}/history", headers=headers)
    assert "confirmed" in [e["action"] for e in hist.json()["items"]]


async def test_bulk_confirm_only_touches_suggested_mappings(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    source_id = await _source(client, headers)
    m_suggested_a = await _mapping(client, headers, source_id, "sa", "a")
    m_suggested_b = await _mapping(client, headers, source_id, "sb", "b")
    m_confirmed = await _mapping(client, headers, source_id, "cf", "c")
    m_rejected = await _mapping(client, headers, source_id, "rj", "d")
    m_retired = await _mapping(client, headers, source_id, "rt", "e")

    await client.post(f"/api/v1/mappings/{m_confirmed}/confirm", headers=headers)
    await client.post(f"/api/v1/mappings/{m_rejected}/reject", headers=headers)
    await client.post(f"/api/v1/mappings/{m_retired}/confirm", headers=headers)
    await client.post(f"/api/v1/mappings/{m_retired}/retire", headers=headers)

    before = await _statuses(client, headers, source_id)
    bogus = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/mappings/bulk-confirm",
        json={
            "data_source_id": source_id,
            "mapping_ids": [
                m_suggested_a,
                m_suggested_b,
                m_confirmed,
                m_rejected,
                m_retired,
                bogus,
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["confirmed"]) == sorted([m_suggested_a, m_suggested_b])
    failed_ids = {f["mapping_id"] for f in body["failed"]}
    assert failed_ids == {m_confirmed, m_rejected, m_retired, bogus}
    assert all(f.get("reason") for f in body["failed"])

    after = await _statuses(client, headers, source_id)
    # the two suggested became confirmed; everything else is exactly as before
    assert after[m_suggested_a] == "confirmed"
    assert after[m_suggested_b] == "confirmed"
    assert after[m_confirmed] == before[m_confirmed] == "confirmed"
    assert after[m_rejected] == before[m_rejected] == "rejected"
    assert after[m_retired] == before[m_retired] == "retired"


async def test_bulk_confirm_rejects_mapping_from_another_data_source(
    client: AsyncClient, seeded
) -> None:
    headers = await admin_headers(client)
    source_a = await _source(client, headers, "A")
    source_b = await _source(client, headers, "B")
    m_a = await _mapping(client, headers, source_a, "sku", "sku")
    m_b = await _mapping(client, headers, source_b, "sku", "sku")

    resp = await client.post(
        "/api/v1/mappings/bulk-confirm",
        json={"data_source_id": source_a, "mapping_ids": [m_a, m_b]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confirmed"] == [m_a]
    assert [f["mapping_id"] for f in body["failed"]] == [m_b]

    assert (await _statuses(client, headers, source_b))[m_b] == "suggested"


async def test_bulk_confirm_unknown_data_source_is_404(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    resp = await client.post(
        "/api/v1/mappings/bulk-confirm",
        json={"data_source_id": str(uuid.uuid4()), "mapping_ids": [str(uuid.uuid4())]},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_bulk_confirm_requires_mapping_confirm_permission(
    client: AsyncClient, seeded
) -> None:
    admin = await admin_headers(client)
    source_id = await _source(client, admin)
    m = await _mapping(client, admin, source_id, "sku", "sku")

    buyer = await buyer_headers(client)  # buyer lacks mapping.confirm
    resp = await client.post(
        "/api/v1/mappings/bulk-confirm",
        json={"data_source_id": source_id, "mapping_ids": [m]},
        headers=buyer,
    )
    assert resp.status_code == 403
