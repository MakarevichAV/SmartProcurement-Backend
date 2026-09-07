"""Reusable migration helpers (T027).

``attach_append_only(table)`` wires the shared ``forbid_mutation()`` trigger onto a table so
that UPDATE and DELETE raise at the database level. Every append-only table's own migration
calls this after ``op.create_table`` (data-model.md §8/§12, FR-063, Constitution X).
"""

from __future__ import annotations

from alembic import op

FORBID_MUTATION_FN = """
CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def create_forbid_mutation_function() -> None:
    op.execute(FORBID_MUTATION_FN)


def drop_forbid_mutation_function() -> None:
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation()")


def attach_append_only(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_append_only "
        f"BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def detach_append_only(table: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
