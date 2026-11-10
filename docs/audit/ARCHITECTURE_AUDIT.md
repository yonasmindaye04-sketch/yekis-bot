# Architecture Audit — Yekis Intelligence

**Scope:** compare the implementation on `audit/initial-baseline` with the intended Yekis Intelligence architecture. Findings are limited to what the repository actually contains.

## 1. Intended vs actual stack

| Intended component | Present in repo | Importable/executable now | Verdict |
|---|---|---|---|
| Python 3.12 | `requires-python = ">=3.12"`; Dockerfile `python:3.12-slim` | Host has 3.15.1 only | Declared, host mismatch |
| FastAPI | `pyproject` dep, `app/main.py` | No (package not installed) | Implemented, unverified |
| PostgreSQL | compose `postgres:16-alpine` | Image cached locally; service not started | Declared |
| SQLAlchemy 2 | `app/db.py`, `app/models.py` | No | Implemented, unverified |
| Alembic | `migrations/env.py`, `0001_initial.py` | **No `alembic.ini` → CLI cannot run** | Partially implemented |
| Redis | compose service + healthcheck | Running for an unrelated project on :6379 | Declared, unused by code |
| Celery / task system | — | — | **Not implemented** |
| Telethon collector | — | — | **Not implemented** |
| aiogram / publisher | — | — | **Not implemented** |
| React/Vite admin UI | — | — | **Not implemented** |
| Docker + Compose | Dockerfile, compose | `compose config` fails without `.env`; build blocked in audit | Partially implemented |

Only the API + persistence layer exists. Everything that makes the platform "intelligence" (collection, queueing, publishing, analytics, administration UI) is absent.

## 2. Backend assessment

### Application initialization
- `app/main.py` uses a `lifespan` context manager and conditionally calls `Base.metadata.create_all` when `auto_create_schema and app_env == "development"`.
- **Inconsistency:** schema creation is controlled by two independent flags. Any environment with `APP_ENV=development` silently bootstraps schema even if a migration-based workflow is intended. There is no explicit `AUTO_CREATE_SCHEMA=false` in `.env.example` guidance for staging.
- No `version` single source: `"0.1.0"` is hardcoded in three places (`main.py` twice, `pyproject.toml`).

### API organization
- Clean separation: one router per resource, `settings.api_prefix` applied at mount time, health under root.
- `PATCH /sources/{id}/status` takes `is_active` as a **query parameter**, not a body model. Every other mutation is JSON-body based. This is an API consistency defect and makes the endpoint awkward for a browser client.
- No pagination, filtering, or sorting contract on any list endpoint (`GET /sources`, `GET /ingestion/posts`, `GET /opportunities`). Each returns the full table ordered by `id DESC`.

### Service boundaries
- `app/services/extractor.py` is a pure function over text — good boundary, no DB or IO.
- Everything else lives in routers: HTTP validation, dedupe, hashing, extraction, ORM writes, and commit all inside `ingest_manual`. There is no ingestion service layer, so the same logic cannot be reused by a future Telegram collector or worker without duplicating it. **This is the single most important structural issue for M2/M3.**
- No repository/query layer; routers query sessions directly.

### Dependency injection
- FastAPI `Depends(get_db)` only. Acceptable at this size. No settings injection, so tests cannot override the engine.

### Configuration management
- `pydantic_settings` with `.env`, `extra="ignore"`. Sound.
- `log_level` is declared and **never used** anywhere (`grep` shows only its definition) → configuration is cosmetic.
- Three different `DATABASE_URL` values across `config.py`, `.env.example`, and `compose` (see Repository Map §5). A developer following the README without editing `.env` gets a confusing connection error.

### Error handling
- Hand-rolled `HTTPException` with string `detail`. No exception hierarchy, no global handler, no `request_id`, no error codes. Clients cannot branch on failures reliably.
- `app/routers/health.py` wraps the probe in a bare `except Exception:` and **discards the exception** → no logs, no reason, only `"degraded"`.
- `db.commit()` is unguarded in `create_source`, `review_opportunity`, and `ingest_manual`. Any constraint violation (e.g. the duplicate race below) surfaces as an unhandled 500.
- No rollback on failure: after a failed commit the session is returned to the pool in a dirty state by `get_db` (`db.close()` without `rollback()`); the next request on that connection can inherit an aborted transaction.

### Logging
- None. No logger, no middleware, no access log configuration, no structured output. `LOG_LEVEL` in `.env.example` has no effect.

### Authentication / authorization
- **Absent.** No dependency, middleware, header check, or role model exists anywhere in the tree. Every route listed in the Repository Map is open.

## 3. Database assessment

