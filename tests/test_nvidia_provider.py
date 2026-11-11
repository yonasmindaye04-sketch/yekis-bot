"""NVIDIA NIM provider tests through the existing OpenAI-compatible adapter.

These tests verify that NVIDIA's hosted inference endpoint
(``https://integrate.api.nvidia.com/v1``) works with the existing provider
architecture: the same request shape, the same strict schema validation, the
same deterministic risk merge, and the same rules fallback.

No test performs a live network call. A live verification is executed only by
``scripts/verify_nvidia_live.py`` and only when a real NVIDIA key is configured
locally.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.config import Settings
from app.services.ingestion import ingest_post
from app.services.llm.base import (
    ProviderAuthError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.services.llm.openai_compatible import OpenAICompatibleProvider
from app.services.llm.orchestrator import (
    extract_with_provider,
    get_provider,
    parse_extra_body,
)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "meta/llama-3.3-70b-instruct"

JOB_POST = (
    "We are hiring a Senior Python Backend Engineer for a fintech client. "
    "Required skills: Python, FastAPI, PostgreSQL, Docker. "
    "Budget: 2500 USD. Deadline: 3 weeks from now. Remote work. "
    "Send your CV to the email address hr@example.com."
)

SCAM_POST = (
    "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. "
    "Pay upfront. Only 2 slots left, act now."
)


def _payload(**overrides):
    payload = {
        "opportunity_type": "freelance_job",
        "title": "Senior Python Backend Engineer",
        "category": "software",
        "skills": ["python", "fastapi", "postgresql", "docker"],
        "budget_amount": 2500.0,
        "budget_currency": "USD",
        "duration": "3 weeks",
        "remote": True,
        "description": "Backend engineering role for a fintech client.",
        "quality_score": 85,
        "risk_flags": [],
        "risk_score": 0,
    }
    payload.update(overrides)
    return payload


def _nvidia_provider(
    content: str | dict | None = None,
    *,
    status: int = 200,
    handler=None,
    **overrides,
) -> OpenAICompatibleProvider:
    if handler is None:
        body = content if content is not None else json.dumps(_payload())

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status,
                json={"choices": [{"message": {"content": body}}]},
            )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    options = {
        "api_key": "nvapi-test-key-not-real",
        "base_url": NVIDIA_BASE_URL,
        "model": NVIDIA_MODEL,
        "timeout_seconds": 10.0,
        "client": client,
    }
    options.update(overrides)
    return OpenAICompatibleProvider(**options)


# --- 1. Valid NVIDIA API response ---------------------------------------


def test_valid_nvidia_response_is_extracted():
    provider = _nvidia_provider()
    result = provider.extract(JOB_POST)

    assert result.opportunity_type == "freelance_job"
    assert result.category == "software"
    assert result.budget_amount == 2500.0
    assert result.budget_currency == "USD"
    assert result.duration == "3 weeks"
    assert "python" in result.skills
    assert result.remote is True


def test_nvidia_request_shape_matches_openai_protocol():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = _nvidia_provider(handler=handler)
    provider._request_payload(JOB_POST)  # payload construction only

    payload = provider._request_payload(JOB_POST)
    assert payload["model"] == NVIDIA_MODEL
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 800
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["content"].startswith("<untrusted_content>")


def test_nvidia_endpoint_url_and_bearer_auth():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_payload())}}]}
        )

    provider = _nvidia_provider(handler=handler)
    provider.extract(JOB_POST)

    request = captured[0]
    assert str(request.url) == f"{NVIDIA_BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer nvapi-test-key-not-real"
    assert "nvapi-test-key-not-real" not in request.content.decode()


# --- 2. Invalid API key ---------------------------------------------------


def test_invalid_api_key_raises_auth_error():
    provider = _nvidia_provider(status=401, content={"detail": "invalid api key"})
    with pytest.raises(ProviderAuthError, match="rejected the credential"):
        provider.extract(JOB_POST)


def test_forbidden_response_raises_auth_error():
    provider = _nvidia_provider(status=403, content={"detail": "forbidden"})
    with pytest.raises(ProviderAuthError):
        provider.extract(JOB_POST)


# --- 3. Provider timeout --------------------------------------------------


def test_provider_timeout_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _nvidia_provider(handler=handler)
    with pytest.raises(ProviderUnavailableError, match="timed out"):
        provider.extract(JOB_POST)


# --- 4. Rate limit --------------------------------------------------------


def test_rate_limit_response_raises_rate_limit_error():
    provider = _nvidia_provider(status=429, content={"detail": "rate limited"})
    with pytest.raises(ProviderRateLimitError, match="rate limited"):
        provider.extract(JOB_POST)


def test_rate_limit_exposes_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "30"})

    provider = _nvidia_provider(handler=handler)
    with pytest.raises(ProviderRateLimitError, match="retry-after: 30"):
        provider.extract(JOB_POST)


# --- 5. Malformed JSON ----------------------------------------------------


def test_malformed_json_is_rejected():
    provider = _nvidia_provider(content="this is not json at all")
    with pytest.raises(ProviderResponseError, match="not valid JSON"):
        provider.extract(JOB_POST)


def test_empty_content_is_rejected():
    provider = _nvidia_provider(content="")
    with pytest.raises(ProviderResponseError, match="did not contain JSON"):
        provider.extract(JOB_POST)


def test_unexpected_envelope_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = _nvidia_provider(handler=handler)
    with pytest.raises(ProviderResponseError, match="completion envelope"):
        provider.extract(JOB_POST)


# --- 6. Invalid structured output -----------------------------------------


def test_unknown_field_is_rejected():
    provider = _nvidia_provider(content=json.dumps(_payload(injected_field="x")))
    with pytest.raises(ProviderResponseError, match="schema validation"):
        provider.extract(JOB_POST)


def test_amount_without_currency_is_rejected():
    provider = _nvidia_provider(content=json.dumps(_payload(budget_currency=None)))
    with pytest.raises(ProviderResponseError, match="schema validation"):
        provider.extract(JOB_POST)


def test_out_of_range_score_is_rejected():
    provider = _nvidia_provider(content=json.dumps(_payload(quality_score=500)))
    with pytest.raises(ProviderResponseError, match="schema validation"):
        provider.extract(JOB_POST)


# --- 7. Rules fallback ----------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "match"),
    [
        (_nvidia_provider(status=401, content={}), "rejected the credential"),
        (_nvidia_provider(status=429, content={}), "rate limited"),
        (_nvidia_provider(status=503, content={}), "unavailable"),
        (_nvidia_provider(content="not json"), "not valid JSON"),
        (_nvidia_provider(content=json.dumps(_payload(extra="x"))), "schema validation"),
    ],
)
def test_provider_failure_falls_back_to_rules(provider, match):
    result, method = extract_with_provider(JOB_POST, provider=provider)

    assert method == "rules_fallback"
    assert result.opportunity_type == "freelance_job"
    assert result.budget_amount == 2500.0
    assert result.budget_currency == "USD"
    assert "python" in result.skills


# --- 8. Extraction accuracy: budget, currency, skills, deadline -----------


def test_extraction_accuracy_for_budget_currency_skills_and_deadline():
    provider = _nvidia_provider()
    result = provider.extract(JOB_POST)

    assert result.budget_amount == 2500.0
    assert result.budget_currency == "USD"
    assert result.duration == "3 weeks"
    assert {"python", "fastapi", "postgresql", "docker"} <= set(result.skills)


def test_nvidia_extraction_is_merged_with_deterministic_rules():
    provider = _nvidia_provider()
    result, method = extract_with_provider(JOB_POST, provider=provider)

    assert method == "llm"
    assert result.budget_amount == 2500.0
    # Deterministic rules fill anything the model left empty.
    assert result.category == "software"
    assert result.duration == "3 weeks"


# --- 9. Suspicious upfront-payment detection ------------------------------


def test_upfront_payment_is_detected_even_when_the_model_ignores_it():
    provider = _nvidia_provider(content=json.dumps(_payload(budget_amount=5000.0)))
    result, method = extract_with_provider(SCAM_POST, provider=provider)

    assert method == "llm"
    assert "upfront_payment" in result.risk_flags
    assert result.risk_score >= 40
    assert result.budget_amount is None, "bait budget must be suppressed"
    assert result.budget_currency is None


def test_model_reported_upfront_payment_is_preserved():
    provider = _nvidia_provider(
        content=json.dumps(_payload(risk_flags=["upfront_payment"], risk_score=45))
    )
    result, _ = extract_with_provider(SCAM_POST, provider=provider)

    assert "upfront_payment" in result.risk_flags
    assert result.budget_amount is None


# --- 10. Preservation of original post text --------------------------------


def test_original_post_text_is_preserved_verbatim(db_session, source):
    provider = _nvidia_provider()
    opportunity, created = ingest_post(
        db_session,
        source_id=source.id,
        external_id="nvidia-1",
        original_text=JOB_POST,
        provider=provider,
    )

    assert created is True
    assert opportunity.extraction_method == "llm"
    from app.models import CollectedPost

    post = db_session.query(CollectedPost).filter_by(external_id="nvidia-1").one()
    assert post.original_text == JOB_POST


def test_provider_never_echoes_the_api_key_in_errors():
    provider = _nvidia_provider(status=401, content={"detail": "invalid key"})
    with pytest.raises(ProviderError) as excinfo:
        provider.extract(JOB_POST)

    assert "nvapi-test-key-not-real" not in str(excinfo.value)
    assert "nvapi-test-key-not-real" not in repr(provider)


# --- Configuration plumbing ------------------------------------------------


def test_get_provider_builds_nvidia_provider_from_settings():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        extraction_provider="llm",
        llm_api_key="nvapi-test-key-not-real",
        llm_base_url=NVIDIA_BASE_URL,
        llm_model=NVIDIA_MODEL,
    )
    provider = get_provider(settings)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._model == NVIDIA_MODEL
    assert provider._base_url == NVIDIA_BASE_URL
    assert "nvapi-test-key-not-real" not in repr(provider)


def test_response_format_can_be_disabled_for_endpoints_that_reject_it():
    provider = _nvidia_provider(response_format="none")
    payload = provider._request_payload(JOB_POST)
    assert "response_format" not in payload


def test_extra_body_is_merged_for_guided_decoding():
    provider = _nvidia_provider(extra_body={"guided_json": {"type": "object"}})
    payload = provider._request_payload(JOB_POST)

    assert payload["guided_json"] == {"type": "object"}
    assert payload["model"] == NVIDIA_MODEL
    assert payload["messages"][0]["role"] == "system"


def test_parse_extra_body_rejects_malformed_json():
    with pytest.raises(ProviderConfigurationError, match="valid JSON"):
        parse_extra_body("{not json")
    with pytest.raises(ProviderConfigurationError, match="JSON object"):
        parse_extra_body("[1, 2]")
    assert parse_extra_body("") == {}
    assert parse_extra_body(None) == {}


def test_provider_requires_a_key():
    with pytest.raises(ProviderConfigurationError, match="LLM_API_KEY"):
        OpenAICompatibleProvider(api_key="", base_url=NVIDIA_BASE_URL, model=NVIDIA_MODEL)


def test_extraction_latency_is_measurable_without_a_network_call():
    provider = _nvidia_provider()
    started = time.perf_counter()
    provider.extract(JOB_POST)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 5000


# --- Bounded retry for transient failures ---------------------------------


def test_timeout_is_retried_once_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ReadTimeout("cold start", request=request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_payload())}}]}
        )

    waits: list[float] = []
    provider = _nvidia_provider(handler=handler, max_attempts=2, retry_wait_seconds=5.0,
                                sleep=waits.append)

    result = provider.extract(JOB_POST)

    assert result.budget_amount == 2500.0
    assert calls["count"] == 2
    assert waits == [5.0]


def test_rate_limit_is_retried_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_payload())}}]}
        )

    waits: list[float] = []
    provider = _nvidia_provider(handler=handler, max_attempts=2, retry_wait_seconds=5.0,
                                sleep=waits.append)

    provider.extract(JOB_POST)

    assert calls["count"] == 2
    assert waits == [5.0]


def test_transient_failure_exhausts_attempts_then_falls_back_to_rules():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("still cold", request=request)

    waits: list[float] = []
    provider = _nvidia_provider(handler=handler, max_attempts=3, retry_wait_seconds=2.0,
                                sleep=waits.append)

    result, method = extract_with_provider(JOB_POST, provider=provider)

    assert method == "rules_fallback"
    assert calls["count"] == 3
    assert waits == [2.0, 4.0]


def test_credential_and_schema_failures_are_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"detail": "invalid key"})

    waits: list[float] = []
    provider = _nvidia_provider(handler=handler, max_attempts=3, retry_wait_seconds=1.0,
                                sleep=waits.append)

    with pytest.raises(ProviderAuthError):
        provider.extract(JOB_POST)

    assert calls["count"] == 1, "a bad credential must not be retried"
    assert waits == []


def test_provider_skills_are_normalised_to_lowercase():
    provider = _nvidia_provider(
        content=json.dumps(_payload(skills=["Python", "FastAPI", "Python", "  PostgreSQL  "]))
    )
    result = provider.extract(JOB_POST)

    assert result.skills == ["python", "fastapi", "postgresql"]


def test_settings_enable_bounded_retries():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        extraction_provider="llm",
        llm_api_key="nvapi-test-key-not-real",
        llm_base_url=NVIDIA_BASE_URL,
        llm_model=NVIDIA_MODEL,
        llm_max_attempts=3,
        llm_retry_wait_seconds=2.0,
    )
    provider = get_provider(settings)

    assert provider._max_attempts == 3
    assert provider._retry_wait_seconds == 2.0


# --- Live verification script (offline logic) ------------------------------


def test_live_verification_script_skips_without_a_key(monkeypatch, capsys):
    from app.config import settings as global_settings
    from scripts import verify_nvidia_live

    monkeypatch.setattr(global_settings, "llm_api_key", None)
    monkeypatch.setattr(global_settings, "llm_base_url", NVIDIA_BASE_URL)

    exit_code = verify_nvidia_live.main([])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert report["status"] == "skipped"
    assert report["live"] is False
    assert "no live request was made" in report["notes"][0]


def test_live_verification_script_refuses_a_non_nvidia_endpoint(monkeypatch, capsys):
    from app.config import settings as global_settings
    from scripts import verify_nvidia_live

    monkeypatch.setattr(global_settings, "llm_api_key", "nvapi-test-key-not-real")
    monkeypatch.setattr(global_settings, "llm_base_url", "https://api.openai.com/v1")

    exit_code = verify_nvidia_live.main([])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert report["status"] == "skipped"
    assert "nvapi-test-key-not-real" not in json.dumps(report)


def test_live_verification_compares_provider_against_rules():
    from app.services.extractor import extract_opportunity
    from scripts.verify_nvidia_live import _compare

    rules = extract_opportunity(JOB_POST)
    comparison = _compare(rules, rules)

    assert set(comparison) == {
        "opportunity_type",
        "category",
        "skills",
        "budget_amount",
        "budget_currency",
        "duration",
        "remote",
        "risk_flags",
    }
    assert all(entry["match"] for entry in comparison.values())


def test_live_verification_report_refuses_to_print_a_key(monkeypatch):
    from app.config import settings as global_settings
    from scripts import verify_nvidia_live

    monkeypatch.setattr(global_settings, "llm_api_key", "nvapi-test-key-not-real")
    report = {"model_id": NVIDIA_MODEL, "notes": []}

    with pytest.raises(SystemExit, match="containing the API key"):
        verify_nvidia_live._print_report({**report, "notes": ["nvapi-test-key-not-real"]})
