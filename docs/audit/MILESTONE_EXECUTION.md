# Milestone Execution Audit — Yekis Intelligence

**Started:** 2026-11-10
**Baseline:** `audit/initial-baseline` (`b46063e`)
**Rule:** every claim below is backed by a command that was actually executed. Live Telegram verification is called out separately and is never simulated.

This document is append-only. Each README "Next milestones" item gets a section with scope, files, evidence, verdict, and remaining risk.

---

## M1 — Telegram authorized-channel collector (Telethon)

**Status:** IMPLEMENTED AND VERIFIED WITH A FAKE CLIENT; LIVE TELEGRAM RUN PENDING CREDENTIALS
**README item:** 1. Telegram authorized-channel collector using Telethon.

### Scope delivered

- Reusable ingestion service (`app/services/ingestion.py`) used by the HTTP router and the collector. Duplicate detection is enforced by the database constraint and translated into a typed `409`, not a `500`.
- `Source` collector state: `last_collected_message_id`, `last_attempt_at`, `last_success_at`, `next_attempt_at`, `consecutive_failures`, `last_error`.
- Telethon adapter (`app/integrations/telegram/client.py`) with lazy import and environment-only credentials.
- Collector (`app/integrations/telegram/collector.py`): active `telegram` sources only, monotonic message-id checkpoints, duplicate accounting, exponential backoff with bounded jitter, per-source health, and a CLI.
- Strict channel-reference parser (`app/integrations/telegram/channel.py`): public `t.me` references only; invite links (`t.me/+...`) are rejected because following them would join a channel.
- One-time interactive login command (`python -m app.integrations.telegram.login`).
- Safe-logging rule: counts and source ids only; message bodies, API hash, and session paths are never logged.
- Corrected the audit's two extractor defects that would have polluted collected data: token-boundary/verb-aware matching (`"does not react well"` is no longer React/software) and risk screening (`risk_flags`, `risk_score`, human-review routing). Upfront-fee text is not stored as a budget.
- Migration `0002_telegram_collector` adds collector state and risk fields.
- `alembic.ini` added so the documented migration workflow is executable.
- `DATABASE_URL` is now required; the hardcoded credential-bearing default was removed.

### Files

```
alembic.ini
app/config.py
app/db.py
app/main.py
app/models.py
app/routers/ingestion.py
app/schemas.py
app/services/extractor.py
app/services/ingestion.py
app/integrations/__init__.py
app/integrations/telegram/{__init__,base,channel,client,collector,login}.py
migrations/versions/0002_telegram_collector.py
tests/conftest.py
tests/test_extractor.py (unchanged, still green)
tests/test_extractor_cases.py
tests/test_ingestion_api.py
tests/test_telegram_collector.py
```

### Executed evidence

Environment: Python 3.15.1 (host; declared target remains 3.12), SQLite used only for the audit/test path, no PostgreSQL or Docker container was started.

