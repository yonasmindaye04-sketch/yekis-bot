"""Stage 3 — structured output contract for AI classification.

The schema is closed (``extra="forbid"``): a model that returns an unknown
field, an impossible budget, or a classification that contradicts its own
publishability flag is rejected and the caller falls back to the
deterministic result.

The schema describes *what the model claims*. It is not evidence: every
invented value is removed by :func:`ground_ai_classification` before it can
reach the database.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.services.classification.taxonomy import (
    ALL_CLASSIFICATIONS,
    ENGAGEMENT_TYPES,
    JOB_CATEGORIES,
    PUBLISHABLE_SET,
    RISK_LEVELS,
)
from app.services.llm.base import ProviderResponseError

Classification = Literal[*ALL_CLASSIFICATIONS]
EngagementType = Literal[*ENGAGEMENT_TYPES]
JobCategory = Literal[*JOB_CATEGORIES]
RiskLevel = Literal[*RISK_LEVELS]


class AIPostClassification(BaseModel):
    """Structured classification returned by an AI provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    classification: Classification
    job_category: JobCategory | None = None
    engagement_type: EngagementType | None = None
    is_genuine_opportunity: bool
    is_publishable: bool
    confidence: float = Field(ge=0.0, le=1.0)
    title: str | None = Field(default=None, max_length=250)
    skills: list[str] = Field(default_factory=list, max_length=20)
    budget: float | None = Field(default=None, ge=0, le=1_000_000_000)
    currency: Literal["USD", "ETB"] | None = None
    duration: str | None = Field(default=None, max_length=120)
    deadline: str | None = Field(default=None, max_length=120)
    client_name: str | None = Field(default=None, max_length=180)
    application_url: str | None = Field(default=None, max_length=1000)
    risk_level: RiskLevel = "none"
    risk_flags: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("skills")
    @classmethod
    def _normalise_skills(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for skill in value:
            normalized = " ".join(skill.split()).lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @field_validator("risk_flags", "evidence", "rejection_reasons")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def _consistency(self) -> AIPostClassification:
        if (self.budget is None) != (self.currency is None):
            raise ValueError("budget and currency must be set together")
        if self.is_publishable and self.classification not in PUBLISHABLE_SET:
            raise ValueError(
                "is_publishable may only be true for a publishable classification"
            )
        if self.is_publishable and not self.is_genuine_opportunity:
            raise ValueError(
                "is_publishable requires is_genuine_opportunity to be true"
            )
        if self.is_genuine_opportunity and self.classification not in PUBLISHABLE_SET:
            raise ValueError(
                "is_genuine_opportunity may only be true for a publishable classification"
            )
        if self.engagement_type and self.classification not in PUBLISHABLE_SET:
            raise ValueError(
                "engagement_type may only be set for a publishable classification"
            )
        return self


def parse_classification_json(content: str) -> AIPostClassification:
    """Parse and validate a classification payload.

    Error messages contain field paths only; model output is never echoed.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(
            "classification response was not valid JSON"
        ) from exc
    try:
        return AIPostClassification.model_validate(parsed)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProviderResponseError(
            f"classification response failed schema validation: {fields}"
        ) from exc


class GroundingResult:
    """Outcome of checking model output against the original post."""

    def __init__(self, value: AIPostClassification, notes: list[str]) -> None:
        self.value = value
        self.notes = notes


def _grounded(value: str | None, original: str) -> bool:
    """True when a claimed value can be found in the original post."""
    if value is None:
        return True
    needle = " ".join(str(value).lower().split())
    if not needle:
        return True
    haystack = " ".join(original.lower().split())
    if needle in haystack:
        return True
    # Numbers are written differently by models ("300.0" for "300").
    try:
        number = float(needle)
    except ValueError:
        return False
    for candidate in {str(int(number)), str(number), f"{number:g}"}:
        if candidate in haystack:
            return True
    return False


def ground_ai_classification(
    result: AIPostClassification, original_text: str
) -> GroundingResult:
    """Remove any value the model could not have read from the post.

    Invented budgets, deadlines, clients, and links are dropped and reported.
    A dropped value is a hallucination signal: the caller lowers the
    confidence and routes the post to human review.
    """
    notes: list[str] = []
    data = result.model_dump()
    dropped: list[str] = []

    for field_name in ("budget", "currency", "duration", "deadline", "client_name"):
        value = data.get(field_name)
        if value is None:
            continue
        if not _grounded(value, original_text):
            dropped.append(field_name)
            data[field_name] = None

    # The budget and its currency are one fact. If one of them is dropped,
    # the other one must go with it.
    if "budget" in dropped:
        dropped.append("currency")
        data["currency"] = None
    if "currency" in dropped:
        dropped.append("budget")
        data["budget"] = None

    application_url = data.get("application_url")
    if application_url and not _grounded(application_url, original_text):
        dropped.append("application_url")
        data["application_url"] = None

    title = data.get("title")
    if title and not _grounded(title, original_text):
        # A paraphrased title is acceptable, so the title is kept, but a title
        # that shares no meaningful token with the post is treated as invented.
        original_tokens = set(_tokenize(original_text))
        title_tokens = set(_tokenize(str(title)))
        if title_tokens and not (title_tokens & original_tokens):
            dropped.append("title")
            data["title"] = None

    if dropped:
        notes.append(
            "The model produced values that are not present in the post and they "
            "were discarded: " + ", ".join(sorted(set(dropped)))
        )

    sanitized = AIPostClassification.model_validate(data)
    return GroundingResult(sanitized, notes)


def _tokenize(text: str) -> list[str]:
    cleaned = "".join(char if char.isalnum() else " " for char in text.lower())
    return [token for token in cleaned.split() if len(token) > 2]
