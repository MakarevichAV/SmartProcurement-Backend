"""Shared job-handler registry (T031).

``python -m app.worker`` loads ``app/worker.py`` as ``__main__``; the first
``from app.worker import ...`` then imports it a *second* time as ``app.worker`` — a
distinct module object with its own globals. If ``HANDLERS`` / ``register`` lived on
``app.worker`` the feature modules' ``@register`` calls would populate a different dict
than the running ``__main__`` uses, and every feature job would fail with
"no handler for kind ...". Keeping the registry here (a module that loads exactly once)
gives both copies the same ``HANDLERS``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import Job

JobHandler = Callable[[AsyncSession, Job], Awaitable[None]]

HANDLERS: dict[str, JobHandler] = {}


def register(kind: str) -> Callable[[JobHandler], JobHandler]:
    """Decorator: bind a handler coroutine to a job ``kind``."""

    def _wrap(fn: JobHandler) -> JobHandler:
        HANDLERS[kind] = fn
        return fn

    return _wrap


_loaded = False


def load_feature_handlers() -> None:
    """Import feature modules once so their ``@register`` decorators populate ``HANDLERS``."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from app.integration import jobs as _integration_jobs  # noqa: F401
    from app.observation import jobs as _observation_jobs  # noqa: F401
