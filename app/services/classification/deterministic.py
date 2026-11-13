"""Stage 2 — deterministic job detection.

This module decides, without any model call, whether a post *offers paid work*.
It is intentionally conservative: a post only becomes a publishable candidate
when there is evidence of a party who wants work done **and** evidence of what
the work is, how to apply, or how it is paid.

Keyword presence alone never proves a job. "Skills you need to have to improve
your employability" mentions skills and employment, yet offers nothing.

Matching uses regular expressions with word boundaries and flexible
separators, never bare substring tests, so "reactor" is not "react" and
"contractor" is not "contract".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.classification.normalize import NormalizedPost

# --- Signal groups ---------------------------------------------------------
# Every pattern is a regular expression matched against the lower-cased,
# normalized text.

#: A party is looking for someone to do work.
SEEKING = (
    (r"\b(?:looking for|looking to hire|looking out for)\b"
    r"(?!\s+(?:a\s+|an\s+|the\s+)?(?:jobs?|work|employment)\b)"),
    (r"\bwe(?:'re| are)?\s+(?:now\s+)?(?:looking|seeking|searching)\b"
    r"(?!\s+for\s+(?:a\s+|an\s+)?(?:jobs?|work|employment)\b)"),
    (r"\b(?:we are hiring|we're hiring|hiring|we want to hire|we are looking for|"
    r"we're looking for|we need|we are in need of|"
    r"anyone available|open to proposals|we need someone|we need a|we need an|"
    r"we need someone to|interested candidates|interested professionals|"
    r"interested freelancers|call for proposals|call for bids|vacancy)\b"),
    # "seeking a consultant" is an offer, "seeking a job" is not.
    (r"\b(?:seeking|searching for)(?!\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:jobs?|work|employment)\b)"),
    # "React developer needed", "designer needed".
    (r"\b(?:developer|designer|engineer|writer|translator|editor|copywriter|"
    r"marketer|analyst|consultant|freelancer|expert|specialist|programmer|"
    r"virtual assistant|illustrator|photographer)\s+(?:is\s+|are\s+)?needed\b"),
    # Amharic: "I need", "we need", "whoever needs".
    r"(?:\bያስፈልጋለሁ\b|\bያስፈልጋለን\b|\bእየፈልጋለሁ\b|\bየሚፈልጉ\b)",
)

#: There is a task, a project, or a deliverable.
TASK = (
    (r"\b(?:deliverables?|responsibilities|scope of work|job description|"
    r"tasks? (?:include|are|will)|your task|the work involves|project involves|"
    r"must deliver|you will (?:be )?(?:build|building|design|designing|develop|"
    r"developing|create|creating|write|writing|translate|translating|manage|"
    r"managing|produce|handle|prepare|edit|record|cut|mount)\b)"),
    (r"\bto (?:build|design|develop|create|write|translate|manage|produce|handle|"
    r"prepare|edit|record|cut|shoot)\b"),
    r"\bmust (?:be able to|have|know|provide|deliver|have experience)\b",
    r"\brequirements?\b",
    # Amharic: "how to do", "deliverable", "you deliver", "project".
    r"(?:\bእንደት አስፈጽም\b|\bመጠናቀቅ\b|\bአስቀምጥ\b|\bፕሮጀክት\b)",
)

#: There is a way to apply or to contact the client.
APPLICATION = (
    (r"\b(?:how to apply|apply (?:here|now|via|by|with)|send your (?:cv|resume|"
    r"portfolio|profile|proposal|bid|work)|comment below|dm me|inbox me|reach out|"
    r"email (?:us|me)|contact (?:us|me)|message me|reply (?:to|with)|"
    r"interested (?:candidates|people|professionals|freelancers)|"
    r"send us your|portfolio|proposals? (?:are )?welcome)\b"),
    (r"\b(?:send|submit)\s+(?:a|your)\s+(?:proposal|bid|quote|portfolio|cv|resume|"
    r"profile|rates|references)\b"),
    # Amharic: "send your portfolio", "how to do".
    r"(?:\bእንደት አስረጋግጡ\b|\bያስቀርቡ\b|\bፖርትፎሊዮ\b)",
)

#: Money, rate, or budget language.
PAYMENT = (
    (r"(?:\b(?:budget|payment|payments|fixed price|per hour|hourly|daily rate|"
    r"rate of|milestone|milestones|invoice|invoicing|compensation|remuneration|"
    r"pay(?:ing)? of|salary|fee of|fees of|price of)\b|\$\s?\d|\busd\b|\betb\b|"
    r"\bbirr\b|\bብር\b)"),
)

#: Duration or deadline language.
DURATION = (
    (r"\b(?:within|in|for|over|lasting|duration|delivery in|deliver in|complete in|"
    r"deadline|starting)\s+(?:is\s+|of\s+)?(?:the\s+next\s+)?\d+\s*(?:day|days|"
    r"week|weeks|month|months|hour|hours)\b"),
    (r"\b\d+\s*(?:day|days|week|weeks|month|months)\s*(?:contract|engagement|"
    r"project|period|assignment|deadline)\b"),
    # Amharic: days, weeks, months.
    r"(?:\bቀናት\b|\bሳምንታት\b|\bወራት\b)",
)

#: A job role is named. This alone is weak evidence, but a role together
#: with a budget or a duration is the terse "title + price" form that job
#: channels use every day.
ROLE = (
    (r"\b(?:developer|designer|engineer|writer|translator|editor|copywriter|"
    r"marketer|analyst|consultant|photographer|videographer|illustrator|"
    r"virtual assistant|accountant|bookkeeper|community manager|"
    r"social media manager|wordpress expert|copy editor|proofreader|"
    r"web developer|mobile developer|backend developer|frontend developer)\b"),
    # Amharic job titles.
    r"(?:\bዲቪሎፕር\b|\bዲዛይነር\b|\bጻፍሪ\b|\bመርጃተኛ\b|\bአማራጭ አማካሪ\b)",
)

#: Freelance wording.
FREELANCE = (
    (r"\b(?:freelance\w*|freelancer|freelancing|per project|gig work|"
    r"independent contractor|self[- ]employed)\b"),
    # Amharic: freelance, freelancer, own time.
    r"(?:\bፈሪላንስ\b|\bፈሪላንሰር\b|\bየራስ ጊዜ\b)",
)

#: Contracted, project based, or fixed term engagement.
CONTRACT = (
    (r"\b(?:project[- ]based|milestone[- ]based|fixed[- ]term|temporary contract|"
    r"short[- ]term|long[- ]term|contract(?: work| project| position| role|"
    r" agreement)?|contractor)\b"),
    # Amharic: contract.
    r"(?:\bኮንትራክት\b)",
)

#: Project based or milestone based wording.
PROJECT_BASED = (
    r"\b(?:project[- ]based|scope of work)\b",
    r"(?:\bበፕሮጀክት መሠረት\b)",
)

#: Consulting style engagement.
CONSULTING = (
    (r"\b(?:consult\w*|advisory|advisor|retainer|expert advice|"
    r"technical advisor|consultancy)\b"),
    r"(?:\bአማራጭ አማካሪ\b)",
)

#: Remote work.
REMOTE = (
    (r"\b(?:remote|work from home|fully remote|wfh|work remotely|anywhere|"
    r"distributed team|remote[- ]first)\b"),
    r"(?:\bርሚድ\b)",
)

#: Permanent or regular employment.
EMPLOYMENT = (
    (r"\b(?:full[- ]time|part[- ]time|permanent(?: position| role| job)?|"
    r"on[- ]site|onsite|in[- ]office|in office|salary|monthly salary|employee|"
    r"staff (?:position|role)|regular employment|full time employment)\b"),
    r"(?:\bሙሉ ጊዜ\b|\bየተማል ጊዜ\b)",
)

#: Internship.
INTERNSHIP = (
    (r"\b(?:internship|intern position|internship program|internship opportunity|"
    r"trainee|student internship|practical attachment)\b"),
    r"(?:\bኢንተርንሽክ\b)",
)

#: Volunteering.
VOLUNTEER = (
    (r"\b(?:volunteer\w*|unpaid|non[- ]paid|without pay|stipend only|"
    r"expenses only|no salary)\b"),
    r"(?:\bበጥርማት\b)",
)

#: Apprenticeship.
APPRENTICE = (
    r"\bapprentice\w*\b",
    r"(?:\bተሳሳይ\b)",
)

#: Content that teaches job seekers instead of offering work.
EDUCATION = (
    (r"\b(?:employability|improve your chances|to land a job|skills you need to have|"
    r"skills you need to|skills needed to|cv tips|resume tips|interview tips|"
    r"portfolio tips|how to get hired|how to pass an interview|how to write a cv|"
    r"how to write a resume|job search|free session|free training|"
    r"job hunting)\b"),
    r"(?:\bየሰራ እድልን ማሳደጊ\b)",
)

#: Skill tips. Requires corroboration so genuine job posts are not captured.
TIPS_WEAK = (
    (r"\b(?:tips|tip for|tip:|quick tip|pro tip|here are some tips|one tip|"
    r"in between job opportunities|we will send you|you should learn|"
    r"skills to learn|learn how to|tutorial|step by step|part \d|"
    r"series of posts)\b"),
    r"(?:\bቅንጥብ\b|\bምክር\b)",
)

#: Training or course advertising.
TRAINING = (
    (r"\b(?:bootcamp|enroll\w*|certificate|certificat\w+|participants?|"
    r"training program|training session|masterclass|webinar|our course|"
    r"course fee|course price|join the course|sign up for the course|"
    r"certificate of completion|curriculum)\b"),
    r"(?:\bስልጠና\b|\bኮርስ\b)",
)

#: News and information.
NEWS = (
    (r"\b(?:breaking news|according to|market update|press release|news update|"
    r"statistics show|survey shows|study finds|inflation|unemployment rate|"
    r"reported that|ministry of)\b"),
    r"(?:\bዜና\b)",
)

#: Announcements.
ANNOUNCEMENT = (
    (r"\b(?:announcing|we are pleased to announce|we are happy to announce|"
    r"happy to announce|now launching|is live|new feature|save the date|"
    r"celebrating|congratulating|thank you for your support|"
    r"we are now collecting|we have launched|we launched|is now open|"
    r"we are glad|glad to share|welcome to)\b"),
    r"(?:\bማስታወቂያ\b|\bተጀምሯል\b)",
)

#: Community discussion.
COMMUNITY = (
    (r"\b(?:what do you think|your thoughts|thoughts on|anyone else|let us discuss|"
    r"let's discuss|discussion|poll|opinions|how do you feel|comment your|"
    r"who else|has anyone|sharing my experience)\b"),
    r"(?:\bእንይመያውቱ\b)",
)

#: Marketplace self-promotion (strong forms).
MARKETPLACE_STRONG = (
    (r"\b(?:post your (?:project|job|service|gig) on yekis|hire on yekis|"
    r"sign up on yekis|create your (?:free )?profile on yekis|"
    r"download the yekis app|your bid on yekis|yekis marketplace|"
    r"on yekis you can|register on yekis|promote on yekis|"
    r"publish your project on yekis|list your services on yekis)\b"),
)

#: Marketplace mention plus a promotional verb, or a bare marketplace link.
MARKETPLACE_WEAK = (
    r"\byekis\.com\b|\bon yekis\b|\byekis platform\b|\byekis app\b",
)

PROMOTION_VERBS = (
    (r"\b(?:post|hire|sign up|create|download|register|promote|advertise|publish|"
    r"list|submit)\b"),
)

#: The author is looking for work, not offering it.
JOB_SEEKER = (
    (r"\b(?:looking|searching)\s+for\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:[a-z][a-z-]*\s+){0,3}?(?:jobs?|employment)\b"),
    (r"\b(?:i|we)\s+(?:am|are|'m|'re)\s+(?:looking|searching)\s+for\s+"
    r"(?:a\s+|an\s+|the\s+)?(?:[a-z][a-z-]*\s+){0,3}?"
    r"(?:jobs?|work|employment)\b"),
    r"\bto\s+land\s+a\s+job\b",
    r"\b(?:apply|applying)\s+(?:for|to)\s+(?:a\s+|the\s+)?jobs?\b",
    r"\blooking\s+for\s+work\b",
)

# --- Analysis result --------------------------------------------------------


@dataclass
class DeterministicAnalysis:
    """Outcome of the deterministic stage."""

    classification: str = "insufficient_information"
    engagement_type: str | None = None
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    offer_groups: list[str] = field(default_factory=list)
    matched_groups: list[str] = field(default_factory=list)
    method: str = "deterministic"

    @property
    def is_publishable_candidate(self) -> bool:
        from app.services.classification.taxonomy import is_publishable_classification

        return is_publishable_classification(self.classification)


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.IGNORECASE)


def _match(patterns: tuple[str, ...], text: str) -> bool:
    return _compile(patterns).search(text) is not None


GROUP_PATTERNS: dict[str, tuple[str, ...]] = {
    "seeking": SEEKING,
    "task": TASK,
    "application": APPLICATION,
    "payment": PAYMENT,
    "duration": DURATION,
    "role": ROLE,
    "freelance": FREELANCE,
    "contract": CONTRACT,
    "project_based": PROJECT_BASED,
    "consulting": CONSULTING,
    "remote": REMOTE,
    "employment": EMPLOYMENT,
    "internship": INTERNSHIP,
    "volunteer": VOLUNTEER,
    "apprentice": APPRENTICE,
    "education": EDUCATION,
    "tips": TIPS_WEAK,
    "training": TRAINING,
    "news": NEWS,
    "announcement": ANNOUNCEMENT,
    "community": COMMUNITY,
    "marketplace_strong": MARKETPLACE_STRONG,
    "marketplace_weak": MARKETPLACE_WEAK,
    "job_seeker": JOB_SEEKER,
}

COMPILED_GROUPS = {name: _compile(patterns) for name, patterns in GROUP_PATTERNS.items()}
PROMOTION_COMPILED = _compile(PROMOTION_VERBS)


def matched_groups(text: str) -> set[str]:
    """Return the names of every signal group present in the text."""
    return {name for name, pattern in COMPILED_GROUPS.items() if pattern.search(text)}


def _has_offer_evidence(groups: set[str]) -> bool:
    """Offer evidence: a seeking party plus supporting detail.

    A seeking phrase alone is not enough ("looking for a job"), and a price
    or a duration alone is not enough either (a course advert lists both).
    Work is only offered when a party asks for it, and the post says enough
    about the task to be actionable.
    """
    support = groups & {"task", "application", "payment", "duration"}
    if "seeking" in groups and support:
        return True
    if "task" in groups and groups & {"payment", "duration"}:
        return True
    # "React designer. Budget $500." is the terse job advert form: a named
    # role plus a price is enough to treat the post as a candidate. It never
    # reaches the automatic approval threshold on its own.
    if "role" in groups and groups & {"payment", "duration"}:
        return True
    return False


def _marketplace_promotion(text: str, groups: set[str]) -> bool:
    if "marketplace_strong" in groups:
        return True
    if "marketplace_weak" in groups:
        if PROMOTION_COMPILED.search(text):
            return True
        # A bare marketplace link in a post with no offer evidence is an
        # advertisement for the marketplace, not an opportunity.
        return not _has_offer_evidence(groups)
    return False


def _publishable_confidence(groups: set[str]) -> float:
    """Deterministic confidence for a publishable candidate.

    Missing budget and deadline reduce confidence on purpose: those posts can
    still be genuine, but they must not clear an autonomous approval threshold
    without a second opinion.
    """
    has_seeking = "seeking" in groups
    has_task = "task" in groups
    has_application = "application" in groups
    has_money = "payment" in groups
    has_duration = "duration" in groups
    has_detail = has_money or has_duration

    if has_seeking and has_task and has_application and has_detail:
        return 0.97
    if has_seeking and has_task and has_detail:
        return 0.94
    if has_seeking and has_task and has_application:
        return 0.94
    if has_seeking and has_application and has_detail:
        return 0.93
    if has_seeking and has_detail:
        return 0.88
    if has_seeking and has_task:
        return 0.85
    return 0.78


def analyze(post: NormalizedPost) -> DeterministicAnalysis:
    """Run the deterministic stage over a normalized post."""
    text = post.lower
    groups = matched_groups(text)

    analysis = DeterministicAnalysis()
    analysis.matched_groups = sorted(groups)

    if _marketplace_promotion(text, groups):
        analysis.classification = "marketplace_promotion"
        analysis.confidence = 0.96
        analysis.reasons.append(
            "The post promotes the Yekis marketplace instead of offering work."
        )
        analysis.evidence.append("Marketplace promotion language detected")
        return analysis

    if _has_offer_evidence(groups):
        analysis.offer_groups = sorted(
            groups & {"seeking", "task", "application", "payment", "duration"}
        )

        if "internship" in groups:
            analysis.classification = "internship"
            analysis.confidence = 0.95
            analysis.reasons.append(
                "The post describes an internship, which is outside the contract scope."
            )
            return analysis

        if "volunteer" in groups:
            analysis.classification = "volunteering"
            analysis.confidence = 0.94
            analysis.reasons.append(
                "The post offers unpaid volunteering, which is outside the contract scope."
            )
            return analysis

        if "apprentice" in groups:
            analysis.classification = "apprenticeship"
            analysis.confidence = 0.93
            analysis.reasons.append(
                "The post describes an apprenticeship, which is outside the contract scope."
            )
            return analysis

        has_contract = "contract" in groups
        if "employment" in groups and not has_contract:
            analysis.classification = _employment_classification(text)
            analysis.confidence = 0.95
            analysis.reasons.append(
                "The post offers permanent or regular employment, not contracted work."
            )
            return analysis

        analysis.classification = _engagement_classification(groups)
        analysis.engagement_type = analysis.classification
        analysis.confidence = _publishable_confidence(groups)
        analysis.evidence.extend(_offer_evidence(groups))
        return analysis

    # --- No offer evidence: the post is not a job opportunity. ------------
    if "internship" in groups:
        analysis.classification = "internship"
        analysis.confidence = 0.92
        analysis.reasons.append(
            "The post describes an internship, which is outside the contract scope."
        )
        return analysis
    if "volunteer" in groups:
        analysis.classification = "volunteering"
        analysis.confidence = 0.92
        analysis.reasons.append(
            "The post offers unpaid volunteering, which is outside the contract scope."
        )
        return analysis
    if "apprentice" in groups:
        analysis.classification = "apprenticeship"
        analysis.confidence = 0.9
        analysis.reasons.append(
            "The post describes an apprenticeship, which is outside the contract scope."
        )
        return analysis
    if "employment" in groups:
        analysis.classification = _employment_classification(text)
        analysis.confidence = 0.9
        analysis.reasons.append(
            "The post offers permanent or regular employment, not contracted work."
        )
        return analysis
    if "education" in groups:
        analysis.classification = "educational_content"
        analysis.confidence = 0.96
        analysis.reasons.append(
            "The post teaches job seekers how to improve their employability "
            "instead of offering work."
        )
        analysis.evidence.append("Job-search teaching language detected")
        return analysis
    if "tips" in groups:
        analysis.classification = "skills_tips"
        analysis.confidence = 0.93
        analysis.reasons.append(
            "The post shares tips or skills advice rather than offering paid work."
        )
        return analysis
    if "training" in groups:
        analysis.classification = "training_announcement"
        analysis.confidence = 0.94
        analysis.reasons.append(
            "The post advertises a course or training programme instead of offering work."
        )
        return analysis
    if "news" in groups:
        analysis.classification = "news_and_information"
        analysis.confidence = 0.93
        analysis.reasons.append("The post reports news rather than offering work.")
        return analysis
    if "announcement" in groups:
        analysis.classification = "general_announcement"
        analysis.confidence = 0.93
        analysis.reasons.append(
            "The post is a general announcement rather than an opportunity."
        )
        return analysis
    if "community" in groups:
        analysis.classification = "community_discussion"
        analysis.confidence = 0.9
        analysis.reasons.append("The post is a community discussion, not an offer.")
        return analysis
    if "job_seeker" in groups:
        analysis.classification = "community_discussion"
        analysis.confidence = 0.9
        analysis.reasons.append(
            "The author is looking for work rather than offering work to others."
        )
        return analysis
    if "seeking" in groups:
        analysis.classification = "ambiguous"
        analysis.confidence = 0.45
        analysis.reasons.append(
            "The post looks like it wants to hire someone but does not describe the "
            "work, the terms, or how to apply; a human must read it."
        )
        return analysis

    analysis.classification = "insufficient_information"
    analysis.confidence = 0.35
    analysis.reasons.append(
        "The post does not contain enough evidence of a work offer to classify it."
    )
    return analysis


def _employment_classification(text: str) -> str:
    """Part-time or full-time, based on the wording of the post."""
    if "part-time" in text or "part time" in text:
        return "part_time_employment"
    return "full_time_employment"


def _engagement_classification(groups: set[str]) -> str:
    """Pick the publishable classification that best matches the evidence."""
    if "consulting" in groups:
        return "consulting_opportunity"
    if "contract" in groups and "remote" in groups:
        return "remote_contract"
    if "project_based" in groups:
        return "project_contract"
    if "contract" in groups and "duration" in groups:
        return "short_term_contract"
    if "contract" in groups:
        return "project_contract"
    if "freelance" in groups:
        return "freelance_job"
    if "remote" in groups and "duration" in groups:
        return "short_term_contract"
    return "freelance_job"


def _offer_evidence(groups: set[str]) -> list[str]:
    labels = {
        "seeking": "The post states that someone is looking for a person to do work",
        "task": "The post describes a task, project, or deliverable",
        "application": "The post explains how to apply or contact the client",
        "payment": "The post states a budget, rate, or payment term",
        "duration": "The post states a duration, deadline, or time frame",
    }
    return [
        labels[name]
        for name in ("seeking", "task", "application", "payment", "duration")
        if name in groups
    ]
