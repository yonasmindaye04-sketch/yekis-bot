# Yekis Intelligence — Build and Design Rules

This document establishes the mandatory engineering standards, design principles, architectural boundaries, database discipline, security protocols, and build/testing rules for the **Yekis Intelligence** service.

These rules incorporate and harmonize standards from across the Yekis ecosystem (including the core Yekis Freelance Marketplace, Afro Suite, and Asella platforms).

Every contributor, agent, and reviewer must strictly adhere to these rules without exception.

---

## 1. Architectural Boundaries & System Identity

### 1.1 Service Purpose
Yekis Intelligence is an asynchronous content discovery, extraction, risk evaluation, human review triage, and approval-gated publishing service for freelance opportunities.

### 1.2 Strict Marketplace Core Isolation
- **Zero Core Financial Access**: Yekis Intelligence has zero access to the parent marketplace financial core (`yekis`).
- **No Financial Capabilities**: It possesses no credentials, no database models, and no API endpoints capable of creating, releasing, or modifying escrow transactions, wallet balances, payouts, milestone payments, disputes, or contracts.
- **Independent Schema & Ports**:
  - Marketplace Core: Express/TypeScript/Prisma on PostgreSQL port `5433`, BullMQ on Redis port `6379`, MinIO S3 on `9000/9001`.
  - Yekis Intelligence: FastAPI/Python 3.12/SQLAlchemy on PostgreSQL port `5440` (schema `yekis_intelligence`), Celery on Redis port `6382`, API on port `8100`.
  - Port isolation must be preserved; never point test or runtime services to marketplace ports.
- **Integration Boundary**: Yekis Intelligence discovers and verifies public opportunities. Approved opportunities are broadcast to designated channels with attribution links directing freelancers into the Yekis marketplace.

### 1.3 Ground Truth Preservation
- Raw collected messages (`collected_posts.original_text`) are immutable ground truth.
- Ingested text, external message IDs, and source URLs must be preserved verbatim. They must never be mutated, overwritten, or silently discarded.

---

## 2. Strict Zero-Emoji & Visual Presentation Policy

### 2.1 Universal Zero-Emoji Standard
- **No Emojis Anywhere**: Never use emoji characters anywhere in the user interface, HTML strings, CSS content, JavaScript/TypeScript code, Python files, user-facing UI, notification text, Telegram bot messages, system logs, buttons, dialogs, or documentation.
- **Visual Indicators**: Use clean typography, SVG glyphs, Lucide icons, FontAwesome icons, or plain text if a visual marker is needed.
- **Corporate Tone**: All automated alerts, reviewer notices, and broadcast messages must maintain a professional, clean corporate tone without emoji clutter.

---

## 3. Badges, Indicators & Visual Styling Standards

### 3.1 Bold Colored Text Only for Indicators
- **No Background Pills or Borders**: All indicators, tags, and status badges (e.g. `pending_review`, `needs_review`, `approved`, `rejected`, collector health, role indicators) must use **bold colored text only**.
- **Prohibited Patterns**:
  - No low-opacity background tints (e.g. `rgba(x, x, x, 0.12)`).
  - No background colors or borders on badges.
  - No dot pseudo-elements (`::before` / `.dot`).
  - No pill-shaped containers with rounded colored borders.
- **Correct Pattern**:
  - Bold green text for `approved`, `active`, `published`, `healthy`.
  - Bold red text for `rejected`, `inactive`, `failed`.
  - Bold amber/warn text for `needs_review`, `sending`, `risk`.
  - Bold blue text for `pending_review`, `scheduled`, roles.

### 3.2 No Decorative Tag Badges on Section Headings
- Do not place `<Badge>` components or tag-style pills beside section or page headings.
- *Wrong*: `<h2>Review Queue <Badge>Live</Badge></h2>`
- *Right*: `<h2>Review Queue</h2>` with a descriptive `<p>` below it.

