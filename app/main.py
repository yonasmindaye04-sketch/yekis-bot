from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings
from app.db import Base, engine
from app import models  # noqa: F401
from app.routers import health, sources, ingestion, opportunities


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_schema and settings.app_env == "development":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(sources.router, prefix=settings.api_prefix)
app.include_router(ingestion.router, prefix=settings.api_prefix)
app.include_router(opportunities.router, prefix=settings.api_prefix)


@app.get("/")
def root():
    return {"service": settings.app_name, "version": "0.1.0", "docs": "/docs"}
