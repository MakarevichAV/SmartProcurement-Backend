"""append_only_guard

Create the shared ``forbid_mutation()`` trigger and attach it to the append-only tables that
exist as of Phase 2 (T027). Later phases attach it to their own append-only tables via
``app.core.migrations.attach_append_only``.

Revision ID: d8607cfff4ac
Revises: a6db370213ce
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.migrations import (
    attach_append_only,
    create_forbid_mutation_function,
    detach_append_only,
    drop_forbid_mutation_function,
)

revision: str = "d8607cfff4ac"
down_revision: str | None = "a6db370213ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPEND_ONLY_TABLES = ("audit_record", "capability_level_event")


def upgrade() -> None:
    create_forbid_mutation_function()
    for table in _APPEND_ONLY_TABLES:
        attach_append_only(table)


def downgrade() -> None:
    for table in _APPEND_ONLY_TABLES:
        detach_append_only(table)
    drop_forbid_mutation_function()
