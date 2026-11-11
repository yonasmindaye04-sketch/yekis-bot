"""Live functional smoke test for a running Yekis Intelligence API.

Runs the full product workflow against a live server and prints a pass/fail
report. It never prints API keys, tokens, or message content beyond the
strings this script creates itself.

Usage (from the project root, with the venv active):

    uvicorn app.main:app --port 8100
    python scripts/smoke_test.py --base-url http://127.0.0.1:8100

Exit code is 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import httpx

from app.config import settings

RESULTS: list[tuple[bool, str, str]] = []


def record(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def load_keys_by_role() -> dict[str, str]:
    """Resolve one API key per role from AUTH_API_KEYS without printing it."""
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Live functional smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="HTTP timeout in seconds; raise it when the service extracts with a "
        "slow provider (NVIDIA NIM can take ~2 minutes per request)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    keys = load_keys_by_role()
    admin = keys.get("admin")
    reviewer = keys.get("reviewer")
    collector = keys.get("collector")
    analyst = keys.get("analyst")
    record(bool(admin), "admin key configured", "AUTH_API_KEYS")
    if not admin:
        print("Cannot continue without an admin key.")
        return 1

    def headers(key: str | None) -> dict[str, str]:
        return {"X-API-Key": key} if key else {}

    tag = uuid.uuid4().hex[:8]
    source_url = f"https://t.me/smoke_test_{tag}"

    with httpx.Client(base_url=base, timeout=args.timeout) as client:
        # --- public surface -------------------------------------------------
        response = client.get("/health")
        record(
            response.status_code == 200 and response.json().get("database") == "connected",
            "GET /health reports the database connected",
            f"HTTP {response.status_code}",
        )

        response = client.get("/admin/")
        record(
            response.status_code == 200 and "Yekis Intelligence" in response.text,
            "GET /admin/ serves the dashboard",
            f"HTTP {response.status_code}",
        )

        response = client.get("/")
        record(response.status_code == 200, "GET / service banner", f"HTTP {response.status_code}")

        # --- authentication matrix -----------------------------------------
        response = client.get("/api/v1/sources")
        record(
            response.status_code == 401,
            "anonymous API access is rejected",
            f"HTTP {response.status_code}",
        )

        response = client.get("/api/v1/sources", headers=headers("not-a-real-key"))
        record(
            response.status_code == 401,
            "invalid API key is rejected",
            f"HTTP {response.status_code}",
        )

        response = client.get("/api/v1/sources", headers=headers(admin))
        record(
            response.status_code == 200,
            "admin key can read sources",
            f"HTTP {response.status_code}",
        )

        if reviewer:
            response = client.post(
                "/api/v1/sources",
                headers=headers(reviewer),
                json={"name": "x", "source_type": "manual", "source_url": source_url},
            )
            record(
                response.status_code == 403,
                "reviewer cannot create sources",
                f"HTTP {response.status_code}",
            )

        if collector:
            response = client.patch(
                "/api/v1/opportunities/1/review",
                headers=headers(collector),
                json={"decision": "approved"},
            )
            record(
                response.status_code == 403,
                "collector cannot review opportunities",
                f"HTTP {response.status_code}",
            )

        # --- source lifecycle ----------------------------------------------
        response = client.post(
            "/api/v1/sources",
            headers=headers(admin),
            json={
                "name": f"Smoke test source {tag}",
                "source_type": "manual",
                "source_url": source_url,
                "category": "software",
                "is_active": True,
            },
        )
        record(
            response.status_code == 201,
            "POST /sources creates a source",
            f"HTTP {response.status_code}",
        )
        if response.status_code != 201:
            return finish()
        source_id = response.json()["id"]

        response = client.patch(
            f"/api/v1/sources/{source_id}",
            headers=headers(admin),
            json={"category": "design"},
        )
        record(
            response.status_code == 200 and response.json()["category"] == "design",
            "PATCH /sources/{id} edits a source",
            f"HTTP {response.status_code}",
        )

        response = client.patch(
            f"/api/v1/sources/{source_id}/status",
            headers=headers(admin),
            json={"is_active": False},
        )
        record(
            response.status_code == 200 and response.json()["is_active"] is False,
            "PATCH /sources/{id}/status toggles activity",
            f"HTTP {response.status_code}",
        )

        response = client.patch(
            f"/api/v1/sources/{source_id}/status",
            headers=headers(admin),
            json={"is_active": True},
        )
        record(
            response.status_code == 200,
            "source can be re-activated",
            f"HTTP {response.status_code}",
        )

        # --- ingestion ------------------------------------------------------
        post_text = (
            "Looking for a React developer to build a dashboard. "
            "Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks. Remote."
        )
        response = client.post(
            "/api/v1/ingestion/manual",
            headers=headers(admin),
            json={
                "source_id": source_id,
                "external_id": f"smoke-{tag}",
                "original_text": post_text,
                "original_url": f"{source_url}/1",
            },
        )
        record(
            response.status_code == 201,
            "POST /ingestion/manual ingests a post",
            f"HTTP {response.status_code}",
        )
        if response.status_code != 201:
            return finish()
        opportunity = response.json()
        opportunity_id = opportunity["id"]
        record(
            opportunity["opportunity_type"] == "freelance_job",
            "extraction classifies the post as a freelance job",
            opportunity["opportunity_type"],
        )
        record(
            opportunity["category"] == "software",
            "extraction classifies the category",
            str(opportunity["category"]),
        )
        record(
            opportunity["budget_amount"] == 300.0 and opportunity["budget_currency"] == "USD",
            "extraction reads the budget",
            f"{opportunity['budget_amount']} {opportunity['budget_currency']}",
        )
        record(
            "react" in opportunity["skills"],
            "extraction finds the react skill",
            ", ".join(opportunity["skills"]),
        )
        record(
            opportunity["review_status"] == "pending_review",
            "clean content enters the review queue",
            opportunity["review_status"],
        )

        response = client.post(
            "/api/v1/ingestion/manual",
            headers=headers(admin),
            json={
                "source_id": source_id,
                "external_id": f"smoke-{tag}",
                "original_text": post_text,
            },
        )
        record(
            response.status_code == 409 and response.json()["detail"]["code"] == "duplicate_post",
            "duplicate ingestion is rejected with 409",
            f"HTTP {response.status_code}",
        )

        response = client.post(
            "/api/v1/ingestion/manual",
            headers=headers(admin),
            json={
                "source_id": source_id,
                "external_id": f"scam-{tag}",
                "original_text": (
                    "URGENT! Send 500 birr registration fee via telebirr to get a $5000 "
                    "remote job. Pay upfront."
                ),
            },
        )
        record(
            response.status_code == 201,
            "risky post is still ingested",
            f"HTTP {response.status_code}",
        )
        if response.status_code == 201:
            risky = response.json()
            record(
                risky["review_status"] == "needs_review",
                "risky post is routed to human review",
                risky["review_status"],
            )
            record(
                "upfront_payment" in risky["risk_flags"],
                "risky post carries a risk flag",
                ", ".join(risky["risk_flags"]),
            )
            record(
                risky["budget_amount"] is None,
                "bait amount is not stored as the budget",
                str(risky["budget_amount"]),
            )

        response = client.get(
            "/api/v1/ingestion/posts", headers=headers(admin), params={"source_id": source_id}
        )
        posts = response.json() if response.status_code == 200 else []
        record(
            response.status_code == 200 and len(posts) == 2,
            "collected posts are listed and the original text is preserved",
            f"{len(posts)} posts",
        )
        record(
            any("registration fee" in post["original_text"] for post in posts),
            "original text is stored verbatim",
        )

        # --- review and audit ----------------------------------------------
        review_key = reviewer or admin
        response = client.patch(
            f"/api/v1/opportunities/{opportunity_id}/review",
            headers=headers(review_key),
            json={"decision": "approved", "reviewer_note": "Smoke test approval"},
        )
        record(
            response.status_code == 200,
            "PATCH /opportunities/{id}/review approves",
            f"HTTP {response.status_code}",
        )
        if response.status_code == 200:
            body = response.json()
            record(
                body["review_status"] == "approved",
                "review status is updated",
                body["review_status"],
            )
            record(
                body["reviewer_note"] == "Smoke test approval",
                "reviewer note is persisted",
            )
            record(
                bool(body["reviewed_by"]),
                "review is attributed to the key principal",
                str(body["reviewed_by"]),
            )

        response = client.get(
            f"/api/v1/opportunities/{opportunity_id}/events", headers=headers(admin)
        )
        events = response.json() if response.status_code == 200 else []
        record(
            response.status_code == 200 and len(events) >= 1,
            "review writes an audit event",
            f"{len(events)} events",
        )
        if events:
            event = events[0]
            record(
                event["from_status"] == "pending_review" and event["to_status"] == "approved",
                "audit event records the transition",
                f"{event['from_status']} -> {event['to_status']}",
            )
            record(bool(event["actor"]), "audit event records the actor", str(event["actor"]))

        response = client.patch(
            f"/api/v1/opportunities/{opportunity_id}/review",
            headers=headers(review_key),
            json={"decision": "approved"},
        )
        response = client.get(
            f"/api/v1/opportunities/{opportunity_id}/events", headers=headers(admin)
        )
        after = response.json() if response.status_code == 200 else []
        record(
            len(after) == len(events),
            "repeating the same decision creates no extra event",
            f"{len(after)} events",
        )

        response = client.patch(
            f"/api/v1/opportunities/{opportunity_id}/review",
            headers=headers(review_key),
            json={"decision": "needs_review"},
        )
        record(
            response.status_code == 200,
            "approved -> needs_review is an allowed transition",
            f"HTTP {response.status_code}",
        )
        response = client.patch(
            f"/api/v1/opportunities/{opportunity_id}/review",
            headers=headers(review_key),
            json={"decision": "approved"},
        )
        record(
            response.status_code == 200,
            "needs_review -> approved is an allowed transition",
            f"HTTP {response.status_code}",
        )

        # --- field editing --------------------------------------------------
        response = client.patch(
            f"/api/v1/opportunities/{opportunity_id}",
            headers=headers(review_key),
            json={
                "title": "Smoke test edited title",
                "budget_amount": 450.0,
                "budget_currency": "USD",
            },
        )
        record(
            response.status_code == 200,
            "PATCH /opportunities/{id} edits fields",
            f"HTTP {response.status_code}",
        )
        if response.status_code == 200:
            body = response.json()
            record(
                body["title"] == "Smoke test edited title" and body["budget_amount"] == 450.0,
                "edited values are returned",
            )
        response = client.get(
            f"/api/v1/opportunities/{opportunity_id}/events", headers=headers(admin)
        )
        edits = [e for e in response.json() if e["event_type"] == "field_edit"]
        record(len(edits) >= 1, "field edit writes an audit event", f"{len(edits)} edits")
        if edits:
            changes = edits[0]["changes"] or {}
            record(
                changes.get("budget_amount", {}).get("to") == 450.0,
                "audit event records the before/after diff",
                json.dumps(changes.get("budget_amount", {})),
            )

        response = client.patch(
            f"/api/v1/opportunities/{opportunity_id}", headers=headers(review_key), json={}
        )
        record(
            response.status_code == 422,
            "empty edit body is rejected",
            f"HTTP {response.status_code}",
        )

        # --- publishing guards ----------------------------------------------
        response = client.post(
            f"/api/v1/opportunities/{opportunity_id}/publish",
            headers=headers(review_key),
            json={"channel": "@smoke_test_channel"},
        )
        code = (
            response.json().get("detail", {}).get("code") if response.status_code != 200 else None
        )
        record(
            response.status_code in (403, 409),
            "publishing is blocked while disabled or not allowlisted",
            f"HTTP {response.status_code} code={code}",
        )

        response = client.get("/api/v1/publications", headers=headers(admin))
        record(
            response.status_code == 200,
            "GET /publications responds",
            f"HTTP {response.status_code}",
        )

        # --- analytics ------------------------------------------------------
        analytics_key = analyst or admin
        response = client.get("/api/v1/analytics/overview", headers=headers(analytics_key))
        record(
            response.status_code == 200,
            "GET /analytics/overview responds",
            f"HTTP {response.status_code}",
        )
        if response.status_code == 200:
            body = response.json()
            record(
                body["total_opportunities"] >= 2 and body["risk_flagged"] >= 1,
                "analytics counts opportunities and risk flags",
                f"opportunities={body['total_opportunities']} risk={body['risk_flagged']}",
            )
            record(
                body["by_status"].get("approved", 0) >= 1,
                "analytics reflects the review decision",
                json.dumps(body["by_status"]),
            )

        for path in ("/api/v1/analytics/sources", "/api/v1/analytics/market"):
            response = client.get(path, headers=headers(analytics_key))
            record(
                response.status_code == 200, f"GET {path} responds", f"HTTP {response.status_code}"
            )

        response = client.get(
            "/api/v1/analytics/trends", headers=headers(analytics_key), params={"days": 30}
        )
        record(
            response.status_code == 200,
            "GET /api/v1/analytics/trends responds",
            f"HTTP {response.status_code}",
        )
        response = client.get(
            "/api/v1/analytics/trends", headers=headers(analytics_key), params={"days": 999}
        )
        record(
            response.status_code == 422, "trends window is bounded", f"HTTP {response.status_code}"
        )

        # --- pagination and validation --------------------------------------
        response = client.get(
            "/api/v1/opportunities", headers=headers(admin), params={"limit": 9999}
        )
        record(
            response.status_code == 422, "list pagination is capped", f"HTTP {response.status_code}"
        )
        response = client.get(
            "/api/v1/opportunities", headers=headers(admin), params={"status": "bogus"}
        )
        record(
            response.status_code == 422,
            "status filter is validated",
            f"HTTP {response.status_code}",
        )
        response = client.get("/api/v1/opportunities/999999", headers=headers(admin))
        record(
            response.status_code == 404,
            "unknown opportunity returns 404",
            f"HTTP {response.status_code}",
        )

        # --- cleanup ---------------------------------------------------------
        response = client.patch(
            f"/api/v1/sources/{source_id}/status",
            headers=headers(admin),
            json={"is_active": False},
        )
        record(
            response.status_code == 200 and response.json()["is_active"] is False,
            "smoke-test source is deactivated for cleanup",
            f"HTTP {response.status_code}",
        )

    return finish()


def finish() -> int:
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    failed = [name for ok, name, _ in RESULTS if not ok]
    print("\n" + "=" * 62)
    print(f"{passed}/{len(RESULTS)} checks passed")
    if failed:
        print("failed checks:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("all functional checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
