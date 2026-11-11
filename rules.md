# Yekis Intelligence — Build and Design Rules

This document establishes the mandatory design principles, architectural boundaries, coding standards, security constraints, and build/testing rules for the **Yekis Intelligence** service.

Every contributor, agent, and reviewer must adhere to these rules without exception.

---

## 1. Architectural Boundaries & System Identity

1. **System Purpose:**  
   Yekis Intelligence is an asynchronous content discovery, extraction, risk evaluation, human review, and approval-gated publishing service built for the Yekis freelance marketplace.
2. **Strict Marketplace Core Isolation:**  
   - Yekis Intelligence has **zero access** to the marketplace financial core.
   - It possesses **no credentials, no database models, and no API endpoints** capable of creating, releasing, or modifying escrow transactions, wallet balances, payouts, milestone payments, or contracts.
   - All persistence is restricted to the dedicated `yekis_intelligence` PostgreSQL schema.
3. **Ground Truth Preservation:**  
   - Collected messages (`collected_posts.original_text`) are immutable ground truth. Raw content must be preserved verbatim and never altered or silently discarded.

---

## 2. Design Rules

### 2.1 Backend Architecture & Technology Stack
- **Language:** Python 3.12+ (Docker standard: Python 3.12-slim).
- **Web Framework:** FastAPI with dependency injection for databases and principals.
- **ORM & Data Layer:** SQLAlchemy 2.0 with modern `Mapped[T]` and `mapped_column()` declarative typing.
- **Migrations:** Alembic. Migrations must be strictly additive, idempotent, and support forward upgrade and backward downgrade.
- **Background Pipeline:** Celery 5.4+ with Redis 7 as broker and result backend.
- **Data Validation:** Pydantic v2 schemas (`BaseModel`) with strict type annotations, validators, and bounds.

### 2.2 Database & Data Modeling Standards
- **Timestamps:** All timestamp columns must use `DateTime(timezone=True)` with UTC defaults (`datetime.now(UTC)`). Bare, naive timestamps are prohibited.
- **Indexing:** Explicit indexes are required on high-frequency query fields:
  - `Opportunity.review_status`
  - `CollectedPost.content_hash`
  - `Source.source_type`
- **Natural Constraints:**
  - Duplicate posts per source are prevented by `UniqueConstraint("source_id", "external_id", name="uq_source_external_post")`.
  - Duplicate publications per opportunity and channel are prevented by `UniqueConstraint("opportunity_id", "channel", name="uq_publication_opportunity_channel")`.
  - One-to-one relationship between `CollectedPost` and `Opportunity` (`collected_post_id` foreign key unique).
- **Eager vs. Lazy Loading:** Relationships frequently consumed together (e.g. `Opportunity.post`) must use `lazy="joined"` to prevent N+1 query patterns.

### 2.3 Content Extraction & LLM Providers
- **Provider Protocol:** Extraction is encapsulated behind the `ExtractionProvider` protocol (`extract(text: str) -> ExtractedOpportunity`).
- **Supported Providers:**
  1. `rules` (default): Fast, deterministic regex extraction with noun/verb disambiguation and token-boundary matching.
  2. `gemini`: Native Google AI Studio Generative Language API adapter (`models/{GEMINI_MODEL}:generateContent`).
  3. `llm`: OpenAI-compatible `/chat/completions` API adapter.
- **Untrusted Content Encapsulation:** Post content sent to LLM providers must be enclosed in explicit `<untrusted_content>` XML tags with system prompts instructing the model to treat input as data, never instructions.
- **Closed Schema Validation:** Model outputs are strictly parsed into `LLMOpportunity` with `model_config = ConfigDict(extra="forbid")`. Unrecognized fields, mismatched currency/amounts, or out-of-bounds numbers are rejected.
- **Deterministic Risk & Safety Merge:**
  - Deterministic risk heuristics always run in parallel.
  - Risk flags from deterministic analysis are merged into the LLM output.
  - If `upfront_payment` is flagged, `budget_amount` and `budget_currency` are suppressed to `None` to prevent fraudulent compensation from being treated as verified.
  - Provider timeouts, HTTP errors, or malformed JSON must cleanly degrade to `rules_fallback` without losing the ingested post.
  - **LLM output must NEVER automatically approve or publish content.**

### 2.4 Human Review & Append-Only Audit Trail
- **Allowed States:** `pending_review`, `needs_review`, `approved`, `rejected`.
- **Enforced Transitions:** Illegal transitions (e.g. directly transitioning from `rejected` without re-review) return HTTP 409 Conflict.
- **Idempotency:** Repeating the same review decision returns the existing state without creating duplicate audit events.
- **Append-Only Audit Events:** Every status change and field edit creates a row in `review_events` containing:
  - `opportunity_id`
  - `actor` (principal name)
  - `event_type` (`status_change` or `field_edit`)
  - `from_status` and `to_status`
  - `note` (reviewer justification)
  - `diff` (before-and-after dictionary of altered fields)
  - `created_at` (UTC timestamp)
- Audit rows are immutable; the API provides no mutation or deletion endpoints for `review_events`.

### 2.5 Telegram Publishing
- **High-Risk Gate:** `PUBLISHING_ENABLED` defaults to `false`. It must fail closed if the channel allowlist `PUBLISHING_ALLOWED_CHANNELS` is empty.
- **Send-Time Verification:** Approval status and channel allowlists are evaluated when scheduling **and re-checked at send-time**. Rejecting an opportunity cancels any scheduled broadcast.
- **Mandatory Attribution:** Broadcast messages must include source attribution (`Source: <name>` and source URL). Attribution is appended after message truncation to ensure it cannot be cut off.
- **At-Most-Once Sending Protection:** The database row status is committed to `sending` before network dispatch. If an unhandled crash occurs mid-send, the row is **not** automatically retried; it transitions to `publication_unknown` for human verification.

