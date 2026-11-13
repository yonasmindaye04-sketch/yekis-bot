"""Stage 3 — AI classification provider.

The AI stage reuses the existing provider architecture: the same OpenAI
compatible transport (which covers NVIDIA NIM) and the same Gemini transport,
the same typed failures, and the same closed-schema validation. Only the
prompt and the output contract differ from extraction.

A provider failure is never fatal: the pipeline falls back to the
deterministic result and lowers the confidence, so a post is never lost
because a model was unavailable.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.config import Settings
from app.config import settings as default_settings
from app.services.classification.schema import AIPostClassification
from app.services.llm.base import ProviderConfigurationError, ProviderError

logger = logging.getLogger(__name__)


class ClassificationProvider(Protocol):
    """A provider that can classify a post."""

    name: str
    model: str

    def classify(self, text: str) -> AIPostClassification: ...


def get_classification_provider(
    settings: Settings | None = None,
) -> ClassificationProvider | None:
    """Return the configured classification provider, or ``None``.

    ``None`` means "deterministic only". That is the default and the safest
    configuration: without a second opinion no post can reach the automatic
    approval threshold.
    """
    settings = settings or default_settings
    provider_name = (settings.classification_provider or "rules").strip().lower()
    if provider_name in ("", "rules", "none", "deterministic"):
        return None

    from app.services.llm.gemini import GeminiProvider
    from app.services.llm.openai_compatible import OpenAICompatibleProvider
    from app.services.llm.orchestrator import parse_extra_body

    if provider_name == "llm":
        if not settings.llm_api_key:
            raise ProviderConfigurationError(
                "CLASSIFICATION_PROVIDER=llm requires LLM_API_KEY"
            )
        return OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.classification_model or settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_input_chars=settings.llm_max_input_chars,
            max_output_tokens=settings.llm_max_output_tokens,
            response_format=settings.llm_response_format,
            extra_body=parse_extra_body(settings.llm_extra_body),
            max_attempts=settings.llm_max_attempts,
            retry_wait_seconds=settings.llm_retry_wait_seconds,
        )
    if provider_name == "gemini":
        if not settings.gemini_api_key:
            raise ProviderConfigurationError(
                "CLASSIFICATION_PROVIDER=gemini requires GEMINI_API_KEY"
            )
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            model=settings.classification_model or settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_input_chars=settings.llm_max_input_chars,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    raise ProviderConfigurationError(
        f"unknown CLASSIFICATION_PROVIDER {settings.classification_provider!r}; "
        "use 'rules', 'llm', or 'gemini'"
    )


def classify_with_provider(
    text: str,
    provider: ClassificationProvider | None = None,
) -> tuple[AIPostClassification | None, str, str | None]:
    """Classify with the provider.

    Returns ``(result, provider_name, error)``. ``result`` is ``None`` when no
    provider is configured or the provider failed; ``error`` is a short,
    secret-free description of the failure.
    """
    if provider is None:
        try:
            provider = get_classification_provider()
        except ProviderConfigurationError as exc:
            logger.warning("classification provider unavailable; using rules: %s", exc)
            return None, "rules", str(exc)
    if provider is None:
        return None, "rules", None

    try:
        return provider.classify(text), getattr(provider, "name", "llm"), None
    except ProviderError as exc:
        logger.warning("AI classification failed; using deterministic result: %s", exc)
        return None, getattr(provider, "name", "llm"), str(exc)
