# Yekis Intelligence — Project Guide

This is the main guide for understanding and working on Yekis Intelligence. It explains what the service does, how the pieces fit together, how data moves through the system, how to configure and run it, and where the current limits are.

For task-specific references:

- [API Reference](API_REFERENCE.md) for every route, request shape, role, and error.
- [Operations Runbook](OPERATIONS.md) for setup, deployment, monitoring, and incident handling.
- [Milestone Execution Audit](audit/MILESTONE_EXECUTION.md) for what was built, the tests that prove it, and live-verification gaps.
- [Documentation Index](README.md) for the full reading order.

> **Historical note:** files in `docs/audit/` (other than `MILESTONE_EXECUTION.md`) are the pre-implementation baseline. They describe the old state and are intentionally not rewritten. Do not treat them as the current architecture.

---

## 1. What this service is

Yekis Intelligence is an independent opportunity-discovery and content-intelligence service for the Yekis marketplace. It:

1. Registers sources (public Telegram channels, manual entries, and reserved website/RSS types).
2. Collects posts from authorized Telegram channels using a Telethon session.
3. Preserves the original post text and URL verbatim.
4. Extracts a structured opportunity with deterministic rules or an optional LLM provider.
5. Flags risk signals and routes suspicious content to human review.
6. Maintains a review queue with attributable, append-only decisions.
7. Publishes approved opportunities to allowlisted Telegram channels with source attribution.
8. Provides market and source-quality analytics over the collected data.

The service deliberately owns its own PostgreSQL database and migrations. It does **not** read or write the marketplace database.

## 2. What this service is not

These are hard boundaries, not temporary gaps:

| Non-goal | Why |
|---|---|
| Escrow, wallet, payout, invoice, milestone, or contract operations | The intelligence service must never be able to move or authorize money. |
| Automatic publishing without human approval | A human reviewer must approve every published opportunity. |
| Joining Telegram channels or reading private chats | Collection is limited to already-registered, active public sources. |
| Scraping non-allowlisted sources | `source_url` is registered and controlled by an admin. |
| Using marketplace credentials or financial APIs | No such credentials or clients exist in this service. |
| Treating risk flags as proof of fraud | A flag routes to review; it does not auto-reject or auto-approve. |

If a future requirement conflicts with one of these, treat it as an architecture decision, not a small feature.

---

## 3. Architecture at a glance

```text
Public Telegram channel(s)
        |
        | Telethon (authorized session, read-only collection)
        v
+---------------------------+
| Collector                 |
| checkpoints, backoff      |
+-------------+-------------+
              | ingest_post()
              v
+---------------------------------------------------------------+
| PostgreSQL (yekis_intelligence)                               |
| sources, collected_posts, opportunities, review_events,       |
| publications, job_executions, dead_letter_jobs                |
+----+-------------------+-------------------+------------------+
     ^                   ^                   ^
     |                   |                   |
     | review/edit       | scheduled jobs    | analytics queries
     |                   |                   |
+----+-------+   +-------+--------+   +------+-----------+
| API        |   | Celery worker  |   | Admin dashboard |
| FastAPI    |   | + beat         |   | /admin/ (static) |
+----+-------+   +-------+--------+   +------+-----------+
     |                   |                   |
     | publish trigger   | publish_due        | X-API-Key
     v                   v                   |
+---------------------------+                 |
| Publisher (Telethon)      |<----------------+
| approved + allowlisted    |
+-------------+-------------+
              |
              v
       Allowlisted Telegram channel
```

Redis is the Celery broker and result backend. Its durable job state is **not** in Redis: job rows live in PostgreSQL so a lost broker message cannot lose an accepted job.

### Runtime components

| Component | Entry point | Responsibility |
|---|---|---|
| API | `uvicorn app.main:app` | HTTP routes, auth, review, publishing trigger, analytics. |
| Admin dashboard | `/admin/` | Static browser UI; stores an API key in local storage and calls the API. |
| Worker | `celery -A app.worker.celery_app:celery_app worker` | Runs collection and publishing jobs. |
| Beat | `celery -A app.worker.celery_app:celery_app beat` | Schedules periodic collection and due-publication checks. |
| Collector | called by the worker or `python -m app.integrations.telegram.collector` | Fetches new messages and calls the ingestion service. |
| Publisher | called by the API or the due-publication task | Sends already-approved opportunities to an allowlisted channel. |
| Extractor | `app/services/extractor.py` and `app/services/llm/` | Produces a candidate opportunity from text. |

