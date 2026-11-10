# Security Baseline — Yekis Intelligence

**Scope:** preliminary review of the code as it exists on `audit/initial-baseline`. No destructive testing, no exploitation attempts, and no public exposure were performed. Findings are based on source inspection plus the executed commands recorded in `QA_BASELINE.md`.

**Severity scale:** Critical (blocks any network deployment) · High · Medium · Low · Info

## 1. Findings

### SEC-01 — No authentication on any endpoint (Critical)

- **Components:** all of `app/`, `app/main.py`
- **Evidence:** `grep -rniE "authenticat|authoriz|jwt|api_key|bearer|oauth" app/` returns nothing. No `Depends` security scheme, no middleware, no `securitySchemes` in any route. All 9 application routes plus `/docs`, `/docs/oauth2-redirect`, `/redoc`, and `/openapi.json` are anonymous (`REPOSITORY_MAP.md` §4).
- **Impact:** anyone who can reach the port can create sources, inject content, list all collected content, and approve or reject opportunities. The review workflow — the platform's only human trust control — is unauthenticated, so its output cannot be trusted for publishing.
- **Remediation:** service-to-service authentication at minimum (static API key or HMAC signed requests from the Yekis Express backend), with a path to admin identity; protect `/docs` outside development; add a startup assertion that refuses to boot without credentials when `APP_ENV != development`.
- **Blocking:** yes — staging and production.

### SEC-02 — No authorization or role model (Critical)

- **Components:** data model, all mutating routes
- **Evidence:** no user, role, or permission model in `app/models.py`; no `reviewed_by` field; no ownership scoping.
- **Impact:** even with authentication added, every caller would be fully privileged. Reviewer actions are unattributable, so an audit trail is impossible.
- **Remediation:** introduce role distinction (collector/service vs reviewer/admin) and record the acting identity on every state change.

### SEC-03 — Silent data loss of reviewer notes (High)

- **Components:** `app/schemas.py:49-51` (`ReviewUpdate.reviewer_note`), `app/routers/opportunities.py:23-31`, `app/models.py`, `migrations/versions/0001_initial.py`
- **Evidence:** `reviewer_note` is declared with `Field(max_length=2000)` and accepted by the endpoint, but `grep` shows it in `schemas.py` only — no column exists in the model or the migration, and the handler never reads `payload.reviewer_note`.
- **Impact:** a reviewer's justification for a rejection — the record a moderator would need later — is validated, acknowledged with 200, and discarded. This is a trust and accountability defect, and it silently violates the client's expectations.
- **Remediation:** either add `reviewer_note` (Text) plus a migration, or remove the field from the schema so the API stops lying. Prefer adding it, together with SEC-02.

### SEC-04 — No audit log for administrative actions (High)

- **Components:** `app/models.py`, review router
- **Evidence:** only `reviewed_at` is stored. No history table, no prior-status record, no actor. Status can be flipped repeatedly and the previous decision is lost.
- **Impact:** requirements "administrative action history" (intended responsibilities §5) and any post-incident investigation are unsatisfiable; an erroneously approved item cannot be traced to a decision.
- **Remediation:** append-only `review_events` (or general `audit_log`) table written in the same transaction as the status change.

### SEC-05 — Container runs as root and ships no least-privilege user (Medium)

- **Components:** `Dockerfile`
- **Evidence:** no `USER` directive; base `python:3.12-slim`; default root.
- **Impact:** any RCE in the API (see SEC-08/09) gains root inside the container and, with a mounted volume or docker socket, wider access.
- **Remediation:** create a non-root user, `USER app`, and drop capabilities in compose.

### SEC-06 — No `.dockerignore`; build context can include secrets and a virtualenv (Medium)

