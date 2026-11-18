# Autonomous Publishing — Safety Report, Rollout and Rollback

**Scope:** the classification engine, the autonomous approval service, and the
autonomous Telegram publishing workflow.
**Branch:** `feature/autonomous-classification-publishing`
**Default state:** disabled, mode `shadow`. Production autonomy is **not**
activated by this branch.

---

## 1. Safety model

| # | Control | Where it lives | Enforced by |
|---|---|---|---|
| 1 | Autonomous publishing is off unless explicitly enabled and configured with a mode, a channel and an approved source list | `app/services/autonomy.py` | `publishing_blocked()`, `resolve_config()` |
| 2 | A post only qualifies when there is evidence of an offer | `app/services/classification/deterministic.py` | `_has_offer_evidence()` |
| 3 | The deterministic risk engine has the final word | `app/services/classification/risk.py`, `pipeline._decide()` | the model can only raise the risk level |
| 4 | Automatic approval needs a publishable classification **and** confidence at or above the threshold | `app/services/classification/pipeline.py` | `_decide()` |
| 5 | Without a second opinion (no model configured) nothing can be auto-approved | `pipeline.classify_post()` | confidence is capped at `0.90` |
| 6 | Only approved, active sources may be published | `autonomy._source_allowed()` | `eligible_publications()`, `recheck_eligibility()` |
| 7 | Only approved job categories may be published | `autonomy._category_allowed()` | same |
| 8 | A duplicate (same content hash) is never published twice | `autonomy._is_duplicate()` | same |
| 9 | Content quality gate (title length, minimum length) | `autonomy._content_valid()` | same |
| 10 | A daily limit and a minimum interval between posts | `autonomy.publishing_blocked()` | re-checked before **every** message |
| 11 | Eligibility is re-checked immediately before the send | `app/services/publishing.py` | `recheck_eligibility()` |
| 12 | A scheduled autonomous publication is cancelled when the opportunity is no longer approved | `publishing.publish_publication()` | status becomes `cancelled` |
| 13 | An uncertain `sending` state is never blindly resent | `publishing.publish_publication()` | `PublicationUnknown` |
| 14 | The destination channel must be allowlisted | `publishing.validate_channel()` | fails closed |
| 15 | Every automatic decision is recorded, append only | `autonomy_decisions`, `review_events` | no update or delete endpoint exists |
| 16 | A human can override any automatic decision, and the override is audited | `app/services/review.py` | event type `manual_override` |
| 17 | Prompt injection in a post flags the post for human review only | `risk.py` | `prompt_injection` never auto-approves |
| 18 | Treated post text is data, never instructions | `app/services/classification/prompts.py` | `<untrusted_content>` delimiter |

## 2. Decision levels

```
                    +---------------------------+
   classified post | deterministic + AI + risk  |
                    +---------------------------+
                                 |
              risk high/critical|-----> quarantine (needs_review, never publishable)
                                 |
              not a publishable  |-----> auto reject (kept for audit, human may override)
              classification    |
                                 |
              publishable but    |-----> human review (confidence / source /
              confidence low /  |        category / duplicate / hallucination)
              source not allowed|
                                 |
              publishable, high |-----> auto approve (approval_method = auto)
              confidence, clean |
              risk, allowed source|
```

## 3. Operating modes

| Mode | Classification | Automatic approval | Publishing | Use it for |
|---|---|---|---|---|
| `shadow` | always | recorded, not applied | none | measuring false positives before any risk |
| `supervised` | always | applied | pilot channel only | validating behaviour on a private channel |
| `production` | always | applied | live channel, limited | steady state |

Rollout sequence: **shadow → supervised → production**. Each step is an
explicit owner action in the dashboard; the system never escalates itself.

### 3.1 Shadow mode (recommended starting point)

```bash
# Environment defaults
AUTONOMY_ENABLED=true
AUTONOMY_MODE=shadow
```

