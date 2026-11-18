"""Create or upgrade the database schema to the current revision.

Used by the scheduled job and by the API startup. It is the same code path as
the application startup, so a database that was created outside Alembic is
reconciled and stamped instead of failing the migration with
``table sources already exists``.
"""

from __future__ import annotations

import logging
import sys

from app.db import engine
from app.schema import ensure_schema


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        status = ensure_schema(engine)
    except Exception as exc:
        print(f"schema migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"schema is ready ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
