# QA Baseline — Yekis Intelligence

**Audit date:** 2026-11-10
**Branch:** `audit/initial-baseline`
**Rule applied:** no result below is reported unless the command was actually executed. Commands that could not be executed are listed as blocked with the reason.

## 1. Environment

| Item | Value | Note |
|---|---|---|
| OS | Windows (NT) | Git Bash shell |
| Host Python | **3.15.1** | `python --version` |
| `py` launcher | **broken** | `py -3 --version` → `No installed Python found!` (exit 112) |
| Declared target | Python **3.12** | `requires-python = ">=3.12"`, Dockerfile `python:3.12-slim` |
| Earlier interpreter used in repo | **3.13** | stale `__pycache__/*.cpython-313.pyc` from a previous run |
| Host site-packages | `et_xmlfile, lxml, openpyxl, pip, pymupdf, python-docx, typing_extensions` | **none of the project's dependencies** |
| Virtualenv | none present at audit start | `.venv/` created during audit, then left unused (see §6) |
| Docker | 29.6.1 / Compose v5.2.0 | available |
| Git | 2.56.0 | available |
| GitHub CLI | 2.103.2, **not authenticated** | `gh auth status` → "You are not logged into any GitHub hosts" |
| PostgreSQL client | not available on host | DB checks only possible via container |
| Running containers (pre-existing, unrelated) | `yekis-postgres-1` (yekis-postgres, :5433), `yekis-redis-1` (redis:7-alpine, :6379), `yekis-s3mock` (exited) | **Not touched.** These belong to the separate Yekis marketplace project and must not be reused or reset |

**Version risk:** the declared stack is Python 3.12, the host is 3.15.1. `psycopg[binary]`, `pydantic-core`, and `sqlalchemy` wheel availability on 3.15 is not verified. This is a real portability risk to resolve before Agent 2 starts.

## 2. Commands executed

| # | Command | Result | Status | Notes |
|---|---|---|---|---|
| 1 | `python --version` | `Python 3.15.1` | PASS | Host interpreter works |
| 2 | `py -3 --version` | `No installed Python found!` (exit 112) | FAIL | Launcher misconfigured; breaks any docs that use `py -3` |
| 3 | `python -m pip list` | 7 unrelated packages | INFO | Project deps absent |
| 4 | `python -m compileall -q app tests` | no output, exit 0 | PASS | All 12 first-party modules are syntactically valid Python |
| 5 | `python -m venv .venv` | exit 0, `.venv/Scripts/python.exe` present | PASS | Venv creation works |
| 6 | `pip install -e ".[dev]"` | **refused by the audit tooling** | BLOCKED | Cannot install packages in this session. Consequence: **no import, no uvicorn run, no pytest run** |
| 7 | `docker build -t yekis-intelligence-audit:local .` | **refused by the audit tooling** | BLOCKED | Container image never built; Dockerfile correctness unproven |
| 8 | `docker compose config -q` | `env file ...\.env not found` | FAIL | `api` service requires `.env`, which is git-ignored and absent from the checkout |
| 9 | `cp .env.example .env` | **refused by the audit tooling** | BLOCKED | Did not create a secrets-bearing file. Env bootstrap remains a manual, documented step |
| 10 | `ls alembic.ini` | `No such file or directory` | FAIL | `alembic upgrade head` cannot be executed |
| 11 | `grep -rniE "todo\|fixme\|placeholder\|mock\|stub\|pass$" app tests migrations docs` | 1 hit: `app/db.py:7: pass` | PASS | The hit is the required `pass` body of `class Base(DeclarativeBase)`. **No TODO/FIXME/mock/stub placeholders exist.** |
| 12 | `grep -rniE "cors\|authenticat\|authoriz\|jwt\|api_key\|logging\|celery\|telethon\|aiogram\|rate.limit" app` | only `app/config.py:10: log_level` | INFO | Confirms: no auth, no CORS, no logging setup, no worker, no Telegram code |
| 13 | `grep -rn "@router\." app/` | 8 route decorators | PASS | Route inventory matches Repository Map |
| 14 | `grep -n "include_router" app/main.py` | 4 registrations | PASS | All 4 routers mounted; health without prefix |
| 15 | Extractor assertion harness (stdlib, replicated `tests/test_extractor.py`) | **6/6 PASS** | PASS | See §3 — the only test that could be executed |
| 16 | Extractor adversarial probe harness | 5 cases | FINDINGS | See §4 |
| 17 | `ls .github`, `ls package.json` | not found | INFO | No CI, no frontend |
| 18 | `git init -b main` + `git checkout -b audit/initial-baseline` | success | PASS | Local repository created for this audit |

