# SmartProcurement-Backend

Backend for Smart Procurement — the AI decision + LORM enforcement layer. Python 3.12 ·
FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16. Modular monolith (`src/app/<module>/`) plus a
worker process.

Spec, plan, and tasks live in the top-level **SmartProcurement** repository
(`specs/001-smart-procurement/`).

## Quick start

```sh
uv sync --extra dev
cp .env.example .env          # then set FERNET_KEY for anything beyond local
uv run uvicorn app.main:app --reload   # http://localhost:8000/health , /openapi.json
uv run pytest
```

`make` targets: `install lint format typecheck test run worker migrate seed`.

## Status

Phase 1 (scaffolding) complete: config, async DB layer, base model mixins, unified error
model + correlation-id middleware, Alembic (async) initialised, test harness, `/health`.
Phases 2+ (identity/auth, LORM, domain, …) per `specs/001-smart-procurement/tasks.md`.
