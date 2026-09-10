"""Deterministic heuristic output for the mock LLM provider (demo support).

The default test suite registers explicit fixtures on ``DeterministicMockProvider`` and those
always win. This module only provides a *fallback* so the quickstart demo
(``LLM_PROVIDER=mock``) still produces sensible structured output when no fixture was
registered — e.g. ``/data-sources/{id}/mapping-suggestions`` against the demo CSV.

It is a name-based heuristic, not a model: it never guesses beyond the field list it is given.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

# (regex on the source field name, canonical_entity, canonical_attribute)
_RULES: list[tuple[str, str, str]] = [
    (r"^(sku|item_?code|material|part_?no)$", "item", "sku"),
    (r"^(name|item_?name|description|title)$", "item", "name"),
    (r"^(category|group|item_?group)$", "item", "category"),
    (r"^(unit|uom|unit_?of_?measure)$", "item", "unit"),
    (r"^(warehouse_?code|wh_?code|location_?code|site)$", "warehouse", "code"),
    (r"^(warehouse_?name|wh_?name)$", "warehouse", "name"),
    (r"^(supplier_?code|vendor_?code|supplier_?id)$", "supplier", "code"),
    (r"^(supplier_?name|vendor_?name)$", "supplier", "name"),
    (r"^(supplier_?approved|is_?approved|approved)$", "supplier", "is_approved"),
    (r"^(on_?hand|on_?hand_?qty|qty_?on_?hand|stock|quantity)$", "stock_level", "quantity"),
    (r"^(min_?qty|reorder_?point|safety_?stock|min_?quantity)$", "stock_level", "min_quantity"),
    (r"^(unit_?price|price|cost)$", "price", "unit_price"),
    (r"^(currency|ccy)$", "price", "currency"),
    (r"^(lead_?time|lead_?time_?days|leadtime)$", "lead_time", "days"),
    (r"^(monthly_?consumption|consumption|usage|demand)$", "consumption", "quantity"),
    (r"^(production_?demand_?qty|production_?qty|prod_?demand)$", "production_demand", "quantity"),
    (r"^(production_?need_?by|need_?by|required_?by)$", "production_demand", "need_by"),
    (r"^(open_?po_?ref|po_?ref|po_?number|purchase_?order)$", "purchase_order", "external_ref"),
    (r"^(open_?po_?qty|po_?qty)$", "purchase_order", "quantity"),
    (
        r"^(open_?po_?expected_?at|po_?expected_?at|expected_?at|eta)$",
        "purchase_order",
        "expected_at",
    ),
]

# fields that also feed a dependent entity's foreign-key reference
_REF_RULES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        r"^(sku|item_?code)$",
        [
            ("stock_level", "item_sku"),
            ("price", "item_sku"),
            ("lead_time", "item_sku"),
            ("consumption", "item_sku"),
            ("production_demand", "item_sku"),
            ("purchase_order", "item_sku"),
        ],
    ),
    (
        r"^(supplier_?code|vendor_?code)$",
        [
            ("price", "supplier_code"),
            ("lead_time", "supplier_code"),
            ("purchase_order", "supplier_code"),
        ],
    ),
    (
        r"^(warehouse_?code|wh_?code)$",
        [("stock_level", "warehouse_code"), ("consumption", "warehouse_code")],
    ),
]

_FIELD_LINE = re.compile(r"^-\s+([^\s(]+)")


def _fields_from_prompt(prompt: str) -> list[str]:
    out: list[str] = []
    for line in prompt.splitlines():
        m = _FIELD_LINE.match(line.strip())
        if m:
            out.append(m.group(1))
    return out


def mapping_suggestions_json(prompt: str) -> str:
    suggestions = []
    for field in _fields_from_prompt(prompt):
        low = field.lower()
        for pattern, entity, attr in _RULES:
            if re.match(pattern, low):
                suggestions.append(
                    {
                        "source_field_path": field,
                        "canonical_entity": entity,
                        "canonical_attribute": attr,
                        "transform": None,
                        "confidence": 0.75,
                        "reasoning": f"field name '{field}' matches {entity}.{attr}",
                    }
                )
                break
        for pattern, refs in _REF_RULES:
            if re.match(pattern, low):
                for entity, attr in refs:
                    suggestions.append(
                        {
                            "source_field_path": field,
                            "canonical_entity": entity,
                            "canonical_attribute": attr,
                            "transform": None,
                            "confidence": 0.7,
                            "reasoning": f"'{field}' identifies the {entity} row's {attr}",
                        }
                    )
    return json.dumps(
        {
            "model_meta": {
                "provider": "mock",
                "model": "deterministic-heuristic",
                "generated_at": datetime.now(UTC).isoformat(),
            },
            "suggestions": suggestions,
        }
    )