### 2.6 Authentication & Authorization (RBAC)
- **Credential Transport:** Requests must pass the API key in `X-API-Key: <key>` or `Authorization: Bearer <key>`.
- **Security:**
  - Raw keys are never stored in databases, logs, or request state.
  - Key lookup uses SHA-256 fingerprints with constant-time equality comparisons (`hmac.compare_digest`).
  - Keys must be at least 16 characters in length.
- **Permission Matrix:**

| Capability | Admin | Reviewer | Collector | Analyst | Anonymous |
|---|:---:|:---:|:---:|:---:|:---:|
| Manage Sources (Create/Patch/Status) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Read Sources | ✅ | ✅ | ✅ | ✅ | ❌ |
| Ingest Content (`/ingestion/manual`) | ✅ | ❌ | ✅ | ❌ | ❌ |
| Read Opportunities & Posts | ✅ | ✅ | ✅ | ✅ | ❌ |
| Edit Opportunity Fields | ✅ | ✅ | ❌ | ❌ | ❌ |
| Review (Approve/Reject/Needs Review) | ✅ | ✅ | ❌ | ❌ | ❌ |
| Publish Opportunities | ✅ | ✅ | ❌ | ❌ | ❌ |
| Read Analytics & Insights | ✅ | ✅ | ❌ | ✅ | ❌ |
| Public Health (`/health`) | ✅ | ✅ | ✅ | ✅ | ✅ |

- Outside development (`APP_ENV != "development"`), `/docs`, `/redoc`, and `/openapi.json` are disabled.
- The service must refuse to start outside development if `AUTH_API_KEYS` is empty.

### 2.7 Admin Mission Control UI
- **Deployment:** Served directly by FastAPI at `/admin/` with no Node.js/npm build step required.
- **Design System:** Responsive dark mode (`ui-sans-serif`, emerald accent, amber warning, red danger, blue info).
- **Safety:** All dynamic values (post text, titles, notes, URLs) must be sanitized with HTML entity escaping (`esc()`) before DOM insertion. Inline `eval()` or unescaped HTML interpolation is forbidden.
- **Features:** Must provide five functional tabs:
  1. Review Queue (with split verbatim vs extracted view and audit history drawer)
  2. Ingest Post (manual submission console with test presets)
  3. Sources Management (health indicators and status toggles)
  4. Publications (broadcast tracker)
  5. Analytics & Insights (KPI cards, skills demand, compensation stats, 30-day activity trends)

---

## 3. Build & Development Rules

### 3.1 Dependency Management
- **Single Source of Truth:** `pyproject.toml` is the sole specification for dependencies.
- **Runtime Dependencies:** All packages required inside the production Docker image (including transitive driver/transport requirements such as `packaging` for Kombu Redis) must be declared in `project.dependencies`.
- **Dev Dependencies:** Testing and linting tools (`pytest`, `ruff`) belong under `project.optional-dependencies.dev`.

### 3.2 Containerization & Security
- **Unprivileged Runtime:** Containers must execute as a non-root system user (`appuser`, UID 1000). The Celery worker must never run as superuser.
- **Volume Isolation:** Data directories (`/app/var`) must have proper ownership assigned to `appuser`.
- **Service Composition:** Docker Compose orchestrates five interconnected services:
  - `api`: FastAPI application (port 8100 -> 8000)
  - `worker`: Celery task worker (concurrency 1, late acks)
  - `beat`: Celery beat periodic scheduler
  - `db`: PostgreSQL 16 Alpine (port 5440 -> 5432)
  - `redis`: Redis 7 Alpine (port 6382 -> 6379, loopback bound)

### 3.3 Code Quality & Linting
- **Linter & Formatter:** Ruff.
- **Configuration:**
  - Target: Python 3.12 (`py312`).
  - Line length: 100 characters.
  - Rules enabled: `E`, `F`, `I` (isort), `UP` (pyupgrade), `B` (bugbear), `ISC` (implicit string concat).
- **Zero-Tolerance Quality Gates:**
  - `ruff check app tests migrations scripts` must exit with 0 errors.
  - `python -m compileall -q app tests migrations scripts` must compile cleanly with 0 errors.

### 3.4 Automated Testing & Acceptance Scenarios
- **Test Runner:** `pytest`.
- **Coverage Requirement:** All unit, router, security, and worker configuration tests must maintain a 100% pass rate.
- **Phase L Acceptance Scenarios:** Any change touching the pipeline must pass all 10 end-to-end scenarios in `tests/test_end_to_end_scenarios.py`:
  1. Complete ingestion pipeline
  2. Duplicate ingestion prevention
  3. Risky opportunity screening & budget suppression
  4. Review workflow & audit history
  5. RBAC role restrictions
  6. Approval-gated publishing with attribution
  7. Scheduled publication cancellation on rejection
  8. Worker crash recovery & idempotency
  9. LLM failure fallback to rules
  10. Cross-system analytics database consistency
- **Test Isolation:** Tests must use SQLite in-memory databases with session fixtures and mock transports for external HTTP/Telegram calls. Tests must never perform network requests to live Telegram channels or public third-party services.

### 3.5 Git & Secret Hygiene
- **Branch Protection:** Never commit directly to `main`. Use feature or verification branches (e.g. `qa/system-integration-verification`).
- **Secret Redaction:**
  - `.env` and `*.session` files must remain strictly listed in `.gitignore`.
  - API keys, hashes, and session strings must never be committed to Git or printed in application logs.
