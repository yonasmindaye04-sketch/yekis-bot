import hashlib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import CollectedPost, Opportunity, Source
from app.schemas import ManualIngestion, PostRead, OpportunityRead
from app.services.extractor import extract_opportunity

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/manual", response_model=OpportunityRead, status_code=201)
def ingest_manual(payload: ManualIngestion, db: Session = Depends(get_db)):
    source = db.get(Source, payload.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.is_active:
        raise HTTPException(status_code=409, detail="Source is inactive")

    existing = db.scalar(select(CollectedPost).where(
        CollectedPost.source_id == payload.source_id,
        CollectedPost.external_id == payload.external_id
    ))
    if existing:
        raise HTTPException(status_code=409, detail="This source post has already been ingested")

    digest = hashlib.sha256(payload.original_text.encode("utf-8")).hexdigest()
    post = CollectedPost(
        source_id=payload.source_id,
        external_id=payload.external_id,
        original_text=payload.original_text,
        original_url=payload.original_url,
        content_hash=digest,
    )
    db.add(post)
    db.flush()

    extracted = extract_opportunity(payload.original_text)
    opportunity = Opportunity(
        collected_post_id=post.id,
        opportunity_type=extracted.opportunity_type,
        title=extracted.title,
        category=extracted.category,
        skills=extracted.skills,
        budget_amount=extracted.budget_amount,
        budget_currency=extracted.budget_currency,
        duration=extracted.duration,
        remote=extracted.remote,
        description=extracted.description,
        quality_score=extracted.quality_score,
        review_status="pending_review",
        extraction_method="rules",
    )
    db.add(opportunity)
    db.commit()
    db.refresh(opportunity)
    return opportunity


@router.get("/posts", response_model=list[PostRead])
def list_posts(db: Session = Depends(get_db)):
    return list(db.scalars(select(CollectedPost).order_by(CollectedPost.id.desc())).all())
