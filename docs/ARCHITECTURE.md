# Architecture decisions

For the current end-to-end architecture, data model, and workflows, read the [Project Guide](PROJECT_GUIDE.md). This file records the durable architectural decisions that shape the code.

## Service boundaries

Yekis Intelligence is a local, independently deployable service. It owns its PostgreSQL database, schema, and Alembic migrations. It supports source registration, Telegram collection, manual ingestion, extraction, review, publishing, and analytics.

This file is the architectural intent. The [Project Guide](PROJECT_GUIDE.md) describes the implemented system.

## Security boundaries

- No marketplace user, wallet, escrow, payout, invoice, milestone, or contract write access. No such clients, tables, or endpoints exist.
- Telegram session strings and API hashes are environment/secret-store material and are never committed, logged, or included in container images.
- Collected content is untrusted input. It is treated as data, never as instructions to the extractor or the model.
- Publishing requires explicit human approval, is disabled by default, and is restricted to an explicit channel allowlist.
- Original post text and source attribution are preserved verbatim.
- Every `/api/v1/*` route requires an authenticated service key, and review/publish actions record the acting principal.

## Database ownership

Yekis Intelligence owns its tables and migrations. It does not share Prisma models or write to marketplace tables. Cross-service integration is through authenticated APIs.

Durable state lives in PostgreSQL:

- `sources`, `collected_posts`, `opportunities`, `review_events`, `publications`;
- `job_executions` and `dead_letter_jobs` for background work.

Redis is a broker/backend only; it is never the source of truth.

## Ingestion and deduplication

- `(source_id, external_id)` is the unique deduplication boundary.
- The API pre-check is a fast path; the database constraint is the guarantee.
- The collector uses the Telegram message id as `external_id` and advances a per-source checkpoint only after successful handling.
- Cross-source and fuzzy duplicate detection are intentionally out of scope for now.

## Extraction strategy

- Rules extraction is the default and runs without external dependencies.
- The LLM provider is optional, OpenAI-compatible, and constrained by a closed Pydantic schema with `extra="forbid"`.
- Any provider failure falls back to rules (`extraction_method=rules_fallback`) rather than dropping the post.
- Deterministic risk flags are always merged into LLM output; an upfront-payment flag suppresses the extracted budget.
- Extracted values are candidates for human review, never verified facts. LLM output never auto-approves.

## Review and audit

- Review status transitions are explicit and validated.
- Every actual status change writes one `review_events` row with actor, previous/new status, note, and time.
- Field edits write a `field_edit` event with before/after values.
- The review trail is append-only by design; there is no update/delete route for events.

## Publishing

- Approval is re-checked at send time, not only at scheduling time.
- A publication row is committed as `sending` before the network call. A row left in `sending` is never automatically resent; a human reconciles it because Telegram has no idempotency key.
- One `(opportunity, channel)` publication stream prevents duplicate sends on replay.
- Every formatted message ends with the source name and URL; attribution is appended after truncation.

## Failure handling

- API sessions always roll back and close; a failed request cannot poison a pooled connection.
- Duplicate ingestion returns `409` with a stable code, not `500`.
- Background jobs are idempotent, retried with bounded exponential backoff and jitter, and moved to a dead-letter table when exhausted.
- `/health` returns `503` when the database probe fails.