or, in the dashboard: *Autonomy* tab → enable → mode `shadow` → save.

In shadow mode the engine classifies every post, records what it **would**
approve (`would_auto_approve`, `would_auto_reject`) and changes nothing else.
Compare those rows with the human decisions on the review queue.

### 3.2 Supervised mode

```bash
AUTONOMY_ENABLED=true
AUTONOMY_MODE=supervised
AUTONOMY_PILOT_CHANNEL=@yekis_pilot
AUTONOMY_APPROVED_SOURCE_IDS=1,2
```

`@yekis_pilot` must also be in `PUBLISHING_ALLOWED_CHANNELS`. Only the pilot
channel is used. Everything else in the control panel (limit, interval,
categories) applies unchanged.

### 3.3 Production mode

```bash
AUTONOMY_ENABLED=true
AUTONOMY_MODE=production
AUTONOMY_TARGET_CHANNEL=@yekis_channel
AUTONOMY_DAILY_PUBLISH_LIMIT=5
AUTONOMY_MIN_INTERVAL_SECONDS=900
```

Start with a low limit and a long interval. The owner can disable autonomous
publishing immediately from the dashboard (set *Autonomous Publishing* to
`Disabled`), which takes effect on the next publishing cycle.

## 4. Deployment

### 4.1 Order of operations

1. **Migrate the database first.**

   ```bash
   alembic upgrade head        # adds migration 0007_autonomy_classification
   ```

   The migration is idempotent: it only adds missing columns and creates the
   two new tables. Existing rows keep `classification = insufficient_information`
   and are re-classified by the next evaluation pass.

2. **Deploy the API, worker and beat** with the new code.

   ```bash
   docker compose up --build -d api worker beat
   ```

3. **Keep autonomy disabled.** `AUTONOMY_ENABLED=false` is the default and the
   value in `.env.example`. Verify:

   ```bash
   curl -H "X-API-Key: $KEY" http://localhost:8100/api/v1/autonomy/settings
   # {"enabled": false, "mode": "shadow", ...}
   ```

4. **Run one shadow cycle** and read the decisions:

   ```bash
   celery -A app.worker.celery_app:celery_app call yekis.autonomy_evaluate
   curl -H "X-API-Key: $KEY" http://localhost:8100/api/v1/autonomy/decisions
   ```

5. **Only then** configure the mode, the approved sources, the categories and
   the limits from the dashboard.

### 4.2 Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `CLASSIFICATION_PROVIDER` | `rules` | `rules` (deterministic only), `llm` (OpenAI compatible / NVIDIA NIM), `gemini` |
| `CLASSIFICATION_MODEL` | empty | model override for the classification stage |
| `AUTONOMY_ENABLED` | `false` | master switch |
| `AUTONOMY_MODE` | `shadow` | `shadow`, `supervised`, `production` |
| `AUTONOMY_AUTO_APPROVAL_THRESHOLD` | `0.95` | confidence required for an automatic approval |
| `AUTONOMY_APPROVED_SOURCE_IDS` | empty | comma separated source ids |
| `AUTONOMY_APPROVED_CATEGORIES` | empty | comma separated job categories |
| `AUTONOMY_DAILY_PUBLISH_LIMIT` | `5` | maximum messages per UTC day |
| `AUTONOMY_MIN_INTERVAL_SECONDS` | `900` | minimum gap between two messages |
| `AUTONOMY_PILOT_CHANNEL` | empty | supervised mode channel |
| `AUTONOMY_TARGET_CHANNEL` | empty | production mode channel |
| `AUTONOMY_EVALUATE_INTERVAL_SECONDS` | `300` | beat cadence of the evaluation task |
| `AUTONOMOUS_PUBLISH_INTERVAL_SECONDS` | `120` | beat cadence of the publishing task |

The dashboard overrides win over the environment. The environment provides the
defaults for a fresh installation.

## 4.3 Enabling automatic publishing on the scheduled job (the actual fix)