| # | Command | Result |
|---|---|---|
| 1 | `.venv/Scripts/python.exe -m pytest -q` | **23 passed** |
| 2 | `alembic upgrade head` on a scratch SQLite DB | 0001 → 0002 applied |
| 3 | `alembic downgrade base` then `alembic upgrade head` | both re-applied successfully |
| 4 | `alembic check` against the migrated DB | `No new upgrade operations detected.` |
| 5 | `python -c "import app.main"` | import succeeds; 4 routers mounted (FastAPI 0.162 wraps included routers as `_IncludedRouter`, so the baseline's `r.path` one-liner no longer enumerates them) |

### Behaviour proven by tests

| Behaviour | Test |
|---|---|
| Two consecutive runs collect messages 1–2 exactly once; the second run starts at checkpoint 2 | `test_collects_new_messages_once_across_two_runs` |
| An already-ingested external id is counted as a duplicate, not inserted again | `test_existing_external_id_is_counted_as_duplicate` |
| A failed fetch records `consecutive_failures`, `last_error`, and `next_attempt_at` without advancing the checkpoint; a run inside the window is skipped; a later run recovers | `test_failure_records_backoff_without_advancing_checkpoint` |
| Inactive and non-Telegram sources are skipped | `test_inactive_and_non_telegram_sources_are_skipped` |
| Only active Telegram sources are polled | `test_collect_sources_only_reads_active_telegram_sources` |
| Message text and source URL are absent from captured logs | `test_message_text_is_never_logged` |
| Invite links, non-Telegram hosts, and empty references are rejected | `test_channel_reference_rejects_invite_links_and_accepts_public_links` |
| The Telethon adapter refuses to start without credentials | `test_telethon_client_refuses_to_start_without_credentials` |
| Duplicate ingestion over HTTP returns `409` with code `duplicate_post` and does not poison the session | `test_duplicate_ingestion_returns_409_with_stable_code` |
| Risky text is preserved verbatim, flagged, and routed to `needs_review` | `test_risky_text_is_flagged_and_routed_to_review` |
| The audit's five extractor probes produce corrected output | `tests/test_extractor_cases.py` |

### Not verified (explicitly)

- **Live Telegram collection.** No authorized Telegram account or private test channel is available in this environment. The Telethon adapter compiles and its credential guard is tested, but no live message has been fetched. Do not report this as a live success.
- **PostgreSQL execution.** Tests ran on SQLite. The migration is written to be SQLite-compatible for audit verification; it should be re-run against the project's PostgreSQL 16 before deployment.
- **Docker/Compose.** Not started in this environment.

### Remaining risk

- Telethon's `min_id` semantics and `get_entity` behavior are not exercised by the fake client; a live session smoke test is the next step once credentials exist.
- The collector commits once per message. A very large backfill should batch writes; the current limit and checkpoint make this safe but not optimal.
- `consecutive_failures` backoff is time-based and per source; it does not yet consume the Celery retry policy (M2).

---

## M2 — Redis/Celery processing pipeline and retry policy

**Status:** IMPLEMENTED AND VERIFIED IN EAGER/TEST MODE; LIVE REDIS BROKER RUN PENDING
**README item:** 2. Redis/Celery processing pipeline and retry policy.

### Scope delivered

- Celery application (`app/worker/celery_app.py`) with Redis broker/backend, late acknowledgements, `worker_prefetch_multiplier=1`, JSON-only serialization, UTC, soft/hard time limits, and a beat schedule for collection.
- Durable job state (`job_executions`) with a unique idempotency key, attempt count, status, payload, result, timestamps, and indexed status.
- Dead-letter table (`dead_letter_jobs`) written in the same transaction as the terminal `dead_lettered` status.
- Bounded exponential backoff with 10% jitter (`app/services/jobs.py`).
- `ingest_job` and `collect_job` wrappers; the ingestion wrapper treats a duplicate as success so a replay never creates a second row.
- Worker and beat services in `docker-compose.yml`; Redis is bound to `127.0.0.1:6382` on the host.
- Migration `0003_job_pipeline`.

### Executed evidence

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **32 passed** |
| 2 | `alembic upgrade head` on a fresh scratch DB | 0001 → 0002 → 0003 applied |
| 3 | `alembic check` | `No new upgrade operations detected.` |
| 4 | `docker compose config -q` | exit 0 |

### Behaviour proven by tests

| Behaviour | Test |
|---|---|
| A transient failure is retried and succeeds on attempt 3; one job row, no dead letter | `test_transient_failure_is_retried_then_succeeds_once` |
| Exhausted retries write exactly one dead-letter row with the error | `test_exhausted_retries_land_in_dead_letter` |
| A succeeded key returns its stored result and never re-executes | `test_succeeded_key_is_not_executed_again` |
| A dead-lettered key is not retried without a new key | `test_dead_lettered_key_is_not_retried_without_a_new_key` |
| A running key is rejected instead of double-executed | `test_running_key_is_not_executed_concurrently` |
| Ingestion replay produces one post and one opportunity | `test_ingestion_retry_does_not_duplicate_rows` |
| Backoff is exponential and bounded | `test_retry_delay_is_exponential_and_bounded` |
| Worker config uses late acks, prefetch 1, JSON, UTC, and a registered beat task | `tests/test_worker_config.py` |

### Not verified (explicitly)

- **A live Redis broker and a real Celery worker process were not started.** The tests prove the retry/idempotency logic and the Celery configuration in eager/test mode. A broker round-trip is an environment step, not a code claim.
- **PostgreSQL execution.** Same limitation as M1: tests use SQLite; the migration was checked there. Re-run against PostgreSQL 16 before deployment.
- **Live collection through Celery.** `collect_job` is exercised indirectly through the collector tests; the Celery wrapper itself has not run against a live Telegram session.

### Remaining risk

- A worker crash while a job is `running` will leave that key blocked (the runner refuses concurrent execution). A stale-lock recovery policy is deliberately deferred; add a `started_at` timeout reaper before scaling beyond one worker.
- Beat runs are not deduplicated across scheduler restarts; the collector's own message-id checkpoint is the safety net.
- `docker compose config` was checked, but no container was started.

## M3 — LLM extraction provider with strict Pydantic validation

**Status:** IMPLEMENTED AND VERIFIED WITH A FAKE HTTP TRANSPORT; NO LIVE MODEL CALL MADE
**README item:** 3. LLM extraction behind a provider interface with strict Pydantic validation.

### Scope delivered

- `ExtractionProvider` protocol with `rules` and OpenAI-compatible `llm` implementations; selection by `EXTRACTION_PROVIDER`.
- Closed `LLMOpportunity` Pydantic schema: `extra="forbid"`, enums for type/category/currency/risk flags, bounds on budget/score/skills/title/description, skill de-duplication, and a model-level rule that amount and currency are set together.
- Strict response handling: JSON extraction, fenced-JSON tolerance, envelope checks, schema validation, and field-path-only error messages (model output is never echoed).
- Prompt-injection isolation: collected text is untrusted data inside `<untrusted_content>` tags with an explicit instruction not to follow embedded instructions.
- Cost/abuse limits: input truncation, `max_tokens`, request timeout, and no key in logs or `repr`.
- Safety merge: deterministic risk flags and risk score are unioned into LLM output; an upfront-payment flag suppresses the extracted budget. A provider failure falls back to `rules` (`rules_fallback`) and does not drop the post.
- Ingestion records `extraction_method` as `rules`, `llm`, or `rules_fallback`.

### Executed evidence

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **42 passed** |
| 2 | `ruff check app tests migrations` | all checks passed |
| 3 | Fake HTTP transport tests | valid JSON accepted; unknown field, invalid JSON, and mismatched currency/amount rejected |

### Behaviour proven by tests

| Behaviour | Test |
|---|---|
| A valid response is validated and mapped to the domain object | `test_valid_llm_response_is_validated_and_mapped` |
| Unknown response fields are rejected | `test_unknown_fields_are_rejected` |
| Amount/currency must be set together | `test_budget_currency_must_match_amount` |
| Non-JSON content is rejected | `test_invalid_json_is_rejected` |
| Injection text stays in the user data block; the key is not in the body or `repr` | `test_prompt_injection_stays_inside_untrusted_content` |
| A missing key refuses provider construction | `test_provider_refuses_to_start_without_api_key` |
| Provider failure falls back to rules | `test_llm_failure_falls_back_to_rules` |
| LLM output cannot hide deterministic risk or a bait budget | `test_llm_result_cannot_hide_deterministic_risk_flags_or_bait_budget` |
| Deterministic extraction fills gaps in LLM output | `test_merge_safety_fills_missing_category_and_skills_from_rules` |
| Ingestion persists `extraction_method=llm` | `test_ingestion_records_llm_as_extraction_method` |

### Not verified (explicitly)

- **No live LLM request was made.** There is no API key in this environment and the tests use `httpx.MockTransport`, so model quality, latency, and provider-specific `response_format` behavior are unmeasured. Do not claim production extraction quality from these results.
- **No cost or token-usage measurement.** The limits are enforced client-side; actual billing depends on the provider.

### Remaining risk

- Prompt injection is mitigated, not eliminated. The schema and human-review routing bound the impact: injected text cannot add fields or suppress deterministic risk flags, and no LLM output is published without approval.
- The LLM could still produce a plausible but incorrect title/skills/budget. That is why every extracted opportunity remains a candidate for human review.
- `response_format=json_object` is assumed; a provider that rejects it will produce a `rules_fallback` result until the adapter is extended.

## M4 — Admin dashboard integration and authenticated service-to-service access

**Status:** IMPLEMENTED AND VERIFIED IN TESTS; LIVE BROWSER/POSTGRES RUN PENDING
**README item:** 4. Admin dashboard integration and authenticated service-to-service access.

### Scope delivered

- API-key authentication (`X-API-Key` or `Authorization: Bearer`) with SHA-256 fingerprint lookup and `hmac.compare_digest`; raw keys are not retained after parsing and are never logged.
- Roles: `admin` (full), `reviewer` (review/edit/approve), `collector` (ingest/read), `analyst` (read/analytics). `admin` passes every role check.
- Role matrix applied per route: source mutation is admin-only; ingestion is collector/admin; review/edit is reviewer/admin; reads are role-scoped.
- Startup fails outside development when `AUTH_API_KEYS` is empty. Docs, Redoc, and OpenAPI are disabled outside development.
- Review integrity: `reviewer_note` and `reviewed_by` persistence, an append-only `review_events` table (actor, event type, from/to status, note, field diff), explicit transition rules, and idempotent repeated decisions.
- Field editing endpoint with before/after audit rows.
- Source JSON-body update and status endpoints; pagination caps on list endpoints; 409 for illegal transitions; 404s for unknown resources.
- `/health` now returns **503** when the database probe fails, without leaking the driver error.
- Static admin dashboard at `/admin/` (no build step): key login, review queue with notes and field editing, source management, publications placeholder, analytics placeholder, loading/empty/error states.
- Explicit CORS allowlist support; wildcard origins are not used.
- Migration `0004_review_audit`.

### Executed evidence

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **75 passed** |
| 2 | `ruff check app tests migrations` | all checks passed |
| 3 | `alembic upgrade head` on a fresh scratch DB | 0001 → 0004 applied |
| 4 | `alembic check` | `No new upgrade operations detected.` |
| 5 | `docker compose config -q` | exit 0 |

### Behaviour proven by tests

| Behaviour | Test |
|---|---|
| Every protected route returns 401 without credentials | `tests/test_auth.py::test_protected_routes_require_credentials` |
| Invalid keys are rejected; Bearer keys are accepted | `test_invalid_key_is_rejected`, `test_bearer_token_is_accepted` |
| Reviewer cannot mutate sources; admin can | `test_reviewer_cannot_create_source`, `test_admin_can_create_source` |
| Collector/analyst cannot review; reviewer can and is attributed | `test_collector_and_analyst_cannot_review`, `test_reviewer_can_review_and_is_recorded` |
| Docs/OpenAPI are disabled outside development | `test_docs_and_openapi_are_disabled_outside_development` |
| Startup fails without keys in production | `test_security_config_fails_loudly_without_keys_in_production` |
| Missing/invalid keys and roles are rejected at config load | `test_load_credentials_*` |
| Review notes round-trip and create exactly one audit event | `test_review_note_round_trip_and_event` |
| Repeated decisions are idempotent; illegal transitions are 409 | `test_repeating_the_same_decision_is_idempotent`, `test_illegal_transition_returns_409` |
| Field edits record before/after changes | `test_field_edit_is_recorded_with_before_and_after` |
| Source update/status use JSON bodies and caps | `tests/test_sources_api.py` |
| `/health` is 200 with a DB and 503 without | `tests/test_health.py` |
| Dashboard is served and contains the review queue | `test_dashboard_is_served` |

### Not verified (explicitly)

- **No real browser session and no live server/PostgreSQL run.** The dashboard is asserted as served HTML; its API calls are the same endpoints covered by tests, but a human click-through was not executed in this environment.
- **No live auth against the Express/marketplace backend.** The header contract is documented, not integrated externally.
- **No rate limiting or request-body cap.** These remain on the security backlog; the service should stay on a trusted network until they land.

### Remaining risk

- API keys are long-lived static credentials. Rotation and per-service revocation are manual; there is no key-management UI.
- The dashboard stores the key in `localStorage`, which is acceptable for a same-origin internal tool but means any XSS on that origin can read it. The dashboard does not render untrusted HTML (all values are escaped).
- CORS is disabled when `CORS_ORIGINS` is empty; a separately hosted frontend must set an explicit origin list.
- The `review_events` table is append-only by policy and API design, but a database superuser can still modify it; there is no cryptographic tamper evidence.

## M5 — Telegram publisher with explicit approval and attribution

**Status:** IMPLEMENTED AND VERIFIED WITH A FAKE PUBLISHER; NO LIVE TELEGRAM SEND
**README item:** 5. Telegram publisher with explicit approval and attribution.

### Scope delivered

- `Publication` model with one row per `(opportunity, channel)`, status, attempts, external message id, schedule, published time, error, and attribution actor; migration `0005_publications`.
- Publishing is disabled by default and fails closed when the channel allowlist is empty.
- Guard order is explicit: approved opportunity → publishing enabled → allowlisted channel → approved state re-checked at send time.
- `sending` is committed before the Telegram call. A row left in `sending` is never automatically resent; the API returns `publication_unknown` for human reconciliation. Replaying a published row returns the original and sends nothing.
- Scheduling: `scheduled_at` creates a `scheduled` row; a beat task (`yekis.publish_due`, every minute) publishes only due rows and re-checks approval.
- Formatter always appends `Source: <name>` and the source URL after truncation, so attribution cannot be cut off.
- Per-opportunity attempt cap via `PUBLISHING_MAX_ATTEMPTS`; failures are persisted with a bounded error.
- Admin dashboard has a Publish action for approved items; the publications tab lists records.

### Executed evidence

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **90 passed** (M5 checkpoint) |
| 2 | `alembic upgrade head` on a fresh scratch DB | 0001 → 0005 applied |
| 3 | `alembic check` | `No new upgrade operations detected.` |
| 4 | `ruff check app tests migrations` | all checks passed |

### Behaviour proven by tests

| Behaviour | Test |
|---|---|
| Unapproved opportunity cannot publish and creates no row | `test_unapproved_opportunity_cannot_be_published` |
| Disabled publishing sends nothing | `test_publishing_disabled_sends_nothing` |
| Channel must be allowlisted | `test_channel_must_be_allowlisted` |
| Successful publish sends once and includes source name/URL | `test_publish_sends_once_with_attribution` |
| Replay returns the same row and does not double-send | `test_replaying_the_same_publication_does_not_double_send` |
| A `sending` row is never resent automatically | `test_a_sending_row_is_never_resent_automatically` |
| Failed send is recorded and can be retried | `test_failed_send_is_recorded_and_can_be_retried` |
| Scheduled rows wait and publish when due | `test_scheduled_publication_waits_for_due_time` |
| Approval is re-checked at send time | `test_approval_is_rechecked_at_send_time` |
| Formatter keeps attribution after truncation | `test_formatting_always_keeps_attribution` |
| API auth, disabled mode, and unapproved guards | `tests/test_publications_api.py` |

### Not verified (explicitly)

- **No live Telegram send was performed.** Tests use `FakePublisher`; `TelethonPublisher` compiles and its credential/session guard is present, but no message reached a channel. Live verification requires owner approval and a private test channel, per the baseline rules.
- **No live beat/worker run.** `publish_due` is tested directly; the Celery wrapper and beat schedule are configured but were not run against a broker.

### Remaining risk

- Telegram has no idempotency key. The `sending` state is the chosen safety trade-off: at-most-once attempt, at the cost of manual reconciliation after a crash.
- A reviewer can revoke approval after scheduling; the send-time re-check prevents the send, but an already published message cannot be unsent by this service.
- The allowlist is an environment string; changing it requires a restart/config update.

---

## M6 — Analytics, source quality, and market insights

**Status:** IMPLEMENTED AND VERIFIED IN TESTS
**README item:** 6. Analytics, source quality, and market insights.

### Scope delivered

- Read-only analytics service (`app/services/analytics.py`) and endpoints under `/api/v1/analytics`:
  - `overview` — source/post/opportunity totals, status mix, type/category buckets, budget ranges by currency, risk count, average quality, approval rate, publication counts, last collection time.
  - `sources` — per-source posts, opportunities, review mix, approval rate, risk flags, average quality, publication success/failure, collector health.
  - `market` — skill demand, budget ranges/median by currency, remote share, category/type mix.
  - `trends` — daily posts, opportunities, approvals, publications over a bounded 1–365 day window.
- Role guard: `analyst`, `reviewer`, and `admin`; `collector` and anonymous callers are refused.
- Analytics read only collected content and publication records; no marketplace financial tables or APIs are touched.

### Executed evidence

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **96 passed** |
| 2 | `ruff check app tests migrations` | all checks passed |
| 3 | `alembic check` | `No new upgrade operations detected.` |
| 4 | `docker compose config -q` | exit 0 |

### Behaviour proven by tests

| Behaviour | Test |
|---|---|
| Overview totals, statuses, risk, approval rate, publication counts | `test_overview_counts_and_statuses` |
| Per-source quality rollup | `test_source_quality_rollup` |
| Skill demand, budget ranges, and remote share | `test_market_insights_skills_budget_and_remote` |
| Trend buckets merge posts/reviews/publications | `test_trends_include_posts_reviews_and_publications` |
| Trend window rejects 0 and 366 days | `test_trends_window_is_bounded` |
| Analyst/reviewer/admin can read; collector/anonymous cannot | `test_analytics_requires_analyst_or_reviewer` |

### Not verified (explicitly)

- **No production-volume performance measurement.** The service loads skill arrays and budget values for in-process aggregation, which is appropriate for the current scale but not a streaming/warehouse design.
- **No PostgreSQL execution.** Tests use SQLite; the `func.date` grouping and aggregate queries should be re-run against PostgreSQL 16 before deployment.

### Remaining risk

- Skill demand is case-sensitive as stored; the extractor already lowercases curated terms, but manual edits can introduce variants. A normalized skill vocabulary is the next refinement.
- Trends are UTC calendar days; a source in another timezone may appear to shift by one day.
- Budget statistics are content signals, not verified market prices; they must not be used for financial decisions.

---

## Final verification after documentation pass

The documentation set was added after the M6 section above. The final code state was re-verified:

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **97 passed** |
| 2 | `ruff check app tests migrations` | all checks passed |
| 3 | `python -m compileall -q app tests migrations` | exit 0 |
| 4 | `alembic upgrade head` on a fresh scratch DB | 0001 → 0005 applied |
| 5 | `alembic check` | `No new upgrade operations detected.` |
| 6 | `docker compose config -q` | exit 0 |
| 7 | `pip install -e ".[dev]"` | succeeded (Python 3.15 host) |

Documentation added:

- `docs/README.md` — index and reading order.
- `docs/PROJECT_GUIDE.md` — architecture, data model, workflows, auth, configuration, development, troubleshooting, limitations.
- `docs/API_REFERENCE.md` — route contracts, roles, transitions, error codes, examples.
- `docs/OPERATIONS.md` — setup, migrations, collector/publisher runbooks, monitoring, backup, incident playbooks.
- `docs/ARCHITECTURE.md` — refreshed decision record linked to the guide.
- `README.md` — documentation links and current architecture summary.
- `Dockerfile` — now copies `alembic.ini` and `migrations/` so the documented in-container migration command works.

Final known gaps remain unchanged: no live Telegram/Redis/LLM/PostgreSQL verification in this environment, no live channel send, no application-level rate limiting, no CI workflow, and no stale-lock reaper for `running` jobs.

---

## Gemini (AI Studio) provider added after the documentation pass

The LLM layer now has a first-class `gemini` provider in addition to the OpenAI-compatible `llm` provider.

**Implementation**

- `app/services/llm/gemini.py` — native Google Generative Language API adapter.
  - `POST {GEMINI_BASE_URL}/models/{GEMINI_MODEL}:generateContent`
  - key in the `x-goog-api-key` header, never in the URL or logs;
  - `systemInstruction` carries the shared extraction prompt;
  - content is wrapped in `<untrusted_content>`;
  - `responseMimeType=application/json`;
  - response is parsed and validated by the same closed `LLMOpportunity` schema;
  - safety blocks, missing candidates, invalid JSON, and schema violations raise a normalized provider error and fall back to rules.
- `app/services/llm/prompts.py` — shared prompt and untrusted-content wrapper.
- `app/services/llm/parsing.py` — shared JSON/schema parsing for all providers.
- `app/services/llm/orchestrator.py` — `EXTRACTION_PROVIDER=gemini` selects the provider; `extraction_method` is recorded as `gemini`.
- `app/config.py` / `.env.example` — `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BASE_URL`.
- Default model: `gemini-2.5-flash` (override with `GEMINI_MODEL`).

**Configuration**

```env
EXTRACTION_PROVIDER=gemini
GEMINI_API_KEY=<AI Studio key>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

**Verification**

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **104 passed** |
| 2 | `ruff check app tests migrations` | all checks passed |
| 3 | Gemini mock-transport tests | header/body shape, untrusted-content delimiters, no key in body/`repr`, schema rejection, safety-block handling, fallback, `extraction_method=gemini` |

**Not verified:** no live Gemini request was made (no AI Studio key in the audit environment). The adapter is proven against a mock HTTP transport; a live smoke test is the next step once a key and model are available. A live failure is designed to degrade to `rules_fallback`, not to lose the post.

---

## Functional verification pass (live API + live integrations)

A full functional check was executed against the running service and the real PostgreSQL database.

### Live API verification

`scripts/smoke_test.py` drives the running API over HTTP and asserts the complete product workflow. Result: **54/54 checks passed**.

| Area | Verified live |
|---|---|
| Public surface | `/health` (DB connected), `/admin/` dashboard served, `/` banner |
| Auth | anonymous → 401, bad key → 401, reviewer blocked from admin route → 403, collector blocked from review → 403 |
| Sources | create (201), edit, status toggle off/on |
| Ingestion | ingest (201), type/category/budget/skill extraction, review-queue routing, duplicate → 409 `duplicate_post` |
| Risk | risky post still ingested, routed to `needs_review`, `upfront_payment` flag present, bait amount not stored as budget |
| Original text | stored verbatim and listed back |
| Review | approve, note persisted, attributed to the key principal, one audit event per transition |
| Transitions | `approved → needs_review`, `needs_review → approved` allowed; repeated identical decision creates no extra event |
| Edits | field edit persists and writes a before/after diff event; empty body → 422 |
| Publishing | blocked with `publishing_disabled` (correct: publishing is off) |
| Analytics | overview/source quality/market/trends respond; counts reflect reviews and risk flags; trends window bounded |
| Validation | pagination cap → 422, bad status filter → 422, unknown id → 404 |

### Database state

| Check | Result |
|---|---|
| `alembic current` | `0005_publications` |
| `alembic check` (PostgreSQL) | `No new upgrade operations detected.` |
| Active `telegram` sources | `0` — this is why the collector correctly returns `[]` |

### Live integration findings

**Telegram account model — real defect found and fixed.** A live read against a public channel with the configured bot session failed with:

```text
telethon.errors.rpcerrorlist.BotMethodInvalidError: The API access for bot users is restricted.
The method you tried to invoke cannot be executed as a bot (caused by GetHistoryRequest)
```

Telegram forbids bots from reading channel history. The collector previously reused the bot token, so collection could never work. Fixed:

- the collector now always uses a **user** session (`TELEGRAM_SESSION_PATH`, default `var/telegram_user`);
- the publisher keeps bot support (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_BOT_SESSION_PATH`), because bots **may** send;
- `python -m app.integrations.telegram.login --as user|bot` makes the account type explicit;
- a bot session used for collection now raises an actionable error naming `--as user`.

