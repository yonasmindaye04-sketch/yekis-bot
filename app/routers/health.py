import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
def health(response: Response):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:  # noqa: BLE001 - readiness must not leak driver details to clients
        logger.exception("health check: database unavailable")
        response.status_code = 503
        return {"status": "degraded", "database": "unavailable"}
