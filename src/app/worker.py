"""Background worker entrypoint (T031): ``python -m app.worker``.

Polls the durable job queue, dispatches each job to its registered handler, and reschedules
failures with backoff. Phase 2 ships the loop + registry; concrete handlers (observe_source,
detect_risks, …) are registered by later phases.

    python -m app.worker                 # run the poll loop
    python -m app.worker --run-once obs  # process due jobs of kind 'obs' once, then exit
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.jobs import queue
from app.jobs.models import Job

JobHandler = Callable[[AsyncSession, Job], Awaitable[None]]

HANDLERS: dict[str, JobHandler] = {}


def register(kind: str) -> Callable[[JobHandler], JobHandler]:
    def _wrap(fn: JobHandler) -> JobHandler:
        HANDLERS[kind] = fn
        return fn

    return _wrap


WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


@register("noop")
async def _noop(session: AsyncSession, job: Job) -> None:
    """A do-nothing handler — smoke/self-test that the loop reaches a terminal state."""
    return None


def _load_feature_handlers() -> None:
    """Import feature modules so their ``@register`` decorators populate ``HANDLERS``."""
    from app.integration import jobs as _integration_jobs  # noqa: F401


_load_feature_handlers()


async def _run_job(session: AsyncSession, job: Job) -> None:
    handler = HANDLERS.get(job.kind)
    if handler is None:
        await queue.mark_failed_or_retry(session, job, f"no handler for kind {job.kind!r}")
        await session.commit()
        return
    try:
        await handler(session, job)
    except Exception as exc:
        await session.rollback()
        # re-fetch the job in a fresh state to record the failure
        fresh = await session.get(Job, job.id)
        if fresh is not None:
            await queue.mark_failed_or_retry(session, fresh, repr(exc))
            await session.commit()
    else:
        await queue.mark_done(session, job.id)
        await session.commit()


async def process_once(*, kind: str | None = None, limit: int = 25) -> int:
    """Claim and run all currently-due jobs (optionally filtered by kind). Returns the count."""
    sm = get_sessionmaker()
    processed = 0
    async with sm() as session:
        jobs = await queue.claim_due(session, worker_id=WORKER_ID, limit=limit)
        await session.commit()
    for job in jobs:
        if kind is not None and job.kind != kind:
            continue
        async with sm() as session:
            db_job = await session.get(Job, job.id)
            if db_job is None:
                continue
            await _run_job(session, db_job)
            processed += 1
    return processed


async def poll_loop(interval_seconds: float = 1.0) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    while not stop.is_set():
        try:
            await process_once()
        except Exception as exc:
            print(f"[worker] poll error: {exc!r}")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.worker")
    parser.add_argument("--run-once", metavar="KIND", nargs="?", const="", default=None)
    args = parser.parse_args()
    if args.run_once is not None:
        kind = args.run_once or None
        count = asyncio.run(process_once(kind=kind))
        print(f"[worker] processed {count} job(s)")
    else:
        print(f"[worker] {WORKER_ID} polling…")
        asyncio.run(poll_loop())


if __name__ == "__main__":
    main()
