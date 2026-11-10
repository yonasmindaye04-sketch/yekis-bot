"""Google Gemini provider using an AI Studio API key.

Uses the native Generative Language API rather than an OpenAI-compatible shim:

    POST {base_url}/models/{model}:generateContent
    x-goog-api-key: <AI Studio key>

The key stays in the request header and is never placed in the URL, logged, or
included in ``repr``. The response is still parsed and validated by the same
closed Pydantic schema as every other provider.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.services.extractor import ExtractedOpportunity
from app.services.llm.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
)
from app.services.llm.parsing import parse_llm_json
from app.services.llm.prompts import SYSTEM_PROMPT, build_user_content


class GeminiProvider:
    name = "gemini"

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
            raise ProviderConfigurationError("GEMINI_API_KEY is not configured")
        if not base_url:
            raise ProviderConfigurationError("GEMINI_BASE_URL is not configured")
        if not model:
            raise ProviderConfigurationError("GEMINI_MODEL is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model.removeprefix("models/")
        self._timeout = timeout_seconds
        self._max_input_chars = max_input_chars
        self._max_output_tokens = max_output_tokens
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def __repr__(self) -> str:
        return f"GeminiProvider(model={self._model!r}, base_url={self._base_url!r})"

    def _request_payload(self, text: str) -> dict[str, Any]:
        content = text[: self._max_input_chars]
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": build_user_content(content)}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": self._max_output_tokens,
                "responseMimeType": "application/json",
            },
        }

    def extract(self, text: str) -> ExtractedOpportunity:
        model = quote(self._model, safe="-._")
        url = f"{self._base_url}/models/{model}:generateContent"
        try:
            response = self._client.post(
                url,
                headers={
                    "x-goog-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json=self._request_payload(text),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Gemini request failed before a response was received"
            ) from exc

        if response.status_code >= 400:
            raise ProviderError(f"Gemini request failed with status {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Gemini response was not valid JSON") from exc

        if not isinstance(data, dict):
            raise ProviderResponseError("Gemini response was not a JSON object")

        candidates = data.get("candidates")
        if not candidates:
            feedback = data.get("promptFeedback")
            reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            suffix = f": {reason}" if reason else ""
            raise ProviderResponseError(f"Gemini returned no candidates{suffix}")

        try:
            parts = candidates[0]["content"]["parts"]
            content = "".join(
                part.get("text", "") for part in parts if isinstance(part, dict)
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                "Gemini response did not match the completion envelope"
            ) from exc

        if not content.strip():
            raise ProviderResponseError("Gemini response did not contain JSON content")

        return parse_llm_json(content)

    def close(self) -> None:
        self._client.close()
