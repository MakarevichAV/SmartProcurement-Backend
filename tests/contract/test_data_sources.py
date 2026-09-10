"""Contract tests for `/data-sources` (T039).

CRUD + `/test` + `/introspect` + `/mapping-suggestions` + `/health-history`
per contracts/rest-api.md ("Data Sources / Mappings").
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.ai.deps import get_llm_provider
from app.ai.provider import DeterministicMockProvider
from app.main import app
from tests.helpers import admin_headers, buyer_headers

pytestmark = pytest.mark.asyncio

_CSV = "sku,name,on_hand_qty,supplier_code\nA-1,Widget,120,ACME\nA-2,Gadget,4,ACME\n"


def _file_source_body(name: str = "Demo file") -> dict:
    return {
        "name": name,
        "kind": "file",
        "connector_type": "file",
        "config": {"format": "csv", "has_header": True, "content": _CSV},
    }


async def _create(client: AsyncClient, headers: dict) -> dict:
    resp = await client.post("/api/v1/data-sources", json=_file_source_body(), headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_create_and_list_data_source(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    created = await _create(client, headers)
    assert created["kind"] == "file"
    assert created["connector_type"] == "file"
    assert created["health"] == "unavailable"  # not tested yet
    assert "id" in created

    listed = await client.get("/api/v1/data-sources", headers=headers)
    assert listed.status_code == 200
    assert any(row["id"] == created["id"] for row in listed.json()["items"])


async def test_create_requires_datasource_manage(client: AsyncClient, seeded) -> None:
    headers = await buyer_headers(client)  # buyer lacks datasource.manage
    resp = await client.post("/api/v1/data-sources", json=_file_source_body(), headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


async def test_config_never_echoes_credential(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    body = _file_source_body("With cred")
    body["credential"] = {"kind": "bearer", "token": "super-secret-value"}
    resp = await client.post("/api/v1/data-sources", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    dumped = resp.text
    assert "super-secret-value" not in dumped
    assert "credential" not in resp.json()["config"]


async def test_test_connection_marks_available(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    created = await _create(client, headers)
    resp = await client.post(f"/api/v1/data-sources/{created['id']}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["health"] == "available"
    assert "checked_at" in body


async def test_introspect_persists_source_fields(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    created = await _create(client, headers)
    resp = await client.post(f"/api/v1/data-sources/{created['id']}/introspect", headers=headers)
    assert resp.status_code == 200, resp.text
    fields = resp.json()
    paths = {f["path"] for f in fields}
    assert {"sku", "name", "on_hand_qty", "supplier_code"} <= paths
    # a second introspect is idempotent (no duplicates)
    again = await client.post(f"/api/v1/data-sources/{created['id']}/introspect", headers=headers)
    assert len({f["path"] for f in again.json()}) == len(again.json())


async def test_mapping_suggestions_create_suggested_mappings(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    created = await _create(client, headers)
    await client.post(f"/api/v1/data-sources/{created['id']}/introspect", headers=headers)

    mock = DeterministicMockProvider()
    mock.register(
        "MappingSuggestionSet",
        {
            "model_meta": {
                "provider": "mock",
                "model": "mock",
                "generated_at": "2026-09-10T00:00:00Z",
            },
            "suggestions": [
                {
                    "source_field_path": "sku",
                    "canonical_entity": "item",
                    "canonical_attribute": "sku",
                    "transform": None,
                    "confidence": 0.9,
                    "reasoning": "column name matches",
                },
                {
                    "source_field_path": "supplier_code",
                    "canonical_entity": "supplier",
                    "canonical_attribute": "code",
                    "transform": None,
                    "confidence": 0.7,
                    "reasoning": "supplier code",
                },
            ],
        },
    )
    app.dependency_overrides[get_llm_provider] = lambda: mock
    try:
        resp = await client.post(
            f"/api/v1/data-sources/{created['id']}/mapping-suggestions", headers=headers
        )
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

    assert resp.status_code == 200, resp.text
    suggestions = resp.json()
    assert len(suggestions) == 2
    assert all(s["status"] == "suggested" for s in suggestions)
    assert {s["canonical_entity"] for s in suggestions} == {"item", "supplier"}


async def test_health_history_returns_snapshot_and_events(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    created = await _create(client, headers)
    await client.post(f"/api/v1/data-sources/{created['id']}/test", headers=headers)
    resp = await client.get(f"/api/v1/data-sources/{created['id']}/health-history", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["health"] == "available"
    assert "events" in body and isinstance(body["events"], list)


async def test_unknown_data_source_is_404(client: AsyncClient, seeded) -> None:
    headers = await admin_headers(client)
    resp = await client.post(
        "/api/v1/data-sources/00000000-0000-0000-0000-000000000000/test", headers=headers
    )
    assert resp.status_code == 404
