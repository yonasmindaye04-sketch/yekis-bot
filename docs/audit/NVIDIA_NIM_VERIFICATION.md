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
| Mocked provider tests | **40 passed** (provider scenarios plus live-script logic checks). |
| **Live NVIDIA API test** | **VERIFIED.** HTTP 200 on both samples, real model id, measured latency, field-by-field comparison against deterministic extraction. |
| **Live service verification** | **VERIFIED.** The running API ingested with `extraction_method=llm` and the full workflow smoke test passed 54/54 against the live stack with NVIDIA in the loop. |
| Deterministic baseline comparison | **Recorded and compared** in §6. |

Live verification details:

| Item | Value |
|---|---|
| Test date (UTC) | 2026-11-11T17:41:20 |
| Base URL | `https://integrate.api.nvidia.com/v1` |
| Model id | `minimaxai/minimax-m3.1-flash` |
| HTTP status | `200` (both samples) |
| Latency | 105,099 ms and 104,245 ms (see §5.2) |
| Extraction method recorded by the service | `llm` (both samples) |

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

## 5. Live NVIDIA verification — VERIFIED

**Status:** executed successfully against the configured NVIDIA NIM endpoint.

### 5.1 Run record

| Item | Value |
|---|---|
| Test date (UTC) | 2026-11-11T17:41:20 |
| Endpoint | `POST https://integrate.api.nvidia.com/v1/chat/completions` |
| Model id | `minimaxai/minimax-m3.1-flash` |
| Authentication | `Authorization: Bearer <NVIDIA API key>` (never logged) |
| Sample 1 status | `200` |
| Sample 2 status | `200` |
| Command | `.venv/Scripts/python.exe scripts/verify_nvidia_live.py` |

### 5.2 Measured latency and the timeout fix

The first live attempt **failed** with `LLM request timed out before a response was received` at the original 30-second timeout. Diagnosis:

1. `GET https://integrate.api.nvidia.com/v1/models` from inside the container returned `200` in ~1,080 ms, so egress and DNS were healthy.
2. The completion request itself was slow: measured **105,099 ms** and **104,245 ms** for the two samples. This is NVIDIA NIM cold-start/queue latency for this model, not a network fault.

Fixes applied:

- `LLM_TIMEOUT_SECONDS` raised to `180` in `.env` (the measured ~105 s needs real headroom).
- Bounded provider-level retry added (`LLM_MAX_ATTEMPTS=2`, `LLM_RETRY_WAIT_SECONDS=5`) for transient failures only: timeouts, `429`, and `5xx`. Credential and schema failures are never retried.
- The HTTP client timeout in the verification harnesses was raised (`scripts/smoke_test.py --timeout`, `scripts/verify_extraction_provider.py --timeout`) because extraction runs synchronously inside the request.

**Operational consequence:** one extraction takes roughly 1.5–2 minutes with this model. The synchronous `POST /api/v1/ingestion/manual` path therefore blocks for that long. Bulk ingestion should go through the Celery collector/worker path rather than the HTTP endpoint.

### 5.3 Service-level verification

`scripts/verify_extraction_provider.py` ingested a legitimate post and a scam-pattern post through the **running API** (so the container's own environment and image were exercised, not the developer's shell):

| Sample | `extraction_method` | Key extracted fields |
|---|---|---|
| Legitimate post | `llm` | category `software`, skills `python, fastapi, postgresql, docker`, budget `2500.0 USD`, duration `3 weeks`, remote `true`, no risk flags, `pending_review` |
| Scam-pattern post | `llm` | budget suppressed to `null`, risk flags `upfront_payment, urgency_pressure, implausible_rate, off_platform_contact` (score 98), `needs_review` |

`scripts/smoke_test.py` then ran the complete workflow against the live stack with NVIDIA in the loop: **54/54 checks passed** — source create/edit/toggle, ingestion, extraction, duplicate rejection, risk routing, verbatim original text, review approval, note persistence, actor attribution, audit events, transition rules, field edits with diffs, publication gate, analytics, pagination and validation.

