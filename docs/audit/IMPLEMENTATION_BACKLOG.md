# Implementation Backlog — Yekis Intelligence

**Priorities:** P0 Critical (data loss, security compromise, financial-boundary breach, or system fundamentally unusable) · P1 High (core workflow broken, essential security control missing) · P2 Medium (important functionality incomplete or unreliable) · P3 Low (maintainability, docs, optimization)

**Agents:** A2 = Backend & database · A3 = Integrations & background processing · A4 = Security & end-to-end QA · A5 = Frontend & admin experience (deferred until a frontend is commissioned)

**Rule for all items:** do not weaken the financial-independence boundary, do not commit secrets, do not reset existing databases, and do not publish to real Telegram channels.

---

## 1. Foundation and environment

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-001 | P0 | The project cannot be imported or run in the audit environment; no dependencies are installed and the host interpreter is 3.15 while the project targets 3.12 | `pyproject.toml`, `Dockerfile` | `pip list` shows no project deps; `pip install` and `docker build` were refused during audit; host `python --version` → 3.15.1 | Nothing can be verified; every runtime claim stays unproven; the team cannot build on it | Pin a 3.12 interpreter, install `.[dev]`, then prove `python -c "import app.main"` and `uvicorn` startup | — | A2 |
| YI-002 | P0 | Alembic cannot run: `alembic.ini` is missing, so the documented `alembic upgrade head` path is dead | `migrations/`, `README.md` | `ls alembic.ini` → No such file; README §Local development instructs the command | No migration-based schema exists in practice; only dev `create_all` works, so schema drift is undetectable | Add `alembic.ini` pointing `script_location = migrations` and reading the URL from `app.config.settings`; verify upgrade/downgrade round-trip on a scratch DB | YI-001 | A2 |
| YI-003 | P1 | One setting has three different values and a hardcoded credential fallback | `app/config.py:8`, `.env.example:4`, `docker-compose.yml:32` | `localhost:5440` (code default) vs `db:5432` (env sample and compose); password `yekis_intel_dev` present in all three | Confusing first-run failures; a service can quietly run against a default credential path (see SEC-11) | Make `DATABASE_URL` required (no default), document per-environment values, keep one compose override | YI-001 | A2 |
| YI-004 | P2 | No CI pipeline: nothing runs lint, tests, or build on change | `.github/` | directory absent | Regressions are only caught by hand; the broken migration workflow went unnoticed precisely because nothing ran it | Add a workflow: `ruff check`, `pytest`, `alembic upgrade head` against a service-container Postgres | YI-001, YI-002 | A4 |
| YI-005 | P2 | `log_level` setting is declared but never applied; no logging exists anywhere | `app/config.py:10` | `grep -rn "log_level" app/` → only the definition | Operators cannot set verbosity; outages are invisible | Configure `logging` from settings at startup; add structured logs and a request-id middleware | YI-001 | A2 |
| YI-006 | P3 | Version string duplicated in three places | `app/main.py:16,25`, `pyproject.toml:7` | `"0.1.0"` hardcoded twice and declared once | Drift between API metadata and package version | Single source (`importlib.metadata.version`) | — | A2 |