---

## 4. Repository map

```text
app/
  config.py                 Settings and get_settings()
  db.py                     SQLAlchemy engine, Base, request session, rollback-on-close
  main.py                   FastAPI app, CORS, static dashboard, routers, lifespan
  models.py                 ORM: Source, CollectedPost, Opportunity, ReviewEvent,
                            Publication, JobExecution, DeadLetterJob
  schemas.py                Pydantic request/response contracts
  routers/
    health.py               GET /health
    sources.py              Source create/list/get/update/status
    ingestion.py            Manual ingest and post listing
    opportunities.py        Review queue, edits, events, publish trigger
    publications.py         Publication list/get
    analytics.py            Overview, source quality, market, trends
  security/
    auth.py                 API-key parsing, roles, startup validation
  services/
    extractor.py            Deterministic rules extractor and risk screening
    ingestion.py            Shared ingest_post() pipeline
    review.py               Review state machine and audit events
    jobs.py                 Idempotent job runner, retries, dead letters
    formatter.py            Publication text and mandatory attribution
    publishing.py           Publish guards, scheduling, replay protection
    analytics.py            Read-only aggregates
    llm/
      base.py               ExtractionProvider protocol and errors
      schema.py             Closed LLMOpportunity schema
      rules_provider.py     Rules provider
      openai_compatible.py  OpenAI-compatible HTTP adapter
      orchestrator.py       Provider selection and safety merge
  integrations/telegram/
    base.py                 ChannelMessage and TelegramClientPort
    channel.py              Strict public-channel parsing
    client.py               Telethon collector adapter and fake
    collector.py            Collection loop, checkpoints, backoff, CLI
    login.py                One-time interactive login
    publisher.py            Telethon publisher adapter and fake
  worker/
    celery_app.py           Celery config and beat schedule
    tasks.py                Ingest, collect, publish-due tasks
  static/admin/index.html   Dependency-free admin dashboard
migrations/                 Alembic revision history
tests/                      Pytest suite (SQLite fixtures)
docs/                       This documentation and the audit trail
```

---

## 5. Data model

### 5.1 Tables

| Table | Purpose | Key constraints |
|---|---|---|
| `sources` | Registered channels/sites and collector health | `source_type` indexed; collector checkpoints and failure state. |
| `collected_posts` | Immutable copy of a collected post | `UNIQUE (source_id, external_id)`; `content_hash` indexed. |
| `opportunities` | Structured candidate derived from one post | `collected_post_id` unique; `review_status` indexed. |
| `review_events` | Append-only review/edit audit trail | Actor, event type, from/to status, note, field diff. |
| `publications` | One publication attempt stream per `(opportunity, channel)` | `UNIQUE (opportunity_id, channel)`; status indexed. |
| `job_executions` | Durable background job state | `idempotency_key` unique; status indexed. |
| `dead_letter_jobs` | Jobs that exhausted retries | One per `job_execution_id`; idempotency key indexed. |

### 5.2 Entity relationships

```text
Source 1 ---- n CollectedPost 1 ---- 1 Opportunity 1 ---- n ReviewEvent
                                          |
                                          +---- n Publication

JobExecution 1 ---- 0..1 DeadLetterJob
```

### 5.3 Ingestion uniqueness

`(source_id, external_id)` is the hard deduplication boundary. The collector uses the Telegram message id as `external_id`.

- A duplicate pre-check makes the common path cheap.
- The database constraint is the real guarantee.
- An `IntegrityError` is translated into `409 duplicate_post`; it is not a 500.
- The collector catches the duplicate and counts it without inserting a second row.

There is no cross-source fuzzy dedupe. The same text posted in two different channels becomes two posts. `content_hash` exists for future work but is not unique.

### 5.4 Opportunity lifecycle

```text
CollectedPost
    |
    v
Opportunity.review_status
    |
    +-- pending_review ---- approved ---- rejected
    |         |                |            |
    |         +-- needs_review +------------+
    |                |
    |                +-- approved
    |                +-- rejected
    |
    +-- risk flags present at ingestion --> needs_review
```

