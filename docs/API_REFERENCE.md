# Yekis Intelligence — API Reference

This is the route-level contract for the service. It matches the FastAPI decorators, Pydantic schemas, and role dependencies in the code.

- Base URL in local development: `http://localhost:8100`
- Business routes: `/api/v1`
- Interactive docs: `GET /docs` (development only)
- OpenAPI document: `GET /openapi.json` (development only)

For architecture and workflow context, read the [Project Guide](PROJECT_GUIDE.md).

---

## 1. Conventions

### Content type

Requests with bodies use `application/json`.

### Timestamps

All timestamps are UTC ISO-8601 strings, for example:

```json
"2026-11-10T12:00:00Z"
```

### Pagination

List endpoints accept:

| Parameter | Default | Range |
|---|---|---|
| `limit` | `50` | `1..200` |
| `offset` | `0` | `>= 0` |

Out-of-range values return `422`.

### Success and error shapes

Successful objects use the Pydantic response model shown for each route.

Expected domain errors use:

```json
{
  "detail": {
    "code": "duplicate_post",
    "message": "Post demo-001 already ingested for source 1"
  }
}
```

FastAPI request-validation errors return the standard `422` shape:

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "name"],
      "msg": "String should have at least 2 characters",
      "input": "x"
    }
  ]
}
```

### Authentication

Send the key in either header:

```http
X-API-Key: <key>
```

```http
Authorization: Bearer <key>
```

A missing key returns `401`. A valid key with the wrong role returns `403`.

---

## 2. Authentication and roles

Configured by `AUTH_API_KEYS`:

```json
{
  "dev-admin-key-change-me-01": {"role": "admin", "name": "dev-admin"},
  "dev-reviewer-key-change-me-01": {"role": "reviewer", "name": "dev-reviewer"},
  "dev-collector-key-change-me-01": {"role": "collector", "name": "dev-collector"},
  "dev-analyst-key-change-me-01": {"role": "analyst", "name": "dev-analyst"}
}
```

| Route group | Required role |
|---|---|
| `POST /sources` | `admin` |
| `GET /sources`, `GET /sources/{id}` | `admin`, `reviewer`, `collector`, `analyst` |
| `PATCH /sources/{id}`, `PATCH /sources/{id}/status` | `admin` |
| `POST /ingestion/manual` | `collector` (admin also passes) |
| `GET /ingestion/posts` | `collector`, `reviewer`, `analyst` (admin also passes) |
| `GET /opportunities`, `GET /opportunities/{id}` | `admin`, `reviewer`, `collector`, `analyst` |
| `GET /opportunities/{id}/events` | `admin`, `reviewer`, `analyst` |
| `PATCH /opportunities/{id}/review` | `reviewer` (admin also passes) |
| `PATCH /opportunities/{id}` | `reviewer` (admin also passes) |
| `POST /opportunities/{id}/publish` | `reviewer` (admin also passes) |
| `GET /publications`, `GET /publications/{id}` | `admin`, `reviewer`, `analyst` |
| `GET /analytics/*` | `analyst`, `reviewer` (admin also passes) |

`admin` passes every role check.

`AUTH_ALLOW_ANONYMOUS_DEV=true` maps anonymous callers to an admin principal in development. Do not enable it outside a local machine.

---

## 3. Public endpoints

### `GET /`

Returns a small service banner.

```json
{
  "service": "Yekis Intelligence",
  "version": "0.3.0",
  "admin": "/admin/",
  "docs": "/docs"
}
```

`docs` appears only when `APP_ENV=development`.

### `GET /health`

Public readiness probe. It runs `SELECT 1` against the configured database.

**200**

```json
{"status": "ok", "database": "connected"}
```

**503**

```json
{"status": "degraded", "database": "unavailable"}
```

The driver error is logged server-side and is not returned to the client.

### `GET /admin/`

Serves the static admin dashboard. The page itself is public; every data call it makes requires an API key.

---

## 4. Sources

### `POST /api/v1/sources`

Requires `admin`.

Request:

```json
{
  "name": "Yekis test source",
  "source_type": "telegram",
  "source_url": "https://t.me/example_jobs",
  "category": "software",
  "is_active": true
}
```

`source_type` must be one of `telegram`, `website`, `rss`, or `manual`.

**201** returns `SourceRead`:

```json
{
  "id": 1,
  "name": "Yekis test source",
  "source_type": "telegram",
  "source_url": "https://t.me/example_jobs",
  "category": "software",
  "is_active": true,
  "created_at": "2026-11-10T12:00:00Z",
  "last_collected_message_id": null,
  "last_attempt_at": null,
  "last_success_at": null,
  "next_attempt_at": null,
  "consecutive_failures": 0,
  "last_error": null
}
```

### `GET /api/v1/sources`

Requires any authenticated role. Query: `limit`, `offset`.

**200** returns a list of `SourceRead`, newest id first.

### `GET /api/v1/sources/{source_id}`

Requires any authenticated role.

**200** `SourceRead`; **404** `source_not_found`.

### `PATCH /api/v1/sources/{source_id}`

Requires `admin`. At least one field must be supplied.

```json
{
  "name": "Renamed source",
  "source_url": "https://t.me/new_name",
  "category": "design",
  "is_active": true
}
```

**200** `SourceRead`; **404** `source_not_found`; **422** for an empty body.

### `PATCH /api/v1/sources/{source_id}/status`

Requires `admin`. JSON body:

```json
{"is_active": false}
```

**200** `SourceRead`; **404** `source_not_found`; **422** when the body is missing or invalid.

> Source status used to be a query parameter. It is a JSON body now.

---

## 5. Ingestion

### `POST /api/v1/ingestion/manual`

Requires `collector` or `admin`.

```json
{
  "source_id": 1,
  "external_id": "demo-001",
  "original_text": "Looking for a React developer to build a dashboard. Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks.",
  "original_url": "https://t.me/example_jobs/1"
}
```

`original_text` is 10–30,000 characters. `external_id` is 1–255 characters and is unique per source.

**201** returns `OpportunityRead`.

Errors:

| Status | Code | Meaning |
|---|---|---|
| `404` | `source_not_found` | `source_id` does not exist. |
| `409` | `source_inactive` | Source exists but `is_active=false`. |
| `409` | `duplicate_post` | `(source_id, external_id)` already exists. |
| `422` | — | Request validation failed. |

Risk behavior: if deterministic extraction finds risk flags, the opportunity is created with `review_status="needs_review"`; otherwise it starts as `pending_review`. A risk flag is not a rejection.

### `GET /api/v1/ingestion/posts`

Requires `collector`, `reviewer`, or `analyst` (admin passes). Query:

| Parameter | Default | Meaning |
|---|---|---|
| `source_id` | none | Filter by source. |
| `limit` | `50` | Page size, max 200. |
| `offset` | `0` | Page offset. |

**200** returns a list of `PostRead`:

```json
{
  "id": 1,
  "source_id": 1,
  "external_id": "demo-001",
  "original_text": "Looking for a React developer...",
  "original_url": "https://t.me/example_jobs/1",
  "content_hash": "<64-character sha256>",
  "collected_at": "2026-11-10T12:00:00Z"
}
```

The original text is stored verbatim. Extraction never overwrites it.

---

## 6. Opportunities

### `GET /api/v1/opportunities`

Requires any authenticated role. Query:

| Parameter | Default | Meaning |
|---|---|---|
| `status` | none | One of `pending_review`, `needs_review`, `approved`, `rejected`. |
| `risk_only` | `false` | When true, only `risk_score > 0`. |
| `limit` | `50` | Page size, max 200. |
| `offset` | `0` | Page offset. |

Ordering is `quality_score DESC, id DESC`.

**200** returns a list of `OpportunityRead`:

```json
{
  "id": 1,
  "collected_post_id": 1,
  "opportunity_type": "freelance_job",
  "title": "Looking for a React developer to build a dashboard.",
  "category": "software",
  "skills": ["react", "tailwind", "typescript"],
  "budget_amount": 300.0,
  "budget_currency": "USD",
  "duration": "2 weeks",
  "remote": null,
  "description": "Looking for a React developer...",
  "quality_score": 100,
  "risk_flags": [],
  "risk_score": 0,
  "review_status": "pending_review",
  "extraction_method": "rules",
  "reviewed_at": null,
  "reviewed_by": null,
  "reviewer_note": null
}
```

`extraction_method` is `rules`, `gemini`, `llm`, or `rules_fallback`.

### `GET /api/v1/opportunities/{opportunity_id}`

Requires any authenticated role. **200** `OpportunityRead`; **404** `opportunity_not_found`.

### `PATCH /api/v1/opportunities/{opportunity_id}/review`

Requires `reviewer` or `admin`.

```json
{
  "decision": "approved",
  "reviewer_note": "Source checked by a human"
}
```

`decision` is one of `approved`, `rejected`, `needs_review`. `reviewer_note` is optional, max 2,000 characters.

**200** returns the updated `OpportunityRead` and appends one `review_events` row when the status actually changes.

Behavior:

- Repeating the current status is an idempotent no-op; no event is created.
- An illegal transition returns `409 invalid_transition`.
- `reviewed_by` is the `name` of the API key's principal.
- `reviewer_note` is persisted.
- The original post text is not modified.

Allowed transitions:

| From | To |
|---|---|
| `pending_review` | `approved`, `rejected`, `needs_review` |
| `needs_review` | `approved`, `rejected`, `needs_review` (no-op) |
| `approved` | `rejected`, `needs_review` |
| `rejected` | `needs_review` |

### `PATCH /api/v1/opportunities/{opportunity_id}`

Requires `reviewer` or `admin`. At least one field is required.

```json
{
  "title": "React dashboard developer",
  "category": "software",
  "skills": ["react", "typescript"],
  "budget_amount": 450.0,
  "budget_currency": "USD",
  "duration": "3 weeks",
  "remote": true,
  "description": "Reviewed description"
}
```

All fields are optional:

| Field | Type | Notes |
|---|---|---|
| `title` | string | 1–250 chars. |
| `category` | string or null | Max 100 chars. |
| `skills` | string[] or null | Max 20; duplicates removed. |
| `budget_amount` | number or null | `>= 0`. |
| `budget_currency` | `USD`, `ETB`, or null | — |
| `duration` | string or null | Max 120 chars. |
| `remote` | boolean or null | Tri-state. |
| `description` | string or null | Max 5,000 chars. |

**200** returns the updated `OpportunityRead`. A changed field writes a `field_edit` review event with `{field: {from, to}}`. Submitting only unchanged values returns 200 without an event. An empty body returns `422`.

### `GET /api/v1/opportunities/{opportunity_id}/events`

Requires `admin`, `reviewer`, or `analyst`. Query: `limit` (default 100, max 500), `offset`.

**200** returns `ReviewEventRead` rows, newest first:

```json
[
  {
    "id": 2,
    "opportunity_id": 1,
    "actor": "Ops reviewer",
    "event_type": "status_change",
    "from_status": "pending_review",
    "to_status": "approved",
    "note": "Source checked by a human",
    "changes": null,
    "created_at": "2026-11-10T12:05:00Z"
  },
  {
    "id": 1,
    "opportunity_id": 1,
    "actor": "Ops reviewer",
    "event_type": "field_edit",
    "from_status": "pending_review",
    "to_status": "pending_review",
    "note": "edited: title",
    "changes": {
      "title": {"from": "Old title", "to": "New title"}
    },
    "created_at": "2026-11-10T12:03:00Z"
  }
]
```

`event_type` is `status_change` or `field_edit`.

### `POST /api/v1/opportunities/{opportunity_id}/publish`

Requires `reviewer` or `admin`.

Immediate publish:

```json
{"channel": "@ops_test_channel"}
```

Scheduled publish:

```json
{
  "channel": "@ops_test_channel",
  "scheduled_at": "2026-11-11T09:00:00Z"
}
```

`channel` accepts `@name`, a bare name, or a public `https://t.me/...` link. Invite links are rejected. `scheduled_at` must be in the future.

**200** returns `PublicationRead`.

Errors:

| Status | Code | Meaning |
|---|---|---|
| `404` | `opportunity_not_found` | No such opportunity. |
| `409` | `not_approved` | The opportunity is not currently `approved`. |
| `409` | `publishing_disabled` | `PUBLISHING_ENABLED=false`. |
| `403` | `channel_not_allowed` | Channel is not in `PUBLISHING_ALLOWED_CHANNELS`. |
| `409` | `publication_unknown` | A previous attempt is still `sending`; reconcile manually. |
| `409` | `publication_exhausted` | Attempt cap reached. |
| `409` | `publication_not_due` | The scheduled time has not arrived (internal/safety path). |
| `422` | `invalid_schedule` | `scheduled_at` is not in the future. |
| `422` | `format_failed` | The message could not be formatted, usually missing attribution. |
| `502` | `publication_failed` | Telegram send failed; inspect the publication record. |

Replay semantics:

- Same opportunity + same channel after success returns the existing published row and sends nothing.
- A row in `sending` is never auto-resent; it may already be public.
- A `failed` row can be retried until `PUBLISHING_MAX_ATTEMPTS`.

---

## 7. Publications

### `GET /api/v1/publications`

Requires `admin`, `reviewer`, or `analyst`. Query: `status` (max 30 chars), `limit`, `offset`.

**200** returns a list of `PublicationRead`:

```json
{
  "id": 1,
  "opportunity_id": 1,
  "channel": "ops_test_channel",
  "status": "published",
  "external_message_id": "42",
  "scheduled_at": null,
  "published_at": "2026-11-10T12:10:00Z",
  "error": null,
  "attempts": 1,
  "created_by": "Ops reviewer",
  "created_at": "2026-11-10T12:09:55Z"
}
```

### `GET /api/v1/publications/{publication_id}`

Requires `admin`, `reviewer`, or `analyst`. **200** `PublicationRead`; **404** `publication_not_found`.

Publication statuses: `pending`, `scheduled`, `sending`, `published`, `failed`.

---

## 8. Analytics

All analytics routes require `analyst`, `reviewer`, or `admin` (`admin` passes automatically). They are read-only.

### `GET /api/v1/analytics/overview`

```json
{
  "total_sources": 1,
  "active_sources": 1,
  "total_posts": 3,
  "total_opportunities": 3,
  "by_status": {
    "pending_review": 0,
    "needs_review": 1,
    "approved": 1,
    "rejected": 1
  },
  "by_type": [{"key": "freelance_job", "count": 2}],
  "by_category": [{"key": "software", "count": 1}],
  "budget_by_currency": [
    {
      "currency": "USD",
      "count": 1,
      "min_amount": 300.0,
      "max_amount": 300.0,
      "avg_amount": 300.0
    }
  ],
  "risk_flagged": 1,
  "avg_quality": 63.33,
  "approval_rate": 50.0,
  "published": 1,
  "publication_failures": 0,
  "last_collected_at": "2026-11-10T12:00:00Z"
}
```

`approval_rate` is `approved / (approved + rejected) * 100`, or `0` when nothing has been reviewed.

### `GET /api/v1/analytics/sources`

Returns a list of per-source quality records. Fields:

`id`, `name`, `source_type`, `is_active`, `posts`, `opportunities`, `approved`, `rejected`, `pending_review`, `needs_review`, `approval_rate`, `risk_flagged`, `avg_quality`, `publications`, `published`, `publication_failures`, `last_success_at`, `last_collected_message_id`, `consecutive_failures`, `last_error`.

### `GET /api/v1/analytics/market`

```json
{
  "skills_demand": [
    {"key": "react", "count": 1}
  ],
  "budget": [
    {
      "currency": "USD",
      "count": 1,
      "min_amount": 300.0,
      "max_amount": 300.0,
      "avg_amount": 300.0,
      "median_amount": 300.0
    }
  ],
  "remote_share": 100.0,
  "by_category": [{"key": "software", "count": 1}],
  "by_type": [{"key": "freelance_job", "count": 2}]
}
```

`remote_share` is the percentage of opportunities with a known `remote` value that are remote. `unknown` is used for null category/currency buckets.

### `GET /api/v1/analytics/trends?days=30`

`days` is 1–365; out-of-range returns `422`.

```json
[
  {
    "day": "2026-11-10",
    "posts": 3,
    "opportunities": 3,
    "approved": 1,
    "published": 1
  }
]
```

Trends use UTC calendar days.

---

## 9. Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` | public | Banner. |
| GET | `/health` | public | DB readiness; 503 when down. |
| GET | `/admin/` | public page | Dashboard shell. |
| POST | `/api/v1/sources` | admin | Create source. |
| GET | `/api/v1/sources` | any role | List sources. |
| GET | `/api/v1/sources/{id}` | any role | Read source. |
| PATCH | `/api/v1/sources/{id}` | admin | Edit source. |
| PATCH | `/api/v1/sources/{id}/status` | admin | Activate/deactivate. |
| POST | `/api/v1/ingestion/manual` | collector/admin | Ingest one post. |
| GET | `/api/v1/ingestion/posts` | collector/reviewer/analyst | List posts. |
| GET | `/api/v1/opportunities` | any role | Review queue with filters. |
| GET | `/api/v1/opportunities/{id}` | any role | Read opportunity. |
| PATCH | `/api/v1/opportunities/{id}/review` | reviewer/admin | Decide; audit event. |
| PATCH | `/api/v1/opportunities/{id}` | reviewer/admin | Edit fields; audit event. |
| GET | `/api/v1/opportunities/{id}/events` | reviewer/admin/analyst | Audit history. |
| POST | `/api/v1/opportunities/{id}/publish` | reviewer/admin | Publish or schedule. |
| GET | `/api/v1/publications` | reviewer/admin/analyst | List publications. |
| GET | `/api/v1/publications/{id}` | reviewer/admin/analyst | Read publication. |
| GET | `/api/v1/analytics/overview` | analyst/reviewer/admin | Totals and status mix. |
| GET | `/api/v1/analytics/sources` | analyst/reviewer/admin | Source quality. |
| GET | `/api/v1/analytics/market` | analyst/reviewer/admin | Skills/budget/remote. |
| GET | `/api/v1/analytics/trends` | analyst/reviewer/admin | Daily trends. |

---

## 10. End-to-end curl example

```bash
export BASE=http://localhost:8100
export KEY='dev-admin-key-change-me-01'

# 1. Create a source
curl -s -X POST "$BASE/api/v1/sources" \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Demo source","source_type":"manual","source_url":"https://t.me/demo","category":"software","is_active":true}'

# 2. Ingest a post (source_id from step 1)
curl -s -X POST "$BASE/api/v1/ingestion/manual" \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"source_id":1,"external_id":"demo-001","original_text":"Looking for a React developer. Budget $300. Delivery in 2 weeks."}'

# 3. Review the opportunity (id from step 2)
curl -s -X PATCH "$BASE/api/v1/opportunities/1/review" \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"decision":"approved","reviewer_note":"Checked by a human"}'

# 4. Publish to an allowlisted channel
curl -s -X POST "$BASE/api/v1/opportunities/1/publish" \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"channel":"@ops_test_channel"}'

# 5. Inspect the audit trail and analytics
curl -s "$BASE/api/v1/opportunities/1/events" -H "X-API-Key: $KEY"
curl -s "$BASE/api/v1/analytics/overview" -H "X-API-Key: $KEY"
```

Publishing requires `PUBLISHING_ENABLED=true` and a channel in `PUBLISHING_ALLOWED_CHANNELS`. Never point development at a real channel.
