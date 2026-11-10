from typing import Protocol

from app.services.extractor import ExtractedOpportunity


class ProviderError(Exception):
    """Base class for provider failures. Messages must never include secrets."""


class ProviderConfigurationError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class ExtractionProvider(Protocol):
    name: str

    def extract(self, text: str) -> ExtractedOpportunity: ...