The scheduled scanner is `.github/workflows/collector.yml`. It runs hourly,
migrates the schema, seeds the sources, and then calls
`python -m scripts.run_collection`. That script now also runs the autonomy
cycle: it evaluates waiting opportunities and, in the supervised or the
production mode, publishes what the engine approved.

**Every one of these steps is required.** Missing any one of them is why the
channel stays quiet.

| # | Step | Where | Symptom when missing |
|---|---|---|---|
| 1 | A model provider for the classification stage | secret `CLASSIFICATION_PROVIDER` (`llm` or `gemini`) | Every post stays at confidence `0.90` and goes to human review. Nothing is ever auto-approved. |
| 2 | A reachable model endpoint | secret `LLM_BASE_URL` | The app falls back to `https://api.openai.com/v1`; the NVIDIA key is rejected with 401 and the pipeline degrades to rules. |
| 3 | A timeout that fits the endpoint | secret `LLM_TIMEOUT_SECONDS` (default `180`) | The request is aborted after the 30 second application default. Every classification silently falls back to the deterministic rules, so nothing can ever be auto-approved. |
| 4 | The engine enabled | secret `AUTONOMY_ENABLED=true` **and** the dashboard switch | `run_collection.py` prints "Autonomy inactive" and skips the cycle. |
| 5 | A mode that publishes | `AUTONOMY_MODE=supervised` or `production` | Shadow mode records decisions but never publishes. |
| 6 | A target channel | secret `AUTONOMY_TARGET_CHANNEL` | `publishing_blocked()` returns "no channel is configured for production mode". |
| 7 | The channel allowlisted | `PUBLISHING_ALLOWED_CHANNELS=@yekis_channel` | The dashboard reports "the channel ... is not in PUBLISHING_ALLOWED_CHANNELS" and the send fails closed. |
| 8 | Approved sources | secret `AUTONOMY_APPROVED_SOURCE_IDS` (for example `1,2`) | Every opportunity is rejected with "the source is not approved for autonomous publishing". |
| 9 | A sane threshold | `AUTONOMY_AUTO_APPROVAL_THRESHOLD=0.95` | A higher threshold means nothing can qualify. |

The source ids are the ids of the seeded rows (`afriwork`, `hahu jobs`). Read
them from the dashboard (*Sources* tab) or from
`SELECT id, name FROM sources ORDER BY id;`.

Steps 4, 5, 6 and 8 can also be set from the dashboard. The dashboard writes
them to `autonomy_settings`, and that row takes precedence over the
environment, so a dashboard change is picked up by the next scheduled run
without touching the secrets.

Verify the configuration of the scheduled job:

```bash
curl -H "X-API-Key: $YI_ADMIN_KEY" http://localhost:8100/api/v1/autonomy/overview
```

The `publishing_blocked_reason` field states exactly why nothing is being
published right now. The same reason is printed in the workflow log.

### 4.4 The split deployment: Vercel (API) + GitHub Actions (collector)

This is the topology the service runs in. The API and the dashboard live on
Vercel; the collector, the classification, and the publishing live in the
GitHub Actions workflow. There is no Celery worker on Vercel, so the workflow
is the only process that can publish.

Both sides share one database. Four defects of that split were fatal, and
all four are fixed:

| Defect | Effect | Fix |
|---|---|---|
| The API created the tables outside Alembic, the workflow ran `alembic upgrade head` | The migration failed with `table sources already exists`, the job aborted, and nothing was ever collected or published | `python -m scripts.migrate` migrates and repairs a database created outside Alembic; `tests/test_schema_parity.py` keeps the models and the migrations identical |
| The workflow did not pass `LLM_TIMEOUT_SECONDS` | Every model call was aborted after 30 seconds and silently degraded to the deterministic rules | The workflow passes `LLM_TIMEOUT_SECONDS` (default `180`), `LLM_MAX_ATTEMPTS`, and `LLM_RETRY_WAIT_SECONDS` |
| One model call per new post, no time budget, 30 minute job timeout | A backlog of new posts pushed the run past the timeout; the job was killed before the publishing phase | `COLLECTION_TIME_BUDGET_SECONDS` bounds the collection phase, the workflow timeout is 60 minutes, and the checkpoint only advances over the messages that were really ingested |
| The dashboard and the workflow read the channel allowlist from their own environment | The dashboard showed "no blocker" while the workflow refused to publish | The allowlist is part of the blocked reason, and the preflight prints it on both sides |

