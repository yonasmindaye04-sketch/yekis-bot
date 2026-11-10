from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    source_type: str = Field(pattern="^(telegram|website|rss|manual)$")
    source_url: str = Field(min_length=3, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    is_active: bool = True


class SourceRead(SourceCreate):
    id: int
    created_at: datetime
    last_collected_message_id: int | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_attempt_at: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    model_config = ConfigDict(from_attributes=True)


class SourceStatusUpdate(BaseModel):
    is_active: bool


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
    risk_flags: list
    risk_score: int
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
    content_hash: str
    collected_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("content_hash")
    @classmethod
    def hash_is_sha256(cls, value: str) -> str:
        if len(value) != 64:
            raise ValueError("content_hash must be a sha256 hex digest")
        return value
