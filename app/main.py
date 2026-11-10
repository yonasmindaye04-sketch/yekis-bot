import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401
from app.config import settings
from app.db import Base, engine
from app.routers import health, ingestion, opportunities, sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level.upper())
    if settings.auto_create_schema and settings.app_env == "development":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(health.router)
app.include_router(sources.router, prefix=settings.api_prefix)
app.include_router(ingestion.router, prefix=settings.api_prefix)
app.include_router(opportunities.router, prefix=settings.api_prefix)


@app.get("/")
def root():
    return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}
