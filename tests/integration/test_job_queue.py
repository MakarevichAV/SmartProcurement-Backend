"""Durable job-queue tests (T032): claim, backoff, recurring re-enqueue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.jobs import queue
from app.jobs.models import Job

pytestmark = pytest.mark.asyncio


async def test_enqueue_and_claim_due(db_session, seeded) -> None:
    await queue.enqueue(db_session, kind="noop", payload={"n": 1})
    await db_session.flush()
    claimed = await queue.claim_due(db_session, worker_id="w1")
    assert [j.kind for j in claimed] == ["noop"]
    assert claimed[0].status == "running"
    assert claimed[0].locked_by == "w1"


async def test_future_jobs_not_claimed(db_session, seeded) -> None:
    await queue.enqueue(
        db_session,
        kind="later",
        run_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await db_session.flush()
    assert await queue.claim_due(db_session, worker_id="w1") == []


async def test_failed_job_reschedules_with_backoff_until_max_attempts(db_session, seeded) -> None:
    job = await queue.enqueue(db_session, kind="flaky", max_attempts=2)
    await db_session.flush()

    await queue.mark_failed_or_retry(db_session, job, "boom")
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.run_at > datetime.now(UTC)

    await queue.mark_failed_or_retry(db_session, job, "boom again")
    assert job.status == "failed"
    assert job.attempts == 2


async def test_reschedule_recurring_enqueues_next_run(db_session, seeded) -> None:
    nxt = await queue.reschedule_recurring(
        db_session, kind="observe", interval_seconds=900, payload={"source": "s1"}
    )
    await db_session.flush()
    assert nxt.kind == "observe"
    assert nxt.run_at > datetime.now(UTC) + timedelta(seconds=800)


async def test_mark_done(db_session, seeded) -> None:
    job = await queue.enqueue(db_session, kind="noop")
    await db_session.flush()
    await queue.mark_done(db_session, job.id)
    refreshed = await db_session.get(Job, job.id)
    assert refreshed is not None and refreshed.status == "done"
