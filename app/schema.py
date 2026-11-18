"""Single schema authority for the database.

The API (Vercel) and the scheduled collector (GitHub Actions) run against the
same database, so the schema has to have exactly one owner: Alembic. Every
table, column, index, and constraint is defined by a migration and the
application models must describe that same database.

Two database states have to converge on the same revision:

* a database Alembic manages - ``alembic upgrade head`` is enough;
* a database that was created outside Alembic (the historical application
  bootstrap, ``Base.metadata.create_all``) - the revision has to be stamped
  first. Without it ``alembic upgrade head`` fails with
  ``table sources already exists``, the scheduled job aborts, and the
  autonomous publishing pipeline never runs at all.

``tests/test_schema_parity.py`` keeps the two schema definitions in step: the
application models and the migrations must always describe the same database,
which is what makes stamping a legacy database safe.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app import models  # noqa: F401  (registers the tables in the metadata)
from app.db import Base

logger = logging.getLogger(__name__)

# Arbitrary but stable key used for the PostgreSQL advisory lock that
# serialises schema work between the API and the scheduled job.
ADVISORY_LOCK_KEY = 0x7965_6B69

MIGRATIONS_DIR_ENV_VAR = "YEKIS_MIGRATIONS_DIR"


def migrations_dir() -> Path | None:
    """Return the directory holding the Alembic migration scripts, if found."""
    candidates: list[Path] = []
    override = os.environ.get(MIGRATIONS_DIR_ENV_VAR)
    if override:
        candidates.append(Path(override))
    # Source checkout: <repo>/app/schema.py -> <repo>/migrations
    candidates.append(Path(__file__).resolve().parent.parent / "migrations")
    # Installed package or serverless bundle: the project root is the cwd.
    candidates.append(Path.cwd() / "migrations")
    candidates.append(Path("/var/task/migrations"))
    # Everything that is importable may carry the scripts next to it.
    candidates.extend(Path(entry) / "migrations" for entry in sys.path if entry)

    for candidate in candidates:
        if (candidate / "versions").is_dir():
            return candidate
    return None


def alembic_config() -> Config | None:
    """Build an Alembic configuration, or ``None`` when Alembic cannot run.

    The configuration is built in memory on purpose: the serverless bundle
    ships the migration scripts but not ``alembic.ini``, and ``migrations/
    env.py`` reads the database URL from the application settings anyway.
    """
    scripts = migrations_dir()
    if scripts is None:
        return None
    config = Config()
    config.set_main_option("script_location", str(scripts))
    return config


def current_revision(engine: sa.Engine) -> str | None:
    """Return the revision the database is stamped with, or ``None``."""
    try:
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            if "alembic_version" not in inspector.get_table_names():
                return None
            return conn.execute(sa.text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
    except Exception:  # pragma: no cover - the database may not exist yet
        logger.debug("could not read the Alembic revision", exc_info=True)
        return None


def model_tables_present(engine: sa.Engine) -> bool:
    """Return whether tables described by the application models already exist."""
    try:
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            existing = set(inspector.get_table_names())
    except Exception:  # pragma: no cover - the database may not exist yet
        return False
    expected = {table.name for table in Base.metadata.sorted_tables}
    return bool(existing & expected)


def _server_default_for(column: sa.Column) -> sa.text | None:
    """Return a DDL default for a column, if one can be derived safely."""
    if column.server_default is not None:
        return column.server_default
    default = column.default
    arg = getattr(default, "arg", None)
    if isinstance(arg, bool):
        return sa.text("true" if arg else "false")
    if isinstance(arg, (int, float)):
        return sa.text(str(arg))
    if isinstance(arg, str):
        return sa.text("'{}'".format(arg.replace("'", "''")))
    return None


def sync_schema(engine: sa.Engine) -> None:
    """Bring a database created outside Alembic in line with the models.

    Only additive work happens here: missing tables are created and missing
    columns are added. Nothing is dropped or renamed.
    """
    Base.metadata.create_all(bind=engine, checkfirst=True)

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                column_type = column.type.compile(dialect=conn.dialect)
                ddl = f"{column_type} NOT NULL"
                if column.nullable:
                    ddl = column_type
                default = _server_default_for(column)
                if default is not None:
                    ddl = f"{ddl} DEFAULT {default.text}"
                logger.warning(
                    "adding missing column %s.%s to the existing table", table.name, column.name
                )
                conn.execute(sa.text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}"))
        conn.commit()


@contextmanager
def schema_lock(engine: sa.Engine) -> Iterator[bool]:
    """Serialise schema work across processes.

    Yields ``True`` when the lock was taken and ``False`` when another process
    is already preparing the schema. PostgreSQL uses a session level advisory
    lock; other backends have a single writer by construction.
    """
    if engine.dialect.name != "postgresql":
        yield True
        return

    with engine.connect() as conn:
        acquired = bool(
            conn.execute(
                sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            ).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
                conn.commit()


def ensure_schema(engine: sa.Engine) -> str:
    """Make sure the database schema matches the current revision.

    Returns a short status describing what was done, for logging and for the
    deployment tooling.
    """
    config = alembic_config()
    if config is None:
        logger.warning(
            "the Alembic migration scripts are not available; "
            "creating the schema from the application models instead"
        )
        sync_schema(engine)
        return "model-bootstrap"

    with schema_lock(engine) as acquired:
        if not acquired:
            logger.info("another process is preparing the schema; skipping")
            return "locked-elsewhere"

        revision = current_revision(engine)
        if revision is None and model_tables_present(engine):
            # The database was created outside Alembic. Reconcile it with the
            # models and stamp the revision, otherwise Alembic tries to create
            # the tables a second time and the migration fails.
            logger.warning(
                "the database has tables but no Alembic revision; "
                "reconciling it with the application models and stamping the revision"
            )
            sync_schema(engine)
            command.stamp(config, "head")
            command.upgrade(config, "head")
            return "stamped"

        command.upgrade(config, "head")
        return "upgraded"
