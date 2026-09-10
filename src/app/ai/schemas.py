"""Typed LLM output schemas (contracts/ai-structured-output.md).

Every LLM interaction goes through ``ai.structured.generate_structured`` with one of these as
the ``output_schema``; raw text that fails validation is treated exactly like AI
unavailability (FR-016a). US1 defines the mapping-suggestion schema; later stories add
``RiskExplanation``, ``ProcurementRecommendation``, ``PolicyDraft``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CanonicalEntity = Literal[
    "item",
    "warehouse",
    "stock_level",
    "supplier",
    "item_supplier",
    "price",
    "lead_time",
    "purchase_order",
    "consumption",
    "production_demand",
    "quality_record",
]


class ModelMeta(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    token_usage: dict[str, Any] | None = None


class MappingSuggestion(BaseModel):
    source_field_path: str
    canonical_entity: CanonicalEntity
    canonical_attribute: str
    transform: dict[str, Any] | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class MappingSuggestionSet(BaseModel):
    model_meta: ModelMeta
    suggestions: list[MappingSuggestion] = Field(default_factory=list)
