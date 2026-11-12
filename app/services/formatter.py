"""Publication formatting with visual hierarchy, concise bullet lists, and platform CTA.

Strict zero-emoji corporate style with Telegram Markdown formatting.
No external source channels are exposed.
"""

from __future__ import annotations

import re

from app.config import settings as default_settings
from app.models import Opportunity, Source

MAX_MESSAGE_LENGTH = 4096


class FormattingError(ValueError):
    pass


def _budget(opportunity: Opportunity) -> str | None:
    if opportunity.budget_amount is None:
        return None
    amount = f"{opportunity.budget_amount:,.2f}".rstrip("0").rstrip(".")
    currency = opportunity.budget_currency or ""
    return f"{amount} {currency}".strip()


def _format_bullets(items: list[str]) -> list[str]:
    return [f"• {item}" for item in items if item]


def _clean_sentences(text: str | None, max_points: int = 2, max_len: int = 300) -> list[str]:
    if not text:
        return []
    raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 10]
    points: list[str] = []
    total = 0
    for p in raw:
        clean = p.rstrip(".")
        if len(clean) > 180:
            cut = clean[:180].rsplit(" ", 1)[0]
            clean = cut.rstrip(" ,;:")
        if total + len(clean) > max_len and points:
            break
        points.append(clean)
        total += len(clean)
        if len(points) >= max_points:
            break
    return points


def format_publication(
    opportunity: Opportunity,
    source: Source | None = None,
    settings=default_settings,
) -> str:
    if not opportunity or not opportunity.title:
        raise FormattingError("opportunity title is required")

    platform_url = getattr(settings, "platform_url", "https://yekis.com")

    sections: list[str] = []

    # 1. Header with clear bold title
    title = opportunity.title.strip().upper()
    sections.append(f"**{title}**")

    # 2. Key Terms / Details as bullet points
    details: list[str] = []
    if opportunity.opportunity_type and opportunity.opportunity_type.lower() != "other":
        details.append(f"Type: {opportunity.opportunity_type.replace('_', ' ').title()}")
    client = getattr(opportunity, "client_name", None)
    if client and client.strip():
        details.append(f"Client: {client.strip()}")
    if opportunity.category and opportunity.category.lower() != "other":
        details.append(f"Category: {opportunity.category.title()}")
    if budget := _budget(opportunity):
        details.append(f"Compensation: {budget}")
    if opportunity.duration:
        details.append(f"Duration: {opportunity.duration.strip()}")
    if opportunity.remote is not None:
        details.append(f"Work Mode: {'Remote' if opportunity.remote else 'On-site'}")

    if details:
        sections.append("DETAILS\n" + "\n".join(_format_bullets(details)))

    # 3. Required Skills as clean bullet points
    if opportunity.skills:
        skill_items = [s.strip().title() for s in opportunity.skills[:6] if s.strip()]
        if skill_items:
            sections.append("REQUIRED SKILLS\n" + "\n".join(_format_bullets(skill_items)))

    # 4. Concise Highlights (brief bullet points instead of long paragraph)
    highlights = _clean_sentences(opportunity.description, max_points=2, max_len=300)
    if highlights:
        sections.append("HIGHLIGHTS\n" + "\n".join(_format_bullets(highlights)))

    # 5. Direct call to action to Yekis platform (always guaranteed at bottom)
    cta = (
        "APPLY ON YEKIS\n"
        "Submit your proposal and work securely on Yekis:\n"
        f"{platform_url}"
    )

    body = "\n\n".join(sections)
    available = MAX_MESSAGE_LENGTH - len(cta) - 2
    if len(body) > available:
        body = body[: available - 2].rstrip() + "…"

    return f"{body}\n\n{cta}"
