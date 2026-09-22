"""Durable-queue binding for ``recompute_aggregates`` (T068).

Completion-chained: after a successful recompute, this handler enqueues ``detect_risks`` in
the same transaction (data-model.md §10). ``detect_risks`` itself has no handler yet (T070);
until then, an enqueued job simply waits — ``app.worker`` retries-then-fails unregistered
kinds gracefully (see ``worker.process_once``), it never crashes the poll loop.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import queue as job_queue
from app.jobs.models import Job
from app.jobs.registry import register
from app.observation.aggregates import recompute_aggregates


@register("recompute_aggregates")
async def recompute_aggregates_handler(session: AsyncSession, job: Job) -> None:
    payload = job.payload
    enterprise_id = uuid.UUID(payload["enterprise_id"])
    item_ids = [uuid.UUID(i) for i in payload.get("item_ids", [])] or None

    await recompute_aggregates(session, enterprise_id=enterprise_id, item_ids=item_ids)

    await job_queue.enqueue(
        session,
        kind="detect_risks",
        payload={"enterprise_id": str(enterprise_id)},
        enterprise_id=enterprise_id,
    )