**Gemini wiring.** `get_provider()` selects `GeminiProvider` with `gemini-2.5-flash` and the native `generateContent` URL; the key is present, absent from `repr`, and absent from the request payload (it is sent only in the `x-goog-api-key` header). A live Gemini call was **not** made from this environment (outbound LLM calls are blocked here); the provider is proven against a mock HTTP transport.

**Redis/Celery.** Broker connectivity was not exercised from this environment (external network access blocked). Worker configuration, beat schedule, retry/backoff, and dead-letter behavior are proven by the test suite in eager mode.

### Final state after this pass

| Check | Result |
|---|---|
| `pytest -q` | **119 passed** |
| `ruff check app tests migrations scripts` | all checks passed |
| `scripts/smoke_test.py` | **54/54 passed** |
| `alembic check` (PostgreSQL) | no drift |

---

## rules.md UI/UX compliance pass

The mandatory visual standards in `rules.md` (sections 2, 3, and 4) were applied to the admin dashboard and are now enforced by automated tests.

### Changes applied to `app/static/admin/index.html`

| Rule | Change |
|---|---|
| 2.1 Zero emoji | Repository-wide scan reports 0 emoji. The dashboard uses no emoji; text separators were replaced with CSS dividers. |
| 3.1 Bold colored indicators only | `.pill` renamed to `.indicator`; `.badge` confirmed as bold colored text with no background, border, or radius. Tab counts (`.badge-count`) converted from a background pill to plain bold text. The `.dot` pseudo-element indicator was removed. State colors follow the mandated mapping (green/red/amber/blue). |
| 3.3 No icon tiles | The brand mark renders as a bare colored glyph with no background or border. |
| 4.1 Theme parity | Added `--accent-contrast`, `--code-text`, `--code-bg`, `--line-hover`, `--shadow-sm`, and hover tokens to both themes; removed the theme-conditional `.orig-text`/`code` overrides in favour of token swaps. All component colors now resolve to tokens. |
| 4.1 Single toggle | The header theme toggle was not wired. It now applies, persists (`yekis.theme`), and initializes from `prefers-color-scheme`, and its label reflects the next mode. |
| 4.2 Date filters | Date inputs use a 150px fixed width in a flex group with wrapping and max-width containment. |
| 4.3 Responsive | Removed the `!important` overrides, added `min-width: 0` for flex/grid children, a 1024px breakpoint, 44px minimum for small buttons, shared-row action buttons (`.actions`), and a full-width mobile toast. |
| 6.3 Session handling | Added a 30-minute inactivity timeout that clears the stored key, and immediate authentication-state clearing on any HTTP 401. |

