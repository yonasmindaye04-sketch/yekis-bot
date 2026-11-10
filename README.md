# Yekis Intelligence

Independent intelligence service for the Yekis marketplace. The first milestone provides a FastAPI service, PostgreSQL persistence, source management, manual post ingestion, deterministic opportunity extraction, and a review queue.

## Architecture

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL
- Redis is reserved for the next milestone's background processing
- Telegram collection and publishing are intentionally not enabled in this first milestone
- The service has no access to marketplace escrow, wallet, payout, or contract operations

## Quick start

1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build`.
3. Open `http://localhost:8100/docs`.
4. Check `GET /health`.
5. Create a source with `POST /api/v1/sources`.
6. Ingest a sample post with `POST /api/v1/ingestion/manual`.
7. Review it with `GET /api/v1/opportunities`.

For local development, `AUTO_CREATE_SCHEMA=true` creates tables on startup. For controlled environments, set it to `false` and run `alembic upgrade head` before starting the API.

## Example source

```json
{
  "name": "Yekis test source",
  "source_type": "telegram",
  "source_url": "https://t.me/example_jobs",
  "category": "software",
  "is_active": true
}
```

## Example ingestion

```json
{
  "source_id": 1,
  "external_id": "demo-001",
  "original_text": "Looking for a React developer to build a dashboard. Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks.",
  "original_url": "https://t.me/example_jobs/1"
}
```

## Local development

```bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8100
```

For local development outside Docker, set `DATABASE_URL` to your PostgreSQL instance.

## API endpoints

- `GET /health`
- `POST /api/v1/sources`
- `GET /api/v1/sources`
- `POST /api/v1/ingestion/manual`
- `GET /api/v1/ingestion/posts`
- `GET /api/v1/opportunities`
- `PATCH /api/v1/opportunities/{id}/review`

## Next milestones

1. Telegram authorized-channel collector using Telethon.
2. Redis/Celery processing pipeline and retry policy.
3. LLM extraction behind a provider interface with strict Pydantic validation.
4. Admin dashboard integration and authenticated service-to-service access.
5. Telegram publisher with explicit approval and attribution.
6. Analytics, source quality, and market insights.
