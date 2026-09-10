"""Durable-queue bindings for the US1 integration jobs (T051/T053).

The business logic lives in ``mapping_ai.py`` (``suggest_mappings_for_source``) and
``sync.py`` (``sync_source``); this module only wires them to the worker's handler registry.
Handlers must not ``commit`` — the worker owns the transaction boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.mapping_ai import suggest_mappings_for_source
from app.integration.sync import sync_source
from app.jobs.models import Job
from app.jobs.registry import register


@register("suggest_mapping")
async def suggest_mapping_handler(session: AsyncSession, job: Job) -> None:
    payload = job.payload
    await suggest_mappings_for_source(
        session,
        enterprise_id=uuid.UUID(payload["enterprise_id"]),
        data_source_id=uuid.UUID(payload["data_source_id"]),
    )


@register("observe_source")
async def observe_source_handler(session: AsyncSession, job: Job) -> None:
    payload = job.payload
    await sync_source(
        session,
        enterprise_id=uuid.UUID(payload["enterprise_id"]),
        data_source_id=uuid.UUID(payload["data_source_id"]),
    )
