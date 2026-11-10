# Next Agent Handoff — Yekis Intelligence

**Baseline branch:** `audit/initial-baseline` (local only at handoff; no remote configured, `gh` unauthenticated)
**Baseline commit:** the commit that introduces `docs/audit/` on this branch
**Audit date:** 2026-11-10
**Source of truth:** `FEATURE_MATRIX.md`, `IMPLEMENTATION_BACKLOG.md`, `QA_BASELINE.md`, `SECURITY_BASELINE.md`

---

## 0. Read this before touching anything

1. **The scaffold is authoritative.** Do not re-initialize the project, rename directories, replace the stack, or rewrite the models. Evolve what exists.
2. **Nothing runtime-verified yet.** Import, HTTP, database, and Docker-build checks were all blocked during the audit (`QA_BASELINE.md` §2). Your first task is to make those checks runnable and record real results.
3. **Financial independence is a hard boundary.** No escrow, payout, wallet, invoice, milestone, or contract code — no exceptions, no "read-only convenience" endpoints.
4. **Never publish to a real Telegram channel.** When the publisher lands, `PUBLISHING_ENABLED=false` must be the default.
5. **Never commit** `.env`, `*.session`, tokens, or session strings. `.gitignore` already covers `.env`; extend it before writing any integration code.
6. **Do not touch the other project's containers** (`yekis-postgres-1` on :5433, `yekis-redis-1` on :6379) or its database. Intelligence uses its own database on :5440.
7. **Every task ends with evidence**: the command you ran, its output, and the test that proves the behaviour.

## 1. Branch and worktree strategy

```sh
git switch audit/initial-baseline
git switch -c feat/a2-backend-data          # Agent 2
git switch -c feat/a3-integrations          # Agent 3
git switch -c feat/a4-security-qa           # Agent 4  (separate worktree if parallel)
git switch -c feat/a5-admin-ui              # Agent 5
```

One agent per branch. Merge order in §6. Where two agents must touch one file, the owner is listed in §5 and the other submits a request instead of editing.

---

## 2. Agent 2 — Backend & database

### Task A2-1 · Unblock and prove the toolchain (YI-001, YI-002)

- **Scope:** make the project runnable and the migration path real. No behaviour changes.
- **Relevant files:** `pyproject.toml`, `migrations/`, new `alembic.ini`, `app/config.py`, `README.md`
- **Required changes:**
  1. Create `alembic.ini` with `script_location = migrations` and no hardcoded URL (the URL already comes from `app.config.settings` in `migrations/env.py`).
  2. Confirm a Python 3.12 environment installs `.[dev]` cleanly; record the interpreter used.
  3. `migrations/env.py` currently imports `settings` at module scope — keep it, but ensure `alembic.ini` does not shadow it.
  4. Make `DATABASE_URL` required (YI-003) rather than defaulting to a credential-bearing URL.
- **Acceptance criteria**
  - `python -c "import app.main; print([r.path for r in app.main.app.routes])"` lists 9 application routes + the default docs routes.
  - `alembic upgrade head` then `alembic downgrade base` then `alembic upgrade head` succeeds on a scratch database.
  - `alembic check` reports no pending model changes (proves model↔migration parity).
- **Required tests:** none new, but attach the three command transcripts to the PR.
- **Dependencies:** none — this unblocks everyone.
- **Risks:** dropping the `config.py` default breaks anyone relying on it; document the `.env` requirement in the README in the same commit.
- **Exclusions:** do not restructure routers, do not add auth, do not change the extractor.

### Task A2-2 · Transaction integrity (YI-010, YI-011)

