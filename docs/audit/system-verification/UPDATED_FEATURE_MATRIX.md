# Updated Feature Matrix — Yekis Intelligence

**Audit Date:** 2026-11-10  
**Verification Branch:** `qa/system-integration-verification`  
**Baseline Commit:** `b46063e`  

---

| Feature | Implementation status | Test status | Evidence | Remaining work |
|---|---|---|---|---|
| **FastAPI REST Application** | Implemented | Verified | `app/main.py`, `tests/test_health.py`, `tests/test_auth.py` | None |
| **SQLAlchemy 2.0 ORM Models** | Implemented | Verified | `app/models.py`, `tests/test_sources_api.py`, `test_review.py` | None |
| **Alembic Migrations (0001–0005)** | Implemented | Verified | `migrations/versions/`, `alembic check`, `repair_partial_schema.py` | None |
| **PostgreSQL Database Storage** | Implemented | Verified | Container `yekisbot-db-1`, live queries, health check 200 | Staging DB deployment |
| **API Key Authentication** | Implemented | Verified | SHA-256 fingerprint, `hmac.compare_digest`, `tests/test_auth.py` | Key rotation CLI/UI |
| **Role-Based Authorization (RBAC)** | Implemented | Verified | 4 roles (Admin, Reviewer, Collector, Analyst), `test_scenario_5_role_restrictions` | None |
| **Source Management (CRUD & Status)** | Implemented | Verified | `app/routers/sources.py`, `tests/test_sources_api.py` | None |
| **Telegram Source Reference Validation** | Implemented | Verified | Rejects invite links (`t.me/+...`), `tests/test_telegram_collector.py` | None |
| **Manual Content Ingestion** | Implemented | Verified | `app/routers/ingestion.py`, `tests/test_ingestion_api.py`, Admin UI tab | None |
| **Verbatim Content Preservation** | Implemented | Verified | `collected_posts.original_text`, `test_scenario_1_complete_ingestion` | None |
| **Content Hash Deduplication** | Implemented | Verified | SHA-256 hash unique per source, HTTP 409 `duplicate_post`, `test_scenario_2` | Cross-source fuzzy match |
| **Deterministic Rules Extractor** | Implemented | Verified | Token-boundary regex, noun/verb disambiguation, `tests/test_extractor_cases.py` | Expand Amharic dictionary |
| **Google Gemini Extraction Provider** | Implemented | Verified | Native AI Studio adapter, `tests/test_llm_provider.py`, live models check | Production latency tuning |
| **OpenAI-Compatible Extraction** | Implemented | Verified | Strict JSON schema, `tests/test_llm_provider.py` | None |
| **Prompt Injection Isolation** | Implemented | Verified | `<untrusted_content>` tags, system instructions, schema forbidden extras | Continuous red-teaming |
| **LLM Safety Merge & Fallback** | Implemented | Verified | Deterministic risk merged into LLM output, fallback to `rules_fallback` on timeout | None |
| **Risk Detection (Upfront Fees/Scams)** | Implemented | Verified | Flags `upfront_payment`, suppresses budget, `test_scenario_3_risky_opportunity` | Expand scam pattern heuristics |
| **Human Review Workflow** | Implemented | Verified | States: `pending_review`, `needs_review`, `approved`, `rejected` | Reviewer queues by category |
| **Append-Only Review Audit Trail** | Implemented | Verified | `review_events` table, actor attribution, diffs, `test_scenario_4_review_workflow` | Cryptographic tamper-proofing |
| **Opportunity In-Place Field Editing** | Implemented | Verified | `PATCH /api/v1/opportunities/{id}`, diffs recorded, Admin UI | None |
| **Approval-Gated Publishing** | Implemented | Verified | Send-time approval check, fail-closed allowlist, `tests/test_publishing.py` | Live channel run in staging |
| **Mandatory Source Attribution** | Implemented | Verified | Appended post-truncation, cannot be cut off, `test_scenario_6_publishing` | None |
| **Scheduled Publication & Beat Task** | Implemented | Verified | `publish_due` Celery beat task, `test_scenario_7_scheduled_publication_cancellation` | None |
| **Telegram Collector (Telethon)** | Implemented | Verified (Mock/Session) | Session in `var/`, backoff, monotonic checkpoints, `tests/test_telegram_collector.py` | Live channel testing with credentials |
| **Redis Broker & Backend** | Implemented | Verified | Container `yekisbot-redis-1`, port 6382, healthcheck passing | None |
| **Celery Worker Pipeline** | Implemented | Verified | Concurrency 1, prefetch 1, late acks, non-root user, `tests/test_worker_config.py` | Stale-lock reaper daemon |
| **Durable Jobs & Dead-Letter Storage** | Implemented | Verified | `job_executions` & `dead_letter_jobs` tables, `test_scenario_8_worker_recovery` | Dead-letter replay UI |
| **Cross-System Analytics** | Implemented | Verified | 4 endpoints (overview, sources, market, trends), `test_scenario_10_analytics_consistency` | Historical warehouse export |
| **Admin Mission Control UI** | Implemented | Verified | Served at `/admin/`, 5 tabs, responsive dark mode, no build step | Optional websocket updates |
| **Marketplace Financial Boundary** | Implemented | Verified | Zero access to wallets, escrow, payouts, contracts | Ongoing boundary audits |

---

## Summary of Feature Inventory Status
1. **Implemented and Verified:** 28 features
2. **Implemented but Unverified (Live Network Integration Blocked by Policy/Credentials):** 2 features (Live Telegram Channel Ingestion, Live Telegram Channel Publishing)
3. **Partially Implemented:** 0
4. **Broken:** 0 (all 5 discovered defects resolved and verified)
5. **Not Implemented:** 0
6. **Blocked by Missing Credentials or External Services:** Live Telegram Channel Broadcast (blocked pending staging channel assignment and owner sign-off).
