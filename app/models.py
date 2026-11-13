from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
    return datetime.now(UTC)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Collector checkpoints and source health (M1). Only the collector service
    # writes these; the API exposes them read-only through SourceRead.
    last_collected_message_id: Mapped[int | None] = mapped_column(Integer)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))

    posts: Mapped[list["CollectedPost"]] = relationship(back_populates="source")


class CollectedPost(Base):
    __tablename__ = "collected_posts"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_source_external_post"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    original_url: Mapped[str | None] = mapped_column(String(1500))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="posts")
    opportunity: Mapped["Opportunity | None"] = relationship(back_populates="post", uselist=False)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collected_post_id: Mapped[int] = mapped_column(ForeignKey("collected_posts.id"), unique=True)
    opportunity_type: Mapped[str] = mapped_column(String(40), default="other")
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100))
    skills: Mapped[list] = mapped_column(JSON, default=list)
    budget_amount: Mapped[float | None] = mapped_column(Float)
    budget_currency: Mapped[str | None] = mapped_column(String(12))
    duration: Mapped[str | None] = mapped_column(String(120))
    remote: Mapped[bool | None]
    description: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(
        String(30), default="pending_review", index=True
    )
    extraction_method: Mapped[str] = mapped_column(String(30), default="rules")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewer_note: Mapped[str | None] = mapped_column(Text)

    # --- Classification engine (separate from the job category) ----------
    # `category` describes the subject matter of the work. `classification`
    # describes what the post actually is. A post can be classified
    # "educational_content" and still be categorised "education".
    classification: Mapped[str] = mapped_column(
        String(40), default="insufficient_information", index=True
    )
    engagement_type: Mapped[str | None] = mapped_column(String(40))
    is_genuine_opportunity: Mapped[bool] = mapped_column(Boolean, default=False)
    is_publishable: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classification_evidence: Mapped[list] = mapped_column(JSON, default=list)
    classification_reasons: Mapped[list] = mapped_column(JSON, default=list)
    classification_method: Mapped[str] = mapped_column(
        String(30), default="deterministic"
    )
    classifier_version: Mapped[str | None] = mapped_column(String(30))
    model_provider: Mapped[str | None] = mapped_column(String(30))
    model_name: Mapped[str | None] = mapped_column(String(120))
    risk_level: Mapped[str] = mapped_column(String(20), default="none", index=True)
    risk_assessment: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- Approval record --------------------------------------------------
    # approval_method is "manual" for a human decision and "auto" for a
    # decision taken by the autonomy engine.
    approval_method: Mapped[str | None] = mapped_column(String(20))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(120))

    # A quarantined opportunity is kept for audit but can never be published.
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Extracted engagement details -------------------------------------
    deadline: Mapped[str | None] = mapped_column(String(120))
    application_url: Mapped[str | None] = mapped_column(String(1000))

    post: Mapped[CollectedPost] = relationship(back_populates="opportunity", lazy="joined")
    events: Mapped[list["ReviewEvent"]] = relationship(
        back_populates="opportunity", order_by="ReviewEvent.id"
    )
    publications: Mapped[list["Publication"]] = relationship(back_populates="opportunity")

    @property
    def original_text(self) -> str | None:
        return self.post.original_text if self.post else None

    @property
    def original_url(self) -> str | None:
        return self.post.original_url if self.post else None

    @property
    def external_id(self) -> str | None:
        return self.post.external_id if self.post else None


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "channel", name="uq_publication_opportunity_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    # Autonomous publications are re-checked at send time and are subject to
    # the daily limit and the minimum interval between posts.
    is_autonomous: Mapped[bool] = mapped_column(Boolean, default=False)
    autonomy_mode: Mapped[str | None] = mapped_column(String(20))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="publications")


class AutonomySettings(Base):
    """Runtime configuration for the autonomous approval and publishing engine.

    The environment provides the defaults; this single-row table holds the
    dashboard overrides. Row id 1 is the only row that is ever used.
    """

    __tablename__ = "autonomy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(20), default="shadow")
    auto_approval_threshold: Mapped[float] = mapped_column(Float, default=0.95)
    approved_source_ids: Mapped[list] = mapped_column(JSON, default=list)
    approved_categories: Mapped[list] = mapped_column(JSON, default=list)
    daily_publish_limit: Mapped[int] = mapped_column(Integer, default=5)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=900)
    pilot_channel: Mapped[str | None] = mapped_column(String(255))
    target_channel: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))


class AutonomyDecision(Base):
    """Append-only log of every automatic decision.

    A row is written for every autonomous approval, rejection, quarantine,
    review request, and skipped publication. Rows are never updated or
    deleted: they are the audit trail that explains why the system did what
    it did.
    """

    __tablename__ = "autonomy_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), nullable=False, index=True
    )
    source_id: Mapped[int | None] = mapped_column(Integer, index=True)
    decision: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    classification: Mapped[str | None] = mapped_column(String(40), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    mode: Mapped[str] = mapped_column(String(20), default="shadow")
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ReviewEvent(Base):
    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, default="status_change")
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    opportunity: Mapped[Opportunity] = relationship(back_populates="events")


class JobExecution(Base):
    __tablename__ = "job_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    payload: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeadLetterJob(Base):
    __tablename__ = "dead_letter_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_execution_id: Mapped[int] = mapped_column(
        ForeignKey("job_executions.id"), unique=True, nullable=False
    )
    task_name: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