### Enforcement

`tests/test_ui_compliance.py` (20 checks) enforces: repository-wide zero emoji, indicator styling rules, indicator state coverage, no dot pseudo-elements, tab counts without pills, theme token parity for both themes, no hardcoded colors outside token blocks, theme-conditional paint-only rules, a single wired header theme toggle, compact date filters, overflow and shrink containment, two-column mobile KPI cards, 44px touch targets, shared mobile action rows, no inline grid columns, the 30-minute inactivity timeout, and 401 credential clearing.

### Executed evidence

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **139 passed** |
| 2 | `ruff check app tests migrations scripts` | all checks passed |
| 3 | `python -m compileall -q app tests migrations scripts` | exit 0 |
| 4 | `docker compose build api` + `docker compose up -d api` | image rebuilt; container serves the updated dashboard byte-for-byte (66,292 bytes) |
| 5 | Served-HTML compliance re-check | 14/14 UI checks pass against the live response |
| 6 | `scripts/smoke_test.py` against the rebuilt container | **54/54 passed** |
| 7 | Markdown link check (22 files) | no missing links |

---

## NVIDIA NIM provider integration

NVIDIA NIM was integrated through the **existing** OpenAI-compatible provider architecture. No provider class was added, the orchestrator contract is unchanged, and the Gemini and rules providers are untouched.

