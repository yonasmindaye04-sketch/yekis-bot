"""Deployment preflight for the scheduled collector.

The API and the scheduled collector run in two different places (Vercel and
GitHub Actions) with two different sets of environment variables, and the
autonomy settings live in the database. When the channel stays quiet it is
almost always because one of these pieces is missing on one side of that
split.

This script prints the effective configuration of the current process
(without any secret value) and lists every reason why the autonomous
publishing pipeline cannot publish right now.

    python -m scripts.preflight          # report only
    python -m scripts.preflight --strict # exit 1 when a blocker is found
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Source

BLOCKER = "BLOCKER"
WARN = "WARN"
OK = "ok"


class Report:
    def __init__(self) -> None:
        self.blockers: list[str] = []
        self.warnings: list[str] = []

    def add(self, level: str, message: str) -> None:
        if level == BLOCKER:
            self.blockers.append(message)
        elif level == WARN:
            self.warnings.append(message)

    def line(self, label: str, value: str, level: str = OK) -> None:
        print(f"  {label:<34} {value}")


def _redact(value: str | None) -> str:
    if not value:
        return "not set"
    return "set"


def _session_file() -> Path:
    path = Path(settings.telegram_session_path)
    if path.suffix != ".session":
        path = path.with_suffix(".session")
    return path


def check_database(report: Report, clean_closed: bool = False) -> None:
    print("\nDatabase")
    with SessionLocal() as db:
        try:
            db.execute(select(1))
        except Exception as exc:
            report.add(BLOCKER, f"the database is not reachable: {exc}")
            report.line("database", "not reachable", BLOCKER)
            return
        report.line("database", "reachable")

        from app.schema import current_revision

        revision = current_revision(db.get_bind())
        if revision is None:
            report.add(
                BLOCKER,
                "the database has no Alembic revision; run `alembic upgrade head` "
                "or `python -m scripts.migrate`",
            )
            report.line("schema revision", "missing", BLOCKER)
        else:
            report.line("schema revision", revision)

        sources = list(db.scalars(select(Source).order_by(Source.id)).all())
        if not sources:
            report.add(BLOCKER, "no sources are registered; run `python -m scripts.seed_sources`")
            report.line("sources", "none", BLOCKER)
            return
        for source in sources:
            state = "active" if source.is_active else "inactive"
            report.line(f"source {source.id}", f"{source.name} ({state})")

        from app.models import Opportunity
        from app.services.classification.deterministic import is_closed_job
        from app.services.review import cleanup_closed_opportunities

        if clean_closed:
            cleaned = cleanup_closed_opportunities(db)
            if cleaned:
                report.line("closed opps auto-rejected", f"{len(cleaned)} ({cleaned})")
            else:
                report.line("closed opps in review queue", "0")
        else:
            active_opps = list(
                db.scalars(
                    select(Opportunity).where(
                        Opportunity.review_status.in_(
                            ("pending_review", "needs_review", "approved")
                        )
                    )
                ).all()
            )
            closed_ids = [
                o.id
                for o in active_opps
                if is_closed_job(o.original_text or "") or is_closed_job(o.title or "")
            ]
            if closed_ids:
                report.add(
                    WARN,
                    f"{len(closed_ids)} closed/expired opportunity(ies) in review queue: "
                    f"{closed_ids}; run with `--clean-closed` to auto-reject",
                )
                report.line(
                    "closed opps in review queue",
                    f"{len(closed_ids)} pending cleanup",
                    WARN,
                )
            else:
                report.line("closed opps in review queue", "0")

        db.commit()


def check_telegram(report: Report) -> None:
    print("\nTelegram")
    if not settings.telegram_enabled:
        report.add(BLOCKER, "TELEGRAM_ENABLED is false; nothing is collected")
        report.line("collector", "disabled", BLOCKER)
    else:
        report.line("collector", "enabled")
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        report.add(BLOCKER, "TELEGRAM_API_ID / TELEGRAM_API_HASH are missing")
    report.line("api id / hash", _redact(str(settings.telegram_api_id or "")))
    session = _session_file()
    if not session.exists():
        report.add(
            BLOCKER,
            f"the Telegram user session {session} is missing; "
            "the collector cannot read channel history without it",
        )
    report.line("user session", "present" if session.exists() else "missing")
    report.line("bot token (publishing)", _redact(settings.telegram_bot_token))


def check_models(report: Report) -> None:
    print("\nModel providers")
    report.line("extraction provider", settings.extraction_provider)
    report.line("classification provider", settings.classification_provider)
    report.line("llm base url", settings.llm_base_url)
    report.line("llm model", settings.llm_model)
    report.line("llm api key", _redact(settings.llm_api_key))
    report.line("llm timeout (s)", str(settings.llm_timeout_seconds))
    report.line("llm max attempts", str(settings.llm_max_attempts))

    uses_llm = settings.extraction_provider == "llm" or settings.classification_provider == "llm"
    if uses_llm and not settings.llm_api_key:
        report.add(BLOCKER, "LLM_API_KEY is missing while a model provider is selected")
    if settings.classification_provider == "rules":
        report.add(
            BLOCKER,
            "CLASSIFICATION_PROVIDER is 'rules'; the deterministic stage caps the "
            "confidence below the automatic approval threshold, so nothing can be "
            "published automatically",
        )
    if uses_llm and settings.llm_timeout_seconds < 90:
        report.add(
            WARN,
            f"LLM_TIMEOUT_SECONDS is {settings.llm_timeout_seconds}; the production "
            "model endpoint answers in 90-180 seconds and would time out",
        )
    if settings.classification_provider not in ("rules", "llm", "gemini"):
        report.add(BLOCKER, f"unknown CLASSIFICATION_PROVIDER {settings.classification_provider!r}")
    if settings.extraction_provider not in ("rules", "llm", "gemini"):
        report.add(BLOCKER, f"unknown EXTRACTION_PROVIDER {settings.extraction_provider!r}")


def check_autonomy(report: Report) -> None:
    print("\nAutonomy and publishing")
    from app.integrations.telegram.channel import channel_reference
    from app.services.autonomy import publishing_blocked, resolve_config
    from app.services.publishing import allowed_channels

    with SessionLocal() as db:
        config = resolve_config(db, settings)
        report.line("enabled", str(config.enabled))
        report.line("mode", config.mode)
        report.line("channel", config.channel or "not set")
        report.line("approval threshold", str(config.threshold))
        report.line("daily publish limit", str(config.daily_publish_limit))
        report.line("approved source ids", str(sorted(config.approved_source_ids)))
        allowlist = sorted(allowed_channels(settings))
        report.line("publishing allowed channels", ", ".join(allowlist) or "none")
        report.line("publishing enabled", str(settings.publishing_enabled))

        if not settings.publishing_enabled:
            report.add(BLOCKER, "PUBLISHING_ENABLED is false; nothing can be sent")
        if not config.enabled:
            report.add(
                BLOCKER,
                "autonomy is disabled; enable it in the dashboard or with AUTONOMY_ENABLED",
            )
        if config.is_shadow:
            report.add(
                BLOCKER,
                "autonomy runs in shadow mode; decisions are recorded but nothing is published",
            )
        if config.channel:
            try:
                reference = channel_reference(config.channel)
            except Exception:
                reference = config.channel
            if reference not in allowlist:
                report.add(
                    BLOCKER,
                    f"the channel {config.channel} is not in PUBLISHING_ALLOWED_CHANNELS",
                )
        if not config.approved_source_ids:
            report.add(BLOCKER, "no source is approved for autonomous publishing")

        known_ids = set(db.scalars(select(Source.id)).all())
        missing = sorted(set(config.approved_source_ids) - known_ids)
        if missing:
            report.add(
                BLOCKER,
                f"the approved source ids {missing} do not exist; "
                "check the ids shown by the source list above",
            )

        blocked = publishing_blocked(db, config, datetime.now(UTC), settings)
        if blocked:
            report.add(BLOCKER, f"publishing is blocked: {blocked}")
            report.line("publishing blocked", blocked, BLOCKER)
        else:
            report.line("publishing blocked", "no")
        db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Report the deployment blockers")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with 1 when a blocker is found",
    )
    parser.add_argument(
        "--clean-closed",
        action="store_true",
        help="auto-reject any closed opportunities found in active review queues",
    )
    args = parser.parse_args()

    print("Yekis deployment preflight")
    print(f"  {'working directory':<34} {os.getcwd()}")

    report = Report()
    check_database(report, clean_closed=args.clean_closed)
    check_telegram(report)
    check_models(report)
    check_autonomy(report)

    print("\nResult")
    if report.warnings:
        for warning in report.warnings:
            print(f"  {WARN} {warning}")
    if report.blockers:
        for blocker in report.blockers:
            print(f"  {BLOCKER} {blocker}")
    if not report.blockers and not report.warnings:
        print("  ok  no blockers found")
    else:
        print(f"  {len(report.blockers)} blocker(s), {len(report.warnings)} warning(s)")

    return 1 if args.strict and report.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
