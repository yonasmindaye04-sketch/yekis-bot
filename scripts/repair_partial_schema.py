"""One-off repair for a partially migrated Yekis Intelligence database.

Use only when:
  * ``alembic current`` is ``0002_telegram_collector``;
  * ``job_executions``, ``dead_letter_jobs``, ``review_events``, and
    ``publications`` already exist;
  * ``opportunities.reviewed_by`` / ``reviewer_note`` are missing.

It adds only the missing additive 0004 objects, then records the database at
``0005_publications`` (head). It is idempotent and refuses unexpected states.

Run from the project root with the venv active:

    python scripts/repair_partial_schema.py
"""

from sqlalchemy import inspect, text

from app.db import engine


def main() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        required = {
            "job_executions",
            "dead_letter_jobs",
            "review_events",
            "publications",
        }
        missing = required - tables
        if missing:
            raise SystemExit(
                "Refusing repair: missing tables "
                f"{sorted(missing)}. Run `alembic upgrade head` instead."
            )

        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if version == "0005_publications":
            print("Already at 0005_publications; nothing to repair.")
            return
        if version != "0002_telegram_collector":
            raise SystemExit(
                "Refusing repair: expected alembic_version "
                f"0002_telegram_collector, found {version!r}."
            )

        conn.execute(
            text(
                "ALTER TABLE opportunities "
                "ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(120)"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE opportunities "
                "ADD COLUMN IF NOT EXISTS reviewer_note TEXT"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_opportunities_review_status "
                "ON opportunities (review_status)"
            )
        )
        conn.execute(
            text(
                "UPDATE alembic_version "
                "SET version_num = '0005_publications'"
            )
        )

    print("Repaired: opportunities 0004 columns/index added; stamped 0005_publications.")
    print("Next: alembic current && alembic check")


if __name__ == "__main__":
    main()
