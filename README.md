# Yekis Intelligence

Independent intelligence service for the Yekis marketplace: FastAPI, PostgreSQL, source management, Telegram collection, background processing, rule/LLM extraction, an audit-trailed review queue, approval-gated publishing, analytics, and an authenticated admin dashboard.

## Documentation

- [Documentation index](docs/README.md) — start here for the reading order.
- [Project guide](docs/PROJECT_GUIDE.md) — architecture, data model, workflows, configuration, development, and troubleshooting.
- [API reference](docs/API_REFERENCE.md) — every route, role, request/response shape, transition, and error code.
- [Operations runbook](docs/OPERATIONS.md) — local/Docker setup, migrations, collector/publisher runbooks, monitoring, backup, and incident playbooks.
- [Milestone execution audit](docs/audit/MILESTONE_EXECUTION.md) — what was implemented, the tests that prove it, and live-verification gaps.

## Architecture

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL
- Redis + Celery for background collection, publishing schedules, and retries
- Telethon for authorized-channel collection; publishing is approval-gated and disabled by default
- API-key service-to-service authentication with admin/reviewer/collector/analyst roles
- Review decisions and field edits are append-only audit events
- The service has no access to marketplace escrow, wallet, payout, or contract operations

## Quick start

1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build api worker beat` (or `docker compose up --build` for just the API and datastores).
3. Check `GET /health` (public).
4. Open `http://localhost:8100/admin/` and connect with an API key from `AUTH_API_KEYS`.
5. Create a source with `POST /api/v1/sources` (admin key).
6. Ingest a sample post with `POST /api/v1/ingestion/manual` (collector or admin key).
7. Review it at `GET /api/v1/opportunities` and `PATCH /api/v1/opportunities/{id}/review` (reviewer or admin key).

All `/api/v1/*` routes require `X-API-Key: <key>` (or `Authorization: Bearer <key>`). In development, `http://localhost:8100/docs` remains available; outside development the docs and OpenAPI routes are disabled. The service refuses to start outside development when `AUTH_API_KEYS` is empty.

For local development, `AUTO_CREATE_SCHEMA=true` creates tables on startup. For controlled environments, set it to `false` and run `alembic upgrade head` before starting the API.

## Example source

```json
{
  "name": "Yekis test source",
  "source_type": "telegram",
  "source_url": "https://t.me/example_jobs",
  "category": "software",
  "is_active": true
}
```

## Example ingestion

```json
{
  "source_id": 1,
  "external_id": "demo-001",
  "original_text": "Looking for a React developer to build a dashboard. Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks.",
  "original_url": "https://t.me/example_jobs/1"
}
```

## Local development

```bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8100
```

For local development outside Docker, set `DATABASE_URL` to your PostgreSQL instance.

The full route list, roles, and error codes are in the [API Reference](docs/API_REFERENCE.md). Setup, migration, and incident procedures are in the [Operations Runbook](docs/OPERATIONS.md).

## API endpoints

- `GET /health` (public)
- `GET /admin/` (static dashboard; data calls need a key)
- `POST /api/v1/sources` (admin)
- `GET /api/v1/sources` (any role; `limit`/`offset`)
- `GET /api/v1/sources/{id}` (any role)
- `PATCH /api/v1/sources/{id}` (admin)
- `PATCH /api/v1/sources/{id}/status` (admin, JSON body `{"is_active": false}`)
- `POST /api/v1/ingestion/manual` (collector or admin)
- `GET /api/v1/ingestion/posts` (collector/reviewer/analyst; `source_id`, `limit`, `offset`)
- `GET /api/v1/opportunities` (any role; `status`, `risk_only`, `limit`, `offset`)
- `GET /api/v1/opportunities/{id}` (any role)
- `PATCH /api/v1/opportunities/{id}/review` (reviewer or admin)
- `PATCH /api/v1/opportunities/{id}` (reviewer or admin; records an audit event)
- `GET /api/v1/opportunities/{id}/events` (reviewer/admin/analyst)
- `POST /api/v1/opportunities/{id}/publish` (reviewer or admin; immediate or scheduled)
- `GET /api/v1/publications` and `GET /api/v1/publications/{id}` (reviewer/admin/analyst)
- `GET /api/v1/analytics/overview`, `/sources`, `/market`, `/trends` (analyst/reviewer/admin)

## Authentication and review audit

`AUTH_API_KEYS` is a JSON object of key → `{role, name}`. Use a distinct key per service and keep reviewer actions attributable:

```env
AUTH_API_KEYS={"dev-admin-key-change-me-01":{"role":"admin","name":"dev-admin"},"dev-reviewer-key-change-me-01":{"role":"reviewer","name":"dev-reviewer"}}
AUTH_ALLOW_ANONYMOUS_DEV=false
```

Roles: `admin` (full access), `reviewer` (review/edit/approve), `collector` (ingest/read), `analyst` (read/analytics). Keys are compared by SHA-256 fingerprint with a constant-time comparison and are never logged. Review decisions are append-only: every status change and field edit writes a `review_events` row with the actor, previous status, new status, note, and field diff.