## 2. Database and backend

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-010 | P1 | Unhandled `IntegrityError` on concurrent duplicate ingestion returns HTTP 500 instead of 409 | `app/routers/ingestion.py:23-31,52-57` | Pre-check `SELECT` then unguarded `commit()`; unique constraint `uq_source_external_post` exists in model+migration | Clients see a server error for a normal condition; silent duplicate-retry failures once a collector automates ingestion | Catch `IntegrityError`, `rollback()`, return 409 | YI-001 | A2 |
| YI-011 | P1 | `get_db` closes without rollback, so a failed transaction can poison the pooled connection | `app/db.py:13-19` | `try/finally: db.close()` only | Next request on that connection can inherit an aborted transaction — intermittent, hard-to-diagnose 500s | `except: db.rollback(); raise` in the dependency | YI-001 | A2 |
| YI-012 | P1 | Reviewer note is accepted and silently discarded | `app/schemas.py:49-51`, `app/routers/opportunities.py:23-31`, `app/models.py`, `migrations/versions/0001_initial.py` | `reviewer_note` appears only in `schemas.py`; no column, never read by the handler | Loss of reviewer justification — the record needed for accountability and dispute handling | Add `reviewer_note: Text` to model + a new Alembic revision; persist it | YI-002 | A2 |
| YI-013 | P1 | No review audit trail and no reviewer identity | `app/models.py`, `app/routers/opportunities.py` | only `review_status` + `reviewed_at` are stored | An approval cannot be traced to a person or time-of-decision; requirement "administrative action history" unmet | Append-only `review_events` table (previous status, new status, actor, note, timestamp) written in the same transaction | YI-012, YI-022 | A2 |
| YI-014 | P2 | Review queue filter has no index; no state machine, so any status can jump to any other | `app/models.py:44`, `app/routers/opportunities.py` | `review_status` has no index; `PATCH` writes the decision blindly | Slow queue as data grows; approved → pending flips are possible and destroy provenance | Index `review_status`; validate legal transitions (`pending_review → approved/rejected/needs_review`) | YI-013 | A2 |
| YI-015 | P2 | `review_status`, `opportunity_type`, and `extraction_method` are unconstrained strings | `app/models.py:29,44,45` | no enum/CHECK constraint, no `Literal` | Typo'd or drifting vocabulary silently corrupts queue filtering and analytics grouping | Python `Enum` + DB CHECK constraint (or native enum) | YI-002 | A2 |
| YI-016 | P2 | No pagination or caps on any list endpoint | `app/routers/sources.py:20`, `ingestion.py:61`, `opportunities.py:12` | full-table `select(...)` returned directly | Memory and latency blow up as content accumulates; unusable admin UI later | `limit`/`offset` (or cursor) with hard maximums, plus a total count | YI-001 | A2 |
| YI-017 | P2 | `PATCH /sources/{id}/status` takes a query parameter while every other mutation is JSON | `app/routers/sources.py:25` | signature `set_source_status(source_id: int, is_active: bool, ...)` | Inconsistent client contract; awkward for fetch/browser usage | Move to a `SourceStatusUpdate` body model | — | A2 |
| YI-018 | P3 | Source metadata cannot be edited or removed; no soft delete | `app/routers/sources.py`, `app/models.py` | only create/list/status routes | Bad sources cannot be corrected; deleting would lose post history | Add `PATCH /sources/{id}` and `is_archived` flag | YI-017 | A2 |
| YI-019 | P3 | `budget_amount` uses `Float` | `app/models.py:36` | `Mapped[float \| None]` | Float rounding is unsuitable for aggregated money reporting | Use `Numeric(14,2)` for storage, convert at the API edge | YI-002 | A2 |
| YI-020 | P3 | Migration lacks server defaults for timestamps; `is_active` default is duplicated | `migrations/versions/0001_initial.py:18,28,56` | columns `nullable=False` with no `server_default` while the model supplies a Python default | Non-ORM writes (bulk import, collector script) fail or diverge | Add `server_default=sa.func.now()` / `sa.true()` and keep model defaults aligned | YI-002 | A2 |

