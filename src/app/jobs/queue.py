"""Durable Postgres-backed job queue (T030; research.md §3).

Enqueue is transactional with the caller's session. The worker claims due jobs with
``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple workers can run safely. Failed jobs are
rescheduled with exponential backoff until ``max_attempts``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job

_BACKOFF_BASE_SECONDS = 5
_BACKOFF_CAP_SECONDS = 3600


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(_BACKOFF_BASE_SECONDS * (2**attempts), _BACKOFF_CAP_SECONDS))


async def enqueue(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict[str, Any] | None = None,
    run_at: datetime | None = None,
    enterprise_id: uuid.UUID | None = None,
    max_attempts: int = 5,
) -> Job:
    """Add a job in the caller's transaction (commit is the caller's responsibility)."""
    job = Job(
        kind=kind,
        payload=payload or {},
        run_at=run_at or datetime.now(UTC),
        enterprise_id=str(enterprise_id) if enterprise_id else None,
        max_attempts=max_attempts,
        status="pending",
    )
    session.add(job)
    await session.flush()
    return job


async def claim_due(session: AsyncSession, *, worker_id: str, limit: int = 10) -> list[Job]:
    """Atomically claim up to ``limit`` due pending jobs for this worker."""
    now = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(Job)
                .where(Job.status == "pending", Job.run_at <= now)
                .order_by(Job.run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for job in rows:
        job.status = "running"
        job.locked_by = worker_id
        job.locked_at = now
    await session.flush()
    return list(rows)


async def mark_done(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        update(Job).where(Job.id == job_id).values(status="done", locked_by=None, locked_at=None)
    )


async def mark_failed_or_retry(session: AsyncSession, job: Job, error: str) -> None:
    job.attempts += 1
    job.last_error = error[:2000]
    job.locked_by = None
    job.locked_at = None
    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "pending"
        job.run_at = datetime.now(UTC) + _backoff(job.attempts)


async def reschedule_recurring(
    session: AsyncSession, *, kind: str, interval_seconds: int, payload: dict[str, Any], **kw: Any
) -> Job:
    """Helper for self-perpetuating jobs: enqueue the next run."""
    return await enqueue(
        session,
        kind=kind,
        payload=payload,
        run_at=datetime.now(UTC) + timedelta(seconds=interval_seconds),
        **kw,
    )
