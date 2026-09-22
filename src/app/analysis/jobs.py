"""Durable-queue binding for ``detect_risks`` (T070).

Deliberately does **not** enqueue ``generate_explanation`` yet: that job kind has no handler
until T071, and the worker's retry-then-fail behavior for unregistered kinds (see
``app.worker.process_once``) would just produce noisy failed-job churn with nothing consuming
it. The completion-chain from ``observe_source`` through ``detect_risks`` is fully wired
(T066/T068); wiring detect_risks -> generate_explanation is T071's job, once a handler exists
to receive it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.rules import detect_risks
from app.jobs.models import Job
from app.jobs.registry import register


@register("detect_risks")
async def detect_risks_handler(session: AsyncSession, job: Job) -> None:
    payload = job.payload
    await detect_risks(session, enterprise_id=uuid.UUID(payload["enterprise_id"]))
