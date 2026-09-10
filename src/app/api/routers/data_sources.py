"""Data Sources endpoints (T055; contracts/rest-api.md "Data Sources / Mappings").

Permissions: ``datasource.manage`` for every route here. Secret material is write-only — it
is accepted in the ``credential`` body and never appears in any response (FR-068).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deps import get_llm_provider
from app.ai.provider import LLMProvider, LLMUnavailable
from app.core.db import get_session
from app.core.errors import UpstreamError
from app.identity.deps import CurrentUser, require
from app.integration import mapping_ai, service
from app.integration.models import DataSource

router = APIRouter(tags=["data-sources"])

_MANAGE = "datasource.manage"


class CredentialIn(BaseModel):
    kind: str
    model_config = {"extra": "allow"}


class DataSourceIn(BaseModel):
    name: str
    kind: str
    connector_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    credential: dict[str, Any] | None = None
    observation_interval_seconds: int | None = None


class DataSourcePatch(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    observation_interval_seconds: int | None = None
    credential: dict[str, Any] | None = None


class UploadIn(BaseModel):
    content: str


class DataSourceOut(BaseModel):
    id: str
    name: str
    kind: str
    connector_type: str
    config: dict[str, Any]
    health: str
    observation_interval_seconds: int
    has_credential: bool
    last_success_at: str | None
    last_check_at: str | None
    last_error: str | None
    created_at: str


class DataSourceList(BaseModel):
    items: list[DataSourceOut]
    next_cursor: str | None = None


class ConnectionCheckOut(BaseModel):
    health: str
    detail: str
    checked_at: str


class SourceFieldOut(BaseModel):
    path: str
    inferred_type: str
    sample_values: list[Any]


class MappingOut(BaseModel):
    id: str
    data_source_id: str
    source_field_path: str
    canonical_entity: str
    canonical_attribute: str
    transform: dict[str, Any] | None
    status: str
    ai_confidence: float | None
    confirmed_by: str | None
    confirmed_at: str | None


def _safe_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Drop the (potentially large) uploaded file body from responses; keep a size hint."""
    out = {k: v for k, v in cfg.items() if k != "content"}
    if cfg.get("content"):
        out["content_bytes"] = len(cfg["content"])
    return out


def _ds_out(ds: DataSource) -> DataSourceOut:
    return DataSourceOut(
        id=str(ds.id),
        name=ds.name,
        kind=ds.kind,
        connector_type=ds.connector_type,
        config=_safe_config(ds.config),
        health=ds.health,
        observation_interval_seconds=ds.observation_interval_seconds,
        has_credential=ds.credential_ref is not None,
        last_success_at=ds.last_success_at.isoformat() if ds.last_success_at else None,
        last_check_at=ds.last_check_at.isoformat() if ds.last_check_at else None,
        last_error=ds.last_error,
        created_at=ds.created_at.isoformat(),
    )


def mapping_out(m: Any) -> MappingOut:
    return MappingOut(
        id=str(m.id),
        data_source_id=str(m.data_source_id),
        source_field_path=m.source_field_path,
        canonical_entity=m.canonical_entity,
        canonical_attribute=m.canonical_attribute,
        transform=m.transform,
        status=m.status,
        ai_confidence=float(m.ai_confidence) if m.ai_confidence is not None else None,
        confirmed_by=str(m.confirmed_by) if m.confirmed_by else None,
        confirmed_at=m.confirmed_at.isoformat() if m.confirmed_at else None,
    )


@router.get("/data-sources", response_model=DataSourceList)
async def list_sources(
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> DataSourceList:
    rows = await service.list_data_sources(session, user.enterprise_id)
    return DataSourceList(items=[_ds_out(r) for r in rows])


@router.post("/data-sources", response_model=DataSourceOut)
async def create_source(
    body: DataSourceIn,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> DataSourceOut:
    ds = await service.create_data_source(
        session,
        enterprise_id=user.enterprise_id,
        name=body.name,
        kind=body.kind,
        connector_type=body.connector_type,
        config=body.config,
        credential=body.credential,
        created_by=user.id,
        observation_interval_seconds=body.observation_interval_seconds,
    )
    await session.commit()
    return _ds_out(ds)


@router.get("/data-sources/{data_source_id}", response_model=DataSourceOut)
async def get_source(
    data_source_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> DataSourceOut:
    return _ds_out(await service.get_data_source(session, user.enterprise_id, data_source_id))


@router.patch("/data-sources/{data_source_id}", response_model=DataSourceOut)
async def patch_source(
    data_source_id: uuid.UUID,
    body: DataSourcePatch,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> DataSourceOut:
    ds = await service.update_data_source(
        session,
        enterprise_id=user.enterprise_id,
        data_source_id=data_source_id,
        name=body.name,
        config=body.config,
        observation_interval_seconds=body.observation_interval_seconds,
        credential=body.credential,
        created_by=user.id,
    )
    await session.commit()
    return _ds_out(ds)


@router.post("/data-sources/{data_source_id}/upload", response_model=DataSourceOut)
async def upload_source_file(
    data_source_id: uuid.UUID,
    body: UploadIn,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> DataSourceOut:
    ds = await service.upload_file_content(
        session,
        enterprise_id=user.enterprise_id,
        data_source_id=data_source_id,
        content=body.content,
    )
    await session.commit()
    return _ds_out(ds)


@router.post("/data-sources/{data_source_id}/test", response_model=ConnectionCheckOut)
async def test_source(
    data_source_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> ConnectionCheckOut:
    check = await service.check_connection(
        session, enterprise_id=user.enterprise_id, data_source_id=data_source_id
    )
    await session.commit()
    return ConnectionCheckOut(
        health=check.health, detail=check.detail, checked_at=check.checked_at.isoformat()
    )


@router.post("/data-sources/{data_source_id}/introspect", response_model=list[SourceFieldOut])
async def introspect_source(
    data_source_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[SourceFieldOut]:
    fields = await service.describe_and_store(
        session, enterprise_id=user.enterprise_id, data_source_id=data_source_id
    )
    await session.commit()
    return [
        SourceFieldOut(
            path=f.path, inferred_type=f.inferred_type, sample_values=list(f.sample_values)
        )
        for f in fields
    ]


@router.post("/data-sources/{data_source_id}/mapping-suggestions", response_model=list[MappingOut])
async def mapping_suggestions(
    data_source_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
    provider: LLMProvider = Depends(get_llm_provider),
) -> list[MappingOut]:
    try:
        created = await mapping_ai.suggest_mappings_for_source(
            session,
            enterprise_id=user.enterprise_id,
            data_source_id=data_source_id,
            provider=provider,
        )
    except LLMUnavailable as exc:
        await session.commit()  # persist the ai_unavailable gap + audit record
        raise UpstreamError(f"AI mapping suggestion unavailable: {exc}") from exc
    await session.commit()
    return [mapping_out(m) for m in created]


@router.get("/data-sources/{data_source_id}/health-history")
async def health_history(
    data_source_id: uuid.UUID,
    user: CurrentUser = Depends(require(_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await service.health_history(
        session, enterprise_id=user.enterprise_id, data_source_id=data_source_id
    )
