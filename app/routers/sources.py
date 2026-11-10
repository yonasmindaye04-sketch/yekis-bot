from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Source
from app.schemas import SourceCreate, SourceRead, SourceStatusUpdate, SourceUpdate
from app.security.auth import require_role

router = APIRouter(prefix="/sources", tags=["sources"])
ANY_ROLE = ("admin", "reviewer", "collector", "analyst")


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "source_not_found", "message": "Source not found"},
    )


@router.post(
    "",
    response_model=SourceRead,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    source = Source(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("", response_model=list[SourceRead], dependencies=[Depends(require_role(*ANY_ROLE))])
def list_sources(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return list(
        db.scalars(select(Source).order_by(Source.id.desc()).limit(limit).offset(offset)).all()
    )


@router.get(
    "/{source_id}",
    response_model=SourceRead,
    dependencies=[Depends(require_role(*ANY_ROLE))],
)
def get_source(source_id: int, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise _not_found()
    return source


@router.patch(
    "/{source_id}",
    response_model=SourceRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise _not_found()
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return source


@router.patch(
    "/{source_id}/status",
    response_model=SourceRead,
    dependencies=[Depends(require_role("admin"))],
)
def set_source_status(
    source_id: int, payload: SourceStatusUpdate, db: Session = Depends(get_db)
):
    source = db.get(Source, source_id)
    if source is None:
        raise _not_found()
    source.is_active = payload.is_active
    db.commit()
    db.refresh(source)
    return source
