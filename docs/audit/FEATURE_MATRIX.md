# Feature Matrix — Yekis Intelligence

**Status vocabulary:** IMPLEMENTED AND VERIFIED · IMPLEMENTED BUT NOT VERIFIED · PARTIALLY IMPLEMENTED · NOT IMPLEMENTED · BROKEN · BLOCKED BY MISSING CREDENTIALS OR EXTERNAL ACCESS

**Verification policy:** "Verified" means an executed command produced evidence during this audit. File presence alone is never sufficient. Where execution was blocked, the status records the block (see `QA_BASELINE.md`).

## 1. Platform and infrastructure

| Feature | Status | Evidence | Missing work |
|---|---|---|---|
| Application startup | BLOCKED BY MISSING CREDENTIALS OR EXTERNAL ACCESS | `app/main.py` defines app + lifespan + 4 routers; `python -m compileall` exit 0 (syntax valid). Import impossible: `fastapi`, `pydantic`, `sqlalchemy` absent from host Python. `pip install -e ".[dev]"` and `docker build` were refused during the audit | Dependency installation + a real startup/import check |
| Configuration management | IMPLEMENTED BUT NOT VERIFIED | `app/config.py` (15 lines) with `pydantic_settings`, `.env` support, `extra="ignore"` | Remove/fix unused `log_level`; unify 3 conflicting `DATABASE_URL` values |
| Database connectivity | BLOCKED BY MISSING CREDENTIALS OR EXTERNAL ACCESS | `compose` declares `postgres:16-alpine` on host :5440; exact image `postgres:16` cached locally; nobody started it. Two *unrelated* containers (`yekis-postgres-1` :5433, `yekis-redis-1` :6379) are running from another project and were deliberately not used | Start a dedicated DB and connect |
| Schema creation via migrations | BROKEN | `migrations/versions/0001_initial.py` exists but **`alembic.ini` is absent**; `ls alembic.ini` → No such file | Add `alembic.ini` + verify `alembic upgrade head` |
| Schema creation via `create_all` | IMPLEMENTED BUT NOT VERIFIED | `main.py:10-12` gated on `auto_create_schema and app_env=="development"` | Verify startup against real DB; decide single authority |
| Table/column parity model↔migration | IMPLEMENTED AND VERIFIED (static) | 29 `mapped_column` entries; migration creates the same names/types; review of both files | Add server defaults where listed in backlog |
| Docker image build | BLOCKED BY MISSING CREDENTIALS OR EXTERNAL ACCESS | `Dockerfile` present (8 lines); `docker build` refused during audit | Build + smoke-run in a permitted environment |
| Docker Compose configuration validity | BROKEN | `docker compose config` → `env file ...\.env not found`; service `api` sets `env_file: .env` which is git-ignored and absent | Document/automate env bootstrap; add `api` healthcheck |
| CI/CD | NOT IMPLEMENTED | no `.github/` directory | Add lint + test workflow |

## 2. Backend features

