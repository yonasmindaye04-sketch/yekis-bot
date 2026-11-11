# Yekis Intelligence — Operations Runbook

This is the practical guide for running, deploying, monitoring, and recovering the service. It assumes you have read the [Project Guide](PROJECT_GUIDE.md) and understand the [API contract](API_REFERENCE.md).

---

## 1. Prerequisites

| Dependency | Version / notes |
|---|---|
| Python | 3.12 target (`requires-python >= 3.12`); the current host used for verification was 3.15 |
| PostgreSQL | 16 (compose uses `postgres:16-alpine`) |
| Redis | 7 (compose uses `redis:7-alpine`) |
| Docker / Compose | Docker 29 + Compose v5 were used during implementation |
| Telegram | An authorized Telethon session for collection/publishing |
| LLM (optional) | A Google Gemini (AI Studio) key or an OpenAI-compatible endpoint and key |

---

## 2. Environment bootstrap

```bash
cp .env.example .env
```

At minimum, set:

```env
DATABASE_URL=postgresql+psycopg://yekis_intel:yekis_intel_dev@localhost:5440/yekis_intelligence

# Generate long random keys; the example values are development-only.
AUTH_API_KEYS={"<admin-key>":{"role":"admin","name":"ops-admin"},"<reviewer-key>":{"role":"reviewer","name":"ops-reviewer"},"<collector-key>":{"role":"collector","name":"collector"},"<analyst-key>":{"role":"analyst","name":"analyst"}}
AUTH_ALLOW_ANONYMOUS_DEV=false

# Required for live collection and publishing.
TELEGRAM_ENABLED=false
# TELEGRAM_API_ID=123456
# TELEGRAM_API_HASH=...
TELEGRAM_SESSION_PATH=var/telegram_collector

# Keep publishing off until you have a private test channel.
PUBLISHING_ENABLED=false
PUBLISHING_ALLOWED_CHANNELS=
```

### Gemini extraction (optional)

Yekis Intelligence supports Google Gemini directly through the AI Studio key:

```env
EXTRACTION_PROVIDER=gemini
GEMINI_API_KEY=<your AI Studio key>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

- Get the key from Google AI Studio and store it only in `.env` or a secret manager.
- `GEMINI_MODEL` must be a model enabled for that AI Studio project.
- The key is sent in the `x-goog-api-key` header; it is never placed in a URL or log.
- If the model is unavailable, rate-limited, blocked by safety, or returns invalid JSON/schema, extraction falls back to rules and the opportunity stores `extraction_method=rules_fallback`.
- Use `EXTRACTION_PROVIDER=llm` plus `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL` only if you are using an OpenAI-compatible endpoint instead.

Shared limits: `LLM_TIMEOUT_SECONDS`, `LLM_MAX_INPUT_CHARS`, and `LLM_MAX_OUTPUT_TOKENS` apply to both providers.

Rules:

- Never commit `.env`, `*.session*`, keys, or tokens.
- `AUTH_API_KEYS` must be valid JSON. Keys must be at least 16 characters.
- Outside `APP_ENV=development`, the app refuses to start with an empty `AUTH_API_KEYS`.
- `AUTH_ALLOW_ANONYMOUS_DEV=true` is local-only; do not use it on a shared host.

---

## 3. Local development

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8100
```

Open:

- `http://localhost:8100/health`
- `http://localhost:8100/admin/`
- `http://localhost:8100/docs` (development only)

`AUTO_CREATE_SCHEMA=true` can create tables on startup in development. The repeatable path is Alembic; set `AUTO_CREATE_SCHEMA=false` for anything shared.

---

## 4. Docker Compose

```bash
cp .env.example .env
# edit .env first
docker compose up --build api worker beat
```

| Service | Host port | Purpose |
|---|---|---|
| `db` | `5440` | PostgreSQL 16, named volume `intelligence_postgres`. |
| `redis` | `127.0.0.1:6382` | Redis broker/backend, loopback-bound. |
| `api` | `8100` | FastAPI (container port 8000). |
| `worker` | — | Celery worker, concurrency 1. |
| `beat` | — | Celery beat scheduler. |

The image now contains `alembic.ini` and `migrations/`, so migrations can run in the API image:

```bash
# One-off migration against the compose database
docker compose run --rm api alembic upgrade head

# Check model/migration parity
docker compose run --rm api alembic check

# Open a shell
docker compose run --rm api python -c "import app.main; print('ok')"
```

