from typing import Protocol

from app.services.extractor import ExtractedOpportunity


class ProviderError(Exception):
    """Base class for provider failures. Messages must never include secrets."""


class ProviderConfigurationError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    """The provider rejected the credential (401/403). Not retryable as-is."""


class ProviderRateLimitError(ProviderError):
    """The provider is rate limiting (429). Retryable after a cool-down."""


class ProviderUnavailableError(ProviderError):
    """The provider is temporarily unavailable (5xx or transport failure)."""


class ExtractionProvider(Protocol):
    name: str

    def extract(self, text: str) -> ExtractedOpportunity: ...
