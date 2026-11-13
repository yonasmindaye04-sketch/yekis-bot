"""Stage 5 and 6 — classification decision and publication eligibility.

The pipeline merges four inputs into one traceable verdict:

1. the deterministic job detection (Stage 2);
2. the optional AI classification (Stage 3);
3. the deterministic risk assessment (Stage 4);
4. the configured confidence threshold.

Two rules dominate every merge:

* **Precision over recall.** A disagreement between the deterministic rule
  engine and the model never produces a publishable verdict. It produces
  human review.
* **A model can never lower a risk verdict.** The risk level is computed from
  the deterministic engine alone. The model's risk opinion can only raise it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.services.classification.ai import classify_with_provider
from app.services.classification.deterministic import DeterministicAnalysis, analyze
from app.services.classification.normalize import NormalizedPost, normalize_post
from app.services.classification.risk import (
    AUTONOMY_BLOCKING_FLAGS,
    RiskAssessment,
    assess,
)
from app.services.classification.schema import (
    AIPostClassification,
    ground_ai_classification,
)
from app.services.classification.taxonomy import (
    CLASSIFIER_VERSION,
    DEFAULT_AUTO_APPROVAL_THRESHOLD,
    NON_JOB_SET,
    NON_TARGET_SET,
    PUBLISHABLE_SET,
    UNSAFE_SET,
    is_publishable_classification,
)

logger = logging.getLogger(__name__)

#: Confidence required before content that is clearly not a publishable
#: opportunity is rejected automatically. The automatic *approval* threshold is
#: deliberately higher: rejecting a good post is cheap, publishing a bad one
#: is not.
AUTO_REJECT_CONFIDENCE = 0.9

#: Confidence ceiling for a classification that only the deterministic stage
#: produced. Without the AI stage there is no second opinion, so no post may
#: reach the automatic approval threshold. The autonomy engine therefore
#: records shadow decisions until a model is configured.
DETERMINISTIC_CONFIDENCE_CAP = 0.9

#: Decisions produced by the pipeline.
DECISION_AUTO_APPROVE = "auto_approve_eligible"
DECISION_AUTO_REJECT = "auto_reject"
DECISION_HUMAN_REVIEW = "human_review"
DECISION_QUARANTINE = "quarantine"


@dataclass
class ClassificationResult:
    """The complete, traceable verdict for one post."""

    classification: str
    engagement_type: str | None = None
    job_category: str | None = None
    is_genuine_opportunity: bool = False
    is_publishable: bool = False
    confidence: float = 0.0
    decision: str = DECISION_HUMAN_REVIEW
    evidence: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    risk_level: str = "none"
    risk_flags: list[str] = field(default_factory=list)
    risk_assessment: dict = field(default_factory=dict)
    method: str = "deterministic"
    model_provider: str | None = None
    model_name: str | None = None
    classifier_version: str = CLASSIFIER_VERSION
    ai_notes: list[str] = field(default_factory=list)
    detected_fields: dict = field(default_factory=dict)
    normalized_text: str = ""

    @property
    def is_unsafe(self) -> bool:
        return self.classification in UNSAFE_SET


def _grounded_terms(terms: list[str], original: str) -> list[str]:
    """Keep only the terms that actually appear in the post."""
    haystack = " ".join(original.lower().split())
    result: list[str] = []
    for term in terms:
        needle = " ".join(term.lower().split())
        if needle and needle in haystack:
            result.append(term)
    return result


def _merge_detected_fields(ai: AIPostClassification, original: str) -> dict:
    """Field values the model produced that are present in the post."""
    detected: dict = {}
    for name in (
        "title",
        "skills",
        "budget",
        "currency",
        "duration",
        "deadline",
        "client_name",
        "application_url",
    ):
        value = getattr(ai, name, None)
        if value in (None, [], ""):
            continue
        if name == "skills":
            grounded = _grounded_terms(list(value), original)
            if grounded:
                detected["skills"] = grounded
            continue
        detected[name] = value
    return detected


def _risk_level_order(level: str) -> int:
    order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return order.get(level, 0)


def classify_post(
    text: str,
    *,
    provider=None,
    threshold: float = DEFAULT_AUTO_APPROVAL_THRESHOLD,
    post: NormalizedPost | None = None,
    use_ai: bool = True,
) -> ClassificationResult:
    """Run the full classification pipeline over one post."""
    normalized = post or normalize_post(text)
    deterministic: DeterministicAnalysis = analyze(normalized)
    risk: RiskAssessment = assess(normalized)

    result = ClassificationResult(
        classification=deterministic.classification,
        engagement_type=deterministic.engagement_type,
        confidence=deterministic.confidence,
        evidence=list(deterministic.evidence),
        rejection_reasons=list(deterministic.reasons),
        risk_level=risk.risk_level,
        risk_flags=list(risk.risk_flags),
        risk_assessment={
            "risk_level": risk.risk_level,
            "risk_score": risk.risk_score,
            "flags": list(risk.risk_flags),
            "findings": list(risk.findings),
            "details": dict(risk.details),
        },
        normalized_text=normalized.text,
    )

    ai_result, provider_name, provider_error = (None, "rules", None)
    if use_ai:
        ai_result, provider_name, provider_error = classify_with_provider(
            normalized.original_text, provider
        )
    if provider_error:
        result.ai_notes.append(f"AI classification unavailable: {provider_error}")

    disagreement = False
    if ai_result is not None:
        grounded = ground_ai_classification(ai_result, normalized.original_text)
        ai = grounded.value
        result.ai_notes.extend(grounded.notes)
        result.model_provider = provider_name
        result.model_name = getattr(provider, "model", None)
        result.method = "ai+deterministic"
        result.detected_fields = _merge_detected_fields(ai, normalized.original_text)

        if grounded.notes:
            # Invented values are a hallucination signal: never auto-approve.
            ai_confidence = ai.confidence * 0.6
        else:
            ai_confidence = ai.confidence

        if ai.classification == deterministic.classification:
            result.classification = ai.classification
            result.engagement_type = ai.engagement_type or result.engagement_type
            result.job_category = ai.job_category
            result.confidence = min(
                0.99, ai_confidence * 0.6 + deterministic.confidence * 0.4
            )
            result.evidence.extend(ai.evidence)
            result.rejection_reasons.extend(ai.rejection_reasons)
        else:
            disagreement = True
            result.classification = _safer_classification(
                deterministic.classification, ai.classification
            )
            result.confidence = min(
                0.7, max(deterministic.confidence, ai.confidence) * 0.6
            )
            result.rejection_reasons.append(
                "The deterministic classifier and the AI classifier disagree "
                f"({deterministic.classification} vs {ai.classification}); a human "
                "must confirm the classification."
            )
            result.evidence.append(
                "Classification disagreement detected between the rule engine and the model"
            )

        # The model may only raise the risk level, never lower it.
        if _risk_level_order(ai.risk_level) > _risk_level_order(result.risk_level):
            result.risk_level = ai.risk_level
            result.risk_assessment["risk_level"] = ai.risk_level
            result.risk_assessment["model_risk_level"] = ai.risk_level
        for flag in ai.risk_flags:
            if flag not in result.risk_flags:
                result.risk_flags.append(flag)
                result.risk_assessment.setdefault("flags", []).append(flag)

    if ai_result is None:
        # Deterministic only: the verdict is capped below the automatic
        # approval threshold.
        result.confidence = min(result.confidence, DETERMINISTIC_CONFIDENCE_CAP)

    result.is_genuine_opportunity = is_publishable_classification(result.classification)
    _apply_risk_classification(result)
    result.decision = _decide(result, threshold=threshold, disagreement=disagreement)
    result.is_publishable = result.decision == DECISION_AUTO_APPROVE
    return result


def _apply_risk_classification(result: ClassificationResult) -> None:
    """Let the deterministic risk engine name the unsafe content.

    A scam, an impersonation, or a misleading advertisement is not an
    "insufficient information" post: the classification must say so, because
    the label drives the dashboard filters and the audit trail.
    """
    flags = set(result.risk_flags)
    if "prompt_injection" in flags:
        # The post tried to address the classifier. It is never processed
        # automatically and never auto-rejected: a human decides.
        result.classification = "needs_human_review"
        result.confidence = min(result.confidence, 0.5)
        return
    if result.risk_level not in ("high", "critical"):
        return
    if flags & {
        "upfront_payment",
        "prohibited_content",
        "payment_request",
        "crypto_payment",
    }:
        result.classification = "scam_or_fraud"
    elif flags & {
        "implausible_rate",
        "deceptive_description",
        "off_platform_evasion",
        "unrealistic_promise",
    }:
        result.classification = "suspicious_opportunity"
    elif flags & {"impersonation", "sensitive_data_request", "unverifiable_client"}:
        result.classification = "misleading_job_advertisement"
    else:
        return
    result.confidence = max(result.confidence, 0.9)
    result.rejection_reasons.append(
        "The deterministic risk engine classified the post as unsafe: "
        + ", ".join(sorted(flags))
    )


def _safer_classification(deterministic: str, ai: str) -> str:
    """Pick the label that cannot cause a bad publish.

    A non-publishable label always wins over a publishable one, because a
    false "freelance job" is what damages trust. When both labels are
    publishable but different, the post goes to human review.
    """
    deterministic_publishable = deterministic in PUBLISHABLE_SET
    ai_publishable = ai in PUBLISHABLE_SET
    if deterministic_publishable and not ai_publishable:
        return ai
    if ai_publishable and not deterministic_publishable:
        return deterministic
    return "needs_human_review"


def _decide(
    result: ClassificationResult, *, threshold: float, disagreement: bool
) -> str:
    """Assign the decision for a classified post."""
    if result.risk_level in ("high", "critical"):
        result.rejection_reasons.append(
            "The deterministic risk engine reported a high or critical risk indicator; "
            "the post is quarantined and cannot be published automatically."
        )
        return DECISION_QUARANTINE

    if "prompt_injection" in result.risk_flags:
        result.rejection_reasons.append(
            "The post contains instructions aimed at the classification system; "
            "it is never processed automatically."
        )
        return DECISION_HUMAN_REVIEW

    if disagreement:
        return DECISION_HUMAN_REVIEW

    if result.classification in UNSAFE_SET:
        result.rejection_reasons.append(
            "The post was classified as unsafe and is quarantined for human review."
        )
        return DECISION_QUARANTINE

    if result.classification in PUBLISHABLE_SET:
        if result.confidence < threshold:
            result.rejection_reasons.append(
                f"Confidence {result.confidence:.2f} is below the automatic approval "
                f"threshold {threshold:.2f}; a human must review the post."
            )
            return DECISION_HUMAN_REVIEW
        if result.ai_notes:
            result.rejection_reasons.append(
                "The model produced values that could not be verified in the post."
            )
            return DECISION_HUMAN_REVIEW
        if AUTONOMY_BLOCKING_FLAGS & set(result.risk_flags):
            result.rejection_reasons.append(
                "A deterministic risk indicator blocks automatic approval of this post."
            )
            return DECISION_HUMAN_REVIEW
        return DECISION_AUTO_APPROVE

    if (
        result.classification in NON_JOB_SET or result.classification in NON_TARGET_SET
    ) and result.confidence >= AUTO_REJECT_CONFIDENCE:
        return DECISION_AUTO_REJECT

    return DECISION_HUMAN_REVIEW
