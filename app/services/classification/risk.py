"""Stage 4 — deterministic risk screening.

The risk engine is the hard floor of the platform. It runs on every post, it
never consults a model, and **no model output can lower its verdict**. A
post flagged here can still be reviewed by a human, but it can never be
auto-approved and it can never be auto-published.

The four legacy flag names (``upfront_payment``, ``off_platform_contact``,
``urgency_pressure``, ``implausible_rate``) are preserved from the original
extractor so existing analytics and dashboards keep working.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.classification.normalize import NormalizedPost

#: Flag name -> (weight, description). Weights are additive and capped at 100.
RISK_RULES: dict[str, tuple[int, str]] = {
    # --- legacy flags (unchanged semantics) ---
    "upfront_payment": (
        60,
        ("The post asks for money up front (registration, joining, training or "
        "application fee, deposit, or a payment to start working)."),
    ),
    "off_platform_contact": (
        20,
        "The post asks the applicant to move to an external channel.",
    ),
    "urgency_pressure": (
        10,
        "The post uses artificial urgency to push a decision.",
    ),
    "implausible_rate": (
        45,
        "The promised earnings are implausible for the work described.",
    ),
    # --- additional deterministic signals ---
    "payment_request": (
        25,
        "The post asks the applicant to send money.",
    ),
    "crypto_payment": (
        35,
        "The post asks for payment in cryptocurrency only.",
    ),
    "sensitive_data_request": (
        45,
        "The post asks for bank, identity, or credential information.",
    ),
    "impersonation": (
        45,
        "The post claims to be an official Yekis entity or a well known company.",
    ),
    "deceptive_description": (
        20,
        "The post describes work in a way that cannot be verified.",
    ),
    "off_platform_evasion": (
        40,
        "The post asks to bypass the platform or avoid its fees.",
    ),
    "suspicious_link": (
        20,
        "The post contains shortened or non-standard links.",
    ),
    "unrealistic_promise": (
        20,
        "The post promises guaranteed or risk free returns.",
    ),
    "prohibited_content": (
        70,
        "The post advertises prohibited or illegal services.",
    ),
    "prompt_injection": (
        30,
        "The post contains instructions aimed at the classification system.",
    ),
    "unverifiable_client": (
        25,
        "The post cannot be reconciled with a real hiring party.",
    ),
}

RISK_FLAG_NAMES: tuple[str, ...] = tuple(RISK_RULES)

#: Flags that always force a high (or critical) risk level on their own.
CRITICAL_FLAGS = frozenset({"upfront_payment", "prohibited_content"})
HIGH_FLAGS = frozenset(
    {"sensitive_data_request", "impersonation", "off_platform_evasion", "implausible_rate"}
)

#: Flags that must never result in an automatic approval, whatever the
#: confidence of the classification.
AUTONOMY_BLOCKING_FLAGS = frozenset(
    {
        "upfront_payment",
        "payment_request",
        "crypto_payment",
        "sensitive_data_request",
        "impersonation",
        "off_platform_evasion",
        "implausible_rate",
        "deceptive_description",
        "unrealistic_promise",
        "prohibited_content",
        "prompt_injection",
        "unverifiable_client",
    }
)

PATTERNS: dict[str, tuple[str, ...]] = {
    "upfront_payment": (
        (r"\b(?:registration|joining|training|application|processing|verification|"
        r"service|membership|security)\s+fee\b"),
        r"\bpay\s+(?:a\s+)?(?:small\s+)?(?:fee|deposit|upfront)\b",
        r"\bsend\s+(?:\d[\d,.]*\s*)?(?:birr|etb|\$|usd)\b",
        r"\bupfront\s+(?:payment|fee|deposit)\b",
        r"\b(?:fee|deposit|payment)\s+(?:is\s+)?required\s+to\s+(?:start|apply|join)\b",
        (r"\bpay\s+(?:us\s+)?\d[\d,.]*\s*(?:birr|etb|\$|usd)?\s*(?:first|to start|"
        r"to activate|before)\b"),
    ),
    "off_platform_contact": (
        r"\b(?:telebirr|telegram|whatsapp|inbox\s+me|dm\s+me|contact\s+me\s+on)\b",
    ),
    "urgency_pressure": (
        (r"\b(?:urgent|urgently|immediately|asap|today\s+only|limited\s+slots|"
        r"act\s+now|last\s+chance|hurry)\b"),
    ),
    "implausible_rate": (
        # A rate of a few hundred dollars per hour is a normal consulting
        # rate. Only four-figure daily or hourly figures are implausible.
        r"(?:\$|usd)\s*\d{4,}\s*(?:per|/|a)\s*(?:day|hour)",
        r"\b\d{4,}\s*(?:usd|dollars?)\s*(?:per|/|a)\s*(?:week|day|hour)",
        r"\bearn\s+\$?\s*\d{4,}\s*(?:per|a|every)\s*day\b",
        r"\b\d{4,}\s*(?:usd|dollars?)\s*(?:per|a|every)\s*day\b",
    ),
    "payment_request": (
        (r"\b(?:send|transfer|wire|remit)\s+(?:us\s+)?\d[\d,.]*\s*"
        r"(?:birr|etb|\$|usd|dollars?)\b"),
        r"\b(?:make|place)\s+a\s+(?:payment|transfer|deposit)\b",
        r"\bbank\s+transfer\s+(?:to|for)\b",
    ),
    "crypto_payment": (
        (r"\b(?:btc|bitcoin|eth|ethereum|usdt|usdc|crypto\w*|binance\s?pay)\b"
        r"[^\n]{0,40}\b(?:wallet|address|send|transfer)\b"),
        r"\b(?:wallet|address)\s*[: ]\s*0x[0-9a-f]{6,}",
    ),
    "sensitive_data_request": (
        (r"\b(?:send|provide|share)\s+(?:us\s+|me\s+)?(?:your\s+|the\s+)?"
        r"(?:bank\s+account|account\s+number|national\s+id|id\s+number|passport|"
        r"cvv|ssn|social\s+security|bank\s+password|pin\s+code|otp|"
        r"one\s+time\s+password|copy\s+of\s+your\s+id|full\s+name\s+and\s+phone)\b"),
        r"\b(?:verify|confirm)\s+your\s+(?:identity|account)\s+by\s+sending\b",
    ),
    "impersonation": (
        r"\b(?:official|verified|authorized)\s+(?:yekis|yekis\s+intelligence)\b",
        r"\b(?:yekis|yekis\s+intelligence)\s+(?:support|admin|official)\s+team\b",
        r"\bi\s+am\s+(?:the\s+)?(?:ceo|founder|owner|admin)\s+of\s+(?:yekis|)\b",
        r"\bas\s+(?:the\s+)?yekis\s+(?:founder|ceo|team)\b",
    ),
    "deceptive_description": (
        r"\bno\s+(?:experience|interview|contract|registration)\s+(?:needed|required)\b",
        r"\bguaranteed\s+(?:income|profit|earnings|job|payment)\b",
        r"\bno\s+skills?\s+(?:needed|required)\b",
    ),
    "off_platform_evasion": (
        r"\b(?:no|zero)\s+(?:commission|platform)\s+(?:fee|fees)?\b",
        r"\bavoid\s+(?:the\s+)?platform\s+fees?\b",
        r"\bdon'?t\s+use\s+(?:the\s+)?platform\b",
        r"\bbypass\s+(?:the\s+)?platform\b",
        r"\bdirect(?:ly)?\s+to\s+my\s+account\b",
        r"\bwithout\s+going\s+through\s+(?:the\s+)?platform\b",
    ),
    "suspicious_link": (
        (r"\b(?:bit\.ly|tinyurl\.com|cutt\.ly|is\.gd|t\.co|shorturl|rebrand\.ly|"
        r"tiny\.cc|ow\.ly)\b"),
        r"\bhttp://\S+",
    ),
    "unrealistic_promise": (
        r"\b(?:guaranteed|100%\s+safe|risk[- ]?free|no\s+risk|guaranteed\s+returns)\b",
        r"\b\d+x\s+(?:in|per)\s+(?:a\s+)?(?:week|month|day)\b",
        r"\bpassive\s+income\b",
    ),
    "prohibited_content": (
        (r"\b(?:hacking\s+(?:services|for\s+hire)|crack(?:ing)?\s+(?:passwords|software)|"
        r"steal\s+(?:credit\s+card|data|accounts)|credit\s+card\s+dump|cvv\s+shop|"
        r"ransomware|forged\s+(?:documents|ids?|passports)|fake\s+id|"
        r"weapons?\s+(?:sale|deal)|explosives?|drug\s+trafficking|"
        r"human\s+trafficking)\b"),
    ),
    "prompt_injection": (
        r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+instructions\b",
        r"\bdisregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\b",
        r"\byou\s+are\s+now\s+(?:a|an|in)\b",
        r"\bsystem\s+prompt\b|\bdeveloper\s+mode\b|\bjailbreak\b",
        r"\b(?:approve|publish|classify)\s+(?:this|the)\s+(?:post|message|item)\b",
        r"\bmark\s+(?:this|it)\s+as\s+(?:approved|safe|high\s+confidence)\b",
        r"\bclassify\s+as\s+(?:a\s+)?(?:high\s+confidence|freelance|safe)\b",
        r"\boverride\s+(?:your|the)\s+(?:rules|classification|instructions)\b",
    ),
    "unverifiable_client": (
        r"\bno\s+(?:company|company\s+name)\s+(?:name\s+)?(?:needed|required|given)\b",
        r"\b(?:anonymous|unknown)\s+(?:company|client|employer)\b",
    ),
}

COMPILED = {
    name: re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.IGNORECASE)
    for name, patterns in PATTERNS.items()
}

SHORTENER_PATTERN = re.compile(
    r"\b(?:bit\.ly|tinyurl\.com|cutt\.ly|is\.gd|t\.co|shorturl|rebrand\.ly|"
    r"tiny\.cc|ow\.ly)\b",
    re.IGNORECASE,
)


@dataclass
class RiskAssessment:
    """Deterministic risk verdict for one post."""

    risk_level: str = "none"
    risk_score: int = 0
    risk_flags: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def is_high_risk(self) -> bool:
        return self.risk_level in ("high", "critical")

    @property
    def blocks_autonomy(self) -> bool:
        """High or critical risk always blocks automatic approval."""
        return self.is_high_risk


def _level_for(score: int, flags: list[str]) -> str:
    if flags and any(flag in CRITICAL_FLAGS for flag in flags):
        return "critical"
    if score >= 70:
        return "critical"
    if flags and any(flag in HIGH_FLAGS for flag in flags):
        return "high"
    if score >= 45:
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def assess(post: NormalizedPost) -> RiskAssessment:
    """Evaluate the deterministic risk rules for a post."""
    text = post.lower
    assessment = RiskAssessment()
    score = 0

    for name, pattern in COMPILED.items():
        if pattern.search(text):
            assessment.risk_flags.append(name)
            weight, description = RISK_RULES[name]
            score += weight
            assessment.findings.append(description)
            assessment.details[name] = description

    # A shortened or plain-http link is only suspicious when the post also
    # asks for something. A single link in an otherwise normal offer is not a
    # risk on its own, but it lowers the score slightly.
    if "suspicious_link" not in assessment.risk_flags:
        if SHORTENER_PATTERN.search(text) or "http://" in text:
            score += 5
            assessment.details["link_note"] = (
                "The post contains a shortened or plain-http link."
            )

    assessment.risk_score = min(100, score)
    assessment.risk_level = _level_for(assessment.risk_score, assessment.risk_flags)
    return assessment