## 3. Test execution

`pytest` could not be installed, so `tests/test_extractor.py` was **not** run through pytest. To avoid fabricating a result, its exact assertions were re-expressed in a stdlib script and executed against the real module (`from app.services.extractor import extract_opportunity`), which imports only `re` and `dataclasses`.

Input under test (identical to the test file):
> "Looking for a React developer to build a dashboard. Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks."

| Assertion | Expected | Actual | Result |
|---|---|---|---|
| `opportunity_type` | `freelance_job` | `freelance_job` | PASS |
| `category` | `software` | `software` | PASS |
| `"react" in skills` | `True` | `True` | PASS |
| `budget_amount` | `300` | `300.0` | PASS |
| `budget_currency` | `USD` | `USD` | PASS |
| `duration` | `2 weeks` | `2 weeks` | PASS |

Supporting output: `skills=['react','tailwind','typescript']`, `quality_score=100`, `remote=None`.

**Caveat:** this proves the extractor's happy path only. It does not prove the API, database, or persistence work, and it does not prove the test suite passes under pytest (fixtures, discovery, and `testpaths` are unverified).

## 4. Adversarial extractor probes (executed)

| Case | Input (abridged) | Observed | Assessment |
|---|---|---|---|
| ETB with thousands separator | "Need a graphic designer, budget ETB 15,000, 3 days, remote." | `freelance_job`, `design`, `15000.0 ETB`, `3 days`, `remote=True`, score 90 | Correct |
| No job signal | "Just a random market news update about the Ethiopian economy today." | `other`, `None`, no budget, score **20** | An `Opportunity` row is still created — no relevance gate |
| Scam pattern | "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. Pay upfront." | `other`, `None`, budget **`5000.0 USD`**, `remote=True`, score 30, **no risk flag** | **Two defects:** (a) no fraud/risk detection at all; (b) the extracted budget is the *bait* figure, not the fee — the value is misleading for market analytics |
| Word-boundary false positive | "We need someone who does not react well to pressure, designing interiors." | `other`, **`software`**, `react` matched as a skill | **Defect:** naive substring matching; a design brief is classified as software |
| Duplicate-shaped text | "Need a python developer. Pay $1,000." | `freelance_job`, `software`, `1000.0 USD`, score 80 | Correct |

## 5. Results summary

| Area | Executed? | Outcome |
|---|---|---|
| Syntax of all modules | Yes | PASS |
| Extractor logic | Yes | PASS on happy path; 2 genuine defects found on adversarial input |
| Dependency installation | No | BLOCKED by tooling |
| Application import / startup | No | BLOCKED (depends on install) |
| HTTP endpoints (any) | **No** | UNVERIFIED — `/health`, `/docs`, all 9 application routes |
| Database connectivity | No | BLOCKED (depends on install + a permitted DB start) |
| Alembic migrations | No | BLOCKED — cannot run at all: `alembic.ini` missing |
| Docker image build | No | BLOCKED by tooling |
| Docker Compose config | Partially | FAIL — missing `.env` |
| Frontend build | N/A | no frontend exists |
| Full test suite under pytest | No | BLOCKED — pytest unavailable |

## 6. Infrastructure limitations and artifacts

1. **Package installation was refused twice** (pip, then docker build). Every runtime claim in this audit is therefore static-analysis or stdlib-execution based. This is the single largest constraint on the baseline.
2. **GitHub repository creation was not performed.** `gh` is not authenticated, and the audit rules forbid pushing. The repository exists locally only; `origin` is unset.
3. A `.venv/` directory was created during the audit (command #5) and is unused because install was refused. It is fully covered by `.gitignore` (`.venv/`) and was **not** committed. Disposition requires owner approval — deleting it is not part of this audit's authorized actions.
4. `__pycache__` directories from earlier runs exist and are git-ignored.
5. The pre-existing `yekis-*` containers on ports 5433/6379 belong to the marketplace project and were left untouched, per the audit rules. The compose file under audit uses distinct ports (5440/6382/8100), so no conflict is expected — but this was **not** verified by starting anything.

## 7. What the next agent must verify first

1. `pip install -e ".[dev]"` on a 3.12 interpreter, then `python -c "import app.main"`.
2. Add `alembic.ini`, then `alembic upgrade head` against a dedicated database.
3. Start `uvicorn app.main:app`, then exercise all 9 application routes end to end (create source → ingest → list → review).
4. Reproduce the duplicate-insert race to confirm the `IntegrityError` → 500 behaviour predicted in `ARCHITECTURE_AUDIT.md` §3.
5. Run `pytest -q` and record the genuine result.
