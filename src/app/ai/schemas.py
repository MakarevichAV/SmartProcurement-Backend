"""Typed LLM output schemas (contracts/ai-structured-output.md).

Every LLM interaction goes through ``ai.structured.generate_structured`` with one of these as
the ``output_schema``; raw text that fails validation is treated exactly like AI
unavailability (FR-016a). US1 defines the mapping-suggestion schema; US2 adds
``RiskExplanation``; later stories add ``ProcurementRecommendation``, ``PolicyDraft``.

Note: unlike ``MappingSuggestionSet``, ``RiskExplanation`` has no ``model_meta`` envelope
field — the contract's own schema block for it (contracts/ai-structured-output.md §2) omits
one, and T061's approved test contract does not supply one either.
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


EvidenceKind = Literal["observation_signal", "sku_aggregate", "domain_row"]
RiskFactorWeight = Literal["low", "med", "high"]


class EvidenceRef(BaseModel):
    kind: EvidenceKind
    ref: str  # id / natural key resolvable by the backend


class RiskFactor(BaseModel):
    name: str
    effect: str
    weight: RiskFactorWeight


class RiskExplanation(BaseModel):
    what: str
    why: str
    data_used: list[EvidenceRef] = Field(min_length=1)  # I-1: MUST be non-empty
    factors: list[RiskFactor] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)  # I-3: mandatory