## 3. Ingestion and extraction

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-030 | P1 | Ingestion logic lives entirely in the router, so a collector/worker cannot reuse it | `app/routers/ingestion.py:13-57` | dedupe + hash + extract + ORM + commit in one function | Agent 3 would duplicate it, producing divergent dedupe behaviour between manual and automatic ingestion | Extract `app/services/ingestion.py` with `ingest_post(db, ...) -> Opportunity`; router becomes a thin adapter | YI-010, YI-011 | A2 |
| YI-031 | P1 | Skill and category matching uses naive substring search | `app/services/extractor.py:38-47,52-58` | executed probe: "does not react well … designing interiors" → `category=software`, `react` in skills | Wrong classification and wrong skill analytics — the core "market intelligence" output is untrustworthy | Tokenize with `re.findall(r"[a-z0-9+#.]+", text)` and match whole tokens; add a regression test | YI-001 | A2 |
| YI-032 | P1 | Classification covers only 4 categories and 2 opportunity types; resources, internships, scholarships, training, and market news are unrepresented | `app/services/extractor.py:52-63` | `opportunity_type` ∈ {`freelance_job`, `other`}; category ∈ {software, design, marketing, media} | The platform's stated purpose (classify opportunities, resources, internships, scholarships, training, news) is unmet | Define an explicit taxonomy (enum) and rule sets per type; keep `other` as fallback only | YI-015, YI-031 | A2 |
| YI-033 | P1 | No risk/fraud screening; suspicious content is treated as a normal opportunity | `app/services/extractor.py`, `app/models.py` | executed probe with an upfront-fee scam → `type=other`, score 30, no flag; budget captured as the bait `5000 USD` | Publishing such content destroys community trust, the platform's stated goal; and risk indicators silently corrupt budget analytics | Add `risk_flags` (JSON) + `risk_score`, rule set for upfront payment, contact-off-platform, unrealistic rates, urgency pressure; route flagged items to `needs_review` with the flag visible; never auto-reject on a flag alone | YI-030 | A2 |
| YI-034 | P2 | No relevance gate: irrelevant text still becomes an opportunity | `app/services/extractor.py`, `app/routers/ingestion.py` | executed probe "random market news update" → opportunity created with score 20 | Review queue fills with noise; reviewer effort wasted; analytics diluted | Apply a configurable minimum score/type gate, or store non-opportunities in a separate rejected bucket | YI-033 | A2 |
| YI-035 | P2 | No cross-source duplicate detection | `app/models.py:24`, `app/routers/ingestion.py` | `content_hash` indexed, not unique; no lookup by hash | The same message reposted by three channels becomes three opportunities and, later, three publications | Unique index on `(content_hash)` or a `duplicate_of_id` link with a normalized hash | YI-030 | A2 |
| YI-036 | P2 | Budget capture is order-dependent and can lock onto bait or non-budget figures | `app/services/extractor.py:60-71` | probe froze on `$5000` from a scam while ignoring `500 birr` | Misleading market/price intelligence | Prefer amounts adjacent to budget keywords (`budget`, `pay`, `salary`, `fee`); support ranges and per-hour/per-project units | YI-033 | A2 |
| YI-037 | P2 | Remote detection can never be `False` | `app/services/extractor.py:73` | returns `True` or `None` | "unknown" and "on-site" are indistinguishable in analytics | Detect explicit on-site terms and return `False` | YI-031 | A2 |
| YI-038 | P3 | No deadline/date extraction; duration is display-only text | `app/services/extractor.py:72` | regex captures `"2 weeks"` only | Cannot schedule expiry, reminders, or "closing soon" ordering | Parse relative dates into `deadline_at` (timezone-aware) | YI-019 | A2 |
| YI-039 | P3 | Extractor has one test and no fixture corpus | `tests/test_extractor.py` | 1 test, 6 assertions; pytest cannot run in audit env | Regressions in the highest-churn module go undetected | Add a table-driven corpus including the probe cases above | YI-001, YI-031 | A2 |

## 4. Telegram integrations

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-040 | P1 | No Telegram collection exists | new `app/integrations/telegram/collector.py` | no `telethon` dependency or import anywhere in the tree | The primary content source of the platform is absent; ingestion is manual-only | Telethon client using an authorized account from environment secrets; fetch only allowlisted channels stored as `Source` rows with `source_type=telegram`; persist `external_id` = message id | YI-030, YI-050 | A3 |
| YI-041 | P2 | No collection checkpoints, no source health, no backoff | `app/models.py` | no checkpoint/offset/health columns | Restarting the collector re-fetches or skips messages; failures are invisible | Track `last_collected_message_id`, `last_success_at`, `consecutive_failures` per source; exponential backoff with jitter | YI-040 | A3 |
| YI-042 | P1 | No Telegram session/credential protection strategy beyond "don't commit it" | `docs/ARCHITECTURE.md` | policy only; no `.gitignore` entry for `*.session`, no secret handling code | A committed Telethon session string is a full account takeover of the collector identity | Add `*.session*` to `.gitignore`, load session/API credentials only from secret storage, and add a startup guard that refuses to boot with a leaked-session-shaped env in a non-dev environment | — | A3 |
| YI-043 | P1 | No publisher; approved content cannot reach a channel | new `app/integrations/telegram/publisher.py` | no `aiogram` dependency | Workflow C is entirely missing; the platform cannot fulfil its purpose | Publish only rows with `review_status=approved`, format with source attribution, write a publication record before sending, and require an explicit human approval step | YI-060, YI-061 | A3 |
| YI-044 | P2 | No destination-channel allowlist or dry-run mode | — | none | Accidental publication to a real channel is a one-line mistake with public consequences | Config allowlist of `chat_id`s + `PUBLISHING_ENABLED=false` default + dry-run logging | YI-043 | A3 |

