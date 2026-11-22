"""Classification taxonomy for the Yekis Intelligence opportunity engine.

The taxonomy answers one question: **does this post actually offer paid,
contracted work that somebody could apply for?**

It is deliberately separate from the *job category* (software, design, marketing
...). A post about a Python contract is a ``freelance_job`` in the ``software``
category. A post that only talks about Python is not a job at all.

Every value in this module is part of the persisted contract: the classifier,
the API filters, the dashboard, and the autonomy service all speak these
vocabularies.
"""

from __future__ import annotations

from typing import Final

# Bumped whenever the classification logic changes materially. It is stored on
# every classified opportunity so a decision can always be traced to the code
# version that produced it.
CLASSIFIER_VERSION: Final[str] = "yi-classifier-1.0.0"

# --- A. Publishable opportunity candidates --------------------------------
PUBLISHABLE_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "freelance_job",
    "project_contract",
    "remote_contract",
    "consulting_opportunity",
    "short_term_contract",
)

# --- B. Non-target opportunities (real work, wrong engagement type) ------
NON_TARGET_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "full_time_employment",
    "part_time_employment",
    "internship",
    "volunteering",
    "apprenticeship",
)

# --- C. Non-job content ---------------------------------------------------
NON_JOB_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "closed_or_filled_job",
    "educational_content",
    "skills_tips",
    "training_announcement",
    "general_announcement",
    "community_discussion",
    "marketplace_promotion",
    "news_and_information",
)

# --- D. Unsafe content ----------------------------------------------------
UNSAFE_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "suspicious_opportunity",
    "scam_or_fraud",
    "misleading_job_advertisement",
    "prohibited_or_unsafe_content",
)

# --- E. Uncertain ---------------------------------------------------------
UNCERTAIN_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "ambiguous",
    "insufficient_information",
    "needs_human_review",
)

ALL_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    PUBLISHABLE_CLASSIFICATIONS
    + NON_TARGET_CLASSIFICATIONS
    + NON_JOB_CLASSIFICATIONS
    + UNSAFE_CLASSIFICATIONS
    + UNCERTAIN_CLASSIFICATIONS
)

PUBLISHABLE_SET: Final[frozenset[str]] = frozenset(PUBLISHABLE_CLASSIFICATIONS)
NON_TARGET_SET: Final[frozenset[str]] = frozenset(NON_TARGET_CLASSIFICATIONS)
NON_JOB_SET: Final[frozenset[str]] = frozenset(NON_JOB_CLASSIFICATIONS)
UNSAFE_SET: Final[frozenset[str]] = frozenset(UNSAFE_CLASSIFICATIONS)
UNCERTAIN_SET: Final[frozenset[str]] = frozenset(UNCERTAIN_CLASSIFICATIONS)

#: Engagement types. A contract opportunity is freelance, project based,
#: consulting, or another non-permanent contracted engagement. Permanent
#: employment is deliberately absent: it is out of the publishing scope.
ENGAGEMENT_TYPES: Final[tuple[str, ...]] = PUBLISHABLE_CLASSIFICATIONS

#: Job categories. These describe *what kind of work* the opportunity is about.
#: They are never evidence that a post is a job.
JOB_CATEGORIES: Final[tuple[str, ...]] = (
    "software",
    "design",
    "marketing",
    "media",
    "writing",
    "data",
    "admin",
    "education",
    "other",
)

#: Free-text category words that map onto the canonical JOB_CATEGORIES
#: vocabulary.
#:
#: The dashboard stores what a human typed ("graphics", "video editor"), the
#: extractor emits the canonical values ("design", "media"). Without this
#: bridge the two never matched and every matching opportunity was silently
#: blocked by the category allowlist.
CATEGORY_SYNONYMS: Final[dict[str, str]] = {
    # design
    "graphic design": "design",
    "graphics": "design",
    "graphic designer": "design",
    "illustration": "design",
    "illustrator": "design",
    "animation": "design",
    "animator": "design",
    "motion graphics": "design",
    "ui ux": "design",
    "ui/ux": "design",
    "ux ui": "design",
    "logo": "design",
    "branding": "design",
    # media
    "video": "media",
    "video editor": "media",
    "video editing": "media",
    "photography": "media",
    "photo": "media",
    "audio": "media",
    "music": "media",
    "content creator": "media",
    "youtube": "media",
    # software
    "software development": "software",
    "web development": "software",
    "web": "software",
    "programming": "software",
    "developer": "software",
    "it": "software",
    # marketing
    "digital marketing": "marketing",
    "sales": "marketing",
    "seo": "marketing",
    "social media": "marketing",
    # writing
    "content writer": "writing",
    "translation": "writing",
    # data
    "data analysis": "data",
    "data analyst": "data",
    "data science": "data",
    # education
    "teaching": "education",
    "teacher": "education",
    "tutor": "education",
    "tutoring": "education",
    # admin
    "administration": "admin",
    "human resources": "admin",
    "hr": "admin",
    "office": "admin",
}


def canonical_category(value: str | None) -> str | None:
    """Map a category word onto the canonical JOB_CATEGORIES vocabulary.

    Returns ``None`` when the value cannot be mapped, which keeps the
    classification names ("freelance_job") usable as allowlist entries in
    their own right.
    """
    if not value:
        return None
    key = " ".join(str(value).strip().lower().split())
    if not key:
        return None
    if key in JOB_CATEGORIES:
        return key
    return CATEGORY_SYNONYMS.get(key)


#: Risk levels produced by the deterministic risk engine.
RISK_LEVELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "critical")

#: Autonomy operating modes (see docs/audit/CLASSIFICATION_ENGINE_PLAN.md).
AUTONOMY_MODES: Final[tuple[str, ...]] = ("shadow", "supervised", "production")

#: Decision labels recorded in the append-only autonomy decision log.
AUTONOMY_DECISIONS: Final[tuple[str, ...]] = (
    "auto_approved",
    "human_review_required",
    "auto_rejected",
    "quarantined",
    "would_auto_approve",
    "would_auto_reject",
    "auto_published",
    "publish_skipped",
)

#: Actor recorded for decisions taken by the system rather than by a person.
SYSTEM_ACTOR: Final[str] = "system:autonomy"

#: Default automatic approval threshold. Configurable at runtime; this value
#: is only the fallback when no dashboard override exists.
DEFAULT_AUTO_APPROVAL_THRESHOLD: Final[float] = 0.95


def is_publishable_classification(classification: str | None) -> bool:
    """True when the classification may enter the autonomous publish path."""
    return bool(classification) and classification in PUBLISHABLE_SET


def is_unsafe_classification(classification: str | None) -> bool:
    return bool(classification) and classification in UNSAFE_SET


def is_non_job_classification(classification: str | None) -> bool:
    return bool(classification) and classification in NON_JOB_SET


def is_non_target_classification(classification: str | None) -> bool:
    return bool(classification) and classification in NON_TARGET_SET
