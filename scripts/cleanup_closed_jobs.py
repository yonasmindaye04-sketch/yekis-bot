"""CLI utility to scan and auto-reject closed/filled/expired opportunities."""

from __future__ import annotations

import sys

from app.db import SessionLocal
from app.services.review import cleanup_closed_opportunities


def main() -> int:
    print("Scanning active review queues for closed, filled, or expired opportunities...")
    db = SessionLocal()
    try:
        closed_ids = cleanup_closed_opportunities(db)
        if closed_ids:
            print(
                f"Successfully auto-rejected {len(closed_ids)} closed opportunities: {closed_ids}"
            )
        else:
            print("No closed opportunities found in active review queues.")
        return 0
    except Exception as exc:
        print(f"Error during cleanup: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
