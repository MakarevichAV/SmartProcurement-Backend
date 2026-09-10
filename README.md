# SmartProcurement-Backend

The AI decision + LORM enforcement layer for Smart Procurement. It ingests enterprise data,
diagnoses procurement risks, forms buying decisions, and — gated by a LORM responsibility
layer — either recommends, asks a human to approve, or (later) executes within an approved
policy. Every authority-bearing action is auditable.

Spec, plan, contracts and tasks live in the top-level **SmartProcurement** repository
(`specs/001-smart-procurement/`). This repo contains only the backend application.

## Architecture

A **modular monolith** — `src/app/<module>/`, each module owns its tables and exposes a
service interface — plus a **worker process** (`python -m app.worker`) that shares the same
code and database. The modules map to the constitution's layers:

| Constitution layer | Module(s) |
|---|---|
| Backend/API | `api/` (routers + DTOs), per-module services |
| AI Decision Layer | `ai/`, (later) `analysis/`, `decision/` |
| LORM Responsibility / Enforcement | `lorm/`, `policies/` |
| Data / Integration | (later) `integration/`, `domain/`, `observation/` |
| Execution | (later) `execution/` |
| Persistence | SQLAlchemy models per module, `audit/`, `jobs/` |

The backend is the **only** security boundary. Third-party LORM files are vendored unmodified
in `src/app/vendor/lorm/` (see `UPSTREAM.md` for the pinned commit).

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 (async, `asyncpg`) · Alembic ·
PostgreSQL 16 · `argon2-cffi` + `pyjwt` (auth) · `cryptography` (secret encryption) ·
`anthropic` SDK (optional, real LLM) · `jsonschema` (LORM policy validation) ·
managed with [`uv`](https://docs.astral.sh/uv/).

## Local setup

Prerequisites: Python 3.12, `uv`, Docker (or an external PostgreSQL 16).

```sh
# 1. dependencies
uv sync --extra dev

# 2. environment
cp .env.example .env
# Generate a Fernet key and paste it into FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3. PostgreSQL (from the workspace root)
cd .. && docker compose up -d postgres && cd backend
#   → 127.0.0.1:5432, db smart_procurement, user/pass sp/sp (loopback only, local dev)

# 4. migrations
uv run alembic upgrade head

# 5. demo seed  (LOCAL DEVELOPMENT ONLY — see "Demo users" below)
uv run python -m app.seed --demo
```

### Environment configuration (`backend/.env`)

| Variable | Purpose | Default |
|----------|---------|---------|
| `ENVIRONMENT` | `local` / `ci` / `staging` / `production` | `local` |
| `DATABASE_URL` | async SQLAlchemy DSN | `postgresql+asyncpg://sp:sp@localhost:5432/smart_procurement` |
| `JWT_SECRET` | HS256 signing key for access tokens | `dev-only-change-me` (change outside local) |
| `FERNET_KEY` | urlsafe-base64 32-byte key for encrypting stored external credentials | *(empty — required for secret features)* |
| `LLM_PROVIDER` | `mock` (deterministic) or `anthropic` | `mock` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | only when `LLM_PROVIDER=anthropic` | — / `claude-sonnet-5` |
| `CORS_ORIGINS` | comma-separated browser origins allowed to call the API | `http://localhost:5173,http://127.0.0.1:5173` |

`.env` is git-ignored. No real secrets are committed.

## Running

```sh
uv run uvicorn app.main:app --reload     # API on http://localhost:8000
uv run python -m app.worker              # background worker (separate shell)
uv run python -m app.worker --run-once   # drain currently-due jobs once, then exit
```

`make` targets (thin wrappers over the above):

| Target | Runs |
|--------|------|
| `make install` | `uv sync --extra dev` |
| `make run` | `uvicorn app.main:app --reload` |
| `make worker` | `python -m app.worker` |
| `make migrate` | `alembic upgrade head` |
| `make seed` | `python -m app.seed --demo` |
| `make lint` | `ruff check` + `black --check` |
| `make format` | `ruff check --fix` + `black` |
| `make typecheck` | `mypy src` (strict) |
| `make test` | `pytest` |

Local development URLs:

| URL | What |
|-----|------|
| http://localhost:8000/health | liveness + version |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/openapi.json | OpenAPI document (the frontend generates types from this) |

## Tests, lint, formatting, types

```sh
uv run pytest                 # 30 tests (contract + integration); needs PostgreSQL running
uv run ruff check src tests   # lint
uv run black --check src tests # formatting
uv run mypy src               # strict type-check
uv run alembic check          # migration ↔ model drift
```

DB-backed tests skip automatically if PostgreSQL is unreachable. LLM-dependent tests use the
deterministic mock — no API key needed. Vendored `src/app/vendor/` is excluded from
ruff/black/mypy (unmodified third-party).

## Capabilities (what this backend does today)

Phases 1–2 (Foundational) **plus Phase 3 / User Story 1 — Data sources & the L0 domain map**
(see "US1" below).

- **Auth & sessions** — `POST /api/v1/auth/login` (email + password), `/auth/refresh`,
  `/auth/logout`, `GET /api/v1/me`.
- **RBAC** — an 11-key permission catalogue (`datasource.manage`, `mapping.confirm`,
  `domain.read`, `recommendation.request`, `approval.act`, `policy.author`, `policy.approve`,
  `capability.read`, `capability.promote`, `audit.read`, `user.manage`) mapped to the three
  roles; every route is permission-gated.
- **Enterprise boundary** — `GET/PATCH /api/v1/enterprise` (single active enterprise;
  `enterprise_id` on every scoped table for future multi-tenancy).
- **Capability registry + LORM levels** — `GET /api/v1/capabilities`,
  `GET /api/v1/capabilities/{key}/history`. The 8 procurement capabilities are seeded at:

  | Capability | Seed level | `l5_allowed` |
  |---|---|---|
  | `proc.inventory.observe` | L0 | yes |
  | `proc.demand.observe` | L1 | yes |
  | `proc.risk.diagnose` | L2 | yes |
  | `proc.order.recommend` | L3 | yes |
  | `proc.po.create` | L3 | yes |
  | `proc.replenish.routine` | L4 | yes |
  | `proc.supplier.add` | L4 | **no** |
  | `proc.payment.release` | L3 | **no** |

- **One-level promotion** — `POST /api/v1/capabilities/{key}/promotion-requests` +
  `POST /api/v1/promotion-requests/{id}/approve|reject`. The service enforces: promotion is
  human-only, **exactly one level** at a time, and **never to L5** for a capped capability;
  it is the only code path that raises `capability.level`, and every change writes an
  append-only `capability_level_event`.
- **Append-only audit** — `audit_record` + `AuditRecorder`; a PostgreSQL trigger makes
  UPDATE/DELETE on `audit_record` and `capability_level_event` raise.
- **Durable job queue + worker** — `job` table drained with `FOR UPDATE SKIP LOCKED`,
  exponential backoff, recurring self-re-enqueue; the worker loop dispatches to registered
  handlers (`noop` plus the US1 `suggest_mapping` / `observe_source` handlers).
- **AI provider seam** — `LLMProvider` (Anthropic + deterministic mock) and a
  `generate_structured` wrapper that validates output and, on persistent failure, opens an
  `ai_unavailable` observability gap + audit record instead of proceeding.
- **LORM policy validation** — vendored schema + `validate_policy.py`, wrapped by
  `app/policies/schema_validator.py` (schema + SPEC 8-1 author ≠ approver, etc.).
- **Secret storage** — Fernet-encrypted `secret` table + `SecretStore` seam.

### US1 — data sources & the L0 domain map

- **Connect / test / introspect** — `POST /api/v1/data-sources` (`file` | `rest` | `sql`;
  credentials go straight to `SecretStore` and never appear in a response), `.../test`
  (updates `data_source.health` + opens/closes a `source`-scoped `observability_gap`),
  `.../introspect` (persists `source_field` rows), `.../upload` (file body),
  `.../health-history`.
- **Connectors** — `SourceConnector` protocol + `file` (CSV/JSON), `rest` (JSON over HTTP,
  bearer/basic/api-key auth, optional page walk) and `sql` (**SELECT-only**, write/DDL
  keywords rejected) implementations behind a registry. Decision/domain code imports none of
  them.
- **AI-suggested mappings** — `POST /api/v1/data-sources/{id}/mapping-suggestions` runs
  `generate_structured(MappingSuggestionSet)`; suggestions whose `source_field_path` is not
  in the introspected set are dropped; each survivor is stored as
  `field_mapping(status=suggested)`. **Nothing is applied automatically.**
- **Mapping lifecycle** — `POST /api/v1/mappings`, `/mappings/{id}/confirm|reject|retire`,
  `PATCH /mappings/{id}`, `GET /mappings/{id}/history`; every transition appends an
  append-only `mapping_change_event`. Only `confirmed` mappings feed sync.
- **Sync** — `sync_source` + the `observe_source` job fetch a batch, map it through confirmed
  mappings, and upsert the 11 canonical entities (`item`, `warehouse`, `supplier`,
  `stock_level`, `price`, `lead_time`, `purchase_order`, `consumption`, `production_demand`,
  `quality_record`, `item_supplier`), each row stamped with `source_provenance` and
  `observability`. **Data path only** — no `observation_signal` diffing, no scheduler, no
  analysis (those are US2).
- **L0 Domain Map** — `GET /api/v1/domain/map` (per-entity counts, source ids and
  observability breakdown + a static relationship graph) and `GET /api/v1/domain/{entity}`
  (paged rows with provenance).
- **No `pgvector`, embeddings, or semantic retrieval** — deliberately deferred (research.md
  §13).

### Authentication behavior

- Access token: HS256 JWT, ~15 min, carries the user's effective permissions; sent by the
  client as `Authorization: Bearer …`.
- Refresh token: opaque random string, **only its SHA-256 hash is stored**, rotated on every
  refresh, individually revocable; delivered as an **httpOnly, SameSite=Lax** cookie
  (`Secure` outside `local`), scoped to `/api/v1/auth`. `logout` revokes it and clears the
  cookie.
- `401` = missing/expired token; `403` = authenticated but lacking the required permission.

### Demo users — LOCAL DEVELOPMENT ONLY

`python -m app.seed --demo` creates one enterprise, the permission catalogue, and three users
with password **`demo`** (override with `SEED_PASSWORD` in the environment before seeding):

| Email | Role |
|-------|------|
| `admin@example.com` | Administrator (all permissions) |
| `buyer@example.com` | Buyer |
| `approver@example.com` | Approver |

These are throwaway local credentials in seed code, not secrets. Do not run `--demo` outside a
local dev database.

## Limitations — intentionally deferred to later phases

The following are **not** implemented yet (see `specs/001-smart-procurement/tasks.md`):

- Observation loop, signals, aggregates, risk detection and AI explanations (Phase 4 / US2) —
  US1's sync writes canonical rows but does **not** diff them into `observation_signal`.
- Recommendations and the `LormEnforcementService.evaluate()` `allow / ask / deny` gate,
  `procurement_action` (Phase 5 / US3).
- L4 approval workflow, execution adapters, dispatch + reconciliation (Phase 6 / US4).
- L5 policies, autopilot, policy expiry scan (Phase 7 / US5).
- Full capability management — AI promotion suggestions, the L4→L5 approver ≠ policy-author
  rule, trust-record gating (Phase 8 / US6).
- Verification of outcomes and **automatic demotion** (Phase 9 / US7).
- Audit-query endpoints (Phase 10 / US8) and user-management endpoints (Phase 11 / US9).
- End-to-end and load tests, `docs/` architecture write-ups (Phase 12).

The worker loop runs but has no domain handlers yet; the AI seam exists but nothing calls it
in a request path yet.
