"""REST source connector (T047): JSON over HTTP.

``config`` (non-secret): ``base_url``, ``resource_paths`` (list), ``pagination``
(``{param, size_param, size}`` — optional page walk), ``incremental_param`` (query param
that carries ``since``), ``records_key`` (where the row list lives in the response body).
``credential`` (from ``SecretStore``): ``{kind: bearer|basic|api_key, ...}`` — never logged,
never sent to the LLM (FR-068).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.integration.connectors.base import (
    ConnectionCheck,
    ConnectorError,
    FetchBatch,
    SourceField,
    infer_type,
)

_SAMPLE_ROWS = 5
_MAX_PAGES = 50
_TIMEOUT_S = 15.0


class RestSourceConnector:
    connector_type = "rest"

    def __init__(self, config: dict[str, Any], credential: dict[str, Any] | None = None) -> None:
        self._base_url = str(config.get("base_url", "")).rstrip("/")
        self._paths: list[str] = list(config.get("resource_paths") or [])
        self._pagination: dict[str, Any] = dict(config.get("pagination") or {})
        self._incremental_param = config.get("incremental_param")
        self._records_key = config.get("records_key")
        self._credential = credential or {}
        if not self._base_url:
            raise ConnectorError("rest connector requires config.base_url")

    async def test_connection(self) -> ConnectionCheck:
        now = datetime.now(UTC)
        target = self._url(self._paths[0]) if self._paths else self._base_url
        try:
            async with self._client() as client:
                resp = await client.get(target)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return ConnectionCheck(health="unavailable", detail=repr(exc), checked_at=now)
        return ConnectionCheck(
            health="available", detail=f"{resp.status_code} from {target}", checked_at=now
        )

    async def describe_schema(self) -> list[SourceField]:
        fields: dict[str, list[str]] = {}
        async with self._client() as client:
            for path in self._paths or [""]:
                rows = self._extract(await self._get_json(client, self._url(path)))
                prefix = f"{path.strip('/')}." if len(self._paths) > 1 and path else ""
                for row in rows[:_SAMPLE_ROWS]:
                    for key, val in _flatten(row).items():
                        fields.setdefault(f"{prefix}{key}", [])
                        if val not in (None, "") and len(fields[f"{prefix}{key}"]) < _SAMPLE_ROWS:
                            fields[f"{prefix}{key}"].append(str(val))
        return [
            SourceField(path=p, inferred_type=infer_type(s), sample_values=s)
            for p, s in fields.items()
        ]

    async def fetch(self, since: datetime | None) -> FetchBatch:
        records: list[dict[str, Any]] = []
        async with self._client() as client:
            for path in self._paths or [""]:
                prefix = f"{path.strip('/')}." if len(self._paths) > 1 and path else ""
                for row in await self._walk(client, self._url(path), since):
                    flat = _flatten(row)
                    records.append({f"{prefix}{k}": v for k, v in flat.items()} if prefix else flat)
        return FetchBatch(
            records=records, cursor=datetime.now(UTC).isoformat(), fetched_at=datetime.now(UTC)
        )

    # -- internals --------------------------------------------------------------

    def _url(self, path: str) -> str:
        return self._base_url if not path else f"{self._base_url}/{path.lstrip('/')}"

    def _client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        kind = self._credential.get("kind")
        auth = None
        if kind == "bearer":
            headers["Authorization"] = f"Bearer {self._credential.get('token', '')}"
        elif kind == "api_key":
            headers[str(self._credential.get("header", "X-API-Key"))] = str(
                self._credential.get("value", "")
            )
        elif kind == "basic":
            auth = (self._credential.get("username", ""), self._credential.get("password", ""))
        return httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers, auth=auth)

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Any:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GET {url} failed: {exc!r}") from exc
        except ValueError as exc:
            raise ConnectorError(f"GET {url} returned non-JSON: {exc}") from exc

    def _extract(self, body: Any) -> list[dict[str, Any]]:
        if self._records_key and isinstance(body, dict):
            body = body.get(self._records_key, [])
        if isinstance(body, dict):
            body = body.get("records", body.get("data", body.get("items", [body])))
        if not isinstance(body, list):
            return []
        return [r for r in body if isinstance(r, dict)]

    async def _walk(
        self, client: httpx.AsyncClient, url: str, since: datetime | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if since and self._incremental_param:
            params[self._incremental_param] = since.isoformat()
        page_param = self._pagination.get("param")
        size_param = self._pagination.get("size_param")
        size = self._pagination.get("size")
        if size_param and size:
            params[size_param] = size

        out: list[dict[str, Any]] = []
        page = 1
        while page <= _MAX_PAGES:
            if page_param:
                params[page_param] = page
            q = "&".join(f"{k}={v}" for k, v in params.items())
            rows = self._extract(await self._get_json(client, f"{url}?{q}" if q else url))
            out.extend(rows)
            if not page_param or not rows or (size and len(rows) < int(size)):
                break
            page += 1
        return out


def _flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, val in row.items():
        name = f"{prefix}{key}"
        if isinstance(val, dict):
            flat.update(_flatten(val, f"{name}."))
        elif isinstance(val, list):
            flat[name] = ",".join(str(x) for x in val if not isinstance(x, dict | list))
        else:
            flat[name] = val
    return flat
