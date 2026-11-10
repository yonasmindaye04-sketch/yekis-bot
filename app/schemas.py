from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    source_type: str = Field(pattern="^(telegram|website|rss|manual)$")
    source_url: str = Field(min_length=3, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    is_active: bool = True


class SourceRead(SourceCreate):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ManualIngestion(BaseModel):
    source_id: int
    external_id: str = Field(min_length=1, max_length=255)
    original_text: str = Field(min_length=10, max_length=30000)
    original_url: str | None = Field(default=None, max_length=1500)


class OpportunityRead(BaseModel):
    id: int
    collected_post_id: int
    opportunity_type: str
    title: str
    category: str | None
    skills: list
    budget_amount: float | None
    budget_currency: str | None
    duration: str | None
    remote: bool | None
    description: str | None
    quality_score: int
    review_status: str
    extraction_method: str
    model_config = ConfigDict(from_attributes=True)


class ReviewUpdate(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|needs_review)$")
    reviewer_note: str | None = Field(default=None, max_length=2000)


class PostRead(BaseModel):
    id: int
    source_id: int
    external_id: str
    original_text: str
    original_url: str | None
    collected_at: datetime
    model_config = ConfigDict(from_attributes=True)