The workflow prints its effective configuration and every blocker before it
collects anything:

```bash
python -m scripts.preflight          # report only
python -m scripts.preflight --strict # exit 1 when a blocker is found
```

The output never contains a secret value, only whether one is set.

Two properties of the split are worth remembering:

- the allowlist is read from the environment of whichever process publishes, so
  `PUBLISHING_ALLOWED_CHANNELS` has to be set on **both** sides;
- the autonomy settings live in the database and are owned by the dashboard,
  so a dashboard change reaches the next scheduled run without a redeploy.

### 4.5 Credential rotation

The API keys of this project were committed to the dashboard in earlier
revisions of a **public** repository. Those keys are compromised: anyone can
read them from the git history, whatever the current file says.

The rotation:

1. generate a new key per role, at least 16 characters;
2. add it to `AUTH_API_KEYS` in the Vercel environment and in the workflow
   secret;
3. redeploy the API and re-run the workflow;
4. remove the old key from both places and redeploy again;
5. confirm that the old key no longer authenticates.

The preset keys are no longer part of the dashboard, and
`tests/test_deployment_contract.py` fails the build when a credential-shaped
string is committed again.

Operational notes:

### 4.6 Operational notes

- `AUTONOMY_EVALUATE_LIMIT` (default 5) bounds how many posts are classified
  per run. A model call can take a minute or more, so the batch must stay
  small enough for the job timeout.
- Shadow mode also evaluates now, so the decision log fills with real
  `would_auto_approve` rows that can be compared with the human decisions.
- The workflow timeout was raised to 60 minutes, and the collection phase has
  its own wall clock budget, so a slow model never moves the publishing phase
  out of the job.

## 5. Rollback

### 5.1 Emergency stop (no redeploy)

```bash
curl -X PATCH -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"enabled": false}' http://localhost:8100/api/v1/autonomy/settings
```

or set *Autonomous Publishing* to `Disabled` in the dashboard. The next
publishing cycle then publishes nothing; already approved opportunities stay in
the review queue and are never sent.

Setting `mode` to `shadow` also stops publishing while keeping the classifier
running and the decision log growing.

### 5.2 Stop publishing but keep the classification

```bash
PUBLISHING_ENABLED=false
```

or remove the channel from `PUBLISHING_ALLOWED_CHANNELS`. The allowlist fails
closed: with an empty allowlist no channel can be used.

### 5.3 Code rollback

```bash
git switch feature/autonomous-classification-publishing   # or the previous tag
docker compose up --build -d api worker beat
```

The classification columns are additive, so the previous code keeps working on
the migrated schema. The new columns are simply ignored.

```bash
alembic downgrade -1        # only if the new columns must be removed
```

The downgrade removes the added columns and the two new tables. It does not
touch `collected_posts`, `opportunities`, `publications` or `review_events`.

### 5.4 Verification after a rollback

```bash
curl http://localhost:8100/health
curl -H "X-API-Key: $KEY" "http://localhost:8100/api/v1/opportunities?group=freelance"
curl -H "X-API-Key: $KEY" http://localhost:8100/api/v1/publications
```

## 6. Failure modes and the behaviour the owner will see

