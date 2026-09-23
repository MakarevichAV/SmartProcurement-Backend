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
uv run pytest                 # 121 tests (unit + contract + integration); needs PostgreSQL running
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
(see "US1" below) **and Phase 4 / User Story 2 — Observation & L1/L2 risk detection with
explanation** (see "US2" below).

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
  exponential backoff, recurring self-re-enqueue; `python -m app.worker` dispatches to
  registered handlers (`noop` plus the US1 `suggest_mapping` / `observe_source` handlers). The
  handler registry lives in `app/jobs/registry.py` (imported exactly once) rather than on
  `app/worker.py` — `python -m app.worker` loads that file as `__main__`, and a plain
  `from app.worker import register` on the registry would import a *second* copy of the
  module with its own dict, so feature `@register()` calls would land on a `HANDLERS` the
  running worker never sees. `app/worker.py` also imports `app.models_registry` before the
  first ORM flush so `Base.metadata` has every table (missing FKs there previously surfaced as
  a misleading `MissingGreenlet` instead of the real `NoReferencedTableError`).
  `tests/integration/test_worker_bootstrap.py` covers both regressions.
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
- **Bulk-confirm** — `POST /api/v1/mappings/bulk-confirm` (`{data_source_id, mapping_ids[]}`)
  confirms every eligible row (belongs to that source, still `suggested`) in one request
  instead of one call per mapping; each id is independently eligible/ineligible, so the
  response reports `{requested, confirmed, failed[]}` rather than failing the whole batch on
  one bad id — an id already confirmed by someone else, or from a different source, comes back
  in `failed` and is left untouched. Every confirmation still appends its own
  `mapping_change_event`.
- **Sync** — `sync_source` + the `observe_source` job fetch a batch, map it through confirmed
  mappings, and upsert the 11 canonical entities (`item`, `warehouse`, `supplier`,
  `stock_level`, `price`, `lead_time`, `purchase_order`, `consumption`, `production_demand`,
  `quality_record`, `item_supplier`), each row stamped with `source_provenance` and
  `observability`. Since Phase 4, the same sync also diffs upserted rows into
  `observation_signal` — see "US2" below. `observe_source` still only runs on its own
  recurring interval; there is no on-demand trigger yet (deferred, **T169**).
- **L0 Domain Map** — `GET /api/v1/domain/map` (per-entity counts, source ids and
  observability breakdown + a static relationship graph) and `GET /api/v1/domain/{entity}`
  (paged, business-readable rows): each row carries `references` — every FK column resolved to
  a compact business identity (`{entity, id, label, ...}`, e.g. an `item_id` resolves to
  `"SKU-123 — Steel Bracket"` rather than a bare UUID) — and `provenance` (`data_source_id`,
  `data_source_name`, the originating `source_fields[]`, `fetched_at`), alongside the row's raw
  `source_provenance` and `observability` state.
- **GET /api/v1/dashboard** — a thin read model (permission `domain.read`) composing the
  existing enterprise-scoped services; no new tables. As of Phase 4, `lorm.open_risks` is a
  real count of non-terminal `risk_finding` rows and `data_health.ai_unavailable_items` is a
  real count of `risk_finding` rows with `ai_status=unavailable` (both excluding terminal
  findings). `lorm.recommendations` / `.approvals` stay `null` — those subsystems
  (`recommendation` / `procurement_action`) land in US3/US4, so the endpoint reports "not
  evaluated," never a fake `0`. `lorm.autopilot` is the real count of capabilities at `L5` — a
  Phase-3-era approximation of "operating under an approved policy" that US5 will refine to
  require a matching *active* policy. `data_health` also reports source counts by health +
  `last_successful_sync` (from `integration.service.list_data_sources`) and canonical-row
  `fresh`/`stale`/`lost` totals (from `domain.map_service.domain_map`).
- **No `pgvector`, embeddings, or semantic retrieval** — deliberately deferred (research.md
  §13).

### US2 — observation & L1/L2 risk detection with explanation

- **Observation** — `sync_source` diffs a fixed watch-list of attributes (stock/price/
  lead_time/quality/demand, FR-014) into an append-only `observation_signal` table (indexed,
  not physically partitioned in v1 — deferred, not required at current volume). **A row's
  first-seen sync establishes a baseline and produces no signal; only a subsequent change to a
  watched attribute does.** Producing signals enqueues `recompute_aggregates`
  (`app/observation/aggregates.py`), which derives per-item `sku_aggregate` rows
  (`avg_daily_consumption`, `days_of_cover`, `next_expected_delivery_at`, `last_price`,
  `price_trend`, `avg_supplier_delay_days`) — items without currently trustworthy
  (`observability != 'lost'`) stock data get no aggregate row.
