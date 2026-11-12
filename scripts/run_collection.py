"""Direct collection runner for scheduled cron workflows (e.g. GitHub Actions)."""

from __future__ import annotations

import sys

from app.db import SessionLocal
from app.worker.tasks import collect_job


def main() -> int:
    print("Starting direct Telegram collection job...")
    db = SessionLocal()
    try:
        outcome = collect_job(db)
        print(f"Collection finished with status: {outcome.status}")
        if outcome.value:
            print(f"Outcome details: {outcome.value}")
        return 0 if outcome.status in ("succeeded", "completed", "skipped") else 1
    except Exception as exc:
        print(f"Error during collection execution: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
