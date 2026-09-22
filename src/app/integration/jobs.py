"""Durable-queue bindings for the US1 integration jobs (T051/T053) + T068 recurring reschedule.

The business logic lives in ``mapping_ai.py`` (``suggest_mappings_for_source``) and
``sync.py`` (``sync_source``); this module only wires them to the worker's handler registry.
Handlers must not ``commit`` — the worker owns the transaction boundary.

``observe_source_handler`` re-enqueues its own next run at the source's
``observation_interval_seconds`` after every attempt (data-model.md §10) — this is the one
step of the observe -> aggregate -> detect chain that is time-driven rather than
completion-chained; the rest (``recompute_aggregates`` -> ``detect_risks``) is wired via plain
``JobQueue.enqueue`` calls in the handler that produces each stage's durable state (T066,
``app/observation/jobs.py``), per data-model.md §10 and the Phase 4 decision to use no new
orchestration framework.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.mapping_ai import suggest_mappings_for_source
from app.integration.models import DataSource
from app.integration.sync import sync_source
from app.jobs import queue as job_queue
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
    enterprise_id = uuid.UUID(payload["enterprise_id"])
    data_source_id = uuid.UUID(payload["data_source_id"])

    await sync_source(session, enterprise_id=enterprise_id, data_source_id=data_source_id)

    ds = await session.get(DataSource, data_source_id)
    if ds is not None:
        await job_queue.reschedule_recurring(
            session,
            kind="observe_source",
            interval_seconds=ds.observation_interval_seconds,
            payload=payload,
            enterprise_id=enterprise_id,
        )
