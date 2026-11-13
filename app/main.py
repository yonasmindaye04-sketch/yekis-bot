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


def _ensure_schema(eng) -> None:
    try:
        import sqlalchemy as sa
        Base.metadata.create_all(bind=eng)
        inspector = sa.inspect(eng)
        existing_tables = set(inspector.get_table_names())
        if "opportunities" in existing_tables:
            existing_opp_cols = {c["name"] for c in inspector.get_columns("opportunities")}
            opp_columns = [
                ("client_name", "VARCHAR(250)", None),
                ("classification", "VARCHAR(40)", "'insufficient_information'"),
                ("engagement_type", "VARCHAR(40)", None),
                ("is_genuine_opportunity", "BOOLEAN", "false"),
                ("is_publishable", "BOOLEAN", "false"),
                ("classification_confidence", "FLOAT", "0.0"),
                ("classification_evidence", "JSON", "'[]'"),
                ("classification_reasons", "JSON", "'[]'"),
                ("classification_method", "VARCHAR(30)", "'deterministic'"),
                ("classifier_version", "VARCHAR(30)", None),
                ("model_provider", "VARCHAR(30)", None),
                ("model_name", "VARCHAR(120)", None),
                ("risk_level", "VARCHAR(20)", "'none'"),
                ("risk_assessment", "JSON", "'{}'"),
                ("approval_method", "VARCHAR(20)", None),
                ("approval_reason", "TEXT", None),
                ("approved_at", "TIMESTAMPTZ", None),
                ("approved_by", "VARCHAR(120)", None),
                ("quarantined", "BOOLEAN", "false"),
                ("deadline", "VARCHAR(120)", None),
                ("application_url", "VARCHAR(1000)", None),
            ]
            with eng.begin() as conn:
                for name, col_type, default in opp_columns:
                    if name not in existing_opp_cols:
                        default_clause = f" DEFAULT {default}" if default is not None else ""
                        conn.execute(sa.text(f"ALTER TABLE opportunities ADD COLUMN {name} {col_type}{default_clause}"))

        if "publications" in existing_tables:
            existing_pub_cols = {c["name"] for c in inspector.get_columns("publications")}
            pub_columns = [
                ("is_autonomous", "BOOLEAN", "false"),
                ("autonomy_mode", "VARCHAR(20)", None),
                ("content_hash", "VARCHAR(64)", None),
            ]
            with eng.begin() as conn:
                for name, col_type, default in pub_columns:
                    if name not in existing_pub_cols:
                        default_clause = f" DEFAULT {default}" if default is not None else ""
                        conn.execute(sa.text(f"ALTER TABLE publications ADD COLUMN {name} {col_type}{default_clause}"))
    except Exception as exc:
        logging.getLogger(__name__).warning("Schema auto-ensure failed: %s", exc)


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
