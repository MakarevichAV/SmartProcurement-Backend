"""SourceConnector protocol + transport-neutral DTOs (T046; contracts/source-connector.md).

``integration/`` reads enterprise data only through these connectors, so the canonical
domain model never depends on a specific ERP (Constitution IX). Connectors are read-only in
v1 — none writes back to the source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

CONNECTOR_TYPES = ("rest", "file", "sql")


class ConnectorError(RuntimeError):
    """A connected external source failed (auth, transport, parse, non-SELECT SQL, …).

    ``integration/service.py`` maps this to the unified error model (502/504) and flips the
    ``data_source`` to ``unavailable`` (contracts/source-connector.md §4).
    """


class ConnectionCheck(BaseModel):
    health: str  # "available" | "unavailable"
    detail: str = ""
    checked_at: datetime


class SourceField(BaseModel):
    """A field discovered by ``describe_schema()`` (distinct from the persisted
    ``integration.models.SourceField`` ORM row)."""

    path: str
    inferred_type: str = "string"
    sample_values: list[str] = Field(default_factory=list)


class FetchBatch(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    cursor: str | None = None
    fetched_at: datetime


@runtime_checkable
class SourceConnector(Protocol):
    connector_type: str

    async def test_connection(self) -> ConnectionCheck: ...

    async def describe_schema(self) -> list[SourceField]: ...

    async def fetch(self, since: datetime | None) -> FetchBatch: ...


def infer_type(values: list[str]) -> str:
    """Best-effort scalar type from sample string values (``number`` | ``date`` | ``string``)."""
    non_empty = [v for v in values if v not in (None, "")]
    if not non_empty:
        return "string"

    def _is_number(v: str) -> bool:
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False

    def _is_date(v: str) -> bool:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%d.%m.%Y"):
            try:
                datetime.strptime(v, fmt)
                return True
            except ValueError:
                continue
        return False

    if all(_is_number(v) for v in non_empty):
        return "number"
    if all(_is_date(v) for v in non_empty):
        return "date"
    return "string"