### 3.3 No Icon Background Tiles
- Icons render as **bare colored glyphs** — no rounded background squares, no tinted tiles, and no shadows behind icons. Surfaces remain clean with bare glyphs.

---

## 4. UI/UX, Theming & Responsiveness Standards

### 4.1 Theme Parity & Dark/Light Mode
- **Full Theme Support**: Both dark mode (`color-scheme: dark` or `[data-theme="dark"]`) and light mode must be supported via CSS custom properties (`var(--bg)`, `var(--panel)`, `var(--line)`, `var(--text)`, `var(--text-muted)`).
- **No Hardcoded Hex in Components**: Never hardcode `#ffffff` or dark colors inside inline component styles; always reference CSS theme tokens.
- **Theme-Conditional CSS Parity Rule**:
  - Theme-conditional CSS may **only ever alter** `color`, `background`, `border-color`, `box-shadow`, and `opacity`.
  - Layout and geometry properties (`width`, `height`, `padding`, `margin`, `gap`, `display`, `grid`, `flex`, `font-size`, `font-weight`, `line-height`, `letter-spacing`, `border-radius`, position offsets) must be **strictly theme-invariant** — defined once in un-themed base styles.
  - Never remove a border in one theme (`border: 0`); recolor it at the same width instead.
- **Single Theme Toggle**: The theme toggle exists solely in the primary header/sidebar navigation. Never place duplicate toggles in footers or secondary panels.

### 4.2 Form Controls & Date Filters
- **Compact Date Range Filters**: All date-range filter inputs (`type="date"`) inside toolbars and filter bars must be wrapped in a flex group with explicit width (e.g. `width: 150px` or `w-[150px]`) on each input to prevent them from stretching full-width.

### 4.3 Mobile Compaction & Responsive Fluidity
- **Fluid Breakpoints**:
  - Mobile Phones: `320px` – `640px` (`sm`)
  - Tablets: `641px` – `1023px` (`md` / `lg`)
  - Laptops & Desktops: `1024px` – `1280px` (`lg` / `xl`)
  - Ultra-wide Displays: `> 1280px` (`2xl`)
- **KPI Stat Cards**: Render two per row at `<= 640px` with compact typography and padding — never one giant vertical card per row. Never set inline `gridTemplateColumns` that defeats responsive CSS.
- **Action Buttons**: Share one row (`flex: 1` or grid) at mobile sizes rather than stacking into giant vertical blocks.
- **Zero Horizontal Overflow**: Viewport roots must maintain `overflow-x: hidden`, flex containers must set `min-w-0`, and long text strings must wrap or truncate gracefully.
- **Touch-Friendly Hit Targets**: Interactive elements (buttons, inputs, tabs, triggers) must maintain a minimum target size of `44x44px` on touch viewports.
- **Modals & Drawers**: Modals must fit mobile viewports (`max-h-[90vh]`, `overflow-y-auto`, compact mobile padding).
- **Reachability Rule**: Every feature, queue, report, and admin tool must be reachable from UI navigation (tabs/menus/sidebar). Unreachable or orphaned routes are considered broken.

---

## 5. Database, SQL & Migration Discipline

### 5.1 Sequential Codes Are MAX-Based
- Human-facing sequential identifiers (`OPP-00001`, `PUB-00001`, `SRC-00001`) must always be generated using `MAX(trailing digits) + 1`:
  - **Never use `COUNT(*) + 1`**: deleting or voiding a record reassigns the same code to the next insert and causes duplicate key collisions.
  - Seed and test scripts must continue numbering from the existing MAX in the table, never restart at 1.
  - Prefix parsers must extract numeric digits and support both hyphenated (`OPP-00042`) and non-hyphenated (`OPP00042`) formats.

