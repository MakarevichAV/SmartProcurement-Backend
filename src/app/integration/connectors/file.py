"""File source connector (T048): operator-uploaded CSV or JSON.

``config`` (non-secret): ``format`` (``csv`` | ``json``), ``delimiter``, ``has_header``,
``content`` (the uploaded text, stored by ``POST /data-sources/{id}/upload``). No credential.
The file is a full snapshot, so ``fetch(since)`` ignores ``since``.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from app.integration.connectors.base import (
    ConnectionCheck,
    ConnectorError,
    FetchBatch,
    SourceField,
    infer_type,
)

_SAMPLE_ROWS = 5


class FileSourceConnector:
    connector_type = "file"

    def __init__(self, config: dict[str, Any], credential: dict[str, Any] | None = None) -> None:
        self._format = str(config.get("format", "csv")).lower()
        self._delimiter = str(config.get("delimiter", ","))
        self._has_header = bool(config.get("has_header", True))
        self._content = config.get("content")

    async def test_connection(self) -> ConnectionCheck:
        now = datetime.now(UTC)
        if not self._content:
            return ConnectionCheck(
                health="unavailable", detail="no file uploaded yet", checked_at=now
            )
        try:
            rows = self._parse()
        except ConnectorError as exc:
            return ConnectionCheck(health="unavailable", detail=str(exc), checked_at=now)
        return ConnectionCheck(
            health="available", detail=f"{len(rows)} record(s) parsed", checked_at=now
        )

    async def describe_schema(self) -> list[SourceField]:
        rows = self._parse()
        if not rows:
            return []
        paths: list[str] = list(rows[0].keys())
        fields: list[SourceField] = []
        for path in paths:
            samples = [str(r[path]) for r in rows[:_SAMPLE_ROWS] if r.get(path) not in (None, "")]
            fields.append(
                SourceField(path=path, inferred_type=infer_type(samples), sample_values=samples)
            )
        return fields

    async def fetch(self, since: datetime | None) -> FetchBatch:
        return FetchBatch(records=self._parse(), cursor=None, fetched_at=datetime.now(UTC))

    # -- internals --------------------------------------------------------------

    def _parse(self) -> list[dict[str, Any]]:
        content = self._content
        if not isinstance(content, str) or not content.strip():
            raise ConnectorError("file source has no content")
        if self._format == "json":
            return self._parse_json(content)
        if self._format == "csv":
            return self._parse_csv(content)
        raise ConnectorError(f"unsupported file format {self._format!r}")

    def _parse_csv(self, content: str) -> list[dict[str, Any]]:
        try:
            if self._has_header:
                reader = csv.DictReader(io.StringIO(content), delimiter=self._delimiter)
                return [dict(row) for row in reader]
            plain = csv.reader(io.StringIO(content), delimiter=self._delimiter)
            return [{f"col_{i}": val for i, val in enumerate(row)} for row in plain if row]
        except csv.Error as exc:
            raise ConnectorError(f"CSV parse error: {exc}") from exc

    def _parse_json(self, content: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ConnectorError(f"JSON parse error: {exc}") from exc
        if isinstance(data, dict):
            # accept {"records": [...]} or a single object
            data = data.get("records", [data])
        if not isinstance(data, list):
            raise ConnectorError("JSON file must be a list of objects or {records: [...]}")
        return [row for row in data if isinstance(row, dict)]
