# NVIDIA NIM Provider Verification Report

**Scope:** NVIDIA NIM hosted inference integrated through the existing OpenAI-compatible provider architecture.
**Report date:** 2026-11-11
**Branch:** `qa/system-integration-verification`
**Rule reference:** `rules.md` §7.2 (provider protocol, injection defense, closed schema, deterministic risk, rules fallback)

---

## 1. Verdict

| Area | Result |
|---|---|
| Integration | **Complete.** NVIDIA NIM works through the existing `EXTRACTION_PROVIDER=llm` path. No architecture rewrite; no new provider class. |
| Mocked provider tests | **35 passed** (31 provider scenarios + 4 live-script logic checks). |
| Live NVIDIA API test | **NOT RUN — blocked.** No NVIDIA API key is configured in this environment, and outbound LLM calls are blocked by the execution environment. See §5. |
| Deterministic baseline comparison | **Recorded** in §6 so a live run can be compared field by field. |

**No live NVIDIA response was observed.** Every provider behaviour below is proven against a mocked HTTP transport. Nothing in this report should be read as a live NVIDIA verification.

---

## 2. What was confirmed before writing code

The existing adapter already implements the OpenAI chat-completions protocol:

| NVIDIA NIM requirement | Existing adapter | Change needed |
|---|---|---|
| `POST https://integrate.api.nvidia.com/v1/chat/completions` | `f"{base_url}/chat/completions"` | None |
| `Authorization: Bearer <NVIDIA API key>` | Bearer header | None |
| Body: `model`, `temperature`, `max_tokens`, `messages` | Present | None |
| Response envelope `choices[0].message.content` | Parsed | None |
| JSON mode `response_format` | Sent as `json_object` | Made configurable (some NIM deployments reject it) |
| Provider-specific request fields (NIM `guided_json`) | Not supported | Added `LLM_EXTRA_BODY` |

**Conclusion:** the core path already supported NVIDIA. The change set is deliberately small and additive.

---

## 3. Changes made

### 3.1 Configuration (`app/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `LLM_RESPONSE_FORMAT` | `json_object` | JSON mode for the provider. Set `none` for deployments that reject the parameter. |
| `LLM_EXTRA_BODY` | empty | Optional JSON object merged into the request body, for example NVIDIA guided decoding. |

Existing variables are reused unchanged: `EXTRACTION_PROVIDER=llm`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`.

### 3.2 Adapter (`app/services/llm/openai_compatible.py`)

- `response_format` and `extra_body` constructor options; extras merge last so standard fields are never clobbered.
- HTTP failures now map to typed, secret-free errors:
  - `401/403` -> `ProviderAuthError`
  - `429` -> `ProviderRateLimitError` (includes `retry-after` when present)
  - `5xx` -> `ProviderUnavailableError`
  - transport/timeout -> `ProviderUnavailableError`
  - other `4xx` -> `ProviderError`
- All of these remain `ProviderError` subclasses, so the orchestrator's rules fallback is unchanged.

### 3.3 Orchestrator (`app/services/llm/orchestrator.py`)

- Passes the new settings to the adapter.
- `parse_extra_body()` rejects malformed JSON loudly as a configuration error instead of silently ignoring it.

### 3.4 Preserved behaviour (explicitly re-verified)

| Guarantee | Where | Status |
|---|---|---|
| Structured output validation | `app/services/llm/schema.py` (`extra="forbid"`) | Unchanged |
| Prompt-injection defense | `app/services/llm/prompts.py` `<untrusted_content>` | Unchanged |
| Deterministic risk merge | `merge_safety()` | Unchanged |
| Upfront-payment budget suppression | `merge_safety()` | Unchanged |
| Rules fallback | `extract_with_provider()` -> `rules_fallback` | Unchanged |
| No auto-approve / auto-publish | Review and publishing rules | Untouched |
| Gemini provider | `app/services/llm/gemini.py` | Untouched |
| Rules provider | `app/services/llm/rules_provider.py` | Untouched |

---

## 4. Mocked test coverage (35 checks)

File: `tests/test_nvidia_provider.py`

| # | Required scenario | Test | Result |
|---|---|---|---|
| 1 | Valid NVIDIA API response | `test_valid_nvidia_response_is_extracted` | PASS |
| 2 | Invalid API key | `test_invalid_api_key_raises_auth_error`, `test_forbidden_response_raises_auth_error` | PASS |
| 3 | Provider timeout | `test_provider_timeout_raises_unavailable` | PASS |
| 4 | Rate-limit response | `test_rate_limit_response_raises_rate_limit_error`, `test_rate_limit_exposes_retry_after` | PASS |
| 5 | Malformed JSON | `test_malformed_json_is_rejected`, `test_empty_content_is_rejected`, `test_unexpected_envelope_is_rejected` | PASS |
| 6 | Invalid structured output | `test_unknown_field_is_rejected`, `test_amount_without_currency_is_rejected`, `test_out_of_range_score_is_rejected` | PASS |
| 7 | Rules fallback | `test_provider_failure_falls_back_to_rules` (5 failure modes) | PASS |
| 8 | Budget, currency, skills, deadline | `test_extraction_accuracy_for_budget_currency_skills_and_deadline`, `test_nvidia_extraction_is_merged_with_deterministic_rules` | PASS |
| 9 | Suspicious upfront-payment detection | `test_upfront_payment_is_detected_even_when_the_model_ignores_it`, `test_model_reported_upfront_payment_is_preserved` | PASS |
| 10 | Original post text preserved | `test_original_post_text_is_preserved_verbatim` | PASS |
| - | Request shape / endpoint / auth | `test_nvidia_request_shape_matches_openai_protocol`, `test_nvidia_endpoint_url_and_bearer_auth` | PASS |
| - | Key never leaks | `test_provider_never_echoes_the_api_key_in_errors` | PASS |
| - | Configuration plumbing | `test_get_provider_builds_nvidia_provider_from_settings`, `test_response_format_can_be_disabled_...`, `test_extra_body_is_merged_for_guided_decoding`, `test_parse_extra_body_rejects_malformed_json`, `test_provider_requires_a_key` | PASS |
| - | Live-script logic (offline) | `test_live_verification_script_skips_without_a_key`, `..._refuses_a_non_nvidia_endpoint`, `test_live_verification_compares_provider_against_rules`, `test_live_verification_report_refuses_to_print_a_key` | PASS |

### Notes on field naming

The current schema has **no separate `deadline` or `location` fields**. The extraction contract maps:

| Requested concept | Schema field | Notes |
|---|---|---|
| Job title | `title` | First line of the post, refined by the provider. |
| Description | `description` | Up to 5,000 characters. |
| Required skills | `skills` | Closed list, max 20, de-duplicated. |
| Budget | `budget_amount` + `budget_currency` | Both must be set together. |
| Deadline | `duration` | Free-text deadline/duration signal, e.g. `3 weeks`. |
| Location | *(not modelled)* | Collected as part of `description`; adding a first-class field is a schema change, out of scope for provider integration. |
| Scam indicators | `risk_flags` + `risk_score` | Deterministic rules always run and always merge in. |

---

## 5. Live NVIDIA verification — NOT RUN

**Status:** not executed.

Reasons:

1. `LLM_API_KEY` is not configured in this environment (`LLM_BASE_URL` is still the OpenAI default; `EXTRACTION_PROVIDER=gemini`).
2. Outbound LLM calls are blocked by the execution environment, so no provider credential could be used even if one were present.

The verification harness is ready at `scripts/verify_nvidia_live.py`. To run it yourself:

```bash
# 1. Configure locally (.env is git-ignored; never paste the key into chat)
EXTRACTION_PROVIDER=llm
LLM_API_KEY=your_nvidia_api_key
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-3.3-70b-instruct   # exact model id from build.nvidia.com

