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
    review_status: Mapped[str] = mapped_column(String(30), default="pending_review")
    extraction_method: Mapped[str] = mapped_column(String(30), default="rules")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    post: Mapped[CollectedPost] = relationship(back_populates="opportunity")
