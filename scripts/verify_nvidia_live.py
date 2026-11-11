"""Live provider verification for an OpenAI-compatible endpoint (NVIDIA NIM).

Runs a real request only when a valid key is configured locally, and records
the exact model ID, UTC test date, HTTP status, latency, and a field-by-field
comparison against deterministic rule extraction.

The API key is never printed, logged, or written to a report.

Usage:

    # NVIDIA NIM (default guard requires an nvidia.com base URL)
    python scripts/verify_nvidia_live.py

    # Explicitly point at another OpenAI-compatible endpoint
    python scripts/verify_nvidia_live.py --allow-non-nvidia

Exit codes: 0 verified, 3 skipped (no key / wrong endpoint), 1 failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime

from app.config import settings
from app.services.extractor import extract_opportunity
from app.services.llm.base import ProviderError
from app.services.llm.openai_compatible import OpenAICompatibleProvider

# One representative freelance post and one scam-shaped post.
SAMPLE_POSTS = [
    (
        "Senior Python Backend Engineer",
        (
            "We are hiring a Senior Python Backend Engineer for a fintech client. "
            "Required skills: Python, FastAPI, PostgreSQL, Docker. "
            "Budget: 2500 USD. Deadline: 3 weeks from now. Remote work."
        ),
    ),
    (
        "Upfront-Fee Scam Pattern",
        (
            "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. "
            "Pay upfront. Only 2 slots left, act now."
        ),
    ),
]

COMPARED_FIELDS = (
    "opportunity_type",
    "category",
    "skills",
    "budget_amount",
    "budget_currency",
    "duration",
    "remote",
    "risk_flags",
)


def _compare(llm_result, rules_result) -> dict:
    comparison = {}
    for field in COMPARED_FIELDS:
        llm_value = getattr(llm_result, field)
        rules_value = getattr(rules_result, field)
        comparison[field] = {
            "provider": llm_value,
            "rules": rules_value,
            "match": llm_value == rules_value,
        }
    return comparison


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live NVIDIA NIM verification")
    parser.add_argument("--allow-non-nvidia", action="store_true")
    args = parser.parse_args(argv)

    base_url = settings.llm_base_url.rstrip("/")
    model = settings.llm_model
    key = settings.llm_api_key

    report = {
        "test_date_utc": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "model_id": model,
        "provider": "openai-compatible",
        "live": False,
        "status": None,
        "latency_ms": None,
        "cases": [],
        "notes": [],
    }

    if not key:
        report["status"] = "skipped"
        report["notes"].append(
            "LLM_API_KEY is not configured locally; no live request was made."
        )
        _print_report(report)
        return 3

    if "nvidia" not in base_url.lower() and not args.allow_non_nvidia:
        report["status"] = "skipped"
        report["notes"].append(
            f"Base URL host is not nvidia.com ({base_url}); pass --allow-non-nvidia "
            "to verify another OpenAI-compatible endpoint."
        )
        _print_report(report)
        return 3

    provider = OpenAICompatibleProvider(
        api_key=key,
        base_url=base_url,
        model=model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_input_chars=settings.llm_max_input_chars,
        max_output_tokens=settings.llm_max_output_tokens,
        response_format=settings.llm_response_format,
    )

    try:
        for label, post in SAMPLE_POSTS:
            rules_result = extract_opportunity(post)
            started = time.perf_counter()
            status = 200
            error = None
            llm_result = None
            try:
                llm_result = provider.extract(post)
            except ProviderError as exc:
                status = 502
                error = str(exc)
            latency_ms = round((time.perf_counter() - started) * 1000, 1)

            case = {
                "label": label,
                "http_status": status,
                "latency_ms": latency_ms,
                "error": error,
            }
            if llm_result is not None:
                case["extraction_method"] = "llm"
                case["comparison"] = _compare(llm_result, rules_result)
            report["cases"].append(case)
            report["live"] = True
            if status != 200:
                report["status"] = "failed"
                report["notes"].append(f"{label}: provider error ({error}).")
    finally:
        provider.close()

    if report["status"] is None:
        report["status"] = "verified" if report["live"] else "failed"

    _print_report(report)
    return 0 if report["status"] == "verified" else 1


def _print_report(report: dict) -> None:
    # Defensive: the key must never reach stdout.
    rendered = json.dumps(report, indent=2, default=str)
    if settings.llm_api_key and settings.llm_api_key in rendered:
        raise SystemExit("refusing to print a report containing the API key")
    print(rendered)


if __name__ == "__main__":
    sys.exit(main())