## 5. Background processing

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-050 | P1 | Redis is declared but unused; no task system exists | `docker-compose.yml:18-25`, `pyproject.toml` | no `redis`/`celery` dependency, no worker module, no imports | No way to run collection or publishing on a schedule; all work is request-bound | Add Celery (or equivalent) with Redis broker; a worker service in compose; task registration and health check | YI-001 | A3 |
| YI-051 | P1 | No retry policy, idempotency key, or dead-letter handling | — | none | Transient DB/Telegram failures either lose work or duplicate it on retry (Workflow F unmet) | Idempotent tasks keyed by `(source_id, external_id)` and `(opportunity_id, channel)`; bounded retries with backoff; failure table for dead letters | YI-050, YI-030 | A3 |
| YI-052 | P2 | No scheduler | — | none | Periodic collection depends on a human | Celery beat schedule per source cadence, honouring source health | YI-050, YI-041 | A3 |
| YI-053 | P2 | No task observability | — | none | Silent worker failures | Structured task logs, success/failure counters, `/health` worker check | YI-050, YI-005 | A3 |

## 6. Admin review and publishing

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-060 | P1 | Reviewers cannot edit extracted fields; only a status decision is possible | `app/routers/opportunities.py` | no update route | Every extraction error must be rejected rather than corrected — the extraction quality ceiling becomes the review ceiling | `PATCH /opportunities/{id}` for title, category, skills, budget, duration, remote, description; record before/after in the audit trail | YI-013 | A2 |
| YI-061 | P1 | No publication record or formatting layer | `app/models.py` | no publication table, no `published_at` | Publication cannot be idempotent, attributable, or audited | `Publication` table (opportunity_id, channel, message_id, status, scheduled_at, published_at, error) + a formatter service | YI-043 | A3 |
| YI-062 | P2 | No scheduling of approved content | — | none | Timed posting is impossible | `scheduled_at` + worker pickup, with cancel support | YI-061, YI-052 | A3 |
| YI-063 | P2 | Approved content is not verifiably the only publishable state | `app/routers/opportunities.py` | status is a free string; no constraint enforcing approved-before-publish | Risk of publishing unapproved content once a publisher exists | Enforce in the query **and** with a DB constraint/trigger-level guard | YI-043, YI-014 | A3 |

## 7. Analytics

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-070 | P2 | No analytics whatsoever | — | no aggregation endpoint, view, or materialized table | The "market intelligence" half of the product is absent | Read-only endpoints: skill demand over time, category mix, budget distribution (with currency separation), client requirement frequency; pre-aggregate for performance | YI-031, YI-032, YI-019 | A2 |
| YI-071 | P2 | Community-needs and market-trend detection absent | — | none | Growth insight requirement unmet | Keyword/trend extraction over collected posts with a time window | YI-070 | A2 |
| YI-072 | P3 | No source-quality metrics | — | none | Cannot prune low-yield or high-risk sources | Per-source yield/rejection/risk rates | YI-070, YI-041 | A2 |

## 8. Security

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-080 | P0 | No authentication on any endpoint | `app/main.py`, all routers | `grep` for auth terms returns nothing; all 9 application routes anonymous; `/docs` public | Total compromise of the review workflow; unusable on any network | Service-to-service auth (API key or HMAC) as a FastAPI dependency applied at router level; refuse to start in non-dev without credentials; protect `/docs` | YI-001 | A4 |
| YI-081 | P0 | No authorization or actor identity | `app/models.py`, review routes | no user/role model, no `reviewed_by` | Reviewer decisions are unattributable and any caller is omnipotent | Roles (service/collector, reviewer, admin) + actor recorded on every mutation | YI-080, YI-013 | A4 |
| YI-082 | P1 | `source_url` is an unvalidated string; `HttpUrl` is imported but unused | `app/schemas.py:1,8` | `source_url: str = Field(min_length=3, max_length=1000)`; dead `HttpUrl` import | Latent SSRF once a web/RSS collector dereferences it (metadata/internal access) | Validate as `AnyHttpUrl`, https-only, host allowlist; forbid private/link-local ranges at fetch time | YI-080 | A4 |
| YI-083 | P2 | No rate limiting or request-size limits; list endpoints unbounded | `app/main.py`, routers | no limiter; `max_length=30000` per request with no quota | Storage/CPU exhaustion by an anonymous or compromised client | Per-key rate limit, body size cap at the proxy, pagination caps | YI-080, YI-016 | A4 |
| YI-084 | P2 | No CORS policy | `app/main.py` | no `CORSMiddleware` | Predictable `allow_origins=["*"]` mistake when the frontend lands | Explicit per-environment origins, no wildcard with credentials | YI-080 | A4 |
| YI-085 | P3 | No dependency scanning or lock file | `pyproject.toml` | ranges only, no hashes, no CI | Vulnerable/yanked releases enter unnoticed; builds not reproducible | Lock file + `pip-audit` in CI + Dependabot; drop unused `python-multipart` | YI-004 | A4 |
| YI-086 | P3 | No secret scanning or pre-commit guard | `.gitignore` | no pre-commit config | A session string or token could be committed once integrations start | `pre-commit` with a secret scanner; extend `.gitignore` with `*.session*` | — | A4 |

