"""Direct collection runner for scheduled cron workflows (e.g. GitHub Actions)."""

from __future__ import annotations

import asyncio
import sys

from app.config import settings
from app.db import SessionLocal
from app.integrations.telegram.publisher import TelethonPublisher
from app.services.publishing import publish_due
from app.worker.tasks import collect_job


def main() -> int:
    print("Starting direct Telegram collection job...")
    db = SessionLocal()
    try:
        # 1. Scrape new messages and ingest opportunities
        outcome = collect_job(db)
        print(f"Collection finished with status: {outcome.status}")
        if outcome.value:
            print(f"Outcome details: {outcome.value}")

        # 2. Check for due / approved publications to broadcast
        print("Checking for due/approved broadcasts...")

        async def _publish():
            publisher = TelethonPublisher(settings=settings)
            try:
                return await publish_due(db, publisher=publisher, settings=settings)
            finally:
                await publisher.close()

        published = asyncio.run(_publish())
        print(f"Broadcast processing complete: {len(published)} items published.")

        return 0 if outcome.status in ("succeeded", "completed", "skipped") else 1
    except Exception as exc:
        print(f"Error during collection/publication execution: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
