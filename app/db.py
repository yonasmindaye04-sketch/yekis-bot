from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # Always return the connection to the pool in a clean state. A request
        # that raised before commit would otherwise leave the pooled connection
        # in an aborted transaction and poison the next request.
        db.rollback()
        db.close()
