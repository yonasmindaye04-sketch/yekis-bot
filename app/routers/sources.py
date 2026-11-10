from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Source
from app.schemas import SourceCreate, SourceRead

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceRead, status_code=201)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    source = Source(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("", response_model=list[SourceRead])
def list_sources(db: Session = Depends(get_db)):
    return list(db.scalars(select(Source).order_by(Source.id.desc())).all())


@router.patch("/{source_id}/status", response_model=SourceRead)
def set_source_status(source_id: int, is_active: bool, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    source.is_active = is_active
    db.commit()
    db.refresh(source)
    return source