### 5.2 SQL Column Qualification & Scope Discipline
- **Qualify Every Column in Joins**: Every column in queries with JOINs must be table-qualified (e.g. `WHERE o.status = :status`, not `WHERE status = :status`) to eliminate ambiguous column crashes when joined tables share column names (`id`, `created_at`, `status`).
- **Count Subqueries**: Side queries and count summaries must never reference table aliases defined only in the outer query.
- **Scoped Writes**: Every `UPDATE` and `DELETE` statement must have an explicit scope filter. Unscoped modifications are strictly prohibited.
- **Historical Value Immutability**: Historical financial values, compensation snapshots, original post texts, and audit diffs must never be modified retrospectively. Rate or currency adjustments apply only prospectively.

### 5.3 Database Modeling Standards
- **UTC Timestamps**: All timestamp columns must use `DateTime(timezone=True)` with UTC defaults (`datetime.now(UTC)`). Naive timestamps are forbidden.
- **Declarative ORM**: SQLAlchemy 2.0 declarative mapping with `Mapped[T]` and `mapped_column()`.
- **Relationships & Lazy Loading**: Frequently co-accessed relationships (e.g. `Opportunity.post`) must use `lazy="joined"` to prevent N+1 query overhead.
- **Natural Constraints**:
  - `UniqueConstraint("source_id", "external_id", name="uq_source_external_post")` prevents duplicate posts per source.
  - `UniqueConstraint("opportunity_id", "channel", name="uq_publication_opportunity_channel")` prevents duplicate channel broadcasts.
  - One-to-one link between `CollectedPost` and `Opportunity`.
- **Indexing**: Required on `Opportunity.review_status`, `CollectedPost.content_hash`, and `Source.source_type`.

### 5.4 Migrations & Seeds
- **Alembic Migrations**:
  - Migrations must be strictly idempotent (`IF NOT EXISTS`, conditional column/index additions).
  - Every migration must support both forward upgrade and backward downgrade.
  - Check constraints and ENUMs must cover all valid values inserted by the application.
- **Re-runnable Seeds**: Seed scripts must check for existing records and continue numbering from existing MAX values.

---

## 6. Authentication, Authorization & Security

### 6.1 Authentication Mechanism
- **Header Transport**: API keys are passed via `X-API-Key: <key>` or `Authorization: Bearer <key>`.
- **Key Cryptography**:
  - Raw keys are never stored in databases, logs, or request state.
  - Lookup uses SHA-256 fingerprints with constant-time equality comparisons (`hmac.compare_digest`).
  - Keys must be at least 16 characters in length.
- **Fail-Closed Startup**: The service must refuse to start outside development if `AUTH_API_KEYS` is empty.
- **Documentation Shielding**: Outside development (`APP_ENV != "development"`), `/docs`, `/redoc`, and `/openapi.json` are disabled.

### 6.2 Role-Based Access Control (RBAC) Matrix

| Capability | Admin | Reviewer | Collector | Analyst | Anonymous |
|---|:---:|:---:|:---:|:---:|:---:|
| Manage Sources (Create/Patch/Status) | Allowed | Denied | Denied | Denied | Denied |
| Read Sources | Allowed | Allowed | Allowed | Allowed | Denied |
| Ingest Content (`/ingestion/manual`) | Allowed | Denied | Allowed | Denied | Denied |
| Read Opportunities & Posts | Allowed | Allowed | Allowed | Allowed | Denied |
| Edit Opportunity Fields | Allowed | Allowed | Denied | Denied | Denied |
| Review (Approve/Reject/Needs Review) | Allowed | Allowed | Denied | Denied | Denied |
| Publish Opportunities | Allowed | Allowed | Denied | Denied | Denied |
| Read Analytics & Insights | Allowed | Allowed | Denied | Allowed | Denied |
| Public Health Check (`/health`) | Allowed | Allowed | Allowed | Allowed | Allowed |

