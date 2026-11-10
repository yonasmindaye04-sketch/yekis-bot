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

**Status:** PENDING

## M3 — LLM extraction provider with strict Pydantic validation

**Status:** PENDING

## M4 — Admin dashboard integration and authenticated service-to-service access

**Status:** PENDING

## M5 — Telegram publisher with explicit approval and attribution

**Status:** PENDING

## M6 — Analytics, source quality, and market insights

**Status:** PENDING
