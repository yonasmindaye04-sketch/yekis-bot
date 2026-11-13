"""Automated compliance checks for rules.md sections 2, 3, and 4.

These tests fail when the admin dashboard or the repository drifts from the
mandatory visual standards: zero emoji, bold-colored-text indicators only,
token-driven theming, and responsive/accessible layout rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "app" / "static" / "admin" / "index.html"

# True emoji: pictographic blocks, emoji-presentation symbols, variation
# selectors. Arrows and other typography are deliberately excluded.
EMOJI_PATTERN = re.compile(
    "[\U0001F000-\U0001FAFF"
    "\U0001F1E6-\U0001FFFD"
    "\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF"
    "\uFE0F\u20E3]"
)

SCANNED_SUFFIXES = {".py", ".md", ".html", ".js", ".css", ".txt"}


def _repo_text_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "rules.md"]
    for folder in ("app", "docs", "scripts", "tests", "migrations"):
        root = ROOT / folder
        if root.exists():
            files.extend(p for p in root.rglob("*") if p.suffix in SCANNED_SUFFIXES)
    return files


def _css_rule_blocks(css: str) -> dict[str, str]:
    """Extract CSS rule bodies keyed by selector (top-level rules only)."""
    blocks: dict[str, str] = {}
    index = 0
    length = len(css)
    while index < length:
        brace = css.find("{", index)
        if brace == -1:
            break
        selector = css[index:brace].strip()
        depth = 1
        cursor = brace + 1
        while cursor < length and depth:
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
            cursor += 1
        body = css[brace:cursor]
        if selector and not selector.startswith("@") and "{" not in selector:
            blocks[selector] = body
        index = cursor
    return blocks


@pytest.fixture(scope="module")
def dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css(dashboard: str) -> str:
    match = re.search(r"<style>(.*?)</style>", dashboard, re.DOTALL)
    assert match, "dashboard must define a <style> block"
    return match.group(1)


@pytest.fixture(scope="module")
def blocks(css: str) -> dict[str, str]:
    return _css_rule_blocks(css)


# --- Rule 2: zero emoji -------------------------------------------------


def test_repository_contains_no_emoji():
    offenders = []
    for path in _repo_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in EMOJI_PATTERN.finditer(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {match.group()!r}")
    assert not offenders, "emoji are forbidden (rules.md 2.1): " + "; ".join(offenders)


def test_dashboard_contains_no_emoji(dashboard: str):
    assert not EMOJI_PATTERN.search(dashboard)


# --- Rule 3: indicators -------------------------------------------------


def test_badge_rule_is_bold_text_without_background_or_border(blocks: dict[str, str]):
    body = blocks.get(".badge", "")
    assert body, ".badge rule must exist"
    lowered = body.lower()
    assert "font-weight: 700" in lowered, "indicators must use bold text"
    assert "background" not in lowered or "background: transparent" in lowered, (
        "indicators must not use a background tint"
    )
    assert "border" not in lowered or "border: none" in lowered, (
        "indicators must not use a border"
    )
    assert "border-radius" not in lowered, "indicators must not be pill shaped"


def test_indicator_classes_cover_every_required_state(css: str):
    for state in (
        "approved",
        "rejected",
        "pending_review",
        "needs_review",
        "scheduled",
        "sending",
        "published",
        "failed",
        "active",
        "inactive",
        "risk",
    ):
        assert f".badge.{state}" in css, f"missing indicator style for {state}"


def test_no_dot_pseudo_element_indicators(dashboard: str):
    assert 'class="dot"' not in dashboard
    assert ".dot::before" not in dashboard
    assert ".dot::after" not in dashboard


def test_tab_counts_are_plain_text_not_pills(blocks: dict[str, str]):
    body = blocks.get(".tabs button .badge-count", "")
    assert body, ".badge-count rule must exist"
    lowered = body.lower()
    assert "background: transparent" in lowered
    assert "border: none" in lowered
    assert "border-radius" not in lowered


def test_indicator_status_text_has_no_pill_container(dashboard: str):
    assert 'class="pill"' not in dashboard
    assert 'class="indicator' in dashboard


# --- Rule 4.1: theming --------------------------------------------------


def test_theme_tokens_are_defined_for_both_themes(css: str):
    required = ("--bg", "--panel", "--line", "--text", "--text-muted", "--accent")
    dark = css.split(':root[data-theme="light"]', 1)[0]
    light = css.split(':root[data-theme="light"]', 1)[1]
    for token in required:
        assert f"{token}:" in dark, f"dark theme must define {token}"
        assert f"{token}:" in light, f"light theme must define {token}"


def test_component_styles_use_tokens_not_hardcoded_colors(css: str):
    """Hex/rgba literals may only appear inside the theme token blocks."""
    token_ranges = []
    for marker in (":root {", ':root[data-theme="light"] {'):
        start = css.find(marker)
        assert start != -1, f"missing theme block {marker}"
        depth = 0
        cursor = css.find("{", start)
        while cursor < len(css):
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        token_ranges.append((start, cursor))

    for lineno, line in enumerate(css.splitlines(), 1):
        if not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", line):
            continue
        offset = sum(len(part) + 1 for part in css.splitlines()[: lineno - 1])
        inside_tokens = any(start <= offset <= end for start, end in token_ranges)
        assert inside_tokens, (
            f"hardcoded color outside theme tokens (rules.md 4.1) at CSS line {lineno}: "
            f"{line.strip()}"
        )


def test_theme_conditional_css_only_changes_paint(blocks: dict[str, str]):
    """Theme blocks may only alter color/background/border/shadow/opacity.

    Custom properties are allowed because they only carry paint values and
    keep the geometry definitions in the shared base rules.
    """
    allowed = {
        "color",
        "background",
        "background-color",
        "border",
        "border-color",
        "border-bottom-color",
        "border-top-color",
        "box-shadow",
        "opacity",
        "color-scheme",
    }
    for selector, body in blocks.items():
        if "data-theme" not in selector:
            continue
        for prop in re.findall(r"([a-zA-Z-]+)\s*:", body):
            if prop.startswith("--"):
                continue
            assert prop in allowed, (
                f"theme-conditional rule '{selector}' changes '{prop}', which must be "
                "theme-invariant (rules.md 4.1)"
            )


def test_single_theme_toggle_in_header(dashboard: str):
    assert dashboard.count('id="themeToggleBtn"') == 1, "exactly one theme toggle is allowed"
    header = dashboard.split("</header>", 1)[0]
    assert 'id="themeToggleBtn"' in header, "the theme toggle belongs in the primary header"
    assert 'addEventListener("click", toggleTheme)' in dashboard, "the toggle must be wired"


def test_theme_toggle_is_wired_to_persistence(dashboard: str):
    assert "function applyTheme" in dashboard
    assert "function toggleTheme" in dashboard
    assert "initTheme()" in dashboard
    assert "yekis.theme" in dashboard


# --- Rule 4.2: date filters ---------------------------------------------


def test_date_filters_use_compact_fixed_width(dashboard: str, css: str):
    assert dashboard.count('type="date"') >= 2, "date range filters must be present"
    body = css.split(".date-filter-group input", 1)
    assert len(body) == 2, "date filter inputs need an explicit width rule"
    assert "width: 150px" in body[1].split("}", 1)[0]


# --- Rule 4.3: responsiveness ------------------------------------------


def test_viewport_prevents_horizontal_overflow(css: str):
    assert "overflow-x: hidden" in css


def test_flex_and_grid_children_can_shrink(css: str):
    assert "min-width: 0" in css


def test_mobile_media_query_enforces_two_kpi_columns(css: str):
    mobile = css.split("@media (max-width: 640px)", 1)
    assert len(mobile) == 2, "a 640px breakpoint is required"
    body = mobile[1]
    assert "grid-template-columns: repeat(2, 1fr)" in body
    assert "min-height: 44px" in body, "touch targets must be at least 44px"


def test_action_buttons_share_a_row_on_mobile(css: str):
    mobile = css.split("@media (max-width: 640px)", 1)[1]
    assert ".actions" in mobile and "flex: 1 1 auto" in mobile


def test_dashboard_has_no_inline_grid_template_columns(dashboard: str):
    """Inline grid columns would defeat the responsive KPI rules."""
    assert "gridTemplateColumns" not in dashboard


# --- Rule 6.3: session handling -----------------------------------------


def test_inactivity_timeout_is_thirty_minutes(dashboard: str):
    assert "30 * 60 * 1000" in dashboard, "a 30-minute inactivity timeout is required"
    assert "clearAuthState" in dashboard


def test_401_response_clears_authentication_state(dashboard: str):
    branch = dashboard.split("response.status === 401", 1)
    assert len(branch) == 2, "401 handling must exist"
    assert "clearAuthState" in branch[1][:400]


# --- Autonomous publishing control panel ---------------------------------


def test_autonomy_panel_is_reachable_from_the_navigation(dashboard: str):
    """Every feature must be reachable from the UI navigation (rules.md 4.3)."""
    assert 'data-tab="autonomy"' in dashboard
    assert 'id="tab-autonomy"' in dashboard
    assert 'btn.dataset.tab === "autonomy"' in dashboard


def test_autonomy_panel_exposes_every_required_control(dashboard: str):
    required = [
        'id="autonomyEnabled"',
        'id="autonomyMode"',
        'id="autonomyThreshold"',
        'id="autonomySources"',
        'id="autonomyCategories"',
        'id="autonomyDailyLimit"',
        'id="autonomyInterval"',
        'id="autonomyCounters"',
        'id="autonomyDecisions"',
    ]
    for control in required:
        assert control in dashboard, f"missing autonomy control {control}"


def test_review_queue_exposes_classification_filters(dashboard: str):
    assert 'id="reviewGroup"' in dashboard
    for group in ("freelance", "employment", "education", "announcement", "suspicious",
                  "ambiguous"):
        assert f'value="{group}"' in dashboard
