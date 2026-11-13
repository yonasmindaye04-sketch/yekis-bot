"""Classification engine for Yekis Intelligence.

The engine answers one question: does this post actually offer paid, contracted
work? The stages are:

* Stage 1 :func:`app.services.classification.normalize.normalize_post`
* Stage 2 :func:`app.services.classification.deterministic.analyze`
* Stage 3 :func:`app.services.classification.pipeline.classify_post` (AI part)
* Stage 4 :func:`app.services.classification.risk.assess`
* Stage 5 and 6 :func:`app.services.classification.pipeline.classify_post`
"""

from app.services.classification.normalize import NormalizedPost, normalize_post
from app.services.classification.pipeline import (
    DECISION_AUTO_APPROVE,
    DECISION_AUTO_REJECT,
    DECISION_HUMAN_REVIEW,
    DECISION_QUARANTINE,
    ClassificationResult,
    classify_post,
)
from app.services.classification.taxonomy import (
    ALL_CLASSIFICATIONS,
    AUTONOMY_DECISIONS,
    AUTONOMY_MODES,
    CLASSIFIER_VERSION,
    DEFAULT_AUTO_APPROVAL_THRESHOLD,
    ENGAGEMENT_TYPES,
    JOB_CATEGORIES,
    NON_JOB_CLASSIFICATIONS,
    NON_TARGET_CLASSIFICATIONS,
    PUBLISHABLE_CLASSIFICATIONS,
    RISK_LEVELS,
    SYSTEM_ACTOR,
    UNCERTAIN_CLASSIFICATIONS,
    UNSAFE_CLASSIFICATIONS,
)

__all__ = [
    "ALL_CLASSIFICATIONS",
    "AUTONOMY_DECISIONS",
    "AUTONOMY_MODES",
    "CLASSIFIER_VERSION",
    "ClassificationResult",
    "DECISION_AUTO_APPROVE",
    "DECISION_AUTO_REJECT",
    "DECISION_HUMAN_REVIEW",
    "DECISION_QUARANTINE",
    "DEFAULT_AUTO_APPROVAL_THRESHOLD",
    "ENGAGEMENT_TYPES",
    "JOB_CATEGORIES",
    "NON_JOB_CLASSIFICATIONS",
    "NON_TARGET_CLASSIFICATIONS",
    "NormalizedPost",
    "PUBLISHABLE_CLASSIFICATIONS",
    "RISK_LEVELS",
    "SYSTEM_ACTOR",
    "UNCERTAIN_CLASSIFICATIONS",
    "UNSAFE_CLASSIFICATIONS",
    "classify_post",
    "normalize_post",
]