For controlled environments:

1. Set `AUTO_CREATE_SCHEMA=false`.
2. Run `alembic upgrade head` as a deployment step.
3. Keep one API replica while migrations are part of startup, or run migrations as a separate job.

Logs:

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f beat
```

Stop without deleting data:

```bash
docker compose down
```

Delete the database volume (destructive, local only):

```bash
docker compose down -v
```

---

## 5. Database migrations

```bash
alembic upgrade head
alembic downgrade -1
alembic check
```

Current revision chain:

```text
0001_initial
  0002_telegram_collector
    0003_job_pipeline
      0004_review_audit
        0005_publications
```

Rules:

- Add a migration for every model change.
- Run `alembic check` before merging; it must report `No new upgrade operations detected.`
- Test both upgrade and downgrade on a scratch database.
- The audit path uses SQLite; the real stack uses PostgreSQL. Re-run migrations against PostgreSQL before a release.

### Repairing a partially created database

If `alembic upgrade head` fails with `DuplicateTable` and `alembic current` is `0002_telegram_collector`, the database already has the 0003-0005 tables but is missing the 0004 `opportunities` columns. Do not drop data. Run the one-off, idempotent repair:

```powershell
python scripts/repair_partial_schema.py
alembic current
alembic check
```

Expected:

```text
0005_publications (head)
No new upgrade operations detected.
```

The repair script refuses to run unless the 0003-0005 tables exist and the recorded revision is `0002_telegram_collector`.

---

## 6. API service runbook

### Start

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Health

```bash
curl -s http://localhost:8100/health
```

- `200` and `database: connected`: ready.
- `503` and `database: unavailable`: the DB probe failed; check PostgreSQL and `DATABASE_URL`.

### Keys and roles

```bash
export YI_ADMIN_KEY='...'
curl -s http://localhost:8100/api/v1/sources -H "X-API-Key: $YI_ADMIN_KEY"
```

- `401`: missing/invalid key.
- `403`: wrong role for the route.
- The dashboard uses the same header; connect it with an admin or reviewer key.

### Docs

`/docs` and `/openapi.json` are only available when `APP_ENV=development`. This is deliberate.

---

## 7. Collector runbook

### One-time registration

1. Create a source as an admin:

```bash
curl -s -X POST http://localhost:8100/api/v1/sources \
  -H "X-API-Key: $YI_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Approved channel","source_type":"telegram","source_url":"https://t.me/approved_channel","category":"software","is_active":true}'
```

Only public channel references (`t.me/name`, `@name`) are accepted. Invite links (`t.me/+...`) are rejected.

2. Set the Telegram credentials in `.env`:

```env
TELEGRAM_ENABLED=true
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_PATH=var/telegram_collector
```

3. Authorize the session once:

```bash
# User account — REQUIRED for reading channel history (the collector)
python -m app.integrations.telegram.login --as user

# Bot — optional, used for publishing
python -m app.integrations.telegram.login --as bot
```

**Telegram account model (important):** bots cannot read channel history. The API rejects `GetHistoryRequest` for bot accounts with `BotMethodInvalidError`. Therefore:

| Purpose | Account type | Session path |
|---|---|---|
| Collect messages | **User** (phone + code + optional 2FA) | `TELEGRAM_SESSION_PATH` (`var/telegram_user`) |
| Publish messages | Bot (token from BotFather) or user | `TELEGRAM_BOT_SESSION_PATH` (`var/telegram_bot`) |

A bot must already be a member/admin of the channel it publishes to. The session files are credentials: git-ignored, never copied into an image or a log.

4. Run a collection:

```bash
python -m app.integrations.telegram.collector
# or one source:
python -m app.integrations.telegram.collector --source-id 1 --limit 50
```

Or run the worker + beat and let the `collect-telegram` schedule call it.

### Check source health

```bash
curl -s http://localhost:8100/api/v1/sources -H "X-API-Key: $YI_ADMIN_KEY"
```

Look at:

- `last_collected_message_id` — checkpoint; should advance.
- `last_success_at` — last successful run.
- `next_attempt_at` — backoff window after failures.
- `consecutive_failures` — reset on success.
- `last_error` — bounded error text.

### Collector failure modes

| Symptom | Action |
|---|---|
| `skipped_reason=backoff` | Wait for `next_attempt_at`; investigate the previous error first. |
| `skipped_reason=source_inactive` | Re-activate the source with `PATCH /sources/{id}/status`. |
| `skipped_reason=not_a_telegram_source` | Source type must be `telegram`. |
| `TelegramNotConfigured` | Set `TELEGRAM_ENABLED`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`. |
| "Bots cannot read channel history" | The session was created with a bot token. | Log in with a user account: `python -m app.integrations.telegram.login --as user`. |
| Collector output is `[]` | There is no active `telegram` source. | Register one with `source_type=telegram` and a public channel URL. |
| Session not authorized | Run the login command once; keep the session file safe. |
| Repeated `last_error` | Check channel access, account status, and network; the checkpoint does not advance on failure. |

