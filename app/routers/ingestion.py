from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CollectedPost
from app.schemas import ManualIngestion, OpportunityRead, PostRead
from app.security.auth import require_role
from app.services.ingestion import IngestionError, ingest_post

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post(
    "/manual",
    response_model=OpportunityRead,
    status_code=201,
    dependencies=[Depends(require_role("collector"))],
)
def ingest_manual(payload: ManualIngestion, db: Session = Depends(get_db)):
    try:
        opportunity, _ = ingest_post(
            db,
            source_id=payload.source_id,
            external_id=payload.external_id,
            original_text=payload.original_text,
            original_url=payload.original_url,
        )
    except IngestionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return opportunity


@router.post(
    "/collect",
    dependencies=[Depends(require_role("collector", "admin"))],
)
async def trigger_collection(
    source_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    from app.integrations.telegram.client import TelethonClient
    from app.integrations.telegram.collector import collect_sources

    client = TelethonClient()
    try:
        source_ids = [source_id] if source_id else None
        results = await collect_sources(db, client, source_ids=source_ids, limit=limit)
        return [
            {
                "source_id": r.source_id,
                "status": r.status,
                "fetched": r.fetched,
                "ingested": r.ingested,
                "duplicates": r.duplicates,
                "error": r.error,
            }
            for r in results
        ]
    finally:
        await client.close()


@router.get(
    "/posts",
    response_model=list[PostRead],
    dependencies=[Depends(require_role("collector", "reviewer", "analyst"))],
)
def list_posts(
    source_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    statement = select(CollectedPost).order_by(CollectedPost.id.desc())
    if source_id is not None:
        statement = statement.where(CollectedPost.source_id == source_id)
    return list(db.scalars(statement.limit(limit).offset(offset)).all())
