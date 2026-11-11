"""OpenAI-compatible chat-completions provider.

Works with OpenAI, NVIDIA NIM (``https://integrate.api.nvidia.com/v1``), and
any other endpoint that speaks the OpenAI chat-completions protocol. The
prompt treats collected text as untrusted data inside an explicit delimiter;
the response is parsed as JSON and validated by the closed
:class:`LLMOpportunity` schema before it can reach the database or the review
queue.

Compatibility knobs (all optional, none change the extraction architecture):

- ``response_format``: ``json_object`` by default; set to ``none`` for
  deployments that reject the JSON-mode parameter.
- ``extra_body``: provider-specific request fields (for example NVIDIA NIM
  ``guided_json``) merged into the request body.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.services.extractor import ExtractedOpportunity
from app.services.llm.base import (
    ProviderAuthError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
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
        response_format: str = "json_object",
        extra_body: dict[str, Any] | None = None,
        max_attempts: int = 1,
        retry_wait_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
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
        self._response_format = response_format
        self._extra_body = dict(extra_body or {})
        self._max_attempts = max(1, max_attempts)
        self._retry_wait_seconds = max(0.0, retry_wait_seconds)
        self._sleep = sleep
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def __repr__(self) -> str:
        # Never let a credential reach a log line or an exception report.
        return f"OpenAICompatibleProvider(model={self._model!r}, base_url={self._base_url!r})"

    @property
    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": SYSTEM_PROMPT}]

    def _request_payload(self, text: str) -> dict[str, Any]:
        content = text[: self._max_input_chars]
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": self._max_output_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_content(content)},
            ],
        }
        if self._response_format.strip().lower() not in ("", "none"):
            payload["response_format"] = {"type": self._response_format}
        # Provider-specific extras (for example NVIDIA guided decoding) are
        # merged last so they can add fields without clobbering the standard
        # request shape.
        payload.update(self._extra_body)
        return payload

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map provider HTTP failures onto typed, secret-free errors."""
        status = response.status_code
        if status in (401, 403):
            raise ProviderAuthError(
                f"LLM provider rejected the credential with status {status}"
            )
        if status == 429:
            retry_after = response.headers.get("retry-after")
            suffix = f" (retry-after: {retry_after})" if retry_after else ""
            raise ProviderRateLimitError(
                f"LLM provider rate limited the request with status 429{suffix}"
            )
        if status >= 500:
            raise ProviderUnavailableError(
                f"LLM provider is unavailable (status {status})"
            )
        raise ProviderError(f"LLM request failed with status {status}")

    def extract(self, text: str) -> ExtractedOpportunity:
        """Extract with bounded retries for transient provider failures.

        Timeouts, rate limits, and 5xx responses are retried (NVIDIA NIM cold
        starts routinely exceed a single request timeout). Credential and
        schema failures are not retried: they are deterministic and the
        caller falls back to rules extraction instead.
        """
        last_error: ProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._extract_once(text)
            except (ProviderRateLimitError, ProviderUnavailableError) as exc:
                last_error = exc
                if attempt >= self._max_attempts:
                    raise
                self._sleep(self._retry_wait_seconds * attempt)
        raise last_error or ProviderError("LLM request failed")

    def _extract_once(self, text: str) -> ExtractedOpportunity:
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
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "LLM request timed out before a response was received"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "LLM request failed before a response was received"
            ) from exc

        if response.status_code >= 400:
            self._raise_for_status(response)

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