---

## 8. Worker and beat runbook

### Start locally

```bash
celery -A app.worker.celery_app:celery_app worker --loglevel=INFO --concurrency=1
celery -A app.worker.celery_app:celery_app beat --loglevel=INFO
```

### What runs

| Task | Trigger | Purpose |
|---|---|---|
| `yekis.collect_telegram` | beat every `COLLECT_INTERVAL_SECONDS` or manual | Collect active Telegram sources. |
| `yekis.publish_due` | beat every 60 seconds | Publish scheduled/due publications. |
| `yekis.ingest_post` | programmatic/worker use | Idempotent ingestion wrapper. |

### Inspect jobs

There is no jobs HTTP endpoint yet; use SQL:

```sql
-- Recent jobs
SELECT id, task_name, status, attempts, last_error, created_at, finished_at
FROM job_executions
ORDER BY id DESC
LIMIT 50;

-- Dead letters needing attention
SELECT d.id, d.task_name, d.idempotency_key, d.attempts, d.error, d.created_at
FROM dead_letter_jobs d
ORDER BY d.id DESC;
```

A dead-lettered job is not automatically retried. Fix the cause, then create a new job/key or requeue deliberately. Do not edit `dead_letter_jobs` as a retry mechanism; it is the evidence trail.

### Worker failure behavior

- Late acknowledgements and `task_reject_on_worker_lost` are enabled.
- Prefetch is 1 to avoid large in-memory batches.
- A running job blocks the same idempotency key. If a worker dies mid-job, the row can remain `running`; there is no stale-lock reaper yet. Inspect and reset it deliberately after confirming the work did not complete.

### Redis

Redis is a broker/result backend, not the source of truth. If Redis is lost:

- queued messages are lost;
- durable job rows and collected data remain in PostgreSQL;
- restart Redis and the worker/beat; safe work is re-driven by the schedule or by manual task submission.

Check Redis:

```bash
docker compose exec redis redis-cli ping
```

---

## 9. Publisher runbook

Publishing is off by default. Do not enable it against a real channel during development.

### Enable for a private test channel

```env
PUBLISHING_ENABLED=true
PUBLISHING_ALLOWED_CHANNELS=@your_private_test_channel
PUBLISHING_MAX_ATTEMPTS=3
```

Restart the API and worker/beat after changing environment values.

### Publish flow

1. Ingest and review an opportunity.
2. Approve it:

```bash
curl -s -X PATCH http://localhost:8100/api/v1/opportunities/1/review \
  -H "X-API-Key: $YI_REVIEWER_KEY" -H 'Content-Type: application/json' \
  -d '{"decision":"approved","reviewer_note":"Source verified"}'
```

3. Publish immediately:

```bash
curl -s -X POST http://localhost:8100/api/v1/opportunities/1/publish \
  -H "X-API-Key: $YI_REVIEWER_KEY" -H 'Content-Type: application/json' \
  -d '{"channel":"@your_private_test_channel"}'
```

4. Or schedule:

```bash
curl -s -X POST http://localhost:8100/api/v1/opportunities/1/publish \
  -H "X-API-Key: $YI_REVIEWER_KEY" -H 'Content-Type: application/json' \
  -d '{"channel":"@your_private_test_channel","scheduled_at":"2026-11-11T09:00:00Z"}'
```

5. Inspect publications:

```bash
curl -s http://localhost:8100/api/v1/publications -H "X-API-Key: $YI_REVIEWER_KEY"
```

### Publication states

| State | Action |
|---|---|
| `scheduled` | Wait for the beat task or publish immediately. |
| `pending` | The immediate publish did not complete; inspect/re-drive. |
| `sending` | **Do not blindly resend.** Check the channel; it may already be posted. |
| `published` | Replays return this row and send nothing. |
| `failed` | Read `error`; fix the cause; retry until `attempts` reaches the cap. |

