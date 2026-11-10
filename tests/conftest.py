import os

# app.config has a required DATABASE_URL; tests use an isolated SQLite database
# installed through the FastAPI dependency override below.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AUTO_CREATE_SCHEMA", "false")
os.environ.setdefault("AUTH_API_KEYS", "")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import Base, get_db  # noqa: E402
from app.models import Source  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSession() as session:
        yield session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_session):
    from app.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def source(db_session):
    item = Source(
        name="Audit test channel",
        source_type="telegram",
        source_url="https://t.me/audit_test_channel",
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item
