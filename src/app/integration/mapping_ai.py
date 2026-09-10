"""AI-assisted field-mapping suggestions (T051).

The LLM *proposes* how raw source fields map onto the canonical domain; every proposal is
persisted as ``field_mapping(status=suggested)`` and a human must ``confirm`` it before sync
uses it (FR-003/FR-004, Constitution IX). The AI never makes a mapping authoritative.

Anti-hallucination (contracts/ai-structured-output.md §1): a suggestion whose
``source_field_path`` is not in the introspected ``source_field`` set is dropped. Invalid /
timed-out / unavailable output goes through the shared fail-safe path (FR-016a).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider
from app.ai.schemas import MappingSuggestionSet
from app.ai.structured import generate_structured
from app.core.errors import DomainRuleError
from app.integration.models import FieldMapping, MappingChangeEvent, SourceField
from app.integration.service import get_data_source

_CAPABILITY_KEY = "proc.inventory.observe"  # L0 "know what exists"

_SYSTEM = (
    "You map raw data-source fields onto Smart Procurement's canonical procurement domain "
    "(item, warehouse, stock_level, supplier, item_supplier, price, lead_time, "
    "purchase_order, consumption, production_demand, quality_record). Only propose mappings "
    "for fields that are present in the provided list. You never apply a mapping; a human "
    "confirms every one."
)


def _snapshot(m: FieldMapping) -> dict[str, Any]:
    return {
        "source_field_path": m.source_field_path,
        "canonical_entity": m.canonical_entity,
        "canonical_attribute": m.canonical_attribute,
        "transform": m.transform,
        "status": m.status,
        "ai_confidence": float(m.ai_confidence) if m.ai_confidence is not None else None,
    }


def _build_prompt(fields: list[SourceField]) -> str:
    lines = ["Suggest canonical mappings for these source fields:"]
    for f in sorted(fields, key=lambda x: x.path):
        samples = ", ".join(str(v) for v in (f.sample_values or [])[:3])
        lines.append(f"- {f.path} (type={f.inferred_type}; e.g. {samples})")
    return "\n".join(lines)


async def suggest_mappings_for_source(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    provider: LLMProvider | None = None,
    correlation_id: uuid.UUID | None = None,
) -> list[FieldMapping]:
    ds = await get_data_source(session, enterprise_id, data_source_id)
    fields = list(
        (await session.execute(select(SourceField).where(SourceField.data_source_id == ds.id)))
        .scalars()
        .all()
    )
    if not fields:
        raise DomainRuleError("introspect the data source before requesting mapping suggestions")
    known = {f.path for f in fields}

    result = await generate_structured(
        MappingSuggestionSet,
        session=session,
        enterprise_id=enterprise_id,
        capability_key=_CAPABILITY_KEY,
        system=_SYSTEM,
        prompt=_build_prompt(fields),
        provider=provider,
        correlation_id=correlation_id,
    )

    seen = {
        (m.source_field_path, m.canonical_entity, m.canonical_attribute)
        for m in (
            await session.execute(select(FieldMapping).where(FieldMapping.data_source_id == ds.id))
        )
        .scalars()
        .all()
    }

    created: list[FieldMapping] = []
    for s in result.suggestions:
        if s.source_field_path not in known:
            continue  # dropped: field not in the introspected set
        key = (s.source_field_path, s.canonical_entity, s.canonical_attribute)
        if key in seen:
            continue
        mapping = FieldMapping(
            data_source_id=ds.id,
            source_field_path=s.source_field_path,
            canonical_entity=s.canonical_entity,
            canonical_attribute=s.canonical_attribute,
            transform=s.transform,
            status="suggested",
            ai_confidence=round(s.confidence, 3),
        )
        session.add(mapping)
        await session.flush()
        session.add(
            MappingChangeEvent(
                field_mapping_id=mapping.id,
                action="suggested",
                actor_id=None,
                before=None,
                after=_snapshot(mapping),
                at=datetime.now(UTC),
            )
        )
        created.append(mapping)
        seen.add(key)

    await session.flush()
    return created
