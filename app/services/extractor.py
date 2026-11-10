"""Deterministic, dependency-free opportunity extraction.

The extractor is intentionally conservative: it is a candidate generator, not a
verifier. Every risky pattern produces a ``risk_flag`` and a human-review route;
a flag is never proof of fraud and never auto-rejects content.
"""

import re
from dataclasses import dataclass, field

# Word-boundary matching avoids the audit's "does not react well" false positive.
SKILL_TERMS = {
    "react": ["react", "react.js", "reactjs"],
    "typescript": ["typescript", "type script"],
    "javascript": ["javascript", "java script", "js"],
    "python": ["python"],
    "node.js": ["node.js", "nodejs", "node"],
    "fastapi": ["fastapi"],
    "tailwind": ["tailwind", "tailwindcss"],
    "figma": ["figma"],
    "photoshop": ["photoshop"],
    "illustrator": ["illustrator"],
    "wordpress": ["wordpress"],
    "seo": ["seo", "search engine optimization"],
    "video editing": ["video editing", "video editor"],
    "graphic design": ["graphic design", "graphic designer"],
    "ui/ux": ["ui/ux", "ui ux", "user experience", "user interface"],
    "excel": ["excel", "spreadsheet"],
    "data analysis": ["data analysis", "data analyst", "data analytics"],
    "copywriting": ["copywriting", "copywriter"],
    "translation": ["translation", "translator", "amharic"],
    "mobile development": ["mobile app", "android", "ios", "flutter"],
    "devops": ["devops", "docker", "kubernetes", "aws"],
}

CATEGORY_TERMS = {
    "software": ["software", "developer", "programming", "react", "python", "typescript",
                 "javascript", "node", "fastapi", "backend", "frontend", "api", "devops"],
    "design": [
        "design", "designer", "designing", "figma", "photoshop", "illustrator",
        "branding", "logo", "ui/ux",
    ],
    "marketing": ["marketing", "seo", "social media", "advertising", "campaign", "growth"],
    "media": ["video", "photography", "animation", "podcast", "editing", "motion"],
    "writing": ["writing", "copywriting", "content", "translation", "article", "editor"],
    "data": ["data", "analytics", "analysis", "dashboard", "machine learning", "excel"],
    "admin": ["admin", "virtual assistant", "data entry", "customer support", "bookkeeping"],
}

JOB_SIGNALS = (
    "looking for", "needed", "need a", "hiring", "developer", "designer", "freelance",
    "freelancer", "contract", "gig", "job", "vacancy", "position", "we are seeking", "seeking a",
)
INTERNSHIP_SIGNALS = ("internship", "intern ", "internship program")
SCHOLARSHIP_SIGNALS = ("scholarship", "fellowship", "tuition", "study grant")
TRAINING_SIGNALS = ("training", "bootcamp", "boot camp", "course", "workshop", "certification")
RESOURCE_SIGNALS = (
    "free template", "resource", "toolkit", "guide", "ebook", "e-book", "cheat sheet",
)
NEWS_SIGNALS = ("news", "announcement", "report", "market update", "economy", "launch")

