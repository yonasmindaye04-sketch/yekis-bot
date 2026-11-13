import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401
from app.config import settings
from app.db import Base, engine
from app.routers import (
    analytics,
    autonomy,
    health,
    ingestion,
    opportunities,
    publications,
    sources,
)
from app.security.auth import validate_security_config

STATIC_DIR = Path(__file__).parent / "static" / "admin"
IS_DEVELOPMENT = settings.app_env == "development"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level.upper())
    validate_security_config(settings)
    if settings.auto_create_schema and IS_DEVELOPMENT:
        Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS client_name VARCHAR(250);")
            )
    except Exception:
        pass
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
