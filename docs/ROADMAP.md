# Roadmap

- M1: API, database, source registry, manual ingestion, rule-based extraction, review queue.
- M2: Telegram source collector with authorized Telethon account, checkpoints, backoff and source health.
- M3: Redis/Celery tasks, retries, dead-letter handling and idempotency.
- M4: LLM extraction provider with strict schema validation, cost limits and prompt-injection isolation.
- M5: Authenticated admin integration with the Yekis Express backend.
- M6: Telegram publishing with admin approval, attribution, scheduling and audit history.
- M7: Market analytics, trend summaries and content performance.

**Execution status (2026-11-10):** the six README milestones (collector, worker/retries, LLM extraction, auth/admin dashboard, approval-gated publisher, analytics) are implemented and covered by the test suite. Evidence and explicit live-verification gaps are recorded in `docs/audit/MILESTONE_EXECUTION.md`.