CURRENCY_PATTERN = re.compile(
    r"(?P<currency>\$|USD|ETB|birr)\s*(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
BUDGET_KEYWORDS = ("budget", "pay", "payment", "salary", "rate", "compensation", "fee", "cost")
RISK_PATTERNS: dict[str, tuple[str, int]] = {
    "upfront_payment": (
        (
            r"\b(?:registration|joining|training|application|processing|verification)\s+fee\b"
            r"|\bpay\s+(?:a\s+)?(?:small\s+)?(?:fee|deposit|upfront)\b"
            r"|\bsend\s+(?:\d[\d,.]*\s*)?(?:birr|etb|\$|usd)\b"
            r"|\bupfront\s+(?:payment|fee|deposit)\b"
        ),
        40,
    ),
    "off_platform_contact": (
        r"\b(?:telebirr|telegram|whatsapp|inbox\s+me|dm\s+me|contact\s+me\s+on)\b",
        20,
    ),
    "urgency_pressure": (
        r"\b(?:urgent|urgently|immediately|asap|today\s+only|limited\s+slots|act\s+now)\b",
        15,
    ),
    "implausible_rate": (
        (
            r"(?:\$|usd)\s*\d{3,}\s*(?:per|/|a)\s*(?:day|hour)"
            r"|\b\d{4,}\s*(?:usd|dollars?)\s*(?:per|/|a)\s*week"
        ),
        25,
    ),
}


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
    risk_flags: list[str] = field(default_factory=list)
    risk_score: int = 0


def _term_matches(text: str, term: str) -> bool:
    """Whole-token phrase match with one semantic guard.

    Token boundaries fix ``reactor``; the audit's false positive was the verb
    in "does not react well", which a boundary still matches. Treat that usage
    (and similar verb forms) as text, not as the React skill.
    """
    if term == "react" and re.search(
        r"\b(?:does\s+not|do\s+not|don'?t|not)\s+react\b"
        r"|\breact(?:s|ed|ing)?\s+(?:well|badly|to|quickly|strongly)\b",
        text,
    ):
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _risk_flags(text: str) -> tuple[list[str], int]:
    lower = text.lower()
    flags: list[str] = []
    score = 0
    for name, (pattern, weight) in RISK_PATTERNS.items():
        if re.search(pattern, lower, re.IGNORECASE):
            flags.append(name)
            score += weight
    return flags, min(100, score)


def _budget(text: str, *, has_upfront_fee: bool) -> tuple[float | None, str | None]:
    """Prefer an amount next to a budget keyword, never a fee/upfront amount."""
    candidates = list(CURRENCY_PATTERN.finditer(text))
    if not candidates:
        return None, None
    if has_upfront_fee:
        # A payment-to-apply amount is not a market budget. Returning it would
        # poison analytics with the scammer's bait figure.
        return None, None

    lower = text.lower()
    best: tuple[int, int, re.Match] | None = None
    for match in candidates:
        window_start = max(0, match.start() - 60)
        window_end = min(len(lower), match.end() + 60)
        window = lower[window_start:window_end]
        for keyword in BUDGET_KEYWORDS:
            position = window.find(keyword)
            if position == -1:
                continue
            distance = abs((window_start + position) - match.start())
            if best is None or distance < best[0]:
                best = (distance, match.start(), match)
    chosen = best[2] if best else candidates[0]
    amount = float(chosen.group("amount").replace(",", ""))
    currency = "USD" if chosen.group("currency").lower() in ("$", "usd") else "ETB"
    return amount, currency


def _classify_type(lower: str) -> str:
    if any(signal in lower for signal in INTERNSHIP_SIGNALS):
        return "internship"
    if any(signal in lower for signal in SCHOLARSHIP_SIGNALS):
        return "scholarship"
    if any(signal in lower for signal in TRAINING_SIGNALS):
        return "training"
    if any(signal in lower for signal in JOB_SIGNALS):
        return "freelance_job"
    if any(signal in lower for signal in RESOURCE_SIGNALS):
        return "resource"
    if any(signal in lower for signal in NEWS_SIGNALS):
        return "news"
    return "other"


def extract_opportunity(text: str) -> ExtractedOpportunity:
    lower = text.lower()
    opportunity_type = _classify_type(lower)

    skills = sorted(
        name for name, variants in SKILL_TERMS.items()
        if any(_term_matches(lower, variant) for variant in variants)
    )

    category = None
    for name, terms in CATEGORY_TERMS.items():
        if any(_term_matches(lower, term) for term in terms):
            category = name
            break

    flags, risk_score = _risk_flags(text)
    budget_amount, budget_currency = _budget(text, has_upfront_fee="upfront_payment" in flags)

    duration_match = re.search(r"(\b\d+\s*(?:days?|weeks?|months?)\b)", text, re.IGNORECASE)
    duration = duration_match.group(1) if duration_match else None

    if any(phrase in lower for phrase in ("remote", "work from home", "anywhere")):
        remote: bool | None = True
    elif any(phrase in lower for phrase in ("on-site", "onsite", "in-office", "in office")):
        remote = False
    else:
        remote = None

    first_line = next(
        (line.strip() for line in text.splitlines() if line.strip()), "New opportunity"
    )
    title = first_line[:247]

    has_job_signal = any(signal in lower for signal in JOB_SIGNALS)
    score = min(
        100,
        20
        + (20 if has_job_signal else 0)
        + (15 if category else 0)
        + (15 if skills else 0)
        + (10 if budget_amount is not None else 0)
        + (10 if duration else 0)
        + (10 if len(text) >= 100 else 0),
    )
    # Risk must not auto-reject, but it must not look like quality either.
    score = max(0, score - risk_score // 2)

    return ExtractedOpportunity(
        opportunity_type=opportunity_type,
        title=title,
        category=category,
        skills=skills,
        budget_amount=budget_amount,
        budget_currency=budget_currency,
        duration=duration,
        remote=remote,
        description=text[:5000],
        quality_score=score,
        risk_flags=flags,
        risk_score=risk_score,
    )