**Changes**

- `app/config.py`: `LLM_RESPONSE_FORMAT` (JSON mode toggle) and `LLM_EXTRA_BODY` (provider-specific request fields, e.g. NVIDIA guided decoding).
- `app/services/llm/openai_compatible.py`: optional `response_format`/`extra_body`; HTTP failures mapped to typed, secret-free errors (`ProviderAuthError` 401/403, `ProviderRateLimitError` 429 with `retry-after`, `ProviderUnavailableError` 5xx/timeout). All remain `ProviderError` subclasses so the rules fallback is preserved.
- `app/services/llm/orchestrator.py`: passes the new settings; `parse_extra_body()` fails loudly on malformed JSON.
- `app/services/extractor.py`: **defect fix** — the budget parser only recognised currency-before-amount (`$300`). Amount-before-currency (`2500 USD`, `45,000 ETB`) is now extracted too, so the deterministic baseline can be compared fairly against provider output.
- `tests/test_nvidia_provider.py`: 35 checks covering all ten required scenarios.
- `scripts/verify_nvidia_live.py`: live verification harness (model id, UTC date, HTTP status, latency, provider-vs-rules comparison). Refuses to run without a key and refuses to print a key.
- `pyproject.toml`: `pythonpath = ["."]` so `scripts/` is importable from tests under bare `pytest`.

