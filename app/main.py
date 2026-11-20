import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401
from app.config import settings
from app.db import engine
from app.routers import (
    analytics,
    autonomy,
    health,
    ingestion,
    opportunities,
    publications,
    sources,
    telegram,
)
from app.schema import ensure_schema, model_tables_present
from app.security.auth import validate_security_config

STATIC_DIR = Path(__file__).parent / "static" / "admin"
IS_DEVELOPMENT = settings.app_env == "development"


def _ensure_schema(eng) -> None:
    """Bring the database schema to the current revision.

    Alembic owns the schema. When the migration scripts are not reachable the
    application falls back to creating the tables from its models, which is
    only ever acceptable for a local checkout.

    ``AUTO_CREATE_SCHEMA=false`` disables schema management completely: the
    database is then expected to be prepared out of band, and the application
    only reports what it finds.
    """
    logger = logging.getLogger(__name__)
    if not settings.auto_create_schema:
        logger.info("Schema management is disabled (AUTO_CREATE_SCHEMA=false)")
        try:
            if not model_tables_present(eng):
                logger.warning(
                    "the database has no tables yet; it is prepared by the migration "
                    "step (python -m scripts.migrate) of the deployment"
                )
        except Exception:  # pragma: no cover - the database may not exist yet
            logger.debug("could not inspect the database", exc_info=True)
        return
    try:
        status = ensure_schema(eng)
        logger.info("Schema is ready (%s)", status)
    except Exception as exc:
        logger.warning("Schema auto-ensure failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level.upper())
    validate_security_config(settings)
    _ensure_schema(engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if IS_DEVELOPMENT else None,
    redoc_url="/redoc" if IS_DEVELOPMENT else None,
    openapi_url="/openapi.json" if IS_DEVELOPMENT else None,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["X-API-Key", "Authorization", "Content-Type"],
    )

app.include_router(health.router)
app.include_router(sources.router, prefix=settings.api_prefix)
app.include_router(ingestion.router, prefix=settings.api_prefix)
app.include_router(opportunities.router, prefix=settings.api_prefix)
app.include_router(publications.router, prefix=settings.api_prefix)
app.include_router(analytics.router, prefix=settings.api_prefix)
app.include_router(autonomy.router, prefix=settings.api_prefix)
app.include_router(telegram.router, prefix=settings.api_prefix)
app.mount("/admin", StaticFiles(directory=STATIC_DIR, html=True), name="admin")


@app.get("/")
def root():
    payload = {
        "service": settings.app_name,
        "version": settings.app_version,
        "admin": "/admin/",
    }
    if IS_DEVELOPMENT:
        payload["docs"] = "/docs"
    return payload