- **Durable pipeline** — all four stages run on the existing `JobQueue` (no new
  scheduler/orchestration framework): `observe_source` (self-reschedules at
  `data_source.observation_interval_seconds`) → `recompute_aggregates` → `detect_risks` →
  `generate_explanation`, each stage enqueuing the next in its own transaction.
- **Deterministic risk detection** (`app/analysis/rules.py`) — `detect_risks` is **purely
  deterministic**; the AI plays no role in whether a risk exists. Six risk types: three
  relational (`likely_shortage`, `insufficient_until_next_delivery`, `production_stop_risk` —
  the last only from `production_demand`) and three against fixed v1 default thresholds
  (`systematic_supplier_delay`: avg lateness > 3 days over ≥ 3 received POs; `price_anomaly`:
  `|price_trend| ≥ 10%`; `quality_degradation`: avg `defect_rate` > 5% — promoting these to
  per-enterprise configurable settings is deferred, **T168**). Identity/dedup is
  `(enterprise_id, risk_type, item_id|supplier_id)`, enforced only against non-terminal
  findings — a `resolved`/`dismissed` finding may recur as a new row; a matching non-terminal
  finding instead gets new evidence linked via `risk_signal_link`.
- **Grounded AI explanation** (`app/analysis/explain.py`) — `generate_explanation` calls
  `generate_structured(RiskExplanation)` to explain an already-detected finding (FR-018/FR-070)
  — never to decide whether it exists. Evidence refs are validated: `observation_signal` (via
  `risk_signal_link`) for most risk types, plus `domain_row`/`purchase_order` refs specifically
  for `systematic_supplier_delay` (which has no single diffable signal by design). Persistent
  failure sets `risk_finding.ai_status=unavailable` — the underlying finding stays fully valid
  — and writes an `ai_unavailable` audit record + observability gap instead of proceeding.
- **Router** `app/api/routers/risks.py` (permission `domain.read`): `GET /api/v1/risks`
  (filter by `status`/`risk_type`/`item_id`, cursor-paginated), `GET /api/v1/risks/{id}`
  (detection + explanation + evidence, 404 for unknown/foreign-enterprise ids alike),
  `POST /api/v1/risks/{id}/dismiss` (non-empty `reason` required → 400 on empty, 409 on an
  already-terminal finding; persists `dismissed_reason`/`dismissed_at`/`dismissed_by` +
  `resolved_at`; records `audit_record(event_type=risk_dismissed)`).
- **Validated** — a Phase 4 integration/manual-validation pass exercised the full pipeline and
  UI against the real running app (dashboard, risks list/detail, dismiss + audit + cache
  refresh) with no defects found. `systematic_supplier_delay`'s `domain_row`/`purchase_order`
  evidence rendering could not be manually exercised, since the demo fixture's purchase order
  is `open`, not `received` — a test-coverage note, not a product defect.

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

- Recommendations and the `LormEnforcementService.evaluate()` `allow / ask / deny` gate,
  `procurement_action` (Phase 5 / US3). **No L3+ capability exists anywhere in the system yet.**
- L4 approval workflow, execution adapters, dispatch + reconciliation (Phase 6 / US4).
- L5 policies, autopilot, policy expiry scan (Phase 7 / US5).
- Full capability management — AI promotion suggestions, the L4→L5 approver ≠ policy-author
  rule, trust-record gating (Phase 8 / US6).
- Verification of outcomes and **automatic demotion** (Phase 9 / US7).
- Audit-query endpoints (Phase 10 / US8) and user-management endpoints (Phase 11 / US9).
- End-to-end and load tests, `docs/` architecture write-ups (Phase 12).
- `GET /api/v1/dashboard`'s `recommendations` / `approvals` counters are `null` until US3/US4
  land; `autopilot` counts capabilities at `L5` without yet checking for a matching active
  policy (US5).

Two smaller gaps from Phase 4 are tracked as deferred backlog rather than scheduled into a
phase (see the Deferred / Post-MVP Backlog section of `tasks.md`):

- **T168** — the three threshold-based L2 risk rules (`systematic_supplier_delay`,
  `price_anomaly`, `quality_degradation`) use fixed v1 default constants; promoting them to
  per-enterprise configurable settings is deferred.
- **T169** — there is no user-facing "Sync Now" trigger for a Data Source; `observe_source`
  only runs on its own recurring interval.
