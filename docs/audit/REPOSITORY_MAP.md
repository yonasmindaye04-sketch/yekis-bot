# Repository Map — Yekis Intelligence

**Audit date:** 2026-11-10
**Branch:** `audit/initial-baseline`
**Repository state:** scaffold baseline (no prior Git history; repository initialized during this audit)
**Total first-party Python:** 506 lines across 16 files

## 1. Top-level layout

| Path | Responsibility | Actual state |
|---|---|---|
| `app/` | FastAPI application package | 12 files, ~381 lines |
| `migrations/` | Alembic environment + revisions | `env.py`, `script.py.mako`, one revision; **no `alembic.ini`** |
| `tests/` | Automated tests | one file, one test function |
| `docs/` | Architecture, roadmap, audit reports | `ARCHITECTURE.md`, `ROADMAP.md`, `audit/` |
| `pyproject.toml` | Packaging, deps, ruff, pytest config | declared deps only; nothing installed locally |
| `Dockerfile` | Container build | 8 lines, copies only `app/` |
| `docker-compose.yml` | db + redis + api services | 3 services; redis unused by code |
| `.env.example` | Environment sample | 6 variables |
| `.gitignore` | VCS exclusions | excludes `.env`, `.venv/`, caches — correct |
| `README.md` | Quick start & API list | accurate in intent, one documented command is broken (missing `alembic.ini`) |

Not present: `.github/` (no CI), `package.json` (no frontend), `celery`/worker module, collector module, publisher module, analytics module, `alembic.ini`, `.dockerignore`, `CONFTEXT`/test fixtures.

## 2. `app/` module map

| Module | Lines | Responsibility | Notable contents |
|---|---|---|---|
| `app/__init__.py` | 0 | package marker | empty |
| `app/config.py` | 15 | Pydantic settings from `.env` | `app_name`, `app_env`, `api_prefix`, `database_url`, `auto_create_schema`, `log_level` |
| `app/db.py` | 19 | Engine, session factory, DI dependency | `Base`, `engine`, `SessionLocal`, `get_db` |
| `app/models.py` | 58 | ORM models | `Source`, `CollectedPost`, `Opportunity`; `utcnow()` |
| `app/schemas.py` | 56 | Pydantic I/O contracts | `SourceCreate`, `SourceRead`, `ManualIngestion`, `OpportunityRead`, `ReviewUpdate`, `PostRead` |
| `app/main.py` | 25 | App factory, router mounting, lifespan | mounts 4 routers; dev-only `create_all` |
| `app/routers/health.py` | 15 | `GET /health` | raw `SELECT 1` probe |
| `app/routers/sources.py` | 33 | Source CRUD subset | `POST ""`, `GET ""`, `PATCH /{id}/status` |
| `app/routers/ingestion.py` | 63 | Manual ingest + post listing | dedupe by `(source_id, external_id)`, sha256, extraction call |
| `app/routers/opportunities.py` | 32 | Review queue | `GET ""`, `PATCH /{id}/review` |
| `app/services/extractor.py` | 75 | Rule-based extraction | keyword sets, 4 regexes, quality score formula, `ExtractedOpportunity` dataclass |

`app/routers/__init__.py` and `app/services/__init__.py` are empty namespace markers.

## 3. Data model map

```
Source (sources)
  id, name, source_type, source_url, category, is_active, created_at
    │ 1..n
    ▼
CollectedPost (collected_posts)
  id, source_id→sources.id, external_id, original_text, original_url,
  content_hash (indexed, NOT unique), collected_at
  UNIQUE (source_id, external_id)  uq_source_external_post
    │ 1..1  (collected_post_id UNIQUE)
    ▼
Opportunity (opportunities)
  id, collected_post_id→collected_posts.id,
  opportunity_type, title, category, skills(JSON), budget_amount(Float),
  budget_currency, duration, remote, description, quality_score,
  review_status, extraction_method, created_at, reviewed_at
```

- No `updated_at` on any table.
- No `reviewer_note`, no `audit_log`, no `publication` table.
- `content_hash` is indexed but not unique → cross-source duplicates are permitted by schema.
- Timestamps use `DateTime(timezone=True)` with Python-side `utcnow()` defaults; the migration adds no `server_default`.

## 4. API surface (9 application routes, all unauthenticated)

| Method | Path | Handler | Declared auth |
|---|---|---|---|
| GET | `/` | `main.root` | none |
| GET | `/health` | `health.health` | none |
| POST | `/api/v1/sources` | `sources.create_source` | none |
| GET | `/api/v1/sources` | `sources.list_sources` | none |
| PATCH | `/api/v1/sources/{source_id}/status` | `sources.set_source_status` | none |
| POST | `/api/v1/ingestion/manual` | `ingestion.ingest_manual` | none |
| GET | `/api/v1/ingestion/posts` | `ingestion.list_posts` | none |
| GET | `/api/v1/opportunities` | `opportunities.list_opportunities` | none |
| PATCH | `/api/v1/opportunities/{opportunity_id}/review` | `opportunities.review_opportunity` | none |

Also exposed by default: `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json`.

Route total: 9 application routes (8 business routes + `/health` + root `/`). The sections below sometimes refer to the "8 business routes" — that excludes the root banner endpoint.

## 5. Infrastructure map

- **Dockerfile:** `python:3.12-slim`, installs package from `pyproject.toml`, copies `app/` only, runs uvicorn on `:8000` as root. Does not copy `migrations/`.
- **docker-compose.yml:** `db` (postgres:16-alpine, host port 5440, healthcheck), `redis` (redis:7-alpine, host port 6382, healthcheck), `api` (build, `env_file: .env`, host port 8100, waits only for `db`). No `api` healthcheck, no migration step, no worker service.
- **Env reconciliation:** `app/config.py` default `localhost:5440`, `.env.example` `db:5432`, compose override `db:5432` — three different values for one setting.

## 6. Runtime processes present

| Process | Responsibility | State |
|---|---|---|
| FastAPI app (`uvicorn app.main:app`) | HTTP API | code exists, not runnable locally (deps absent) |
| — | Collector worker | absent |
| — | Celery/queue worker | absent |
| — | Scheduler | absent |
| — | Frontend dev server | absent |