### Recovering a `sending` row

This is a manual, high-care operation because Telegram has no idempotency key:

1. Check the destination channel for the message.
2. If it is present, mark the publication as published with a backup and the real message id:

```sql
BEGIN;
UPDATE publications
SET status = 'published',
    published_at = now(),
    external_message_id = '<message-id>',
    error = NULL,
    updated_at = now()
WHERE id = <publication-id>;
COMMIT;
```

3. If it is definitely absent, mark it `failed` with an explanatory error, then retry through the API if attempts remain:

```sql
BEGIN;
UPDATE publications
SET status = 'failed',
    error = 'manually verified: message not present in channel',
    updated_at = now()
WHERE id = <publication-id>;
COMMIT;
```

Back up the table before manual SQL. Never mark a row `published` without confirming the message exists.

### Disabling publishing quickly

```env
PUBLISHING_ENABLED=false
```

Restart the API and worker/beat. The API will return `409 publishing_disabled`; the beat task will skip.

---

## 10. Monitoring

### Health and availability

| Check | How |
|---|---|
| API process | `GET /health` (503 on DB failure) |
| PostgreSQL | container healthcheck or `pg_isready` |
| Redis | `redis-cli ping` |
| Worker | `celery -A app.worker.celery_app:celery_app inspect ping` |
| Beat | process/log presence; one beat instance only |

### Metrics worth watching

- collected posts per hour and `last_success_at` per source;
- `consecutive_failures` and `last_error` per source;
- `job_executions` failures and `dead_letter_jobs` count;
- publication `failed` count and `sending` age;
- `needs_review` and `pending_review` queue depth;
- API 401/403 rates and 5xx rates;
- database connections and disk usage.

### Useful SQL

```sql
-- Review queue depth
SELECT review_status, count(*) FROM opportunities GROUP BY review_status;

-- Sources in backoff
SELECT id, name, consecutive_failures, next_attempt_at, last_error
FROM sources
WHERE next_attempt_at IS NOT NULL AND next_attempt_at > now()
ORDER BY next_attempt_at;

-- Failed publications
SELECT id, opportunity_id, channel, attempts, error, updated_at
FROM publications
WHERE status = 'failed'
ORDER BY id DESC;

-- Stuck sending publications
SELECT id, opportunity_id, channel, updated_at
FROM publications
WHERE status = 'sending'
ORDER BY updated_at;
```

---

## 11. Backup and restore

The only durable state is PostgreSQL. Redis is a broker; the session file is a credential, not a backup target.

### Backup

```bash
docker compose exec -T db pg_dump -U yekis_intel -d yekis_intelligence > yekis_intelligence_$(date +%Y%m%d_%H%M%S).sql
```

Store backups encrypted and access-controlled. They contain collected content, reviewer identities, and publication records.

### Restore (into a separate database first)

```bash
# Create a scratch database and restore there before touching production.
docker compose exec -T db createdb -U yekis_intel yekis_intelligence_restore
docker compose exec -T db psql -U yekis_intel -d yekis_intelligence_restore < backup.sql
```

Validate counts and the Alembic revision, then switch over deliberately.

### Bot session files

Back up the Telegram session only if you have a secure secret store. If a session is lost, re-run the login command; if it is leaked, revoke the session from Telegram immediately.

---

## 12. Security checklist

Before exposing the service to any shared network:

- [ ] `APP_ENV` is not `development`.
- [ ] `AUTH_API_KEYS` has strong, distinct keys per role, and the dev example keys are removed.
- [ ] `AUTH_ALLOW_ANONYMOUS_DEV=false`.
- [ ] `/docs` and `/openapi.json` are disabled (automatic outside development).
- [ ] TLS terminates at a trusted reverse proxy; the app itself does not terminate TLS.
- [ ] PostgreSQL and Redis are not publicly exposed.
- [ ] `PUBLISHING_ENABLED=false` unless an approved private channel is configured.
- [ ] `PUBLISHING_ALLOWED_CHANNELS` contains only intended channels.
- [ ] `CORS_ORIGINS` is explicit if a browser frontend is hosted elsewhere.
- [ ] `.env`, `*.session*`, tokens, and session strings are absent from the image and VCS.
- [ ] Database backups are encrypted and access-controlled.
- [ ] Rate limiting and request-body caps are provided by the proxy until application-level limits land.
- [ ] Logs are aggregated and searched for credentials and message bodies (the code does not log them).

