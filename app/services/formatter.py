"""Deterministic publication formatting with mandatory source attribution.

Plain text only. The original source name and URL are appended after the body
is sized, so truncation can never remove attribution.
"""

from __future__ import annotations

from app.models import Opportunity, Source

MAX_MESSAGE_LENGTH = 4096


class FormattingError(ValueError):
    pass


def _budget(opportunity: Opportunity) -> str | None:
    if opportunity.budget_amount is None:
        return None
    amount = f"{opportunity.budget_amount:,.2f}".rstrip("0").rstrip(".")
    return f"{amount} {opportunity.budget_currency or ''}".strip()


def format_publication(opportunity: Opportunity, source: Source) -> str:
    if source is None or not source.name:
        raise FormattingError("a source attribution is required")

    header = f"{opportunity.title}"
    details: list[str] = []
    if opportunity.opportunity_type:
        details.append(opportunity.opportunity_type.replace("_", " ").title())
    if opportunity.category:
        details.append(opportunity.category.title())
    if budget := _budget(opportunity):
        details.append(f"Budget: {budget}")
    if opportunity.duration:
        details.append(f"Duration: {opportunity.duration}")
    if opportunity.remote is not None:
        details.append("Remote" if opportunity.remote else "On-site")
    if opportunity.skills:
        details.append("Skills: " + ", ".join(opportunity.skills[:12]))
    if opportunity.description:
        details.append(opportunity.description.strip()[:1600])

    attribution = f"\n\nSource: {source.name}"
    if source.source_url:
        attribution += f"\n{source.source_url}"

    body = header
    if details:
        body += "\n\n" + "\n".join(details)

    available = MAX_MESSAGE_LENGTH - len(attribution)
    if available < 120:
        raise FormattingError("attribution leaves too little room for a message")
    if len(body) > available:
        body = body[: available - 2].rstrip() + "…"
    return body + attribution