- **Components:** repository root
- **Evidence:** no `.dockerignore` file exists, while `.venv/`, `__pycache__/`, and (after any local setup) `.env` sit in the same directory that `docker build .` sends as context. The Dockerfile copies only `pyproject.toml`, `README.md`, and `app/`, so nothing leaks into the image *today*, but the context is transmitted to the daemon and would flood CI logs/caches.
- **Impact:** accidental future leakage when a `COPY . .` is introduced; large, slow builds.
- **Remediation:** add `.dockerignore` with `.env`, `.venv`, `__pycache__`, `.git`, `tests`, `docs`.

### SEC-07 — Unvalidated `source_url` enables stored SSRF-class risk once collectors exist (Medium, latent)

- **Components:** `app/schemas.py` (`SourceCreate.source_url`), `app/routers/sources.py`
- **Evidence:** `source_url: str = Field(min_length=3, max_length=1000)` — a plain string. `HttpUrl` is imported in `schemas.py` but **unused** (dead import), showing URL validation was intended and abandoned.
- **Impact:** today the value is only stored, so risk is latent. As soon as the M2 website/RSS collector dereferences `source_url`, an admin-supplied or attacker-supplied source can target `http://169.254.169.254/`, `file://`, or internal services (metadata theft, internal port scanning). Combined with SEC-01, an anonymous caller can register such a source.
- **Remediation:** validate with `AnyHttpUrl` and an https-only + host allowlist policy now; enforce egress restrictions in the collector.

### SEC-08 — Unbounded client input reaches storage and extraction (Medium)

- **Components:** `app/schemas.py` (`ManualIngestion.original_text`), `app/routers/ingestion.py`
- **Evidence:** `min_length=10, max_length=30000` per request, but no per-source rate limit, no payload quota, and no pagination on `GET /ingestion/posts`. `POST` also writes 30 KB to `original_text` *and* a 5 KB `description`.
- **Impact:** trivially amplified storage exhaustion and response bloat; unbounded list endpoints return the whole table into memory.
- **Remediation:** rate limiting per source/API key, pagination with hard caps, and request body size limits at the proxy.

### SEC-09 — `count`-style injection surface mitigated by ORM, but no input hardening beyond Pydantic (Medium)

- **Components:** `app/routers/opportunities.py`
- **Evidence:** `GET /opportunities?status=` interpolates a **raw string** into `Opportunity.review_status == status`. SQLAlchemy parameterizes it, so classic SQL injection is not reachable — verified by inspection: no `text()`, `f"..."`, or string concatenation builds SQL except the literal `SELECT 1` in `health.py`.
- **Impact:** Low for injection; Medium for input validation — an arbitrary 200-char string is accepted and used as a filter, and `review_status` itself is an unconstrained `String(30)` (no CHECK constraint, no enum), so vocabulary drift and typo'd statuses silently pollute the review queue.
- **Remediation:** constrain the query parameter with a `Literal`/enum matching a DB CHECK constraint.

### SEC-10 — Error handling leaks nothing today, but hides everything (Low)

- **Components:** `app/routers/health.py:10-15`, unguarded `db.commit()` calls
- **Evidence:** `except Exception:` in `/health` returns `{"status":"degraded"}` with no logging — no traceback, no driver message, nothing. Elsewhere, unhandled `IntegrityError` (the duplicate race) yields FastAPI's generic 500 with a stack trace **only if** `debug` were enabled; `FastAPI(...)` is constructed without `debug`, so no traceback is returned to clients.
- **Impact:** no client-facing leakage (good), but outages are undiagnosable (bad), and failed commits leave the pooled session in an aborted transaction because `get_db` closes without `rollback()`.
- **Remediation:** log exceptions server-side with correlation IDs; add `try/except IntegrityError` → 409; add `db.rollback()` in the dependency's `finally`.

### SEC-11 — Secrets hygiene: baseline is correct, with two gaps (Low)