**Verification**

| # | Command | Result |
|---|---|---|
| 1 | `pytest -q` | **174 passed** |
| 2 | `python -m pytest -q` | **174 passed** |
| 3 | `ruff check app tests migrations scripts` | all checks passed |
| 4 | `python -m compileall -q app tests migrations scripts` | exit 0 |
| 5 | Live NVIDIA request | **NOT RUN** — no NVIDIA key is configured locally and outbound LLM calls are blocked in this environment |

Full detail, the deterministic baseline, and the live-run procedure are in `NVIDIA_NIM_VERIFICATION.md`.

---

## NVIDIA NIM live verification (follow-up)

The NVIDIA NIM integration was verified live and the complete workflow was exercised against the running stack.

**Live provider run**

| Item | Value |
|---|---|
| Date (UTC) | 2026-11-11T17:41:20 |
| Endpoint | `POST https://integrate.api.nvidia.com/v1/chat/completions` |
| Model id | `minimaxai/minimax-m3.1-flash` |
| HTTP status | `200` on both samples |
| Latency | 105,099 ms and 104,245 ms |

**Defects found and fixed during live verification**

1. **30 s timeout was too short.** Every live request timed out and silently fell back to rules. Diagnosis: `GET /v1/models` returned `200` in ~1,080 ms from inside the container, so egress was healthy; NIM cold starts for this model simply take ~105 s. Fixed with `LLM_TIMEOUT_SECONDS=180` and a bounded provider-level retry (`LLM_MAX_ATTEMPTS=2`, `LLM_RETRY_WAIT_SECONDS=5`) for transient failures only (timeout, 429, 5xx). Credential and schema failures are never retried.
2. **The Gemini fallback was silently broken.** The service was still on `EXTRACTION_PROVIDER=gemini` and `gemini-2.5-flash` returns `404` for the configured AI Studio key. The rules fallback hid the misconfiguration completely. The Gemini adapter now reports a 404 as "model not available for this API key".
3. **Provider skill casing was inconsistent.** The model returned `Python`/`FastAPI` while the rules extractor emits lowercase, which would split analytics demand entries. Provider skills are now normalised to lowercase.

**Live service verification**

| Check | Result |
|---|---|
| `scripts/verify_nvidia_live.py` | verified, HTTP 200 both samples |
| `scripts/verify_extraction_provider.py` | `extraction_method=llm` for both samples; budget `2500.0 USD`; scam sample budget suppressed; 4 risk flags; score 98 |
| `scripts/smoke_test.py` (live stack, NVIDIA active) | **54/54 passed** |

Full detail: `NVIDIA_NIM_VERIFICATION.md`.
