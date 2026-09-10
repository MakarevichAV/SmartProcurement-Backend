"""Contract tests for the enriched `GET /domain/{entity}` detail rows.

FK columns are resolved to a business label (``references``); ``provenance`` names the
connected data source and splits the contributing source fields. ``source_provenance`` is
left untouched for back-compat. Both enrichments are enterprise-scoped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Item, LeadTime, Price, PurchaseOrder, StockLevel, Supplier, Warehouse
from app.integration.models import DataSource
from tests.helpers import admin_headers

pytestmark = pytest.mark.asyncio


def _prov(ds_id: uuid.UUID, fields: str) -> dict:
    return {
        "data_source_id": str(ds_id),
        "source_field_path": fields,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


async def _graph(session: AsyncSession, enterprise_id: uuid.UUID) -> dict:
    tag = uuid.uuid4().hex[:8]
    ds = DataSource(
        enterprise_id=enterprise_id,
        name=f"Demo Enterprise CSV {tag}",
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True, "content": "x\n1\n"},
        health="available",
    )
    session.add(ds)
    await session.flush()

    item = Item(
        enterprise_id=enterprise_id,
        sku=f"SKU-{tag}",
        name="Hex Bolt M8",
        category="fasteners",
        unit="ea",
        is_active=True,
        source_provenance=_prov(ds.id, "sku,name,category,unit"),
        observability="fresh",
    )
    wh = Warehouse(
        enterprise_id=enterprise_id,
        code=f"WH-{tag}",
        name="Main store",
        source_provenance=_prov(ds.id, "warehouse_code"),
        observability="fresh",
    )
    sup = Supplier(
        enterprise_id=enterprise_id,
        code=f"ACME-{tag}",
        name="Acme Industrial",
        is_approved=True,
        notes="",
        source_provenance=_prov(ds.id, "supplier_code,supplier_name"),
        observability="fresh",
    )
    session.add_all([item, wh, sup])
    await session.flush()

    session.add(
        StockLevel(
            enterprise_id=enterprise_id,
            item_id=item.id,
            warehouse_id=wh.id,
            quantity=120,
            min_quantity=400,
            source_provenance=_prov(ds.id, "min_qty,on_hand_qty,sku,warehouse_code"),
            observability="fresh",
        )
    )
    session.add(
        Price(
            enterprise_id=enterprise_id,
            item_id=item.id,
            supplier_id=sup.id,
            unit_price=12.5,
            currency="USD",
            source_provenance=_prov(ds.id, "sku,supplier_code,unit_price,currency"),
            observability="fresh",
        )
    )
    session.add(
        LeadTime(
            enterprise_id=enterprise_id,
            item_id=item.id,
            supplier_id=sup.id,
            days=7,
            source_provenance=_prov(ds.id, "sku,supplier_code,lead_time_days"),
            observability="fresh",
        )
    )
    session.add(
        PurchaseOrder(
            enterprise_id=enterprise_id,
            external_ref=f"PO-{tag}",
            item_id=item.id,
            supplier_id=sup.id,
            quantity=2000,
            status="open",
            origin="external",
            source_provenance=_prov(ds.id, "open_po_ref,open_po_qty,sku,supplier_code"),
            observability="fresh",
        )
    )
    await session.flush()
    return {
        "ds": ds,
        "item": item,
        "wh": wh,
        "sup": sup,
        "sku": item.sku,
        "wh_code": wh.code,
        "sup_code": sup.code,
    }


async def _rows(client: AsyncClient, headers: dict, entity: str) -> list[dict]:
    resp = await client.get(f"/api/v1/domain/{entity}?limit=200", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def test_stock_level_row_is_readable_without_uuids(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    g = await _graph(db_session, seeded.id)
    headers = await admin_headers(client)
    row = next(r for r in await _rows(client, headers, "stock-level") if r["quantity"] == 120.0)

    # references: business label present, raw uuid kept as technical detail
    refs = row["references"]
    assert refs["item_id"]["entity"] == "item"
    assert refs["item_id"]["label"] == f"{g['sku']} — Hex Bolt M8"
    assert refs["item_id"]["sku"] == g["sku"]
    assert refs["item_id"]["id"] == row["item_id"]  # raw uuid still on the row
    assert g["wh_code"] in refs["warehouse_id"]["label"]

    # provenance: names the source; keeps the field list as a list
    prov = row["provenance"]
    assert prov["data_source_name"] == g["ds"].name
    assert prov["data_source_id"] == str(g["ds"].id)
    assert set(prov["source_fields"]) == {"min_qty", "on_hand_qty", "sku", "warehouse_code"}

    # source_provenance unchanged (back-compat)
    assert row["source_provenance"] == {
        "data_source_id": str(g["ds"].id),
        "source_field_path": "min_qty,on_hand_qty,sku,warehouse_code",
        "fetched_at": row["source_provenance"]["fetched_at"],
    }
    assert row["observability"] == "fresh"


@pytest.mark.parametrize("entity", ["price", "lead-time", "purchase-order"])
async def test_item_and_supplier_references_resolve(
    client: AsyncClient, db_session: AsyncSession, seeded, entity: str
) -> None:
    g = await _graph(db_session, seeded.id)
    headers = await admin_headers(client)
    row = next(
        r
        for r in await _rows(client, headers, entity)
        if r.get("references", {}).get("item_id", {}).get("sku") == g["sku"]
    )
    refs = row["references"]
    assert refs["item_id"]["sku"] == g["sku"]
    assert g["sup_code"] in refs["supplier_id"]["label"]
    assert refs["supplier_id"]["entity"] == "supplier"


async def test_entity_without_foreign_keys_still_has_provenance(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    g = await _graph(db_session, seeded.id)
    headers = await admin_headers(client)
    row = next(r for r in await _rows(client, headers, "item") if r["sku"] == g["sku"])
    assert row.get("references") in (None, {})
    assert row["provenance"]["data_source_name"] == g["ds"].name


async def test_reference_and_source_name_lookups_are_enterprise_scoped(
    client: AsyncClient, db_session: AsyncSession, seeded
) -> None:
    from app.enterprise.models import Enterprise

    headers = await admin_headers(client)  # log in while there is still exactly one enterprise

    other = Enterprise(name="Other Co", base_currency="USD")
    db_session.add(other)
    await db_session.flush()
    other_ds = DataSource(
        enterprise_id=other.id,
        name="Foreign source",
        kind="file",
        connector_type="file",
        config={},
        health="available",
    )
    other_item = Item(
        enterprise_id=other.id,
        sku="FOREIGN-1",
        name="Foreign",
        source_provenance={},
        observability="fresh",
    )
    db_session.add_all([other_ds, other_item])
    await db_session.flush()

    # a row in *my* enterprise that (artificially) points at the other enterprise's rows
    db_session.add(
        StockLevel(
            enterprise_id=seeded.id,
            item_id=other_item.id,
            warehouse_id=None,
            quantity=999,
            source_provenance=_prov(other_ds.id, "sku"),
            observability="fresh",
        )
    )
    await db_session.flush()

    row = next(r for r in await _rows(client, headers, "stock-level") if r["quantity"] == 999.0)
    # neither the cross-enterprise item nor the cross-enterprise source name leak
    assert row["references"].get("item_id") in (None, {})
    assert row["provenance"]["data_source_name"] is None
