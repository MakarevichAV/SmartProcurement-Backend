"""US1 onboarding journey (T043).

file source -> test -> introspect -> AI suggest -> confirm a subset -> sync -> canonical
domain rows created, each carrying `source_provenance` and `observability` (FR-007/FR-013).
Only *confirmed* mappings are applied (FR-004/FR-005): price mappings left unconfirmed here,
so no `price` rows appear.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import DeterministicMockProvider
from app.domain.models import Item, Price, Supplier
from app.integration.mapping_ai import suggest_mappings_for_source
from app.integration.mapping_service import confirm_mapping
from app.integration.models import DataSource, FieldMapping
from app.integration.service import check_connection, create_data_source, describe_and_store
from app.integration.sync import sync_source

pytestmark = pytest.mark.asyncio

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "demo_enterprise.csv"

_SUGGESTIONS = [
    ("sku", "item", "sku"),
    ("name", "item", "name"),
    ("category", "item", "category"),
    ("unit", "item", "unit"),
    ("supplier_code", "supplier", "code"),
    ("supplier_name", "supplier", "name"),
    ("unit_price", "price", "unit_price"),
    ("sku", "price", "item_sku"),
    ("supplier_code", "price", "supplier_code"),
]


def _mock_provider() -> DeterministicMockProvider:
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
                    "source_field_path": p,
                    "canonical_entity": e,
                    "canonical_attribute": a,
                    "transform": None,
                    "confidence": 0.8,
                    "reasoning": "demo",
                }
                for (p, e, a) in _SUGGESTIONS
            ],
        },
    )
    return mock


async def test_onboarding_journey_populates_domain_with_provenance(
    db_session: AsyncSession, seeded
) -> None:
    csv_text = _FIXTURE.read_text()

    src = await create_data_source(
        db_session,
        enterprise_id=seeded.id,
        name="Demo enterprise export",
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True, "content": csv_text},
        credential=None,
        created_by=uuid.uuid4(),
    )

    check = await check_connection(db_session, enterprise_id=seeded.id, data_source_id=src.id)
    assert check.health == "available"

    fields = await describe_and_store(db_session, enterprise_id=seeded.id, data_source_id=src.id)
    assert {f.path for f in fields} >= {"sku", "supplier_code", "unit_price"}

    await suggest_mappings_for_source(
        db_session,
        enterprise_id=seeded.id,
        data_source_id=src.id,
        provider=_mock_provider(),
    )

    # confirm only item + supplier mappings; leave price mappings 'suggested'
    to_confirm = (
        (
            await db_session.execute(
                select(FieldMapping).where(
                    FieldMapping.data_source_id == src.id,
                    FieldMapping.canonical_entity.in_(("item", "supplier")),
                )
            )
        )
        .scalars()
        .all()
    )
    assert to_confirm
    for m in to_confirm:
        await confirm_mapping(
            db_session, enterprise_id=seeded.id, mapping_id=m.id, actor_id=uuid.uuid4()
        )

    result = await sync_source(db_session, enterprise_id=seeded.id, data_source_id=src.id)
    assert result.upserts.get("item", 0) >= 2
    assert result.upserts.get("supplier", 0) >= 1
    # price mappings were never confirmed -> this sync created no price rows
    assert result.upserts.get("price", 0) == 0

    from_this_source = Item.source_provenance["data_source_id"].astext == str(src.id)
    items = (await db_session.execute(select(Item).where(from_this_source))).scalars().all()
    assert len(items) >= 2
    for it in items:
        assert it.observability == "fresh"
        assert "fetched_at" in it.source_provenance

    suppliers = (
        (
            await db_session.execute(
                select(Supplier).where(
                    Supplier.source_provenance["data_source_id"].astext == str(src.id)
                )
            )
        )
        .scalars()
        .all()
    )
    assert {s.code for s in suppliers}

    # no price rows attributable to this source
    price_here = (
        await db_session.execute(
            select(func.count())
            .select_from(Price)
            .where(Price.source_provenance["data_source_id"].astext == str(src.id))
        )
    ).scalar_one()
    assert price_here == 0

    # source health reflects the successful fetch
    refreshed = await db_session.get(DataSource, src.id)
    assert refreshed is not None
    assert refreshed.health == "available"
    assert refreshed.last_success_at is not None
