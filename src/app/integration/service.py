"""Data-source service (T050): connect, test, introspect, upload, health history.

* Credentials are handed straight to ``SecretStore`` — never stored in ``config``, never
  returned by the API, never logged, never sent to the LLM (FR-068, Constitution §15).
* ``test_connection`` updates ``data_source.health`` and opens/closes a ``source``-scoped
  ``observability_gap`` (FR-002/FR-011/FR-015).
* ``describe_schema`` persists ``source_field`` rows idempotently (FR-002).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainRuleError, NotFoundError, UpstreamError
from app.core.secrets import DbSecretStore
from app.integration.connectors.base import ConnectionCheck, ConnectorError, SourceConnector
from app.integration.connectors.registry import build_connector
from app.integration.models import (
    CONNECTOR_TYPES,
    SOURCE_KINDS,
    DataSource,
    SourceField,
)
from app.observation.models import ObservabilityGap
from app.observation.observability import ObservabilityService

_SECRET_KEYS_IN_CONFIG = ("credential", "password", "token", "secret", "api_key")


async def get_data_source(
    session: AsyncSession, enterprise_id: uuid.UUID, data_source_id: uuid.UUID
) -> DataSource:
    row = (
        await session.execute(
            select(DataSource).where(
                DataSource.id == data_source_id, DataSource.enterprise_id == enterprise_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("data source not found")
    return row


async def list_data_sources(session: AsyncSession, enterprise_id: uuid.UUID) -> list[DataSource]:
    return list(
        (
            await session.execute(
                select(DataSource)
                .where(DataSource.enterprise_id == enterprise_id)
                .order_by(DataSource.created_at.desc(), DataSource.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def create_data_source(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    name: str,
    kind: str,
    connector_type: str,
    config: dict[str, Any],
    credential: dict[str, Any] | None,
    created_by: uuid.UUID | None,
    observation_interval_seconds: int | None = None,
) -> DataSource:
    if kind not in SOURCE_KINDS:
        raise DomainRuleError(f"kind must be one of {SOURCE_KINDS}")
    if connector_type not in CONNECTOR_TYPES:
        raise DomainRuleError(f"connector_type must be one of {CONNECTOR_TYPES}")

    safe_config = {k: v for k, v in (config or {}).items() if k not in _SECRET_KEYS_IN_CONFIG}

    credential_ref: uuid.UUID | None = None
    if credential:
        credential_ref = await DbSecretStore(session).put(
            enterprise_id=enterprise_id,
            purpose="source_credential",
            plaintext=json.dumps(credential),
            created_by=created_by,
        )

    row = DataSource(
        enterprise_id=enterprise_id,
        name=name,
        kind=kind,
        connector_type=connector_type,
        config=safe_config,
        credential_ref=credential_ref,
        health="unavailable",
    )
    if observation_interval_seconds:
        row.observation_interval_seconds = observation_interval_seconds
    session.add(row)
    await session.flush()
    return row


async def update_data_source(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    observation_interval_seconds: int | None = None,
    credential: dict[str, Any] | None = None,
    created_by: uuid.UUID | None = None,
) -> DataSource:
    ds = await get_data_source(session, enterprise_id, data_source_id)
    if name is not None:
        ds.name = name
    if config is not None:
        merged = dict(ds.config)
        merged.update({k: v for k, v in config.items() if k not in _SECRET_KEYS_IN_CONFIG})
        ds.config = merged
    if observation_interval_seconds is not None:
        ds.observation_interval_seconds = observation_interval_seconds
    if credential:
        ds.credential_ref = await DbSecretStore(session).put(
            enterprise_id=enterprise_id,
            purpose="source_credential",
            plaintext=json.dumps(credential),
            created_by=created_by,
        )
    await session.flush()
    return ds


async def upload_file_content(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    content: str,
) -> DataSource:
    ds = await get_data_source(session, enterprise_id, data_source_id)
    if ds.connector_type != "file":
        raise DomainRuleError("upload is only valid for a 'file' connector")
    ds.config = {**ds.config, "content": content}
    await session.flush()
    return ds


async def load_credential(session: AsyncSession, ds: DataSource) -> dict[str, Any] | None:
    if ds.credential_ref is None:
        return None
    raw = await DbSecretStore(session).get(ds.credential_ref)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None


async def _connector(session: AsyncSession, ds: DataSource) -> SourceConnector:
    credential = await load_credential(session, ds)
    try:
        return build_connector(ds.connector_type, dict(ds.config), credential)
    except ConnectorError as exc:
        raise UpstreamError(str(exc)) from exc


async def check_connection(
    session: AsyncSession, *, enterprise_id: uuid.UUID, data_source_id: uuid.UUID
) -> ConnectionCheck:
    ds = await get_data_source(session, enterprise_id, data_source_id)
    connector = await _connector(session, ds)
    try:
        check: ConnectionCheck = await connector.test_connection()
    except ConnectorError as exc:
        check = ConnectionCheck(health="unavailable", detail=str(exc), checked_at=datetime.now(UTC))

    ds.last_check_at = check.checked_at
    obs = ObservabilityService(session)
    if check.health == "available":
        ds.health = "available"
        ds.last_error = None
        await obs.close(
            enterprise_id=enterprise_id,
            scope="source",
            scope_ref=str(ds.id),
            reason="source_unavailable",
        )
    else:
        ds.health = "unavailable"
        ds.last_error = check.detail
        await obs.open(
            enterprise_id=enterprise_id,
            scope="source",
            scope_ref=str(ds.id),
            reason="source_unavailable",
        )
    await session.flush()
    return check


async def describe_and_store(
    session: AsyncSession, *, enterprise_id: uuid.UUID, data_source_id: uuid.UUID
) -> list[SourceField]:
    ds = await get_data_source(session, enterprise_id, data_source_id)
    connector = await _connector(session, ds)
    try:
        discovered = await connector.describe_schema()
    except ConnectorError as exc:
        raise UpstreamError(str(exc)) from exc

    existing = {
        f.path: f
        for f in (
            await session.execute(select(SourceField).where(SourceField.data_source_id == ds.id))
        )
        .scalars()
        .all()
    }
    out: list[SourceField] = []
    for field in discovered:
        row = existing.get(field.path)
        if row is None:
            row = SourceField(data_source_id=ds.id, path=field.path)
            session.add(row)
        row.inferred_type = field.inferred_type
        row.sample_values = list(field.sample_values)
        out.append(row)
    await session.flush()
    return out


async def health_history(
    session: AsyncSession, *, enterprise_id: uuid.UUID, data_source_id: uuid.UUID
) -> dict[str, Any]:
    ds = await get_data_source(session, enterprise_id, data_source_id)
    gaps = list(
        (
            await session.execute(
                select(ObservabilityGap)
                .where(
                    ObservabilityGap.enterprise_id == enterprise_id,
                    ObservabilityGap.scope == "source",
                    ObservabilityGap.scope_ref == str(ds.id),
                )
                .order_by(ObservabilityGap.opened_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "health": ds.health,
        "last_check_at": ds.last_check_at.isoformat() if ds.last_check_at else None,
        "last_success_at": ds.last_success_at.isoformat() if ds.last_success_at else None,
        "last_error": ds.last_error,
        "events": [
            {
                "reason": g.reason,
                "opened_at": g.opened_at.isoformat(),
                "closed_at": g.closed_at.isoformat() if g.closed_at else None,
            }
            for g in gaps
        ],
    }