| Feature | Status | Evidence | Missing work |
|---|---|---|---|
| Source creation | IMPLEMENTED BUT NOT VERIFIED | `POST /api/v1/sources` → `sources.create_source`; `SourceCreate` validates `name` 2–180, `source_type` in (telegram/website/rss/manual); no dup-name check, unguarded `commit` | Verify; return 409 on conflict; align with auth |
| Source listing | IMPLEMENTED BUT NOT VERIFIED | `GET /api/v1/sources`, ordered `id DESC` | Pagination |
| Source update (rename/category/url) | NOT IMPLEMENTED | only `/status` exists | Full update endpoint |
| Source disable/enable | IMPLEMENTED BUT NOT VERIFIED | `PATCH /api/v1/sources/{id}/status?is_active=bool` → 404 if missing | Move to JSON body; verify persistence |
| Source deletion / soft delete | NOT IMPLEMENTED | no route, no `deleted_at` | Decide retention policy |
| Manual ingestion | IMPLEMENTED BUT NOT VERIFIED | `POST /api/v1/ingestion/manual`; 404 unknown source, 409 inactive source, 409 duplicate pair, sha256 `content_hash`, flush+commit | Guard `IntegrityError` race (currently 500); verify end-to-end |
| Original content preservation | IMPLEMENTED BUT NOT VERIFIED | `CollectedPost.original_text` (Text, not truncated) + `original_url` | Verify; enforce immutability on edit |
| Post listing | IMPLEMENTED BUT NOT VERIFIED | `GET /api/v1/ingestion/posts` | Pagination, source filter |
| Website/RSS collection | NOT IMPLEMENTED | `source_type` accepts `website`/`rss`, nothing consumes them | Collector module |
| Telegram collection | NOT IMPLEMENTED | no Telethon import/dependency anywhere | Whole M2 |
| Background jobs / queue | NOT IMPLEMENTED | no celery/redis dependency; `redis` compose service unused | Whole M3 |
| Retry / failure recovery | NOT IMPLEMENTED | no retry logic, no dead-letter, no failure table | Whole M3 |
| Authentication | NOT IMPLEMENTED | no auth dependency, middleware, or header check in any file | Blocker |
| Authorization / roles | NOT IMPLEMENTED | no user/role model | Blocker |
| Audit logging | NOT IMPLEMENTED | no audit table, no reviewer identity stored; `reviewed_at` only | Add audit trail |
| Error handling consistency | PARTIALLY IMPLEMENTED | `HTTPException` with string details in 3 routers; `health` swallows all exceptions; commits unguarded; no global handler | Exception hierarchy + handler + rollback |
| Logging / observability | NOT IMPLEMENTED | no logger anywhere; `log_level` setting unused | Structured logging + request id |
| CORS | NOT IMPLEMENTED | no `CORSMiddleware` | Needed before frontend |
| Rate limiting | NOT IMPLEMENTED | none | Before public exposure |

## 3. Intelligence features

| Feature | Status | Evidence | Missing work |
|---|---|---|---|
| Structured opportunity extraction | IMPLEMENTED AND VERIFIED | Executed the 6 assertions from `tests/test_extractor.py` directly (pytest unavailable) with a stdlib harness: **6/6 PASS** — `freelance_job`, `software`, `react` in skills, `300.0 USD`, `2 weeks` | Formalize as pytest run; extend cases |
| Budget extraction (USD) | IMPLEMENTED AND VERIFIED | probe `"Pay $1,000."` → `1000.0 USD` | Centralize one money parser |
| Budget extraction (ETB) | IMPLEMENTED AND VERIFIED | probe `"budget ETB 15,000"` → `15000.0 ETB` | Secondary currency pattern is a bare `birr\|ETB` match; test more formats |
| Duration extraction | IMPLEMENTED AND VERIFIED | `"3 days"`, `"2 weeks"` matched | Date/deadline extraction (relative dates) missing |
| Remote detection | PARTIALLY IMPLEMENTED | returns `True` or `None` (never `False`); probe `"remote"` → `True` | Use tri-state `bool \| None` with explicit "on-site" detection |
| Skill extraction | PARTIALLY IMPLEMENTED | 17 hardcoded `SKILL_TERMS`, matched by naive `in` substring | Word-boundary matching; curated skill taxonomy; DB-driven list |
| Category classification | BROKEN | probe `"does not react well to pressure, designing interiors."` → `category=software` and `react` matched, because `"react"` matched inside `"react well"`. Also only 4 categories exist | Tokenized matching; expand taxonomy |
| Opportunity type classification | PARTIALLY IMPLEMENTED | only `freelance_job` or `other`, driven by a 7-phrase keyword list | Add resource/internship/scholarship/training/news as required |
| Quality scoring | IMPLEMENTED AND VERIFIED (behavioural) | additive 0–100 formula; probe cases produced 20, 30, 50, 80, 90, 100 | Score is never used to filter or sort; no completeness semantics per field |
| Relevance filtering / thresholding | NOT IMPLEMENTED | probe `"Just a random market news update…"` still produced an `Opportunity` row (score 20, type `other`) | Minimum threshold or separate reject bucket |
| Duplicate detection (within source) | IMPLEMENTED BUT NOT VERIFIED | pre-check on `(source_id, external_id)` + DB unique constraint | 409 on constraint race |
| Duplicate detection (across sources) | NOT IMPLEMENTED | `content_hash` indexed but not unique; no lookup by hash | Hash-based cross-source dedupe |
| Near-duplicate / fuzzy detection | NOT IMPLEMENTED | none | Similarity thresholding |
| Risk / fraud detection | NOT IMPLEMENTED | probe `"URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. Pay upfront."` → `type=other, score=30`, **no flag**, and budget captured as the bait `5000 USD` | Buying-signal rules + `risk_flags` + human-review routing |
| LLM extraction provider | NOT IMPLEMENTED | rule extractor only, `extraction_method="rules"` hardcoded | M4 |