## 9. Frontend

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-090 | P2 | No administrative interface exists | — | no `package.json`, no React/Vite, no static mount | Admins must use `/docs` and raw JSON; review at scale is impractical | React/Vite admin app: review queue, field editing, source management, publish queue, analytics; loading/error/empty states; typed API client | YI-080, YI-060, YI-084 | A5 |
| YI-091 | P3 | No API contract artifact for the client | — | OpenAPI is generated but not exported/versioned | Frontend and backend drift | Commit a generated `openapi.json` and generate the client types from it | YI-090 | A5 |

## 10. Deployment and operations

| ID | Pri | Issue | Files | Evidence | Impact | Fix | Deps | Agent |
|---|---|---|---|---|---|---|---|---|
| YI-100 | P1 | Compose fails without a manually created `.env`; documented quick start is broken | `docker-compose.yml:29`, `README.md` | `docker compose config` → `env file ...\.env not found` | A new developer cannot start the stack by following the README | Provide an explicit `env_file` optional/override pattern or a documented `cp .env.example .env` bootstrap step with non-secret dev values | YI-003 | A3 |
| YI-101 | P1 | The API container never runs migrations and has no healthcheck | `Dockerfile`, `docker-compose.yml:27-38` | Dockerfile copies only `app/`; no `alembic` in image; no api healthcheck; only `db` is waited on | Containers can start with an empty/outdated schema while reporting healthy | `COPY migrations` + `alembic.ini`, an entrypoint that waits for DB and runs `alembic upgrade head`, and a healthcheck with `depends_on: service_healthy` | YI-002 | A3 |
| YI-102 | P2 | `/health` returns HTTP 200 when the database is down | `app/routers/health.py:8-15` | exception path returns `{"status":"degraded"}` with status code 200 | Orchestrators and load balancers cannot detect failure; this is the check the compose healthcheck would rely on | Return 503 on failure, distinguish liveness vs readiness, include the reason in logs (not the body) | YI-005 | A3 |
| YI-103 | P2 | No `.dockerignore`; image runs as root | Dockerfile, repo root | no `.dockerignore`; no `USER` | Large/slow contexts, potential future secret inclusion, root-in-container on compromise | Add `.dockerignore`; create and use a non-root user | YI-085 | A3 |
| YI-104 | P3 | Redis is exposed on a host port but nothing consumes it | `docker-compose.yml:18-25` | unused by code | Unnecessary attack surface and confusion | Bind to `127.0.0.1` or move to an internal network until YI-050 lands | YI-050 | A3 |
| YI-105 | P3 | Documentation drift after each change | `README.md`, `docs/` | README asserts `alembic upgrade head` and endpoint behaviour that do not hold | Agents and developers work from false assumptions | Update README as part of each backlog item's acceptance criteria | — | All |

---

## 11. Recommended execution order

1. **Unblock** — YI-001, YI-002, YI-003 (nothing else can be verified before these).
2. **Security floor** — YI-080, YI-081 (must precede any network exposure).
3. **Data integrity** — YI-011, YI-010, YI-012, YI-013.
4. **Reusable ingestion** — YI-030, then extraction correctness YI-031, YI-032, YI-033.
5. **Review completeness** — YI-060, YI-014, YI-015, YI-016.
6. **Automation** — YI-050, YI-051, YI-040, YI-041.
7. **Publishing** — YI-061, YI-043, YI-063, YI-044.
8. **Intelligence** — YI-070, YI-071.
9. **Delivery** — YI-090 (frontend), YI-100..YI-104 (operations).
