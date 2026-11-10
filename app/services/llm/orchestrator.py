"""Provider selection and safety merge.

An LLM result is never trusted on its own for risk: deterministic flags are
merged in, and an upfront-payment signal always suppresses the extracted budget.
A provider failure falls back to rules rather than blocking ingestion.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.config import Settings
from app.config import settings as default_settings
from app.services.extractor import ExtractedOpportunity, extract_opportunity
from app.services.llm.base import (
    ExtractionProvider,
    ProviderConfigurationError,
    ProviderError,
)
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.rules_provider import RulesProvider

logger = logging.getLogger(__name__)


def get_provider(settings: Settings | None = None) -> ExtractionProvider:
    settings = settings or default_settings
    provider = settings.extraction_provider.strip().lower()
    if provider == "rules":
        return RulesProvider()
    if provider == "llm":
        if not settings.llm_api_key:
            raise ProviderConfigurationError(
                "EXTRACTION_PROVIDER=llm requires LLM_API_KEY"
            )
        return OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_input_chars=settings.llm_max_input_chars,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    raise ProviderConfigurationError(
        f"unknown EXTRACTION_PROVIDER {settings.extraction_provider!r}; use 'rules' or 'llm'"
    )


def merge_safety(
    primary: ExtractedOpportunity, rules: ExtractedOpportunity
) -> ExtractedOpportunity:
    flags = list(dict.fromkeys(primary.risk_flags + rules.risk_flags))
    risk_score = max(primary.risk_score, rules.risk_score)
    budget_amount = primary.budget_amount
    budget_currency = primary.budget_currency
    if "upfront_payment" in flags:
        budget_amount = None
        budget_currency = None
    return replace(
        primary,
        category=primary.category or rules.category,
        skills=primary.skills or rules.skills,
        duration=primary.duration or rules.duration,
        remote=primary.remote if primary.remote is not None else rules.remote,
        budget_amount=budget_amount,
        budget_currency=budget_currency,
        risk_flags=flags,
        risk_score=risk_score,
    )


def extract_with_provider(
    text: str,
    provider: ExtractionProvider | None = None,
) -> tuple[ExtractedOpportunity, str]:
    """Return ``(extracted, extraction_method)``.

    ``extraction_method`` is one of ``rules``, ``llm``, or ``rules_fallback``.
    """

    if provider is None:
        try:
            provider = get_provider()
        except ProviderConfigurationError as exc:
            logger.warning("extraction provider unavailable; using rules: %s", exc)
            return extract_opportunity(text), "rules"

    if provider.name == "rules":
        return provider.extract(text), "rules"

    rules_result = extract_opportunity(text)
    try:
        llm_result = provider.extract(text)
    except ProviderError as exc:
        logger.warning("LLM extraction failed; falling back to rules: %s", exc)
        return rules_result, "rules_fallback"

    return merge_safety(llm_result, rules_result), "llm"