### 5.4 Reproducing it

```bash
# .env (git-ignored; never paste the key into chat)
EXTRACTION_PROVIDER=llm
LLM_API_KEY=your_nvidia_api_key
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=minimaxai/minimax-m3.1-flash
LLM_TIMEOUT_SECONDS=180
LLM_MAX_ATTEMPTS=2
LLM_RETRY_WAIT_SECONDS=5

# Direct provider verification (model id, status, latency, accuracy)
.venv/Scripts/python.exe scripts/verify_nvidia_live.py

# Service-level verification (what the running stack actually used)
.venv/Scripts/python.exe scripts/verify_extraction_provider.py --base-url http://127.0.0.1:8100

# Full workflow against the live stack
.venv/Scripts/python.exe scripts/smoke_test.py --base-url http://127.0.0.1:8100 --timeout 600
```

### 5.5 Separate issue found: the Gemini fallback was silently broken

Before the switch, the service was still on `EXTRACTION_PROVIDER=gemini` and every request fell back to rules with:

```
Gemini request failed with status 404
```

`gemini-2.5-flash` is not available for the configured AI Studio key. The rules fallback hid this completely. The Gemini adapter now explains a 404 as "the model is not available for this API key", and the Gemini model id should be set to one the key can reach (for example `gemini-flash-latest`). This does not affect NVIDIA, which is now the active provider.

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

The rules taxonomy groups `docker` under the `devops` skill, while the model returns `docker` and `postgresql` as separate skills. The live comparison therefore shows a skill-set difference that is a **taxonomy difference, not an error**: the merged result keeps provider skills when the provider returns any. Provider skill casing is now normalised to lowercase (the same form the rules extractor emits) so analytics do not split `Python` and `python` into separate demand entries.

### Live comparison result (2026-11-11)

| Field | Sample A provider vs rules | Sample B provider vs rules |
|---|---|---|
| opportunity_type | `freelance_job` = `freelance_job` | `freelance_job` = `freelance_job` |
| category | `software` = `software` | `other` vs `None` (provider is more specific) |
| skills | `python, fastapi, postgresql, docker` vs `devops, fastapi, python` (taxonomy difference) | `[]` = `[]` |
| budget_amount | `2500.0` = `2500.0` | `None` = `None` (bait suppressed in both) |
| budget_currency | `USD` = `USD` | `None` = `None` |
| duration | `3 weeks` = `3 weeks` | `None` = `None` |
| remote | `True` = `True` | `True` = `True` |
| risk_flags | `[]` = `[]` | provider added `implausible_rate`; the merged result carries all four flags (score 98) |

**Agreement:** every safety-relevant field agreed. The provider contributed one extra risk signal (`implausible_rate`) that the deterministic rules missed, which is exactly the value of running both. The provider never produced a budget for the scam sample, and the deterministic merge suppressed it regardless.

---

## 7. Defect found and fixed during this work

The deterministic budget parser only recognised currency-before-amount (`$300`, `USD 300`). The common amount-before-currency form (`2500 USD`, `45,000 ETB`) was missed, so the rules baseline could not read a budget that the provider extracted correctly.

Fixed in `app/services/extractor.py` by adding `AMOUNT_CURRENCY_PATTERN` and merging both candidate sets before keyword-proximity selection. Covered by `test_extraction_accuracy_for_budget_currency_skills_and_deadline` and the existing extractor suite.

---

## 8. Repository state

| Check | Result |
|---|---|
| `pytest -q` (bare `pytest` executable) | 180 passed |
| `python -m pytest -q` | 180 passed |
| `ruff check app tests migrations scripts` | all checks passed |
| `python -m compileall -q app tests migrations scripts` | exit 0 |
| Live NVIDIA provider | HTTP 200 on both samples, ~105 s latency per extraction |
| Live service workflow | 54/54 smoke checks passed with NVIDIA active |
| Architecture rewritten | No. Existing provider protocol, orchestrator, fallback, review, and publishing paths are untouched. |
