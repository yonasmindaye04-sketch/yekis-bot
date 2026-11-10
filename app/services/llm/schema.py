"""Strict, closed schema for LLM-extracted opportunities.

Every field is explicit and unknown fields are rejected. The model is the
boundary: a provider either returns exactly this shape or the caller falls back
to the deterministic rules extractor.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.extractor import ExtractedOpportunity

OpportunityType = Literal[
    "freelance_job",
    "internship",
    "scholarship",
    "training",
    "resource",
    "news",
    "other",
]
Category = Literal["software", "design", "marketing", "media", "writing", "data", "admin", "other"]
RiskFlag = Literal[
    "upfront_payment",
    "off_platform_contact",
    "urgency_pressure",
    "implausible_rate",
]

Skill = Annotated[str, Field(min_length=1, max_length=60)]


class LLMOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opportunity_type: OpportunityType
    title: str = Field(min_length=1, max_length=250)
    category: Category | None = None
    skills: list[Skill] = Field(default_factory=list, max_length=20)
    budget_amount: float | None = Field(default=None, ge=0, le=1_000_000_000)
    budget_currency: Literal["USD", "ETB"] | None = None
    duration: str | None = Field(default=None, max_length=120)
    remote: bool | None = None
    description: str = Field(min_length=1, max_length=5000)
    quality_score: int = Field(default=0, ge=0, le=100)
    risk_flags: list[RiskFlag] = Field(default_factory=list, max_length=4)
    risk_score: int = Field(default=0, ge=0, le=100)

    @field_validator("skills")
    @classmethod
    def unique_skills(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for skill in value:
            normalized = " ".join(skill.split())
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                result.append(normalized)
        return result

    @field_validator("risk_flags")
    @classmethod
    def unique_risk_flags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def currency_matches_amount(self) -> LLMOpportunity:
        if (self.budget_amount is None) != (self.budget_currency is None):
            raise ValueError("budget_amount and budget_currency must be set together")
        return self

    def to_extracted(self) -> ExtractedOpportunity:
        return ExtractedOpportunity(
            opportunity_type=self.opportunity_type,
            title=self.title[:247],
            category=self.category,
            skills=self.skills,
            budget_amount=self.budget_amount,
            budget_currency=self.budget_currency,
            duration=self.duration,
            remote=self.remote,
            description=self.description[:5000],
            quality_score=self.quality_score,
            risk_flags=list(self.risk_flags),
            risk_score=self.risk_score,
        )