# 2. Run the live verification
.venv/Scripts/python.exe scripts/verify_nvidia_live.py
```

It prints a JSON report containing the exact model ID, UTC test date, HTTP status, latency in milliseconds, and a field-by-field comparison against deterministic extraction. It refuses to run without a key, refuses a non-NVIDIA base URL unless `--allow-non-nvidia` is passed, and refuses to print a report that contains the key.

**Fields to record when it runs:** model ID, test date, HTTP status, latency, and per-field agreement with the rules baseline in §6.

---

## 6. Deterministic baseline for comparison

Computed with the current rules extractor on the two sample posts used by the live script (`scripts/verify_nvidia_live.py`).

### Sample A — legitimate freelance post

> We are hiring a Senior Python Backend Engineer for a fintech client. Required skills: Python, FastAPI, PostgreSQL, Docker. Budget: 2500 USD. Deadline: 3 weeks from now. Remote work.

| Field | Rules baseline |
|---|---|
| opportunity_type | `freelance_job` |
| category | `software` |
| skills | `devops`, `fastapi`, `python` |
| budget_amount | `2500.0` |
| budget_currency | `USD` |
| duration | `3 weeks` |
| remote | `True` |
| risk_flags | `[]` |
| risk_score | `0` |
| quality_score | `100` |

### Sample B — upfront-fee scam pattern

> URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. Pay upfront. Only 2 slots left, act now.

| Field | Rules baseline |
|---|---|
| opportunity_type | `freelance_job` |
| category | `None` |
| skills | `[]` |
| budget_amount | `None` (bait amount suppressed) |
| budget_currency | `None` |
| duration | `None` |
| remote | `True` |
| risk_flags | `upfront_payment`, `off_platform_contact`, `urgency_pressure` |
| risk_score | `75` |
| quality_score | `13` |

### Known difference to watch for

The rules taxonomy groups `docker` under the `devops` skill, while a language model will normally return `docker` and `postgresql` as separate skills. The live comparison will therefore show a skill-set difference that is a **taxonomy difference, not an error**. The merged result keeps provider skills when the provider returns any.

---

## 7. Defect found and fixed during this work

The deterministic budget parser only recognised currency-before-amount (`$300`, `USD 300`). The common amount-before-currency form (`2500 USD`, `45,000 ETB`) was missed, so the rules baseline could not read a budget that the provider extracted correctly.

Fixed in `app/services/extractor.py` by adding `AMOUNT_CURRENCY_PATTERN` and merging both candidate sets before keyword-proximity selection. Covered by `test_extraction_accuracy_for_budget_currency_skills_and_deadline` and the existing extractor suite.

---

## 8. Repository state

| Check | Result |
|---|---|
| `pytest -q` (bare `pytest` executable) | 174 passed |
| `python -m pytest -q` | 174 passed |
| `ruff check app tests migrations scripts` | all checks passed |
| `python -m compileall -q app tests migrations scripts` | exit 0 |
| Architecture rewritten | No. Existing provider protocol, orchestrator, fallback, review, and publishing paths are untouched. |