## Telegram collector

The collector only reads channels already registered as active `telegram` sources. It never joins a channel, never reads private chats, and stores no message text in logs. Checkpoints and source health are persisted on `sources`, so two consecutive runs cannot ingest the same message twice.

```bash
# One-time interactive login (creates the git-ignored session file)
.venv/Scripts/python -m app.integrations.telegram.login

# Collect every active Telegram source
.venv/Scripts/python -m app.integrations.telegram.collector

# Or restrict to one source
.venv/Scripts/python -m app.integrations.telegram.collector --source-id 1 --limit 50
```

Required environment: `TELEGRAM_ENABLED=true`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_SESSION_PATH` (defaults to `var/telegram_collector`, which is git-ignored). A failed run increments `consecutive_failures` and sets an exponential `next_attempt_at`; a subsequent run inside that window skips the source instead of hammering Telegram.

## LLM extraction

Extraction is behind a provider interface. The deterministic rules provider is the default and needs no credentials. The LLM provider is OpenAI-compatible, opt-in, and validated by a closed Pydantic schema (`extra="forbid"`): unknown fields, mismatched currency/amount, or out-of-range values are rejected.

```env
EXTRACTION_PROVIDER=llm
LLM_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

Collected text is treated as untrusted data inside an explicit `<untrusted_content>` delimiter. A provider timeout, HTTP failure, malformed JSON, or schema violation falls back to the rules extractor (`extraction_method=rules_fallback`) instead of losing the post. Deterministic risk flags are always merged into LLM output, and an upfront-payment signal suppresses any extracted budget. LLM output never auto-approves anything.

## Background processing

The Celery worker and beat scheduler use Redis as broker and backend. Durable job state, attempt counts, and dead letters live in PostgreSQL (`job_executions`, `dead_letter_jobs`), so a lost broker message cannot lose an accepted job.

```bash
# In Docker Compose
docker compose up --build api worker beat

# Locally
celery -A app.worker.celery_app:celery_app worker --loglevel=INFO --concurrency=1
celery -A app.worker.celery_app:celery_app beat --loglevel=INFO
```

Retries are bounded by `CELERY_TASK_MAX_RETRIES` with exponential backoff and jitter. A task that exhausts its retries is written to `dead_letter_jobs` and is not retried again for the same idempotency key. Ingestion replays are idempotent: the same `(source_id, external_id)` produces one row, and a repeated job returns the original result.

## Publishing

Publishing is off by default (`PUBLISHING_ENABLED=false`) and fails closed when `PUBLISHING_ALLOWED_CHANNELS` is empty. A message can be created only for an opportunity whose review status is `approved`; the approval and allowlist are re-checked at send time, so rejecting a scheduled item cancels it.

```bash
# Immediate publish (reviewer or admin key)
curl -X POST http://localhost:8100/api/v1/opportunities/1/publish \
  -H "X-API-Key: $YI_REVIEWER_KEY" -H "Content-Type: application/json" \
  -d '{"channel":"@ops_test_channel"}'

# Schedule for later (a beat task publishes due rows every minute)
curl -X POST http://localhost:8100/api/v1/opportunities/1/publish \
  -H "X-API-Key: $YI_REVIEWER_KEY" -H "Content-Type: application/json" \
  -d '{"channel":"@ops_test_channel","scheduled_at":"2026-11-11T09:00:00Z"}'
```

A `publications` row is written before the network call. A row left in `sending` after a crash is **not** automatically resent (it may already be public); the API returns `publication_unknown` so a human can reconcile it. Replaying a successful publish for the same `(opportunity, channel)` returns the original row and sends nothing. Every formatted message ends with the source name and URL; attribution is appended after truncation so it can never be cut off.

## Analytics

Read-only analytics are available to `analyst`, `reviewer`, and `admin` roles:

- `GET /api/v1/analytics/overview` — source/post/opportunity totals, review status mix, risk count, approval rate, publication counts, last collection time.
- `GET /api/v1/analytics/sources` — per-source volume, approval rate, average quality, risk flags, publication success/failure, and collector health.
- `GET /api/v1/analytics/market` — skill demand, budget ranges by currency, remote share, category and opportunity-type mix.
- `GET /api/v1/analytics/trends?days=30` — daily posts, opportunities, approvals, and publications.

These aggregates read only collected content and publication records. They never touch marketplace escrow, wallet, payout, or contract data.

## Next milestones

Status is tracked with evidence in [`docs/audit/MILESTONE_EXECUTION.md`](docs/audit/MILESTONE_EXECUTION.md).

1. ✅ Telegram authorized-channel collector using Telethon.
2. ✅ Redis/Celery processing pipeline and retry policy.
3. ✅ LLM extraction behind a provider interface with strict Pydantic validation.
4. ✅ Admin dashboard integration and authenticated service-to-service access.
5. ✅ Telegram publisher with explicit approval and attribution.
6. ✅ Analytics, source quality, and market insights.
