"""Vendored LORM policy validator wrapper (T019).

Confirms the vendored schema + semantic checks are reachable and enforce the rules Smart
Procurement relies on (SPEC 8-1 author ≠ approver; L5 requires policy/bounds/verification).
"""

from __future__ import annotations

from app.policies.schema_validator import validate_policy_doc

_META = {"project": "smart-procurement", "owner": "ops"}


def test_minimal_valid_policy() -> None:
    result = validate_policy_doc({"lorm_policy": "1.0", "metadata": _META})
    assert result.valid
    assert result.errors == []


def test_schema_violation_reported() -> None:
    result = validate_policy_doc({"lorm_policy": "bad", "metadata": {"project": "x"}})
    assert not result.valid
    assert any("lorm_policy" in e for e in result.errors)
    assert any("owner" in e for e in result.errors)


def test_author_equals_approver_rejected() -> None:
    doc = {
        "lorm_policy": "1.0",
        "metadata": _META,
        "capabilities": [
            {
                "id": "proc.replenish.routine",
                "level": "L5",
                "bounds": {"targets": ["*"]},
                "verification": {"expect": "received == ordered"},
                "policy": {
                    "version": 1,
                    "author": "alice",
                    "approved_by": "alice",
                    "approved_at": "2026-01-01",
                    "expires": "2026-12-31",
                    "tested": "canary 2026-01",
                },
            }
        ],
    }
    result = validate_policy_doc(doc)
    assert not result.valid
    assert any("approved_by must differ from" in e for e in result.errors)


def test_l5_without_required_blocks_rejected() -> None:
    doc = {
        "lorm_policy": "1.0",
        "metadata": _META,
        "capabilities": [{"id": "proc.replenish.routine", "level": "L5"}],
    }
    result = validate_policy_doc(doc)
    assert not result.valid
