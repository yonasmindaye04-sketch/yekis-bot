"""The database schema must have exactly one definition.

The API (Vercel) and the scheduled collector (GitHub Actions) share one
database. The application models and the Alembic migrations therefore have to
describe the same schema: the application prepares the database on startup
and the scheduled job migrates it, and the repair path for a database that
was created outside Alembic only works when the two definitions agree.

These tests are the safety net for that repair path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from app.db import Base
from app.schema import alembic_config, current_revision, ensure_schema, model_tables_present

HEAD = ScriptDirectory.from_config(alembic_config()).get_current_head()


def _database_snapshot(engine: sa.Engine) -> dict:
    """Describe the physical schema of a database."""
    inspector = sa.inspect(engine)
    tables: dict[str, dict] = {}
    for name in sorted(inspector.get_table_names()):
        if name == "alembic_version":
            continue
        columns = {
            column["name"]: {
                "type": str(column["type"]),
                "nullable": bool(column["nullable"]),
                "defaulted": column.get("default") is not None,
            }
            for column in inspector.get_columns(name)
        }
        indexes = {
            index["name"]: bool(index.get("unique"))
            for index in inspector.get_indexes(name)
        }
        uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(name)
        }
        tables[name] = {"columns": columns, "indexes": indexes, "uniques": uniques}
    return tables


def _model_snapshot() -> dict:
    """Describe the schema the application models ask for."""
    tables: dict[str, dict] = {}
    for table in Base.metadata.sorted_tables:
        columns = {
            column.name: {
                "type": str(column.type),
                "nullable": bool(column.nullable),
                "defaulted": column.default is not None or column.server_default is not None,
            }
            for column in table.columns
        }
        indexes = {
            index.name: bool(index.unique)
            for index in table.indexes
        }
        uniques = {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        tables[table.name] = {"columns": columns, "indexes": indexes, "uniques": uniques}
    return tables


def _assert_same_schema(actual: dict, expected: dict) -> None:
    assert set(actual) == set(expected), "the tables of the two schemas differ"
    for table_name, expected_table in expected.items():
        actual_table = actual[table_name]
        assert set(actual_table["columns"]) == set(expected_table["columns"]), (
            f"the columns of {table_name} differ"
        )
        for column_name, expected_column in expected_table["columns"].items():
            actual_column = actual_table["columns"][column_name]
            assert actual_column["type"] == expected_column["type"], (
                f"the type of {table_name}.{column_name} differs"
            )
            assert actual_column["nullable"] == expected_column["nullable"], (
                f"the nullability of {table_name}.{column_name} differs"
            )
            if not expected_column["defaulted"] and not actual_column["defaulted"]:
                continue
            # One side may use a database default and the other an ORM
            # default; what matters is that a value can always be written.
            assert (
                actual_column["defaulted"] or expected_column["defaulted"]
            ), f"{table_name}.{column_name} has no default in either schema"


def test_models_and_migrations_describe_the_same_database(tmp_path, monkeypatch):
    """The application bootstrap and the migration path must agree."""
    from app.config import settings

    migrated_url = f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setattr(settings, "database_url", migrated_url)

    migrated = sa.create_engine(migrated_url)
    bootstrap = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'other.db'}")
    Base.metadata.create_all(bind=bootstrap)

    # The migration path.
    from alembic import command

    command.upgrade(alembic_config(), "head")

    assert current_revision(migrated) == HEAD
    _assert_same_schema(_database_snapshot(migrated), _model_snapshot())

    bootstrap_snapshot = _database_snapshot(bootstrap)
    _assert_same_schema(bootstrap_snapshot, _model_snapshot())

    # Both paths end up at the same physical schema.
    _assert_same_schema(_database_snapshot(migrated), bootstrap_snapshot)


def test_legacy_database_without_a_revision_is_repaired(tmp_path, monkeypatch):
    """A database created outside Alembic must be repaired, not left broken.

    This is the state the deployment was in: the API created the tables on a
    cold start, so ``alembic upgrade head`` failed with
    ``table sources already exists`` and the scheduled job never ran.
    """
    from app.config import settings

    url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    engine = sa.create_engine(url)
    Base.metadata.create_all(bind=engine)

    assert current_revision(engine) is None
    assert model_tables_present(engine)

    status = ensure_schema(engine)

    assert status == "stamped"
    assert current_revision(engine) == HEAD
    _assert_same_schema(_database_snapshot(engine), _model_snapshot())


def test_an_empty_database_is_migrated(tmp_path, monkeypatch):
    from app.config import settings

    url = f"sqlite+pysqlite:///{tmp_path / 'empty.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    engine = sa.create_engine(url)
    assert current_revision(engine) is None
    assert not model_tables_present(engine)

    status = ensure_schema(engine)

    assert status == "upgraded"
    assert current_revision(engine) == HEAD


def test_a_migrated_database_is_left_alone(tmp_path, monkeypatch):
    from app.config import settings

    url = f"sqlite+pysqlite:///{tmp_path / 'current.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    engine = sa.create_engine(url)
    ensure_schema(engine)

    status = ensure_schema(engine)

    assert status == "upgraded"
    assert current_revision(engine) == HEAD
