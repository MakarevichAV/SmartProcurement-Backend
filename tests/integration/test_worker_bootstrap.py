"""Regression coverage for the ``python -m app.worker`` bootstrap (T031).

Two defects that only surfaced once US1 added the first real job handlers:

1. **Handler registry** — ``app/worker.py`` is loaded as ``__main__`` by ``python -m``; a
   plain ``from app.worker import register`` then imports it a *second* time, so feature
   ``@register`` calls landed on a different ``HANDLERS`` dict than the running ``__main__``
   used, and every feature job failed with "no handler for kind ...". The registry now lives
   in ``app.jobs.registry`` (loaded once), shared by every copy of ``app.worker``.

2. **Incomplete metadata** — the worker never imported ``app.models_registry``, so
   ``Base.metadata`` was missing ``enterprise`` (and other tables). The first ORM ``flush``
   in ``sync_source`` raised ``NoReferencedTableError`` for ``item.enterprise_id`` (surfacing
   confusingly as ``MissingGreenlet`` during unwind). ``app/worker.py`` now imports
   ``app.models_registry`` like ``alembic/env.py`` does.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_importing_worker_yields_complete_metadata_and_handlers() -> None:
    """A fresh interpreter importing only ``app.worker`` must have every FK resolvable
    and the feature handlers registered (covers defect 2, and defect 1's happy path)."""
    result = _run("""
        import app.worker  # noqa: F401  (does not run main(); triggers models + handlers)
        from app.core.db import Base
        from app.worker import HANDLERS

        tables = set(Base.metadata.tables)
        missing = {"enterprise", "item", "data_source", "field_mapping"} - tables
        assert not missing, f"Base.metadata missing tables: {missing}"

        # every foreign key must resolve to a registered table/column
        unresolved = []
        for table in Base.metadata.tables.values():
            for fk in table.foreign_keys:
                try:
                    _ = fk.column
                except Exception as exc:  # NoReferencedTableError / NoReferencedColumnError
                    unresolved.append((str(fk.parent), repr(exc)))
        assert not unresolved, f"unresolved foreign keys: {unresolved}"

        assert {"noop", "observe_source", "suggest_mapping"} <= set(HANDLERS), sorted(HANDLERS)
        print("ok")
        """)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "ok" in result.stdout


def test_worker_run_as_main_shares_the_handler_registry() -> None:
    """Running the module as ``__main__`` (the ``python -m`` path) must populate the same
    shared registry the run loop reads (covers defect 1's specific mechanism)."""
    result = _run("""
        import runpy, sys, contextlib
        sys.argv = ["app.worker", "--help"]
        with contextlib.suppress(SystemExit):
            runpy.run_module("app.worker", run_name="__main__")
        from app.jobs.registry import HANDLERS
        assert {"noop", "observe_source", "suggest_mapping"} <= set(HANDLERS), sorted(HANDLERS)
        print("ok")
        """)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "ok" in result.stdout


def test_worker_reexports_shared_registry_objects() -> None:
    """``from app.worker import HANDLERS/register`` keeps working and is the shared object."""
    import app.jobs.registry as registry
    import app.worker as worker

    assert worker.HANDLERS is registry.HANDLERS
    assert worker.register is registry.register
