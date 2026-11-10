# Remaining Backlog & Engineering Roadmap — Yekis Intelligence

**Audit Date:** 2026-11-10  
**Verification Branch:** `qa/system-integration-verification`  
**Tracking Context:** Post-Audit Integration & Hardening  

---

## 1. Prioritized Task Backlog

| Task ID | Title | Priority | Category | Owner | Dependencies | Description | Acceptance Criteria |
|---|---|---|---|---|---|---|---|
| **YEKIS-B01** | Ingress Rate Limiting & DoS Shield | **P0** | Security / Infra | DevOps | Nginx / Caddy Proxy | Add rate limiting for API ingestion and authentication endpoints to prevent brute force or resource exhaustion. | Rate limit `/api/v1/ingestion/manual` to 20 req/min per IP; return HTTP 429 Too Many Requests when exceeded. |
| **YEKIS-B02** | Stale Job Recovery / Lock Reaper | **P1** | Backend / Reliability | Backend | Celery Pipeline | Implement a periodic reaper task to detect `job_executions` left in `running` state if a worker process crashes mid-task. | Jobs with status `running` and `started_at < now - 30 minutes` are reset to `retrying` or dead-lettered with clear audit trail. |
| **YEKIS-B03** | Staging End-to-End Live Telegram Rehearsal | **P1** | QA / Integration | QA Engineer | Staging Channel | Provision dedicated private test channel, execute interactive bot login, collect 20 sample messages, and broadcast 1 approved opportunity. | Zero unhandled flood-wait exceptions; Monotonic checkpoint advance; Formatted attribution verified on live Telegram client. |
| **YEKIS-B04** | Dynamic API Key Management & Rotation CLI | **P2** | Security / Auth | Security | Database | Provide an administrative CLI tool (`python -m app.security.keys`) to generate, inspect, and rotate keys without restarting the application. | Generated keys are cryptographically secure, SHA-256 fingerprinted, and immediately active. |
| **YEKIS-B05** | Normalized Skill Vocabulary Engine | **P2** | Extraction / Analytics | Data Engineer | Extractor | Normalize extracted and edited skills against a curated taxonomy (e.g. `React`, `React.js`, `reactjs` -> `React`) before indexing. | Analytics skill aggregation merges variants into a canonical representation. |
| **YEKIS-B06** | Real-Time WebSocket Feed for Admin Dashboard | **P3** | Frontend / UX | Frontend | FastAPI WebSockets | Implement a lightweight Server-Sent Events (SSE) or WebSocket channel to push incoming ingested opportunities to the review queue without manual refresh. | New opportunities appear automatically in the UI review queue within 2 seconds of ingestion. |
| **YEKIS-B07** | Automated CI/CD Workflow Pipeline | **P2** | DevOps | DevOps | GitHub Actions | Add `.github/workflows/ci.yml` running linting, compile check, pytest suite (115 tests), and docker build verification on pull requests. | Clean PR status checks; failure blocks merge to main. |

---

## 2. Risk Mitigation & Implementation Timeline

- **Immediate (Sprint 1):** Deploy Nginx reverse proxy with rate limiting (YEKIS-B01) and setup GitHub Actions CI workflow (YEKIS-B07).
- **Sprint 2 (Staging Transition):** Conduct live Telegram collection and publishing rehearsal in private staging channel (YEKIS-B03), and implement stale job lock reaper (YEKIS-B02).
- **Sprint 3 (Production Hardening):** Deploy dynamic key rotation (YEKIS-B04) and canonical skill normalization (YEKIS-B05).
