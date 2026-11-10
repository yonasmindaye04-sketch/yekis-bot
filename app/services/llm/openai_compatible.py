"""OpenAI-compatible chat-completions provider.

Works with OpenAI and any compatible endpoint. The prompt treats collected text
as untrusted data inside an explicit delimiter; the response is parsed as JSON
and validated by the closed :class:`LLMOpportunity` schema before it can reach
the database or the review queue.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.extractor import ExtractedOpportunity
from app.services.llm.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
)
from app.services.llm.parsing import parse_llm_json
from app.services.llm.prompts import SYSTEM_PROMPT, build_user_content


class OpenAICompatibleProvider:
    name = "llm"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_input_chars: int = 12000,
        max_output_tokens: int = 800,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ProviderConfigurationError("LLM_API_KEY is not configured")
        if not base_url:
            raise ProviderConfigurationError("LLM_BASE_URL is not configured")
        if not model:
            raise ProviderConfigurationError("LLM_MODEL is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._max_input_chars = max_input_chars
        self._max_output_tokens = max_output_tokens
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def __repr__(self) -> str:
        # Never let a credential reach a log line or an exception report.
        return f"OpenAICompatibleProvider(model={self._model!r}, base_url={self._base_url!r})"

    @property
    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": SYSTEM_PROMPT}]

    def _request_payload(self, text: str) -> dict[str, Any]:
        content = text[: self._max_input_chars]
        return {
            "model": self._model,
            "temperature": 0,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_content(content)},
            ],
        }

    def extract(self, text: str) -> ExtractedOpportunity:
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=self._request_payload(text),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("LLM request failed before a response was received") from exc

        if response.status_code >= 400:
            raise ProviderError(f"LLM request failed with status {response.status_code}")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                "LLM response did not match the completion envelope"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderResponseError("LLM response did not contain JSON content")

        return parse_llm_json(content)

    def close(self) -> None:
        self._client.close()
