from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
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
    budget_amount: Mapped[float | None]
    budget_currency: Mapped[str | None] = mapped_column(String(12))
    duration: Mapped[str | None] = mapped_column(String(120))
    remote: Mapped[bool | None]
    description: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(30), default="pending_review")
    extraction_method: Mapped[str] = mapped_column(String(30), default="rules")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post: Mapped[CollectedPost] = relationship(back_populates="opportunity")