| Failure | Behaviour | Evidence |
|---|---|---|
| Model provider is down or slow | the deterministic result is used, the confidence drops, the post goes to human review, ingestion is never blocked | `ai_notes` on the opportunity, `does not contain` note in the decision log |
| Telegram send fails | the publication row becomes `failed` with the error, the post is not retried automatically by the cycle | `publications.status`, `publications.error` |
| The process dies mid-send | the row stays `sending` and is **not** resent; the API reports `publication_unknown` for a human to reconcile | `publications.status = sending` |
| The worker restarts | the cycle re-runs, the `(opportunity, channel)` unique constraint and the publish-time check prevent a second message | one `publications` row, one message |
| The opportunity is rejected after scheduling | the autonomous publication is cancelled, not sent | `publications.status = cancelled` |
| The daily limit is reached | the cycle stops with `the daily publishing limit has been reached` | `outcome["skipped"]` |
| The channel is not allowlisted | nothing is sent, the cycle records the reason | `outcome["skipped"]` |
| The source was deactivated | the opportunity is no longer eligible, the send-time recheck blocks it | `recheck_eligibility()` |

## 7. Security boundaries that are unchanged

- No escrow, wallet, payout, invoice, milestone or contract code was added.
  The service still has no marketplace financial access and never had any.
- API keys stay in the environment. No credential is read from a post, logged,
  or written to the database.
- Authentication and role based access are unchanged. The new endpoints follow
  the same matrix: settings are admin only, reads are admin/reviewer/analyst,
  evaluation is reviewer or admin.
- `review_events` and `autonomy_decisions` are append only. No route updates or
  deletes them.
- Original post text is never mutated. The normalizer builds a separate
  representation.
- The channel allowlist is part of the blocked reason, so a channel that is
  not allowlisted is reported instead of silently skipped.
- The preset keys are gone from the dashboard, and a committed credential now
  fails the test suite (`tests/test_deployment_contract.py`). The keys that
  were committed to the public repository before that have to be revoked; see
  §4.5.

## 8. Remaining limitations and backlog

1. **The dataset is small.** 27 labelled cases. Shadow mode is the only way to
   get a real false positive rate.
2. **No live model measurement.** The evaluation report uses a simulated model.
   A live run against NVIDIA NIM or Gemini is still required before the
   supervised rollout, and it must use a private channel.
3. **Amharic coverage is shallow.** Two dataset cases and a hand written signal
   set. Real Amharic posts will expose gaps.
4. **No live Telegram verification.** The publishing path is proven with a fake
   publisher only. A send against an authorised test channel is still open.
5. **Duplicate detection is content-hash based.** A reworded duplicate is not
   detected.
6. **The AI stage is single pass.** There is no second model and no ensemble.
   The "disagreement" handling relies on the deterministic engine disagreeing.
7. **Category approval is coarse.** A channel that mixes categories can only be
   approved or rejected as a whole.
8. **No rate limit on the source level.** The daily limit is global per
   channel, not per source.
9. **The existing `implausible_rate` extractor heuristic was tightened** from
   three to four digits so that a normal `$100 per hour` consulting rate is no
   longer flagged. Existing analytics will change accordingly.

## 9. Sign-off checklist before production autonomy

- [ ] Shadow mode ran for at least one full collection cycle.
- [ ] Every `would_auto_approve` decision was reviewed by a human.
- [ ] The false positive rate in that sample is zero or the threshold and the
      signal set were tightened accordingly.
- [ ] `CLASSIFICATION_PROVIDER` points at a live model and the model latency
      is acceptable for the collection cadence.
- [ ] The pilot channel received at least one message in supervised mode and
      the message was verified by a human.
- [ ] The daily limit and the minimum interval are set to conservative values.
- [ ] `PUBLISHING_ALLOWED_CHANNELS` contains exactly one channel.
- [ ] The rollback command has been executed once in the test environment.
- [ ] `python -m scripts.preflight` reports no blocker in the scheduled job
      environment.
- [ ] Every API key that was ever committed to the repository is revoked and
      replaced.
