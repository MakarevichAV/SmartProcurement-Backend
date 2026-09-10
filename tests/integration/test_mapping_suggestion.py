"""AI mapping-suggestion output test (T042; contracts/ai-structured-output.md §1).

`MappingSuggestionSet` is schema-validated; a suggestion whose `source_field_path` is not in
the introspected `source_field` set is dropped; nothing is auto-applied — every surviving
suggestion becomes a `field_mapping(status=suggested)` awaiting human confirm (FR-004).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import DeterministicMockProvider, LLMUnavailable
from app.integration.mapping_ai import suggest_mappings_for_source
from app.integration.models import DataSource, FieldMapping, SourceField

pytestmark = pytest.mark.asyncio


async def _source_with_fields(session: AsyncSession, enterprise_id: uuid.UUID) -> DataSource:
    src = DataSource(
        enterprise_id=enterprise_id,
        name="src",
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True, "content": "sku,qty\nA-1,5\n"},
        health="unavailable",
    )
    session.add(src)
    await session.flush()
    for path, typ in (("sku", "string"), ("qty", "number")):
        session.add(
            SourceField(data_source_id=src.id, path=path, inferred_type=typ, sample_values=["A-1"])
        )
    await session.flush()
    return src


def _payload(suggestions: list[dict]) -> dict:
    return {
        "model_meta": {
            "provider": "mock",
            "model": "mock",
            "generated_at": "2026-09-10T00:00:00Z",
        },
        "suggestions": suggestions,
    }


async def test_valid_suggestions_become_suggested_mappings(
    db_session: AsyncSession, seeded
) -> None:
    src = await _source_with_fields(db_session, seeded.id)
    mock = DeterministicMockProvider()
    mock.register(
        "MappingSuggestionSet",
        _payload(
            [
                {
                    "source_field_path": "sku",
                    "canonical_entity": "item",
                    "canonical_attribute": "sku",
                    "transform": None,
                    "confidence": 0.92,
                    "reasoning": "name match",
                },
                {
                    "source_field_path": "qty",
                    "canonical_entity": "stock_level",
                    "canonical_attribute": "quantity",
                    "transform": None,
                    "confidence": 0.6,
                    "reasoning": "quantity-like",
                },
                {
                    "source_field_path": "does_not_exist",
                    "canonical_entity": "item",
                    "canonical_attribute": "name",
                    "transform": None,
                    "confidence": 0.5,
                    "reasoning": "hallucinated field",
                },
            ]
        ),
    )

    created = await suggest_mappings_for_source(
        db_session,
        enterprise_id=seeded.id,
        data_source_id=src.id,
        provider=mock,
    )

    assert {m.source_field_path for m in created} == {"sku", "qty"}  # unknown path dropped
    assert all(m.status == "suggested" for m in created)

    rows = (
        (
            await db_session.execute(
                select(FieldMapping).where(FieldMapping.data_source_id == src.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert not any(r.status == "confirmed" for r in rows)  # nothing auto-applied
    assert not any(r.confirmed_by for r in rows)


async def test_schema_invalid_output_is_treated_as_unavailable(
    db_session: AsyncSession, seeded
) -> None:
    src = await _source_with_fields(db_session, seeded.id)
    mock = DeterministicMockProvider()
    # missing required fields -> ValidationError -> fail-safe
    mock.register("MappingSuggestionSet", {"suggestions": [{"source_field_path": "sku"}]})
    with pytest.raises(LLMUnavailable):
        await suggest_mappings_for_source(
            db_session, enterprise_id=seeded.id, data_source_id=src.id, provider=mock
        )


async def test_introspect_required_before_suggesting(db_session: AsyncSession, seeded) -> None:
    src = DataSource(
        enterprise_id=seeded.id,
        name="empty",
        kind="file",
        connector_type="file",
        config={"format": "csv", "has_header": True, "content": ""},
        health="unavailable",
    )
    db_session.add(src)
    await db_session.flush()
    mock = DeterministicMockProvider()
    with pytest.raises(Exception):  # noqa: B017 - DomainRuleError, no fields discovered yet
        await suggest_mappings_for_source(
            db_session, enterprise_id=seeded.id, data_source_id=src.id, provider=mock
        )
