"""Verify which extraction provider the running service actually used.

Drives the live API (no direct provider calls) by ingesting a sample post and
reporting what the service recorded. This proves the running containers have
picked up the intended provider configuration.

Usage:

    python scripts/verify_extraction_provider.py --base-url http://127.0.0.1:8100

The script creates a source, ingests two posts (one legitimate, one scam
pattern), prints the recorded extraction method and fields, then deactivates
the source it created.

Exit codes: 0 provider verified, 1 provider mismatch or API error.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx

from app.config import settings

LEGIT_POST = (
    "We are hiring a Senior Python Backend Engineer for a fintech client. "
    "Required skills: Python, FastAPI, PostgreSQL, Docker. "
    "Budget: 2500 USD. Deadline: 3 weeks from now. Remote work."
)

SCAM_POST = (
    "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. "
    "Pay upfront. Only 2 slots left, act now."
)


def load_keys_by_role() -> dict[str, str]:
    try:
        parsed = json.loads(settings.auth_api_keys)
    except json.JSONDecodeError:
        return {}
    by_role: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(value, dict):
            role = str(value.get("role", "")).lower()
            by_role.setdefault(role, key)
    return by_role


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the live extraction provider")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument(
        "--expect",
        default="llm",
        choices=["llm", "gemini", "rules", "rules_fallback"],
        help="Extraction method the service should have recorded",
    )
    args = parser.parse_args(argv)

    keys = load_keys_by_role()
    key = keys.get("admin") or keys.get("collector")
    if not key:
        print("No admin or collector key found in AUTH_API_KEYS.")
        return 1

    tag = uuid.uuid4().hex[:8]
    base = args.base_url.rstrip("/")
    headers = {"X-API-Key": key, "Content-Type": "application/json"}

    with httpx.Client(base_url=base, timeout=120.0) as client:
        response = client.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "name": f"Provider verification {tag}",
                "source_type": "manual",
                "source_url": f"https://t.me/provider_verify_{tag}",
                "category": "software",
                "is_active": True,
            },
        )
        if response.status_code != 201:
            print(f"Could not create a source: HTTP {response.status_code} {response.text}")
            return 1
        source_id = response.json()["id"]
        print(f"Created verification source id={source_id}")

        methods: list[str] = []
        try:
            for label, external_id, text in (
                ("legitimate post", f"verify-{tag}-1", LEGIT_POST),
                ("scam-pattern post", f"verify-{tag}-2", SCAM_POST),
            ):
                response = client.post(
                    "/api/v1/ingestion/manual",
                    headers=headers,
                    json={
                        "source_id": source_id,
                        "external_id": external_id,
                        "original_text": text,
                    },
                )
                if response.status_code != 201:
                    print(f"{label}: ingest failed HTTP {response.status_code} {response.text}")
                    return 1
                body = response.json()
                methods.append(body["extraction_method"])
                print(f"\n{label}:")
                print(f"  extraction_method : {body['extraction_method']}")
                print(f"  opportunity_type  : {body['opportunity_type']}")
                print(f"  category          : {body['category']}")
                print(f"  skills            : {body['skills']}")
                print(f"  budget            : {body['budget_amount']} {body['budget_currency']}")
                print(f"  duration          : {body['duration']}")
                print(f"  remote            : {body['remote']}")
                print(f"  risk_flags        : {body['risk_flags']} (score {body['risk_score']})")
                print(f"  review_status     : {body['review_status']}")
                print(f"  quality_score     : {body['quality_score']}")
        finally:
            client.patch(
                f"/api/v1/sources/{source_id}/status",
                headers=headers,
                json={"is_active": False},
            )
            print(f"\nDeactivated verification source id={source_id}")

    expected = args.expect
    if expected == "rules_fallback":
        ok = all(method == "rules_fallback" for method in methods)
    else:
        ok = all(method == expected for method in methods)

    print(f"\nExpected extraction_method={expected}; observed {methods}")
    if ok:
        print("PASS: the running service used the expected provider.")
        return 0
    print("FAIL: the running service used a different provider.")
    print(
        "If the containers are stale, recreate them so they pick up the new .env:\n"
        "  docker compose up -d --force-recreate api worker beat"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
