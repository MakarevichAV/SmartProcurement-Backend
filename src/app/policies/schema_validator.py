"""Adapter around the vendored LORM policy validator (T019).

Wraps ``vendor/lorm/lorm-policy.schema.json`` + ``vendor/lorm/validate_policy.py`` for use
inside the backend: takes a canonical policy dict and returns a structured result instead of
printing + ``sys.exit``. The vendored files are never modified (research.md §6a).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from app.vendor.lorm import POLICY_SCHEMA_PATH
from app.vendor.lorm.validate_policy import semantic_checks

_SCHEMA: dict[str, Any] = json.loads(POLICY_SCHEMA_PATH.read_text())
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)


@dataclass
class PolicyValidation:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_policy_doc(doc: dict[str, Any]) -> PolicyValidation:
    """Validate a policy document against the vendored LORM schema + semantic rules."""
    schema_errors = [
        f"at {'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(_VALIDATOR.iter_errors(doc), key=lambda e: list(e.path))
    ]
    sem_errors, sem_warnings = semantic_checks(doc)
    all_errors = schema_errors + list(sem_errors)
    return PolicyValidation(valid=not all_errors, errors=all_errors, warnings=list(sem_warnings))
