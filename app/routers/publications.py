from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Publication
from app.schemas import PublicationRead
from app.security.auth import require_role

router = APIRouter(prefix="/publications", tags=["publications"])


@router.get(
    "",
    response_model=list[PublicationRead],
    dependencies=[Depends(require_role("admin", "reviewer", "analyst"))],
)
def list_publications(
    status: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    statement = select(Publication).order_by(Publication.id.desc())
    if status:
        statement = statement.where(Publication.status == status)
    return list(db.scalars(statement.limit(limit).offset(offset)).all())


@router.get(
    "/{publication_id}",
    response_model=PublicationRead,
    dependencies=[Depends(require_role("admin", "reviewer", "analyst"))],
)
def get_publication(publication_id: int, db: Session = Depends(get_db)):
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "publication_not_found", "message": "Publication not found"},
        )
    return publication
