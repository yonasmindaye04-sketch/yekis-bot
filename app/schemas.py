from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    source_type: str = Field(pattern="^(telegram|website|rss|manual)$")
    source_url: str = Field(min_length=3, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    is_active: bool = True


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    source_url: str | None = Field(default=None, min_length=3, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    is_active: bool | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> "SourceUpdate":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self


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
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    reviewer_note: str | None = None
    original_text: str | None = None
    original_url: str | None = None
    external_id: str | None = None
    model_config = ConfigDict(from_attributes=True)


class OpportunityUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=250)
    category: str | None = Field(default=None, max_length=100)
    skills: list[str] | None = Field(default=None, max_length=20)
    budget_amount: float | None = Field(default=None, ge=0, le=1_000_000_000)
    budget_currency: str | None = Field(default=None, pattern="^(USD|ETB)$")
    duration: str | None = Field(default=None, max_length=120)
    remote: bool | None = None
    description: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def at_least_one(self) -> "OpportunityUpdate":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        return self

    @field_validator("skills")
    @classmethod
    def unique_skills(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        result: list[str] = []
        for skill in value:
            normalized = " ".join(skill.split())
            if normalized and normalized.casefold() not in seen:
                seen.add(normalized.casefold())
                result.append(normalized)
        return result


class ReviewUpdate(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|needs_review)$")
    reviewer_note: str | None = Field(default=None, max_length=2000)


class ReviewEventRead(BaseModel):
    id: int
    opportunity_id: int
    actor: str
    event_type: str
    from_status: str | None
    to_status: str
    note: str | None
    changes: dict | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PublishRequest(BaseModel):
    channel: str = Field(min_length=2, max_length=255)
    scheduled_at: datetime | None = None


class PublicationRead(BaseModel):
    id: int
    opportunity_id: int
    channel: str
    status: str
    external_message_id: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    error: str | None
    attempts: int
    created_by: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class Bucket(BaseModel):
    key: str
    count: int


class BudgetStat(BaseModel):
    currency: str
    count: int
    min_amount: float
    max_amount: float
    avg_amount: float
    median_amount: float | None = None


class AnalyticsOverview(BaseModel):
    total_sources: int
    active_sources: int
    total_posts: int
    total_opportunities: int
    by_status: dict[str, int]
    by_type: list[Bucket]
    by_category: list[Bucket]
    budget_by_currency: list[BudgetStat]
    risk_flagged: int
    avg_quality: float
    approval_rate: float
    published: int
    publication_failures: int
    last_collected_at: datetime | None


class SourceQualityRead(BaseModel):
    id: int
    name: str
    source_type: str
    is_active: bool
    posts: int
    opportunities: int
    approved: int
    rejected: int
    pending_review: int
    needs_review: int
    approval_rate: float
    risk_flagged: int
    avg_quality: float
    publications: int
    published: int
    publication_failures: int
    last_success_at: datetime | None
    last_collected_message_id: int | None
    consecutive_failures: int
    last_error: str | None


class MarketInsights(BaseModel):
    skills_demand: list[Bucket]
    budget: list[BudgetStat]
    remote_share: float
    by_category: list[Bucket]
    by_type: list[Bucket]


class TrendPoint(BaseModel):
    day: str
    posts: int
    opportunities: int
    approved: int
    published: int


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