- **Scope:** make failure modes correct, observable, and non-poisoning.
- **Relevant files:** `app/db.py`, `app/routers/ingestion.py`, `app/routers/sources.py`, `app/routers/opportunities.py`
- **Required changes:** `db.rollback()` in the `get_db` failure path; wrap each mutation's `commit()` so `IntegrityError` returns 409 (not 500) with a stable error code; keep the pre-check as a fast path only.
- **Acceptance criteria:** a duplicate `POST /api/v1/ingestion/manual` returns 409 on first and subsequent calls; a forced constraint violation returns 409; a subsequent request on the same connection succeeds (no poisoned transaction).
- **Required tests:** `tests/api/test_ingestion_dedup.py` covering sequential duplicate; a concurrency test using two sessions (or a monkeypatched pre-check that always misses) to prove the constraint path returns 409.
- **Dependencies:** A2-1.
- **Risks:** the duplicate pre-check is what makes the common path cheap — keep it.
- **Exclusions:** do not add retry logic in the API layer (that is Agent 3's worker concern).

### Task A2-3 · Review integrity: note, audit trail, transitions (YI-012, YI-013, YI-014, YI-015)

- **Scope:** make review decisions durable, attributable, and append-only.
- **Relevant files:** `app/models.py`, `app/schemas.py`, `app/routers/opportunities.py`, new `migrations/versions/0002_*.py`
- **Required changes:** add `reviewer_note` (Text) to `Opportunity` **and** persist it; add `review_events` (id, opportunity_id, actor, from_status, to_status, note, created_at); validate transitions (`pending_review → approved|rejected|needs_review`, and `needs_review → approved|rejected`); convert status vocabularies to enums with DB constraints; index `review_status`.
- **Acceptance criteria:** a review with a note returns the note on a subsequent GET; every status change produces exactly one `review_events` row; an illegal transition returns 422/409; `alembic check` clean.
- **Required tests:** `tests/api/test_review.py` — note round-trip, illegal transition, event row creation, unknown opportunity → 404.
- **Dependencies:** A2-1. `actor` is populated from Agent 4's auth context — if auth is not merged yet, accept `actor: str | None` and leave a single documented seam (`app/routers/opportunities.py`) for A4 to fill. **Do not invent a user table** — coordinate with A4 (YI-081).
- **Risks:** the `actor` seam is the highest-conflict point in the plan; agree on the dependency signature before both agents write code.
- **Exclusions:** no admin UI, no publishing fields.

### Task A2-4 · Extraction service + correctness (YI-030, YI-031, YI-032, YI-033)

- **Scope:** move ingestion out of the router and fix the three proven extraction defects.
- **Relevant files:** new `app/services/ingestion.py`, `app/services/extractor.py`, `app/routers/ingestion.py`, `app/models.py` (add `risk_flags`, `risk_score`), new migration
- **Required changes:**
  1. `ingest_post(db, source, external_id, text, url) -> Opportunity` used by the router now and by the collector later.
  2. Word/token-boundary skill and category matching (fixes the "react well" → software false positive).
  3. Taxonomy for opportunity types (job, resource, internship, scholarship, training, news, other) and a category set that matches the product, not 4 keywords.
  4. Risk screening: upfront-payment fee requests, off-platform contact pressure, urgency pressure, implausible rates → `risk_flags` list + `risk_score`, and route to `needs_review`. **Flagging is never proof of fraud** — it must not auto-reject.
  5. Budget extraction must prefer amounts adjacent to budget/pay/fee keywords.
- **Acceptance criteria:** all five audit probe cases in `QA_BASELINE.md` §4 produce the corrected, documented output; the original single test still passes.
- **Required tests:** table-driven `tests/services/test_extractor_cases.py` seeded from the probe table, plus `tests/api/test_ingestion_pipeline.py` exercising the new service through the router.
- **Dependencies:** A2-2 (service extraction touches the same handler — do them sequentially in one branch).
- **Risks:** taxonomy changes invalidate existing rows; the `other` fallback must stay so old data remains valid.
- **Exclusions:** no LLM provider, no cross-source dedupe, no analytics in this task.

### Task A2-5 · API ergonomics (YI-016, YI-017, YI-060)

- **Scope:** make the API usable by a real client.
- **Relevant files:** `app/routers/*.py`, `app/schemas.py`
- **Required changes:** `limit`/`offset` with hard caps on all list endpoints; `PATCH /sources/{id}` body model for status; `PATCH /opportunities/{id}` for field editing (recording before/after in `review_events`).
- **Acceptance criteria:** caps enforced (422 above maximum); source status works from a JSON body; edited fields visible on read and recorded as an event.
- **Required tests:** pagination boundary tests; source-update test; opportunity-edit test asserting an event row.
- **Dependencies:** A2-3.
- **Exclusions:** breaking-change removal of the query-parameter form without a documented deprecation note in `README.md`.

---

## 3. Agent 3 — Integrations & background processing

### Task A3-1 · Compose and container correctness (YI-100, YI-101, YI-102, YI-103, YI-104)

- **Scope:** make `docker compose up` actually produce a working API with a migrated schema.
- **Relevant files:** `docker-compose.yml`, `Dockerfile`, new `.dockerignore`, `app/routers/health.py`
- **Required changes:** document/automate `.env` bootstrap so `compose config` succeeds; `COPY` `alembic.ini` + `migrations/` into the image and run `alembic upgrade head` at entrypoint after waiting for the DB; add an `api` healthcheck; return **503** from `/health` when the DB is unreachable; non-root `USER`; bind Redis to loopback until it is used.
- **Acceptance criteria:** from a clean checkout, `docker compose up --build` yields a healthy `api` with all three tables present; `/health` returns 200 with the DB up and 503 with it stopped.
- **Required tests:** manual transcript for the two health cases; committed `docker compose config` output.
- **Dependencies:** A2-1 (`alembic.ini`).
- **Risks:** an entrypoint running migrations can race when scaled to multiple replicas; note it and keep to one API replica for now.
- **Exclusions:** no worker service yet, no collector.

### Task A3-2 · Task system (YI-050, YI-051, YI-052, YI-053)

- **Scope:** real background processing with idempotency and failure handling.
- **Relevant files:** `pyproject.toml`, new `app/worker/` (celery app, tasks), `docker-compose.yml` (worker + beat)
- **Required changes:** Celery + Redis broker; tasks call Agent 2's `ingest_post` and later the publisher; idempotency keys; bounded retries with exponential backoff and jitter; dead-letter table for exhausted retries; beat schedule per source cadence; worker health surfaced.
- **Acceptance criteria:** a task that raises transiently is retried and eventually succeeds without creating a duplicate row (proved by counting rows); an exhausted task lands in the dead-letter table with its error.
- **Required tests:** `tests/worker/test_idempotency.py` and `tests/worker/test_retry.py` using eager mode (no broker required in CI).
- **Dependencies:** A2-4 (service layer), A3-1.
- **Exclusions:** no Telegram code in this task — the worker is proven with a stub task first.

### Task A3-3 · Telegram collector (YI-040, YI-041, YI-042)

- **Scope:** authorized-channel collection only.
- **Relevant files:** new `app/integrations/telegram/collector.py`, `app/models.py` (checkpoint/health columns — **coordinate with Agent 2, who owns the models**)
- **Required changes:** Telethon client with credentials from environment secrets only; consume only `Source` rows with `source_type=telegram` and `is_active`; store `external_id` = message id so the existing unique constraint dedupes; per-source `last_collected_message_id`, `last_success_at`, `consecutive_failures`; exponential backoff; never log message contents or credentials; add `*.session*` to `.gitignore` before the first line of code.
- **Acceptance criteria:** a run against a test channel collects new messages once and only once across two consecutive runs; a revoked/blocked account records a failure without crashing the worker; no secret appears in logs (attach a grep of the captured output).
- **Required tests:** unit tests with a faked Telethon client (no network); dedupe assertion across two runs.
- **Dependencies:** A3-2, A2-3 (schema ownership).
- **Risks:** **BLOCKED** without an authorized account and test channel. If inaccessible, implement behind an interface with a fake client and mark live verification as pending — do not simulate success.
- **Explicit exclusions:** no publishing, no joining channels, no scraping of non-allowlisted sources, no user account impersonation.

### Task A3-4 · Publishing pipeline (YI-061, YI-043, YI-062, YI-063, YI-044)

- **Scope:** approved-only, idempotent, attributed publication.
- **Relevant files:** new `app/integrations/telegram/publisher.py`, `app/models.py` (`Publication` — coordinate with Agent 2), formatter service
- **Required changes:** `Publication` table (opportunity_id, channel, external_message_id, status, scheduled_at, published_at, error, attempts); formatter with source attribution; publish only `review_status=approved` (enforced in query **and** guarded); `PUBLISHING_ENABLED=false` default with a destination allowlist; write the record before sending so a crash cannot double-post; scheduling via beat.
- **Acceptance criteria:** an unapproved opportunity cannot be published (assert the guard rejects it); replaying the same publish task twice yields one message and one `Publication` row; with publishing disabled, nothing is sent and the attempt is logged.
- **Required tests:** guard test, idempotency test, disabled-mode test — all with a fake channel.
- **Dependencies:** A3-2, A2-5 (approved-state constraints), A4 (auth on the publish trigger).
- **Risks:** irreversible public side effects. Live verification requires owner authorization for a private test channel; **stop and request approval** before any real send.
- **Exclusions:** no auto-publish without human approval; no publishing of flagged-but-unreviewed content.

---

## 4. Agent 4 — Security & end-to-end QA

### Task A4-1 · Authentication and authorization (YI-080, YI-081) — **P0, must land before any deployment**

- **Scope:** close the anonymous-admin hole without breaking the service-to-service contract.
- **Relevant files:** new `app/security/` (dependency + config), `app/main.py`, all routers, `.env.example`
- **Required changes:** FastAPI security dependency applied at router level; service-to-service credential (API key header or HMAC) plus a reviewer/admin role distinction; refuse to start when `APP_ENV != development` and no credential is configured; protect `/docs` and `/openapi.json` outside development; populate the `actor` seam from A2-3; constant-time comparison; credentials never logged.
- **Acceptance criteria:** every route returns 401 without credentials and 403 for an insufficient role; a configured reviewer can review; a collector credential cannot review; startup fails loudly without credentials in a non-dev env.
- **Required tests:** `tests/security/test_auth_matrix.py` asserting 401/403/200 per route × role; a startup-failure test.
- **Dependencies:** A2-3 (actor seam). Agree the dependency contract with Agent 2 **before** either branch writes code.
- **Risks:** breaking the (nonexistent but imminent) Express integration — version the credential header and document it in `README.md`.
- **Exclusions:** no full user-management UI, no OAuth provider, no session cookies for the API.

### Task A4-2 · Input and surface hardening (YI-082, YI-083, YI-084, YI-085, YI-086)

- **Required changes:** `AnyHttpUrl` + https/allowlist validation for `source_url` (removes the dead `HttpUrl` import); per-key rate limiting; explicit CORS origins; body-size caps; lock file + `pip-audit` + Dependabot; pre-commit secret scanning; drop unused `python-multipart`.
- **Acceptance criteria:** `source_url=http://169.254.169.254/` is rejected; requests over the limit get 429; the audit command reports no known-vulnerable pinned dependency.
- **Required tests:** `tests/security/test_input_validation.py`, rate-limit test.
- **Dependencies:** A4-1.
- **Exclusions:** no WAF/proxy configuration in this repo.

### Task A4-3 · End-to-end workflow verification (Workflows A–F)

- **Scope:** turn the audit's hypotheses into executed evidence.
- **Required changes:** a `tests/e2e/` suite with a disposable database (per-test transactional rollback or a dedicated schema) proving:
  - **A** source → ingest → extract → classify → dedupe → quality → pending review;
  - **B** pending → inspect → edit → approve/reject → audit event recorded;
  - **C** approved → format → publish (fake channel) → publication record; unapproved blocked;
  - **D** same external message ingested twice → one row, 409, one publication;
  - **E** suspicious text → flagged, original preserved verbatim, human review still required;
  - **F** DB/worker failure injected → recorded, retried, no duplicates.
- **Acceptance criteria:** suite passes; `QA_BASELINE.md` §5 rows currently marked UNVERIFIED/BLOCKED are updated with real results.
- **Dependencies:** A2-*, A3-*.
- **Risk:** these tests are the first honest proof the platform works — do not weaken assertions to make them pass.
- **Exclusions:** no live Telegram send, no production data.

---

## 5. Agent 5 — Frontend & admin experience

**Condition:** engage only after A4-1 and A2-5 are merged, and only if a frontend is commissioned.

- **Scope:** React/Vite admin app; review queue, field editing, source management, publish queue, analytics views; explicit loading, error, and empty states; typed client generated from the exported OpenAPI document.
- **Acceptance criteria:** a reviewer can complete Workflow B entirely in the browser against a real API; every list has an empty state and every failure surfaces a message; no wildcard CORS.
- **Dependencies:** A4-1, A4-2 (CORS), A2-5, YI-091.
- **Exclusions:** no marketplace admin features, no financial views of any kind.

---

## 6. File ownership and merge order

| Path | Owner | Others |
|---|---|---|
| `app/models.py`, `migrations/` | **A2** | A3/A4/A5 submit schema requests; never edit directly |
| `app/services/`, `app/routers/ingestion.py`, `app/routers/opportunities.py`, `app/routers/sources.py`, `app/db.py`, `app/config.py`, `alembic.ini` | **A2** | A4 reads, requests the actor seam |
| `app/main.py` | **A4** | A3 requests the `/health` change via a documented patch; final say A4 |
| `app/routers/health.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore` | **A3** | — |
| `app/security/`, `tests/security/`, `tests/e2e/`, `.github/` | **A4** | — |
| `app/worker/`, `app/integrations/` | **A3** | — |
| `frontend/` (new) | **A5** | — |
| `docs/audit/` | **nobody** | append-only history; update statuses, never rewrite findings |
| `README.md` | **A2** initially | each agent updates its own section |

**Merge order:** A2-1 → A2-2 → A2-3 → **A4-1** (security floor before anything network-facing) → A2-4 → A2-5 → A3-1 → A3-2 → A3-3 → A3-4 → A4-2 → A4-3 → A5.

Rebase each branch onto `audit/initial-baseline` before merge; run `ruff` and the full `pytest` suite locally (not just "CI will catch it").

## 7. Definition of done (applies to every task)

- [ ] Behaviour change is covered by a test that fails without the change.
- [ ] `pytest -q` and `ruff check` pass locally, with the output attached to the PR.
- [ ] `alembic check` clean if models changed.
- [ ] No secret, session file, or `.env` in the diff.
- [ ] No financial-boundary code introduced.
- [ ] `docs/audit/FEATURE_MATRIX.md` row statuses updated with real evidence.
- [ ] `README.md` updated if an endpoint or command changed.

## 8. Known blockers to escalate rather than work around

| Blocker | Needs |
|---|---|
| Package installation refused in the audit environment | Owner-run `pip install -e ".[dev]"` or a permitted CI environment |
| GitHub remote absent (`gh` unauthenticated) | Owner runs `gh auth login` and creates the repository, or provides a remote URL |
| Telethon live verification | An authorized Telegram account + a private test channel |
| Publishing verification | Explicit owner approval for a private test channel; **never** a real channel during development |
| Python 3.15 host vs 3.12 target | Owner decision: pin 3.12 locally or widen `requires-python` after testing |