Known security gaps in the current code:

- static long-lived API keys with manual rotation;
- no application-level rate limiting or body-size cap;
- no key-management UI;
- no stale-lock reaper for `running` jobs;
- the dashboard stores the key in `localStorage` (same-origin internal tool).

---

## 13. Incident playbooks

### API returns 503 on `/health`

1. Check PostgreSQL: `docker compose ps db`, `pg_isready`.
2. Check `DATABASE_URL` and credentials.
3. Check DB disk space and connection saturation.
4. Restore DB service; `/health` should return 200.
5. If the DB was restored from backup, re-run `alembic upgrade head`.

### Redis is down / worker is idle

1. `docker compose ps redis`; `redis-cli ping`.
2. Restart Redis, then worker and beat.
3. Durable job rows remain in PostgreSQL; dead-lettered or `failed` jobs can be inspected.
4. Re-drive collection/publishing intentionally; do not assume queued messages survived.

### Duplicate posts or repeated ingestion

- `(source_id, external_id)` is the dedupe key. A duplicate returns `409` and does not create a row.
- If a source emits the same text under different message ids, that is **not** deduplicated. Decide whether to fix the collector mapping or add a cross-source dedupe feature; do not delete data casually.
- The collector's checkpoint prevents re-reading the same message on normal runs.

### Suspicious or scam content

1. Risk flags route the opportunity to `needs_review`; they do not reject it.
2. Inspect the original post via `GET /api/v1/ingestion/posts`.
3. Reject the opportunity with a note; the audit event records the actor.
4. Do not publish a flagged item unless a human has explicitly approved it after review.
5. If a source is consistently malicious, deactivate it (`PATCH /sources/{id}/status`) and keep the records for audit.

### Publication failed

1. Read the publication row (`GET /api/v1/publications/{id}`).
2. Check `error` and `attempts`.
3. Check the channel allowlist and Telegram session.
4. Fix the cause; retry while attempts remain.
5. If the row is `sending`, follow the reconciliation procedure above; never blind-resend.

### Leaked API key

1. Remove the key from `AUTH_API_KEYS`.
2. Restart the API (and any process that caches settings).
3. Review `review_events` and `publications.created_by` for actions by that identity.
4. Rotate related keys and invalidate the Telegram session if it may be exposed.

### Worker backlog

1. Check `celery -A app.worker.celery_app:celery_app inspect active`.
2. Check `job_executions` and `dead_letter_jobs`.
3. Scale worker replicas only after confirming jobs are safe to run concurrently; the job runner prevents duplicate execution per idempotency key, but collection sources share checkpoints.
4. Keep a single beat instance.

### Accidental publication

1. Disable publishing immediately (`PUBLISHING_ENABLED=false`) and restart.
2. Delete or edit the message in Telegram manually; the service cannot unsend it.
3. Record the incident; preserve the publication row and review events.
4. Do not delete rows from the database; they are the audit evidence.

---

## 14. Deployment checklist

### Before deploy

- [ ] `pytest -q` passes.
- [ ] `ruff check app tests migrations` passes.
- [ ] `alembic check` is clean.
- [ ] `docker compose config -q` passes.
- [ ] New migrations are tested against PostgreSQL, both upgrade and downgrade.
- [ ] `.env` has production keys and no dev/example credentials.
- [ ] `PUBLISHING_ENABLED` is correct for the environment.
- [ ] Backups are current.

### Deploy

1. Run `alembic upgrade head` as a separate step.
2. Deploy the API with `AUTO_CREATE_SCHEMA=false`.
3. Deploy one worker and one beat; verify `/health`.
4. Verify a read endpoint with a key, then the dashboard.
5. Verify collection on a test source; verify a scheduled publication on a private test channel only.

### After deploy

- Watch `/health`, API 5xx, worker logs, dead letters, and publication failures.
- Confirm `last_success_at` advances for active sources.
- Confirm no secret appeared in logs.
- Keep the previous image available for rollback; database rollback is separate and must be planned.

---

## 15. Known operational gaps

- No CI pipeline is committed; run the quality gates locally.
- No jobs/publications admin UI for dead-letter requeue or manual publication reconciliation.
- No stale-lock reaper for `running` job rows.
- No automatic key rotation or secret manager integration.
- No application-level rate limiting or request-body caps.
- No alerting integration; use your platform's log/metric alerts on the queries above.
- Analytics is in-process SQL; revisit if data volume grows.