| Aspect | Assessment |
|---|---|
| ORM style | SQLAlchemy 2.0 `Mapped[...]` / `mapped_column` — modern and correct |
| Relationships | `Source.posts`, `CollectedPost.source`, `CollectedPost.opportunity`, `Opportunity.post` with `back_populates` — consistent |
| Uniqueness | `UNIQUE (source_id, external_id)` present. `content_hash` **not unique** |
| Indexes | `ix_collected_posts_content_hash` only. No index on `opportunities.review_status` (the review queue's primary filter) or `created_at` |
| Data types | `Float` for `budget_amount` — acceptable for display, unsuitable for exact money arithmetic (banker's concerns) if analytics ever aggregates precision-critical values |
| Timezone | `DateTime(timezone=True)` + aware `utcnow()` — correct choice |
| Migration history | single revision `0001_initial`, `down_revision=None`; revision id is a string, not a hash (fine but non-standard) |
| Model↔migration parity | 29 mapped columns; migration creates the same columns. `collected_at`/`created_at` are `nullable=False` **without server defaults**, so non-ORM writers must supply values. `is_active` has `server_default=true` in the migration but a Python-side default in the model — divergent sources of truth |
| Transaction boundaries | One implicit transaction per request via request-scoped session; `db.flush()` inside ingestion is correctly used to obtain `post.id` |
| Migration runner | **No `alembic.ini`.** `alembic upgrade head` (documented in README) fails immediately |

### Duplicate-handling race
`ingest_manual` performs a `SELECT` for an existing `(source_id, external_id)`, then inserts. Two concurrent requests with the same pair both pass the check; the second hits `uq_source_external_post` and the unguarded `commit()` raises `IntegrityError` → HTTP 500 instead of the intended 409. The deduplication contract is therefore enforced by the database but **not handled** by the application.

## 4. Background processing

Not implemented. `redis` is declared in compose and health-checked but no code imports a Redis client; `pyproject.toml` lists no `redis`, `celery`, or `rq` dependency. There is no worker process, task registry, retry policy, dead-letter queue, or scheduler. `docs/ROADMAP.md` correctly defers this to M3, but this means **retry and idempotency guarantees for ingestion do not exist today**.

## 5. Telegram integrations

Not implemented — collection (Telethon) and publishing (aiogram) both absent. `Source.source_type` accepts `telegram`, and `source_url` stores a channel URL, but nothing consumes them. There is no credential model, no session storage, no checkpoint table, no allowed-channel allowlist, and no publication record. `docs/ARCHITECTURE.md` states the intended constraints (no session strings in VCS, no auto-publish, preserve attribution) — these are **policy, not enforcement**.

## 6. Frontend

Absent. No `package.json`, no build tooling, no static mounting. The admin interface exists only as OpenAPI/swagger at `/docs`. Because the intended architecture requires an administrative review UI, M5 is fully unstarted.

## 7. Coupling and consistency issues

1. **Router-embedded business logic** blocks reuse by collector/worker (see §2).
2. **Three-way `DATABASE_URL` divergence** between code default, `.env.example`, and compose.
3. **Inconsistent mutation style** (body vs query parameter).
4. **Dead configuration** (`log_level`) gives a false impression of operational control.
5. **Schema creation bypasses migrations** in development, so the migration path is never exercised during normal development — which is why the missing `alembic.ini` went unnoticed.
6. **`ReviewUpdate.reviewer_note`** is an accepted request field with no storage anywhere in the model or migration: the API validates it and silently discards it.
7. **Extraction and review status vocabularies are unconstrained strings** (`review_status`, `opportunity_type`, `extraction_method`), so typos and future drift are unchecked; only the PATCH schema constrains its own input.

## 8. Architectural boundary check (financial)

Verified by inspection and search: no wallet, escrow, payout, invoice, milestone, or contract model/table/endpoint exists, and no HTTP client or crypto/payment dependency is imported. The service holds no marketplace credentials. **The financial-independence boundary is respected by this scaffold.** This must remain an explicit non-goal in every subsequent milestone.

## 9. Verdict

The scaffold is a coherent, well-typed **single-tenant CRUD API with a deterministic extractor**. It follows its own documented M1 intent for persistence and manual ingestion, but:

- it is **not runnable in this environment** without dependency installation,
- its documented migration workflow is **broken**,
- its review workflow **loses data**,
- it has **no authentication**, so it cannot face a network,
- and the collection → classification → dedupe → risk → publish chain that defines the product is **not started**.

Implementation follows the intended architecture *in shape* (layers, naming, config, migration discipline) but the intended architecture is roughly **15–20% realized**.