- **Evidence (good):** `.gitignore` excludes `.env`, `.venv/`, caches, `dist/`, `*.egg-info`. No `.env` file exists in the tree. No credential, token, session string, or key is committed anywhere (`grep` for telegram/bot token patterns finds nothing because no integration exists).
- **Gaps:** (a) `.env.example` ships a real development database password (`yekis_intel_dev`) that is also hardcoded as the *default* in `app/config.py:8` and duplicated in `docker-compose.yml:32` — three copies of one credential, none of them obviously "example only", and the application will happily run against that default if `.env` is missing; (b) no secret-scanning or pre-commit guard exists.
- **Remediation:** remove the default from `config.py` so a missing `DATABASE_URL` fails loudly; mark `.env.example` values as non-production; add a secret scanner to CI.

### SEC-12 — CORS and rate limiting absent (Low → Medium before frontend)

- **Evidence:** no `CORSMiddleware`; no limiter of any kind.
- **Impact:** pre-frontend this is inert. If a browser client is ever pointed at the API without CORS configuration, someone will "fix" it with `allow_origins=["*"]` while credentials are in play — a foreseeable regression.
- **Remediation:** configure explicit origins per environment when M5 starts, together with SEC-08's limiter.

### SEC-13 — Dependency and supply-chain state unverified (Low, unverified)

- **Evidence:** `pyproject.toml` pins ranges (`fastapi>=0.115,<1.0`, `sqlalchemy>=2.0,<3.0`, `psycopg[binary]>=3.2,<4.0`, `alembic>=1.13,<2.0`, `pydantic-settings>=2.5,<3.0`, `python-multipart>=0.0.9`, dev: `pytest>=8.0`, `httpx>=0.27`, `ruff>=0.6`). No lock file, no hashes, no `pip-audit`/Dependabot config, no CI.
- **Impact:** builds are not reproducible, and a vulnerable or yanked release can enter unnoticed. `python-multipart` is declared but unused (no form endpoints), enlarging the attack surface for nothing.
- **Remediation:** add a lock file, `pip-audit` in CI, Dependabot, and drop unused dependencies.

### SEC-14 — Unrelated databases/services are running on the developer host (Info)

- **Evidence:** `docker ps` shows `yekis-postgres-1` (:5433) and `yekis-redis-1` (:6379) from the separate marketplace project.
- **Impact:** low if left alone; dangerous if a future agent points this service at a shared database or resets it. None were touched during this audit.
- **Remediation:** in the handoff, restate that Intelligence must use its own database and never the marketplace's.

## 2. Financial boundary verification

| Control | Status | Evidence |
|---|---|---|
| No escrow release | PASS | no escrow/wallet/payout/contract/invoice model, route, or dependency exists |
| No payout initiation | PASS | no payment SDK or HTTP client in dependencies |
| No wallet mutation | PASS | no financial tables |
| No milestone/contract state change | PASS | not modelled |
| No shared marketplace DB access | PASS | separate `database_url`, own migrations (`docs/ARCHITECTURE.md`) |

The financial-independence boundary is **intact** and must remain an explicit non-goal. Note that it currently holds partly because so little is implemented — SEC-07 and a future website collector are the first places it could be eroded (e.g. fetching marketplace endpoints with stored credentials).

## 3. Deployment blockers

The service must not be exposed to any untrusted network until all of these are closed:

1. **SEC-01** authentication on every route (including `/docs`).
2. **SEC-02** authorization/identity on review and publish actions.
3. **SEC-03/SEC-04** reviewer note persistence and an audit trail — required before any approval decision has real consequences.
4. **SEC-11** removal of the hardcoded default credential path.
5. Infrastructure: working migrations (`alembic.ini`), a valid compose environment, and an `api` healthcheck — none of which currently function (`QA_BASELINE.md` §2).

## 4. Explicitly out of scope

No penetration testing, fuzzing, dependency CVE scan, TLS/reverse-proxy review, secret scanning run, or container escape testing was performed. No service was started or exposed. These belong to Agent 4.