### 6.3 Session & Transport Security
- **Inactivity Timeout**: Client-side sessions must enforce a 30-minute inactivity timeout that clears authentication state.
- **401 Response Handling**: Immediate clearance of authentication state on HTTP 401.
- **Strict CSRF & Origin Verification**: Allowed origins must be matched **exactly** against configured origins — never using `startsWith`, which permits attacker subdomain attacks (e.g. `domain.com.attacker.io`).
- **Telegram WebApp Cryptography**: Incoming Telegram requests must validate `initData` using HMAC-SHA256 with the bot token secret. Never trust client-supplied user IDs without cryptographic verification.
- **Secret Redaction**: Zero committed credentials. Secret scanners must verify that `.env`, `*.session`, and `.session-journal` files are never tracked in Git.

---

## 7. Pipeline, Extraction & Publishing Guarantees

### 7.1 Content Ingestion & Deduplication
- Content deduplication uses SHA-256 content hashes and source external ID constraints.
- Re-submitting identical content from the same source returns the existing record idempotently without creating duplicates.

### 7.2 Extraction & LLM Provider Safety
- **Provider Protocol**: Extraction is abstracted behind the `ExtractionProvider` protocol (`extract(text: str) -> ExtractedOpportunity`).
- **Supported Providers**: `rules` (deterministic regex), `gemini` (Google AI Studio Generative Language API), and `llm` (OpenAI-compatible `/chat/completions`).
- **Prompt Injection Defense**: Untrusted post text must be enclosed within `<untrusted_content>` XML tags with explicit system prompts directing models to treat input as data, never instructions.
- **Closed Schema Validation**: LLM outputs must be parsed into Pydantic models with `model_config = ConfigDict(extra="forbid")`. Unrecognized fields or malformed numbers are rejected.
- **Deterministic Risk Heuristics**:
  - Deterministic risk analysis runs on every post.
  - Risk heuristics detect upfront fees, suspicious payment methods, unrealistic compensation, and external contact evasion.
  - If `upfront_payment` is flagged, `budget_amount` and `budget_currency` are suppressed to `None` to prevent fraudulent compensation from being treated as verified.
  - **LLMs must NEVER automatically approve or publish content.**
  - Provider timeouts, HTTP errors, or schema failures fall back gracefully to `rules_fallback` without losing the ingested post.

### 7.3 Human Review & Append-Only Audit Trail
- **Allowed States**: `pending_review`, `needs_review`, `approved`, `rejected`.
- **Enforced Transitions**: Illegal transitions return HTTP 409 Conflict.
- **Idempotency**: Submitting an identical decision returns the existing state without creating duplicate audit rows.
- **Append-Only Audit Trail**: Every status change and field edit creates an immutable record in `review_events` containing:
  - `opportunity_id`
  - `actor` (principal name)
  - `event_type` (`status_change` or `field_edit`)
  - `from_status` and `to_status`
  - `note` (reviewer justification)
  - `diff` (before-and-after dictionary of modified fields)
  - `created_at` (UTC timestamp)
- The API provides no mutation or deletion endpoints for `review_events`.

### 7.4 Telegram Publishing Protocol
- **High-Risk Gate**: `PUBLISHING_ENABLED` defaults to `false`. Publishing fails closed if the destination allowlist `PUBLISHING_ALLOWED_CHANNELS` is empty.
- **Send-Time Verification**: Approval status and channel allowlists are verified when scheduling and **re-verified at send-time**. Rejecting an opportunity cancels any scheduled broadcast.
- **Mandatory Attribution**: Broadcast messages must retain original source attribution (`Source: <name>` and source URL). Attribution is appended after message sizing so truncation cannot remove it.
- **At-Most-Once Sending Protection**: The database row status transitions to `sending` before network dispatch. If an unhandled network error or process termination occurs mid-send, the publication is not automatically retried; it transitions to `publication_unknown` for human verification.

---

## 8. Build, Testing & Quality Gates

### 8.1 Runtime & Dependencies
- **Runtime**: Python 3.12+ (Docker image: `python:3.12-slim`).
- **Non-Root Execution**: Containers execute under an unprivileged user (`appuser`, UID 1000). The Celery worker must never run with superuser privileges.
- **Single Source of Truth**: All runtime dependencies belong in `pyproject.toml` under `project.dependencies`. Development and testing tools belong under `project.optional-dependencies.dev`.

