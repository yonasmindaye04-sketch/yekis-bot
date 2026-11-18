"""The deployment contract of the split deployment.

The API runs on Vercel and the scheduled collector runs in GitHub Actions.
Both talk to the same database and both need the same environment. These
tests lock down the parts of that contract that break silently:

* the environment the scheduled job passes to the application;
* the schema migration step, which has to repair a database that the API
  created outside Alembic;
* the absence of credentials in the repository.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "collector.yml"
VERCEL = ROOT / "vercel.json"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_passes_the_model_timeout():
    """The production model answers in 90-180 seconds.

    Without an explicit timeout the application used its 30 second default,
    every request timed out, and every classification silently fell back to
    the deterministic rules, which can never reach the approval threshold.
    """
    workflow = _workflow()
    assert "LLM_TIMEOUT_SECONDS:" in workflow
    assert "LLM_MAX_ATTEMPTS:" in workflow
    assert "LLM_RETRY_WAIT_SECONDS:" in workflow

    match = re.search(r"LLM_TIMEOUT_SECONDS:\s*\$\{\{[^}]*\|\|\s*'(\d+)'", workflow)
    assert match is not None, "the model timeout must have a default"
    assert int(match.group(1)) >= 90


def test_the_workflow_selects_the_model_providers():
    workflow = _workflow()
    assert "CLASSIFICATION_PROVIDER:" in workflow
    assert "EXTRACTION_PROVIDER:" in workflow
    assert "LLM_BASE_URL:" in workflow
    assert "LLM_API_KEY:" in workflow
    # The provider defaults to "rules" when the secret is missing, and the
    # deterministic stage never reaches the automatic approval threshold.
    assert re.search(r"CLASSIFICATION_PROVIDER:\s*\$\{\{[^}]*\|\|\s*'llm'", workflow)


def test_the_workflow_publishes_only_to_allowlisted_channels():
    workflow = _workflow()
    assert "PUBLISHING_ALLOWED_CHANNELS:" in workflow
    assert "PUBLISHING_ENABLED:" in workflow


def test_the_workflow_migrates_the_database_through_the_repairable_path():
    """``alembic upgrade head`` alone fails on an API-created database."""
    workflow = _workflow()
    assert "python -m scripts.migrate" in workflow
    assert "alembic upgrade head" not in workflow, (
        "the bare migration command fails when the API created the tables first"
    )


def test_the_workflow_has_room_for_the_model_calls():
    workflow = _workflow()
    match = re.search(r"timeout-minutes:\s*(\d+)", workflow)
    assert match is not None
    assert int(match.group(1)) >= 45, "a run needs room for the model calls"

    assert "COLLECTION_TIME_BUDGET_SECONDS:" in workflow


def test_the_workflow_reports_the_effective_configuration():
    assert "python -m scripts.preflight" in _workflow()


def test_vercel_ships_the_migrations_and_has_a_function_budget():
    config = json.loads(VERCEL.read_text(encoding="utf-8"))
    function = config["functions"]["api/index.py"]
    include = function["includeFiles"]
    if isinstance(include, str):
        include = [include]
    assert any(pattern.startswith("migrations/") for pattern in include), (
        "the API needs the migration scripts to prepare the database"
    )
    assert int(function["maxDuration"]) >= 30


def _tracked_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        return []
    return [ROOT / name for name in result.stdout.split("\0") if name]


CREDENTIAL_PATTERNS = (
    # Production API keys of this project.
    re.compile(r"yk-[a-z]{3}-[0-9a-f]{16,}"),
    # NVIDIA NIM keys.
    re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"),
    # Telegram bot tokens.
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
)


def test_no_credential_is_committed():
    """The repository is public: a committed key is a leaked key."""
    offenders: list[str] = []
    for path in _tracked_files():
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".ico"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(content):
                offenders.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert not offenders, "a credential is committed: " + "; ".join(offenders)
