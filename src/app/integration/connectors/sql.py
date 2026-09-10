"""SQL source connector (T049): read-only queries against an external database.

``config`` (non-secret): ``dsn`` (SQLAlchemy URL **without** credentials, e.g.
``postgresql+asyncpg://db-host/warehouse``), ``queries`` (``{entity: "SELECT ..."}``).
``credential`` (from ``SecretStore``): ``{username, password}``.

**Read-only**: any statement that is not a single ``SELECT`` is rejected before it runs
(contracts/source-connector.md). Row keys are prefixed ``<entity>.<column>`` so a mapping's
``source_field_path`` stays unambiguous across queries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.integration.connectors.base import (
    ConnectionCheck,
    ConnectorError,
    FetchBatch,
    SourceField,
    infer_type,
)

_SAMPLE_ROWS = 5
_MAX_ROWS = 50_000


def _assert_select_only(sql: str) -> None:
    stripped = (
        "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        .strip()
        .rstrip(";")
        .lstrip("(")
        .strip()
    )
    lowered = stripped.lower()
    if not lowered.startswith(("select", "with ")):
        raise ConnectorError("SQL source accepts read-only SELECT/WITH queries only")
    forbidden = (
        "insert ",
        "update ",
        "delete ",
        "drop ",
        "alter ",
        "create ",
        "truncate ",
        "grant ",
        "revoke ",
        "merge ",
        "call ",
        "exec ",
    )
    if any(tok in f" {lowered} " for tok in forbidden):
        raise ConnectorError("SQL source query contains a forbidden write/DDL keyword")


class SqlSourceConnector:
    connector_type = "sql"

    def __init__(self, config: dict[str, Any], credential: dict[str, Any] | None = None) -> None:
        dsn = config.get("dsn") or config.get("dsn_without_credentials")
        if not dsn:
            raise ConnectorError("sql connector requires config.dsn")
        self._queries: dict[str, str] = dict(config.get("queries") or {})
        for sql in self._queries.values():
            _assert_select_only(sql)
        cred = credential or {}
        url = make_url(str(dsn))
        if cred.get("username"):
            url = url.set(username=cred["username"], password=cred.get("password"))
        self._url = url

    def _engine(self) -> AsyncEngine:
        return create_async_engine(self._url, pool_pre_ping=True)

    async def test_connection(self) -> ConnectionCheck:
        now = datetime.now(UTC)
        engine = self._engine()
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            return ConnectionCheck(health="unavailable", detail=repr(exc), checked_at=now)
        finally:
            await engine.dispose()
        return ConnectionCheck(health="available", detail="SELECT 1 ok", checked_at=now)

    async def describe_schema(self) -> list[SourceField]:
        fields: list[SourceField] = []
        engine = self._engine()
        try:
            async with engine.connect() as conn:
                for entity, sql in self._queries.items():
                    rows = (await conn.execute(text(_limit(sql, _SAMPLE_ROWS)))).mappings().all()
                    if not rows:
                        continue
                    for col in rows[0].keys():
                        samples = [str(r[col]) for r in rows if r[col] not in (None, "")]
                        fields.append(
                            SourceField(
                                path=f"{entity}.{col}",
                                inferred_type=infer_type(samples),
                                sample_values=samples,
                            )
                        )
        except SQLAlchemyError as exc:
            raise ConnectorError(f"describe_schema failed: {exc}") from exc
        finally:
            await engine.dispose()
        return fields

    async def fetch(self, since: datetime | None) -> FetchBatch:
        records: list[dict[str, Any]] = []
        engine = self._engine()
        try:
            async with engine.connect() as conn:
                for entity, sql in self._queries.items():
                    rows = (await conn.execute(text(_limit(sql, _MAX_ROWS)))).mappings().all()
                    for r in rows:
                        records.append({f"{entity}.{k}": v for k, v in dict(r).items()})
        except SQLAlchemyError as exc:
            raise ConnectorError(f"fetch failed: {exc}") from exc
        finally:
            await engine.dispose()
        return FetchBatch(
            records=records, cursor=datetime.now(UTC).isoformat(), fetched_at=datetime.now(UTC)
        )


def _limit(sql: str, n: int) -> str:
    return f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) AS _sp_sub LIMIT {n}"