### 8.2 Code Quality & Static Analysis Gates
- **Linter**: Ruff configured for Python 3.12 (`py312`), line length 100, rules `E`, `F`, `I` (isort), `UP` (pyupgrade), `B` (bugbear), `ISC` (implicit string concat).
- **Mandatory Pre-Commit Checks**:
  1. `ruff check app tests migrations scripts` must report 0 errors.
  2. `python -m compileall -q app tests migrations scripts` must compile cleanly with 0 errors.

### 8.3 Automated Testing & Acceptance Gates
- **Test Runner**: `pytest`.
- **Pass Rate**: 100% of all unit, router, security, worker, and integration tests must pass.
- **Phase L Acceptance Scenarios**: All changes affecting the pipeline must pass all 10 end-to-end scenarios in `tests/test_end_to_end_scenarios.py`:
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
- **Test Isolation**: Tests must run against SQLite in-memory databases with session fixtures and mock transports for external HTTP and Telegram networks. Tests must never make live calls to external services.

### 8.4 Git & Release Hygiene
- **Branch Protection**: Never commit directly to `main`. Use structured feature or QA branches (e.g. `qa/system-integration-verification`).
- **Clean Diffs**: Avoid touching unrelated files or reformating code outside the scope of the task.
- **Documentation Parity**: Whenever endpoints, models, configuration, or workflows change, update `README.md`, `rules.md`, and documentation under `docs/` in the same commit.

---

## Compliance Status (maintained)

The standards in this document are enforced in CI and by the local quality gates. Keep this section current when behaviour changes.

| Rule area | Enforcement | Status |
|---|---|---|
| 2. Zero emoji | `tests/test_ui_compliance.py::test_repository_contains_no_emoji` scans `app/`, `docs/`, `scripts/`, `tests/`, `migrations/`, `README.md`, and `rules.md` for pictographic characters | Enforced, 0 hits |
| 3.1 Bold colored indicators only | `test_badge_rule_is_bold_text_without_background_or_border`, `test_indicator_classes_cover_every_required_state`, `test_tab_counts_are_plain_text_not_pills`, `test_no_dot_pseudo_element_indicators` | Enforced |
| 3.2 No decorative badges on headings | Manual review; indicators are rendered in card bodies and table cells, never beside a heading | Enforced |
| 3.3 No icon background tiles | Bare glyphs only; the brand mark is plain text with no tile | Enforced |
| 4.1 Theme parity and token use | `test_theme_tokens_are_defined_for_both_themes`, `test_component_styles_use_tokens_not_hardcoded_colors`, `test_theme_conditional_css_only_changes_paint`, `test_single_theme_toggle_in_header`, `test_theme_toggle_is_wired_to_persistence` | Enforced |
| 4.2 Compact date filters | `test_date_filters_use_compact_fixed_width` | Enforced |
| 4.3 Responsive and touch | `test_viewport_prevents_horizontal_overflow`, `test_flex_and_grid_children_can_shrink`, `test_mobile_media_query_enforces_two_kpi_columns`, `test_action_buttons_share_a_row_on_mobile`, `test_dashboard_has_no_inline_grid_template_columns` | Enforced |
| 6.3 Session handling | `test_inactivity_timeout_is_thirty_minutes`, `test_401_response_clears_authentication_state` | Enforced |
| 8.2 Quality gates | `ruff check app tests migrations scripts` and `python -m compileall -q app tests migrations scripts` | Enforced |
| 8.3 Automated tests | `pytest` (full suite green, SQLite in-memory, no live external calls) | Enforced |
| 8.4 Documentation parity | `README.md`, `rules.md`, and `docs/` updated in the same commit as behaviour changes | Process rule |

The dashboard is served from the container image. After changing
`app/static/admin/index.html`, rebuild and restart the API so the change is
visible:

```sh
docker compose up --build -d api
```
