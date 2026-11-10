# End-to-End Test Results — Phase L Acceptance Scenarios

**Audit Date:** 2026-11-10  
**Test Suite:** `tests/test_end_to_end_scenarios.py`  
**Test Runner:** `pytest 9.2.0`  
**Overall Verdict:** **10 / 10 SCENARIOS PASSED (100% SUCCESS)**  

---

### Scenario 1: Complete Ingestion Pipeline
- **Title:** Complete freelance opportunity ingestion from source registration to review queue.
- **Description:** Verifies that a valid channel message is ingested, parsed, classified, quality scored, screened for risks, and recorded in `pending_review` status while preserving the exact verbatim original text and source link.
- **Preconditions:** Registered active Telegram source.
- **Input:**
  - Source Name: "Freelance Tech Board"
  - External ID: `scen-001`
  - Text: `"We are seeking a Full Stack React and Python developer. Experience with FastAPI and Tailwind is required. Budget is $1,200 USD. Project deadline is 3 weeks."`
- **Expected Result:**
  - Opportunity created with `opportunity_type = "freelance_job"`.
  - Extracted skills contain `react` and `python`.
  - Budget extracted as `1200.0 USD`.
  - Quality score > 0; risk score = 0; risk flags empty.
  - Review status = `pending_review`.
  - Exactly one `CollectedPost` and one `Opportunity` stored in DB.
- **Actual Result:**
  - Status 201 Created returned. All fields correctly extracted.
  - Exactly 1 post and 1 opportunity in database; original text preserved verbatim.
- **Test Reference:** `test_scenario_1_complete_ingestion`
- **Status:** **PASSED**

---

### Scenario 2: Duplicate Ingestion Prevention & Idempotency
- **Title:** Monotonic deduplication across repeated ingestion calls.
- **Description:** Verifies that sending the exact same external message twice to an active source results in HTTP 409 Conflict with a stable error code and creates no duplicate records.
- **Preconditions:** Active source exists.
- **Input:** Identical external ID `scen-dup-002` sent twice with text `"Need a senior backend engineer. Pay $1,500 USD."`.
- **Expected Result:**
  - First request returns HTTP 201.
  - Second request returns HTTP 409 Conflict with `detail.code = "duplicate_post"`.
  - Database contains exactly 1 post and 1 opportunity row.
- **Actual Result:**
  - First call created ID. Second call returned 409 with code `duplicate_post`.
  - Database row count verified at 1 post and 1 opportunity.
- **Test Reference:** `test_scenario_2_duplicate_ingestion`
- **Status:** **PASSED**

---

### Scenario 3: Risky Opportunity Detection & Bait Budget Suppression
- **Title:** Automatic risk screening and suppression of fraudulent compensation.
- **Description:** Tests the safety pipeline against upfront registration fee scams. Ensures that the original scam text is preserved for review, the risk flag `upfront_payment` is raised, review status is routed to `needs_review`, and the fake advertised budget is NOT recorded as a verified earning.
- **Preconditions:** Active source exists.
- **Input:** `"Pay ETB 500 registration fee to receive a remote job paying USD 5,000."`
- **Expected Result:**
  - Original text preserved verbatim.
  - Flag `upfront_payment` added to `risk_flags`.
  - `risk_score` > 0.
  - `budget_amount` is `None` (suppressed).
  - Status set to `needs_review`.
  - Never automatically approved or published.
- **Actual Result:**
  - Opportunity returned with `risk_flags = ["upfront_payment"]`, `risk_score = 40`, `budget_amount = null`, `review_status = "needs_review"`.
- **Test Reference:** `test_scenario_3_risky_opportunity`
- **Status:** **PASSED**

---

### Scenario 4: Review Workflow & Append-Only Audit Trail
- **Title:** Human reviewer field editing, approval, and audit history verification.
- **Description:** Simulates an authorized reviewer inspecting an opportunity, correcting extracted title and category, approving the opportunity with a justification note, and verifying that append-only audit events capture before-and-after states.
- **Preconditions:** Opportunity in pending review status; reviewer credentials.
- **Input:**
  - `PATCH /api/v1/opportunities/{id}` with `{"title": "Senior Mobile UI/UX Illustrator", "category": "design"}`
  - `PATCH /api/v1/opportunities/{id}/review` with `{"decision": "approved", "reviewer_note": "Verified client legitimacy and rate."}`
- **Expected Result:**
  - Record updated with new title, category, reviewed_by, reviewed_at, and approved status.
  - `GET /api/v1/opportunities/{id}/events` returns at least 2 events (`field_edit` and `status_change`).
  - Actor recorded as `test-reviewer`.
- **Actual Result:**
  - Title and category updated; audit trail verified with full diff and reviewer attribution.
- **Test Reference:** `test_scenario_4_review_workflow`
- **Status:** **PASSED**

---

### Scenario 5: Role-Based Access Control (RBAC) Restrictions
- **Title:** Strict security boundaries between roles.
- **Description:** Verifies that a collector cannot approve opportunities (403), an analyst cannot ingest content (403), and unauthenticated callers cannot access protected endpoints (401).
- **Preconditions:** Configured API keys for admin, reviewer, collector, analyst.
- **Input:** Cross-role API calls.
- **Expected Result:**
  - Collector approving opportunity -> HTTP 403 `insufficient_role`.
  - Analyst ingesting post -> HTTP 403 `insufficient_role`.
  - Anonymous request to protected endpoints -> HTTP 401 `authentication_required`.
