import re
from dataclasses import dataclass


SKILL_TERMS = [
    "react", "typescript", "javascript", "python", "node.js", "fastapi",
    "tailwind", "figma", "photoshop", "illustrator", "wordpress", "seo",
    "video editing", "graphic design", "ui/ux", "excel", "data analysis",
]


@dataclass
class ExtractedOpportunity:
    opportunity_type: str
    title: str
    category: str | None
    skills: list[str]
    budget_amount: float | None
    budget_currency: str | None
    duration: str | None
    remote: bool | None
    description: str
    quality_score: int


def extract_opportunity(text: str) -> ExtractedOpportunity:
    lower = text.lower()
    job_signals = ("looking for", "needed", "need a", "hiring", "developer", "designer", "freelance")
    is_job = any(term in lower for term in job_signals)

    skills = sorted({term for term in SKILL_TERMS if term in lower})
    category = None
    if any(x in lower for x in ("react", "developer", "programming", "software", "python")):
        category = "software"
    elif any(x in lower for x in ("design", "figma", "photoshop", "illustrator")):
        category = "design"
    elif any(x in lower for x in ("marketing", "seo", "social media")):
        category = "marketing"
    elif any(x in lower for x in ("video", "photography", "animation")):
        category = "media"

    budget_amount = None
    budget_currency = None
    match = re.search(r"(\$|USD\s*)(\d+(?:,\d{3})*(?:\.\d{1,2})?)", text, re.I)
    if match:
        budget_amount = float(match.group(2).replace(",", ""))
        budget_currency = "USD"
    else:
        match = re.search(r"(ETB|birr)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", text, re.I)
        if match:
            budget_amount = float(match.group(2).replace(",", ""))
            budget_currency = "ETB"

    duration_match = re.search(r"(\b\d+\s*(?:days?|weeks?|months?)\b)", text, re.I)
    duration = duration_match.group(1) if duration_match else None
    remote = True if any(x in lower for x in ("remote", "work from home", "online")) else None

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "New opportunity")
    title = first_line[:247]
    score = min(100, 20 + (20 if is_job else 0) + (15 if category else 0)
                + (15 if skills else 0) + (10 if budget_amount is not None else 0)
                + (10 if duration else 0) + (10 if len(text) >= 100 else 0))

    return ExtractedOpportunity(
        opportunity_type="freelance_job" if is_job else "other",
        title=title,
        category=category,
        skills=skills,
        budget_amount=budget_amount,
        budget_currency=budget_currency,
        duration=duration,
        remote=remote,
        description=text[:5000],
        quality_score=score,
    )
