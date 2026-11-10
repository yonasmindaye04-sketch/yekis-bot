from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    AnalyticsOverview,
    MarketInsights,
    SourceQualityRead,
    TrendPoint,
)
from app.security.auth import require_role
from app.services import analytics

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(require_role("analyst", "reviewer"))],
)


@router.get("/overview", response_model=AnalyticsOverview)
def get_overview(db: Session = Depends(get_db)):
    return analytics.overview(db)


@router.get("/sources", response_model=list[SourceQualityRead])
def get_source_quality(db: Session = Depends(get_db)):
    return analytics.source_quality(db)


@router.get("/market", response_model=MarketInsights)
def get_market_insights(db: Session = Depends(get_db)):
    return analytics.market(db)


@router.get("/trends", response_model=list[TrendPoint])
def get_trends(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    return analytics.trends(db, days=days)