- **Actual Result:**
  - All unauthorized requests correctly rejected with expected HTTP status and typed error codes.
- **Test Reference:** `test_scenario_5_role_restrictions`
- **Status:** **PASSED**

---

### Scenario 6: Approval-Gated Publishing with Attribution
- **Title:** Safe publishing of approved content with mandatory source attribution.
- **Description:** Verifies that an approved opportunity is formatted and sent to an allowlisted channel, that attribution is preserved after any truncation, and that the publication record is committed.
- **Preconditions:** Approved opportunity; publishing enabled in test settings; allowlisted destination channel.
- **Input:** `publish_opportunity` with channel `@audit_channel`.
- **Expected Result:**
  - Publication record written with status `published`.
  - Formatted broadcast message contains `"Source: <source_name>"` and the source URL.
  - Exactly 1 message sent via publisher adapter.
- **Actual Result:**
  - Publication status = `published`, normalized channel = `audit_channel`, sent text includes attribution and URL.
- **Test Reference:** `test_scenario_6_publishing`
- **Status:** **PASSED**

---

### Scenario 7: Scheduled Publication Cancellation on Rejection
- **Title:** Send-time approval re-check prevents broadcast of revoked content.
- **Description:** Tests scheduling an approved opportunity for future publication, subsequent revocation/rejection by a reviewer prior to the scheduled time, and execution of the `publish_due` scheduler.
- **Preconditions:** Approved opportunity scheduled for `NOW + 2 hours`.
- **Input:** Reviewer calls reject before scheduled time; `publish_due` triggered.
- **Expected Result:**
  - `publish_due` detects opportunity is no longer approved.
  - Broadcast is aborted; zero messages sent.
  - Publication remains unposted.
- **Actual Result:**
  - `fake_publisher.sent` remains empty (0 sent). `publish_due` returned empty list and logged warning `scheduled publication skipped id=1 code=not_approved`.
- **Test Reference:** `test_scenario_7_scheduled_publication_cancellation`
- **Status:** **PASSED**

---

### Scenario 8: Background Worker Recovery & State Preservation
- **Title:** Idempotent job execution with bounded retries and recovery from worker crash.
- **Description:** Simulates an unexpected failure during job execution, monotonic attempt count increments, exponential backoff, worker recovery, and idempotent replay handling.
- **Preconditions:** Celery runner with transient failure simulation.
- **Input:** Task that fails on attempt 1 and succeeds on attempt 2.
- **Expected Result:**
  - Attempt 1 recorded as retrying with backoff delay.
  - Attempt 2 executes and records status `succeeded` with attempts = 2.
  - Subsequent rerun with same idempotency key returns stored result without running task again.
- **Actual Result:**
  - Monotonic attempt counting verified. Idempotency key replay returned stored value; task function called exactly twice.
- **Test Reference:** `test_scenario_8_worker_recovery`
- **Status:** **PASSED**

---

### Scenario 9: LLM Extraction Failure Fallback
- **Title:** Graceful degradation from LLM timeout to deterministic rules extraction.
- **Description:** Simulates an external LLM provider timeout or 5xx outage and verifies that the extraction orchestrator seamlessly falls back to the deterministic rules extractor, recording `extraction_method = "rules_fallback"`.
- **Preconditions:** Provider configured with MockTransport throwing `httpx.TimeoutException`.
- **Input:** `"Looking for a Python Django developer to build an API. Budget is $800 USD."`
- **Expected Result:**
  - Request does not crash or discard the post.
  - Extracted opportunity populated by deterministic engine (`skills = ["python"]`, `budget = 800 USD`).
  - Method recorded as `rules_fallback`.
- **Actual Result:**
  - Fallback executed smoothly. Opportunity preserved with `extraction_method = "rules_fallback"`.
- **Test Reference:** `test_scenario_9_llm_failure_fallback`
- **Status:** **PASSED**

---

### Scenario 10: Cross-System Analytics Consistency
- **Title:** Analytics aggregates match database records exactly.
- **Description:** Seeds known sources, posts, approved opportunities, and scam items. Queries `/api/v1/analytics/overview` and `/api/v1/analytics/market` as an analyst and verifies mathematical consistency.
- **Preconditions:** Seeded database with 3 posts, 1 approved opportunity, 1 risk-flagged opportunity.
- **Input:** Analyst API calls to analytics endpoints.
- **Expected Result:**
  - `total_sources = 1`, `active_sources = 1`.
  - `total_posts = 3`, `total_opportunities = 3`.
  - `by_status["approved"] = 1`, `by_status["pending_review"] = 1`, `by_status["needs_review"] = 1`.
  - `risk_flagged = 1`.
  - Market budget counts and min/max match seeded values.
- **Actual Result:**
  - All aggregate numbers matched database ground truth with zero variance.
- **Test Reference:** `test_scenario_10_analytics_consistency`
- **Status:** **PASSED**
