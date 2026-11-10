from fastapi import APIRouter
from sqlalchemy import text

from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:
        return {"status": "degraded", "database": "unavailable"}