## 4. Review and publishing features

| Feature | Status | Evidence | Missing work |
|---|---|---|---|
| Review queue listing | IMPLEMENTED BUT NOT VERIFIED | `GET /api/v1/opportunities?status=` filter on `review_status` (no index) | Pagination; sort by quality |
| Approve / reject / needs_review | IMPLEMENTED BUT NOT VERIFIED | `PATCH /api/v1/opportunities/{id}/review`, schema pattern-restricted; sets `reviewed_at` | Verify; enforce state transitions |
| Field editing by reviewer | NOT IMPLEMENTED | no update endpoint for opportunity fields | Core Workflow B gap |
| Reviewer note | BROKEN | `ReviewUpdate.reviewer_note` accepted, validated (≤2000), then discarded — `reviewer_note` appears in `schemas.py` only, not in `models.py` or the migration | Add column + migration, or remove the field |
| Reviewer identity / audit record | NOT IMPLEMENTED | no `reviewed_by`, no audit table | Blocker for accountability |
| Re-review / status history | NOT IMPLEMENTED | single mutable `review_status` field | History table |
| Content formatting for publishing | NOT IMPLEMENTED | none | M6 |
| Publishing to Telegram | NOT IMPLEMENTED | no aiogram/telegram dependency | M6 |
| Publication record / idempotent publish | NOT IMPLEMENTED | no publication table; no `published_at` | M6 |
| Scheduling | NOT IMPLEMENTED | none | M6 |
| Analytics / market intelligence | NOT IMPLEMENTED | no aggregation endpoints, no analytics tables | M7 |

## 5. Frontend features

| Feature | Status | Evidence | Missing work |
|---|---|---|---|
| Admin interface | NOT IMPLEMENTED | no `package.json`, no React/Vite, no static mount | Whole M5 |
| Source management UI | NOT IMPLEMENTED | — | M5 |
| Opportunity review UI | NOT IMPLEMENTED | — | M5 |
| Publishing management UI | NOT IMPLEMENTED | — | M5 |
| Analytics UI | NOT IMPLEMENTED | — | M7 |

## 6. Testing

| Feature | Status | Evidence | Missing work |
|---|---|---|---|
| Unit tests for extractor | IMPLEMENTED AND VERIFIED (manually) | `tests/test_extractor.py`, 1 test; pytest not installed, so the 6 assertions were executed through an equivalent stdlib script → all passed | Run under pytest |
| API/route tests | NOT IMPLEMENTED | no `TestClient` usage, no `conftest.py` | Add |
| Database/migration tests | NOT IMPLEMENTED | none | Add against ephemeral DB |
| Integration / workflow tests | NOT IMPLEMENTED | none | Add for Workflows A–F |
| Test isolation & fixtures | NOT IMPLEMENTED | `tests/` has no fixture, no DB session override | Add |
| Coverage tooling | NOT IMPLEMENTED | `pytest` config has `testpaths` only | Add |

## 7. Summary counts

| Status | Count |
|---|---|
| IMPLEMENTED AND VERIFIED | 7 |
| IMPLEMENTED BUT NOT VERIFIED | 11 |
| PARTIALLY IMPLEMENTED | 4 |
| BROKEN | 4 |
| NOT IMPLEMENTED | 36 |
| BLOCKED BY MISSING CREDENTIALS OR EXTERNAL ACCESS | 3 |
| **Total assessed capabilities** | **65** |

Of 65 assessed capabilities, **7 are verified** (~11%) and **36 are absent** (~55%). Everything downstream of storage — collection, classification breadth, risk, editing, audit, publishing, analytics, frontend — is absent.