Allowed transitions are enforced in `app/services/review.py`:

| From | Allowed to |
|---|---|
| `pending_review` | `approved`, `rejected`, `needs_review` |
| `needs_review` | `approved`, `rejected`, `needs_review` (same-status no-op) |
| `approved` | `rejected`, `needs_review` |
| `rejected` | `needs_review` |

Every actual status change writes exactly one `review_events` row with the actor from the API key. Repeating the same decision returns the current opportunity and creates no second event. Field edits write a `field_edit` event with `{field: {from, to}}` changes.

### 5.5 Publication lifecycle

```text
pending ---- publishing enabled + approved + allowlisted ----> sending ---- send ok ----> published
   |                                                              |
   |                                                              +---- send failed ----> failed
   |
   +---- scheduled_at set ----> scheduled ---- due + checks ----> sending

sending (after a crash) is never automatically resent;
requires human reconciliation.
```

Publication states:

| State | Meaning |
|---|---|
| `pending` | Created for an immediate publish but not yet sent. |
| `scheduled` | Waiting for `scheduled_at`; the beat task will publish when due. |
| `sending` | A send was attempted. If it is still here after a crash, do not assume it failed. |
| `published` | Confirmed sent; `external_message_id` and `published_at` are set. |
| `failed` | The last send raised; `error` is stored, and retry is allowed until the attempt cap. |

### 5.6 Job lifecycle

`job_executions.status` is one of `pending`, `running`, `succeeded`, `failed`, `dead_lettered`.

- The runner refuses to execute a key that is already `running`.
- A succeeded key returns its stored result and never runs again.
- A failed key is retried up to `CELERY_TASK_MAX_RETRIES` with exponential backoff and jitter.
- Exhaustion writes a `dead_letter_jobs` row in the same transaction as the terminal `dead_lettered` status.
- A dead-lettered key is not retried again; create a new key after fixing the cause.

---

## 6. Workflows

### 6.1 Manual ingestion (HTTP)

```text
POST /api/v1/ingestion/manual
  -> auth: collector or admin
  -> source exists and is active
  -> (source_id, external_id) pre-check
  -> preserve original_text, compute sha256
  -> extract (rules or LLM + fallback)
  -> merge deterministic risk flags
  -> review_status = needs_review if risk flags else pending_review
  -> commit CollectedPost + Opportunity
  -> return OpportunityRead (201)
```

Failure modes:

| Condition | Response |
|---|---|
| Unknown source | `404 source_not_found` |
| Inactive source | `409 source_inactive` |
| Duplicate `(source_id, external_id)` | `409 duplicate_post` |
| Validation failure | `422` |

### 6.2 Telegram collection

```text
Source (source_type=telegram, is_active=true)
  -> if next_attempt_at is in the future: skip (backoff)
  -> fetch messages with id > last_collected_message_id (oldest first)
  -> for each message:
       ingest_post(...)
       duplicate -> count, no insert
       success   -> count insert
  -> advance checkpoint to highest message id
  -> clear failures, set last_success_at
```

On failure, the collector does **not** advance the checkpoint. It increments `consecutive_failures`, stores `last_error`, and sets `next_attempt_at` with exponential backoff and jitter.

The collector only reads public channel references. Invite links (`t.me/+...`) are rejected because following them would join a channel. It never logs message bodies, API hashes, or session contents.

### 6.3 Extraction

```text
text
  -> rules extractor (always runs)
  -> provider = get_provider()
       rules  -> return rules result, method "rules"
       gemini -> call Google Gemini (AI Studio key)
       llm    -> call OpenAI-compatible endpoint
                 success -> merge provider result with rules risk flags
                 failure -> return rules result, method "rules_fallback"
  -> risk flags unioned; risk_score = max
  -> if upfront_payment is present: budget is forced to null
```

Provider options:

- `EXTRACTION_PROVIDER=rules` — deterministic, no credentials, always available.
- `EXTRACTION_PROVIDER=gemini` — native Google Generative Language API using an AI Studio key (`GEMINI_API_KEY`). The key is sent in the `x-goog-api-key` header and is never placed in a URL or log.
- `EXTRACTION_PROVIDER=llm` — any OpenAI-compatible chat-completions endpoint (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`). This is the path for **NVIDIA NIM** (`https://integrate.api.nvidia.com/v1`), which needs no separate provider class.

`extraction_method` is stored on the opportunity as `rules`, `gemini`, `llm`, or `rules_fallback`. The original text is always preserved on `collected_posts`; LLM output never replaces it.

### 6.4 Review and edit

```text
Reviewer API key
  -> PATCH /opportunities/{id}/review
       validate transition
       update review_status, reviewed_at, reviewed_by, reviewer_note
       append ReviewEvent(status_change)
  -> PATCH /opportunities/{id}
       diff supplied fields
       update opportunity
       append ReviewEvent(field_edit) with before/after
```

Every write is attributable to the API key's `name` in `AUTH_API_KEYS`.

### 6.5 Publishing

```text
POST /opportunities/{id}/publish
  -> auth: reviewer or admin
  -> opportunity exists
  -> review_status == approved
  -> PUBLISHING_ENABLED == true
  -> channel is in PUBLISHING_ALLOWED_CHANNELS
  -> create/reuse Publication (one per opportunity+channel)
  -> if scheduled_at: status=scheduled, return
  -> status=sending (committed before network call)
  -> format message with source attribution
  -> send via Telegram
       ok     -> status=published, external_message_id, published_at
       failed -> status=failed, error, attempts++
  -> if the row was already published: return it, do not send again
  -> if the row is sending: refuse with publication_unknown
```

The message formatter appends `Source: <name>` and the source URL after truncating the body, so attribution cannot be cut off.

### 6.6 Analytics

Analytics are read-only SQL aggregates over the service's own data:

- overview: counts, status mix, type/category mix, budget ranges, approval rate, publication counts;
- source quality: per-source volume, approval rate, quality, risk, publication success, collector health;
- market: skill demand, budget ranges/median, remote share, category/type mix;
- trends: daily posts, opportunities, approvals, publications.

No marketplace financial table or API is touched.

---

## 6.7 Admin dashboard design standards

The dashboard (`app/static/admin/index.html`) is a single dependency-free page served at `/admin/`. It follows the mandatory visual standards in `rules.md` (sections 2, 3, and 4) and those rules are enforced by `tests/test_ui_compliance.py`.

| Standard | Implementation |
|---|---|
| Zero emoji | No emoji anywhere in the repository; enforced by a repository-wide scan test. |
| Indicators | Status is shown as **bold colored text only** (`.badge`, `.indicator`): green for approved/active/published/healthy, red for rejected/inactive/failed, amber for needs_review/sending/risk, blue for pending_review/scheduled/roles. No background pills, no borders, no dot pseudo-elements. |
| Theming | Dark and light themes driven by CSS custom properties (`--bg`, `--panel`, `--line`, `--text`, `--text-muted`, plus semantic tokens). Theme blocks only redefine color values; all geometry stays in the shared base rules. |
| Color discipline | No hardcoded hex or rgba in component styles; every color resolves to a theme token. |
| Single toggle | One theme toggle, in the primary header, persisted to `localStorage` and initialized from `prefers-color-scheme`. |
| Date filters | Date-range inputs are grouped in a flex row with a fixed 150px width each. |
| Responsive | Breakpoints at 640/1024px. KPI cards render two per row on phones, action buttons share one row, the viewport hides horizontal overflow, flex children can shrink (`min-width: 0`), and touch targets are at least 44x44px. |
| Session safety | The client enforces a 30-minute inactivity timeout that clears the stored key, and an HTTP 401 clears authentication state immediately. |

Every feature is reachable from the top navigation (Review queue, Ingest Post, Sources, Publications, Analytics).

## 7. Authentication and roles

Every `/api/v1/*` route requires a key, except the public `/`, `/health`, and static `/admin/` page.

```http
X-API-Key: <key>
```

or:

```http
Authorization: Bearer <key>
```

Keys are configured as JSON in `AUTH_API_KEYS`:

```env
AUTH_API_KEYS={"dev-admin-key-change-me-01":{"role":"admin","name":"dev-admin"},"dev-reviewer-key-change-me-01":{"role":"reviewer","name":"dev-reviewer"}}
```

Keys must be at least 16 characters. The auth lookup keeps only a SHA-256 fingerprint; comparisons use `hmac.compare_digest`. Raw keys are not copied into request state or logs.

| Role | Can do |
|---|---|
| `admin` | Everything, including source management. |
| `reviewer` | Read, review, edit opportunities, publish approved items. |
| `collector` | Ingest posts and read collection data. |
| `analyst` | Read opportunities, publications, and all analytics. |

`admin` passes every role check. The route matrix is documented in [API Reference](API_REFERENCE.md#authentication-and-roles).

Startup behavior:

- `APP_ENV != development` and `AUTH_API_KEYS` empty: the app refuses to start.
- `APP_ENV=development` and `AUTH_API_KEYS` empty: it starts with a warning, but every protected route returns `401`.
- `AUTH_ALLOW_ANONYMOUS_DEV=true` is a development-only escape hatch that treats anonymous callers as admins. Do not use it outside a local machine.
- `/docs`, `/redoc`, and `/openapi.json` are only mounted when `APP_ENV=development`.

---

## 8. Configuration reference

`app/config.py` uses `pydantic-settings` and reads `.env` (environment variables take priority). `DATABASE_URL` is required; there is no credential-bearing default.

### Core

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `Yekis Intelligence` | Service name in responses. |
| `APP_VERSION` | `0.3.0` | Exposed by `/` and docs. |
| `APP_ENV` | `development` | Enables dev-only schema creation and docs; controls startup auth validation. |
| `API_PREFIX` | `/api/v1` | Mount prefix for business routes. |
| `DATABASE_URL` | **required** | SQLAlchemy URL. Use `postgresql+psycopg://...` for the real stack. |
| `AUTO_CREATE_SCHEMA` | `true` | Development convenience; in controlled environments set `false` and run Alembic. |
| `LOG_LEVEL` | `INFO` | Root logging level. |

### Authentication / CORS

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_API_KEYS` | empty | JSON object of key -> `{role, name}`. Required outside development. |
| `AUTH_ALLOW_ANONYMOUS_DEV` | `false` | Dev-only anonymous admin. Never enable in a shared environment. |
| `CORS_ORIGINS` | empty | Comma-separated explicit origins. Wildcards are not used. |

### Telegram collection

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_ENABLED` | `false` | Gate for the collector CLI/task. |
| `TELEGRAM_API_ID` | unset | From `my.telegram.org`; environment only. |
| `TELEGRAM_API_HASH` | unset | From `my.telegram.org`; never commit or log. |
| `TELEGRAM_BOT_TOKEN` | unset | Bot token from BotFather, used for **publishing only**. Bots cannot read channel history. |
| `TELEGRAM_SESSION_PATH` | `var/telegram_user` | User session used by the **collector**; created with `login --as user`. |
| `TELEGRAM_BOT_SESSION_PATH` | `var/telegram_bot` | Bot session used by the publisher; created with `login --as bot`. |
| `TELEGRAM_PROXY_TYPE` | empty | `socks5` or `http`. Routes Telegram MTProto through a proxy (Cloudflare WARP or a Cloudflare Tunnel in front of a SOCKS5 endpoint). Empty connects directly. |
| `TELEGRAM_PROXY_HOST` | empty | Proxy host, e.g. `127.0.0.1` for a local WARP proxy. |
| `TELEGRAM_PROXY_PORT` | `0` | Proxy port, e.g. `40000` for WARP proxy mode. |
| `TELEGRAM_PROXY_RDNS` | `true` | Resolve DNS through the proxy (recommended for SOCKS5). |
| `TELEGRAM_FETCH_LIMIT` | `100` | Maximum messages per fetch. |

### Background processing

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6382/0` | Celery broker/backend. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks synchronously in tests/dev. |
| `CELERY_TASK_MAX_RETRIES` | `5` | Job attempt cap. |
| `CELERY_RETRY_BASE_SECONDS` | `10` | Exponential backoff base. |
| `CELERY_RETRY_MAX_SECONDS` | `3600` | Backoff ceiling. |
| `CELERY_TASK_TIME_LIMIT_SECONDS` | `300` | Worker task time limit. |
| `COLLECT_INTERVAL_SECONDS` | `900` | Beat interval for collection. |

### Extraction / LLM

| Variable | Default | Purpose |
|---|---|---|
| `EXTRACTION_PROVIDER` | `rules` | `rules`, `gemini`, or `llm`. |
| `GEMINI_API_KEY` | unset | Required when `EXTRACTION_PROVIDER=gemini`; from Google AI Studio. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model enabled for your AI Studio project. |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | Native Gemini API base. |
| `LLM_API_KEY` | unset | Required when `EXTRACTION_PROVIDER=llm`. Holds the NVIDIA API key for NIM. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL; use `https://integrate.api.nvidia.com/v1` for NVIDIA NIM. |
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI-compatible model name; use the exact NVIDIA model id. |
| `LLM_RESPONSE_FORMAT` | `json_object` | JSON mode. Set `none` for deployments that reject the parameter. |
| `LLM_EXTRA_BODY` | empty | Optional JSON object of provider-specific request fields, e.g. NVIDIA guided decoding. |
| `LLM_TIMEOUT_SECONDS` | `30` | Outbound request timeout for both providers. NVIDIA NIM measured ~105 s per extraction, so use `180`. |
| `LLM_MAX_ATTEMPTS` | `2` | Bounded provider-level retry for transient failures (timeout, `429`, `5xx`). Credential and schema failures are never retried. |
| `LLM_RETRY_WAIT_SECONDS` | `5` | Base wait between provider retries (multiplied by the attempt number). |
| `LLM_MAX_INPUT_CHARS` | `12000` | Input truncation limit for both providers. |
| `LLM_MAX_OUTPUT_TOKENS` | `800` | Response token cap for both providers. |

### Publishing

| Variable | Default | Purpose |
|---|---|---|
| `PUBLISHING_ENABLED` | `false` | Master switch; off by default. |
| `PUBLISHING_ALLOWED_CHANNELS` | empty | Comma-separated allowlist; publishing fails closed when empty. |
| `PUBLISHING_MAX_ATTEMPTS` | `3` | Attempt cap per publication. |

---

## 9. Running the service

### Local development

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env
# edit .env: DATABASE_URL, AUTH_API_KEYS, and any optional integrations
alembic upgrade head
uvicorn app.main:app --reload --port 8100
```

Open:

- `http://localhost:8100/health` (public)
- `http://localhost:8100/admin/` (static dashboard; enter an API key)
- `http://localhost:8100/docs` (development only)

`AUTO_CREATE_SCHEMA=true` can create tables in development, but the supported, repeatable path is Alembic. Use `AUTO_CREATE_SCHEMA=false` once you rely on migrations.

### Docker Compose

```bash
cp .env.example .env
# edit .env first
docker compose up --build api worker beat
```

Services:

| Service | Host port | Notes |
|---|---|---|
| `db` | `5440` | PostgreSQL 16; named volume `intelligence_postgres`. |
| `redis` | `127.0.0.1:6382` | Loopback-bound broker. |
| `api` | `8100` | FastAPI on container port 8000. |
| `worker` | — | Celery worker, concurrency 1. |
| `beat` | — | Celery beat scheduler. |

The API and worker images include `alembic.ini` and `migrations/`, so you can run migrations inside the container:

```bash
docker compose run --rm api alembic upgrade head
```

For controlled environments set `AUTO_CREATE_SCHEMA=false` and run migrations as an explicit deployment step.

### Minimal smoke test

```bash
# The key must exist in AUTH_API_KEYS.
export YI_ADMIN_KEY='dev-admin-key-change-me-01'

curl -s http://localhost:8100/health

curl -s -X POST http://localhost:8100/api/v1/sources \
  -H "X-API-Key: $YI_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test source","source_type":"manual","source_url":"https://t.me/example","category":"software","is_active":true}'

curl -s -X POST http://localhost:8100/api/v1/ingestion/manual \
  -H "X-API-Key: $YI_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"source_id":1,"external_id":"demo-001","original_text":"Looking for a React developer. Budget $300. Delivery in 2 weeks."}'

curl -s "http://localhost:8100/api/v1/opportunities?status=pending_review" \
  -H "X-API-Key: $YI_ADMIN_KEY"
```

---

## 10. Development workflow

### Definition of done

For every behavior change:

1. Add or update a test that fails without the change.
2. Run `pytest -q` and `ruff check app tests migrations`.
3. If models changed, add a migration and run `alembic check`.
4. Update the [Project Guide](PROJECT_GUIDE.md), [API Reference](API_REFERENCE.md), or [Operations Runbook](OPERATIONS.md) if behavior changed.
5. Do not introduce financial-boundary code.
6. Do not commit `.env`, `*.session*`, tokens, or credentials.

### Common commands

```bash
pytest -q
ruff check app tests migrations
ruff format --check app tests migrations     # if you use the formatter
alembic upgrade head
alembic downgrade -1
alembic check
docker compose config -q
```

### Test architecture

`tests/conftest.py` provides:

- an in-memory SQLite database per test;
- `Base.metadata.create_all` for the current models;
- an override of the `get_db` dependency;
- an admin-authenticated `client` and separate `anon_client`, `reviewer_client`, `collector_client`, and `analyst_client` fixtures;
- a reusable `source` fixture.

Tests deliberately avoid PostgreSQL, Redis, Docker, Telegram, and the network. Provider behavior is tested with fakes:

- `FakeTelegramClient` for collection;
- `FakePublisher` for publishing;
- `httpx.MockTransport` for the LLM adapter.

This keeps the suite fast and deterministic. It also means the suite does **not** prove live provider behavior; see the audit for the live-verification gaps.

### Adding an endpoint

1. Add a Pydantic request/response model in `app/schemas.py`.
2. Add a router in `app/routers/`, with an explicit `require_role(...)` dependency.
3. Put business logic in `app/services/` so the worker/collector can reuse it.
4. Add an API test using the appropriate client fixture.
5. Update `docs/API_REFERENCE.md` and, if needed, the README.

### Adding a migration

```bash
alembic revision -m "describe the change"
# edit migrations/versions/<revision>_<slug>.py
alembic upgrade head
alembic check
```

Keep migrations compatible with both PostgreSQL and the SQLite audit path where practical. Avoid PostgreSQL-only server defaults in migrations if you want `alembic check` to remain runnable on SQLite.

### Adding an extraction provider

1. Implement the `ExtractionProvider` protocol (`name`, `extract`) in `app/services/llm/`.
2. Validate output through a closed Pydantic model; reject unknown fields.
3. Return an `ExtractedOpportunity` or raise `ProviderError`.
4. Wire selection in `get_provider()`.
5. Add tests for a valid response, invalid response, injection isolation, and fallback.
6. Never let a provider result bypass the deterministic risk merge or human review.

### Adding a source type

`SourceCreate.source_type` currently allows `telegram|website|rss|manual`. A new collector should:

1. consume only active sources of its type;
2. use `ingest_post()` rather than writing tables directly;
3. persist a checkpoint and health fields;
4. never log content or credentials;
5. add a fake-client test and document the operational runbook.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 authentication_required` | Missing or invalid `X-API-Key`/Bearer key. | Add a key from `AUTH_API_KEYS`; check the key is at least 16 chars. |
| `403 insufficient_role` | Authenticated but wrong role. | Use an admin key or the role the route requires. |
| App exits at startup with `DATABASE_URL` | No `.env` and no environment variable. | Set `DATABASE_URL`; the setting is intentionally required. |
| App exits in non-development with `AUTH_API_KEYS is empty` | No service credentials configured. | Configure at least one key; this is deliberate. |
| `/docs` or `/openapi.json` returns 404 | `APP_ENV` is not `development`. | Expected. Use the API reference or run locally with `APP_ENV=development`. |
| `alembic` says no such file `alembic.ini` | Running from the wrong directory or an old image. | Run from the repo root; rebuild after the Dockerfile change. |
| `alembic check` reports diffs | Model and migration drifted. | Add a migration and re-run. |
| Worker does nothing | Redis or worker/beat not running. | Start `worker` and `beat`; check `REDIS_URL`. |
| Collector skips a source | Source inactive, not `telegram`, in backoff, or not configured. | Check `is_active`, `source_type`, `next_attempt_at`, `last_error`. |
| Collector fails at login | Missing/invalid Telegram credentials or unauthorized session. | Set `TELEGRAM_API_ID`/`HASH`, run `python -m app.integrations.telegram.login --as user` once. |
| Collector says "Bots cannot read channel history" | The session was created with a bot token. | Re-authenticate as a user: `python -m app.integrations.telegram.login --as user`. |
| Collector returns `[]` | No active `telegram` source exists. | Register a source with `source_type=telegram` and a public channel URL. |
| Publishing returns `publishing_disabled` | `PUBLISHING_ENABLED=false`. | Enable it only for an approved test channel. |
| Publishing returns `channel_not_allowed` | Destination missing from the allowlist. | Add the channel to `PUBLISHING_ALLOWED_CHANNELS`. |
| Publishing returns `publication_unknown` | A previous attempt is still in `sending`. | Check the channel manually; do not blindly resend. |
| Publishing returns `publication_exhausted` | Attempt cap reached. | Inspect `publications.error`; fix the cause; create a new publication flow if needed. |
| LLM extraction never runs | `EXTRACTION_PROVIDER` is `rules`, or the key is missing. | Set `gemini` + `GEMINI_API_KEY`, or `llm` + `LLM_API_KEY`; check `extraction_method`. |
| LLM output looks like rules | Provider failed, was rate-limited, was safety-blocked, or returned invalid JSON/schema. | Check logs for `LLM extraction failed`; the fallback is intentional. |
| LLM output looks like rules | Provider failed; fallback is active. | Check logs for `LLM extraction failed`; inspect the provider response/schema. |
| `/health` returns 503 | Database probe failed. | Check PostgreSQL availability and `DATABASE_URL`. |
| Duplicate ingestion returns 409 | `(source_id, external_id)` already exists. | Expected; reuse the existing opportunity or use a correct external id. |

---

## 12. Known limitations and next steps

These are explicit, not hidden:

- **Live integrations are not yet verified in this environment.** Telegram collection/publishing, Redis/Celery, PostgreSQL, and live Gemini/OpenAI calls were tested with fakes or SQLite. The Gemini adapter is covered by a mock HTTP transport; the audit records each gap.
- **Telegram account model:** bots cannot read channel history (`GetHistoryRequest` is restricted for bot accounts) and cannot enumerate dialogs (`getDialogs` is restricted too). Collection requires a user session; a bot token is used only for publishing.
- **Collection cost:** extraction runs synchronously per message, so provider latency dominates bulk collection. At the measured NVIDIA latency of ~105 s per extraction, a 100-message backfill takes about three hours.
- **Static API keys.** No rotation UI or per-key revocation workflow. Rotate by editing `AUTH_API_KEYS` and restarting.
- **No rate limiting or request-body cap yet.** Keep the service on a trusted network until these land.
- **No CI workflow committed.** Run the local quality gates before merging; the test suite is fast enough.
- **Cross-source/fuzzy duplicate detection is absent.** Dedupe is exact on `(source_id, external_id)`.
- **Skill vocabulary is not normalized across manual edits.** Analytics counts what is stored.
- **Analytics aggregate in process.** Acceptable at the current scale, not a warehouse design.
- **`sending` publications require manual reconciliation.** This is a deliberate at-most-once trade-off.
- **`AUTO_CREATE_SCHEMA` can mask migration drift in development.** Prefer Alembic for anything shared.
- **The dashboard is a lightweight internal tool.** It stores the key in local storage and uses `prompt()` for the publish channel; it is not a full product frontend.

---

## 13. Glossary

| Term | Meaning |
|---|---|
| Source | A registered channel/site that may be collected. |
| Collected post | The original text and URL, preserved verbatim. |
| Opportunity | A structured candidate extracted from one collected post. |
| Review event | An append-only record of a status change or field edit. |
| Publication | The per-opportunity, per-channel publishing record. |
| Extraction method | `rules`, `llm`, or `rules_fallback`. |
| Risk flags | Deterministic signals such as upfront payment or urgency. |
| Idempotency key | A unique key that prevents duplicate job execution. |
| Dead letter | A job that exhausted its retries and needs human attention. |
| Allowlist | The explicit set of channels the publisher may use. |
| Attribution | The source name and URL appended to every published message. |
