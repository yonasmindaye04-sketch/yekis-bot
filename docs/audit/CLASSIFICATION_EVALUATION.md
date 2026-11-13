# Classification Evaluation Report

**Engine:** `yi-classifier-1.0.0`
**Dataset:** `tests/fixtures/classification_dataset.json` (27 labelled cases: English, Amharic, mixed)
**Reproduce:** `.venv/Scripts/python.exe scripts/evaluate_classification.py`

---

## 1. What is measured

| Metric | Definition | Why it matters |
|---|---|---|
| Precision | share of posts classified as a publishable opportunity that really are one | a wrongly published scam or tip damages trust |
| Recall | share of genuine freelance or contract opportunities that are found | a missed opportunity can be reviewed later |
| False positive rate | share of non-opportunity posts wrongly called publishable | this is the number that must stay near zero |
| False negative rate | share of genuine opportunities routed to human review | this is the cost of being safe |
| Automatic approval accuracy | share of auto-approved posts that are genuine opportunities | the quality of the unattended path |
| Scam detection recall | share of scam/unsafe posts classified as unsafe | a scam must never be published |

The positive class is "publishable opportunity": the expected classification is
one of `freelance_job`, `project_contract`, `remote_contract`,
`consulting_opportunity`, `short_term_contract`.

## 2. Results — deterministic stage only (default configuration)

```
dataset cases           : 27
true positives          : 9
false positives         : 0
true negatives          : 18
false negatives         : 0

precision               : 1.0000
recall                  : 1.0000
f1                      : 1.0000
false positive rate     : 0.0000
false negative rate     : 0.0000
accuracy                : 1.0000

auto approved           : 0
auto approval accuracy  : None
scam detection recall   : 1.0000 (3/3)

per language:
  am     tp=1 fp=0 fn=0 tn=1
  en     tp=7 fp=0 fn=0 tn=17
  mixed  tp=1 fp=0 fn=0 tn=0

mismatches: none
```

**Nothing is auto-approved without a model.** The deterministic stage caps its
own confidence at `0.90`, below the `0.95` automatic approval threshold, because
there is no second opinion. A rules-only deployment therefore produces shadow
decisions only. This is deliberate: precision is bought with recall.

## 3. Results — with a model that agrees with the rule engine (simulated)

`scripts/evaluate_classification.py --simulate-ai` replays the dataset with a
stub model that returns the rule engine's classification at `0.98` confidence.
This measures the **autonomous path**, not a real model.

```
precision               : 1.0000
recall                  : 1.0000
false positive rate     : 0.0000
false negative rate     : 0.0000

auto approved           : 8
auto approval accuracy  : 1.0
scam detection recall   : 1.0000 (3/3)
```

The eight auto-approved cases are the seven genuine opportunities plus the
labelled duplicate. The duplicate is auto-approved *by the classifier*; the
autonomy service rejects it at the publishing stage because the same
`content_hash` already exists on another post (`_is_duplicate`), so it is never
published twice.

## 4. Results — with a model that disagrees (simulated)

A stub model that always answers `freelance_job` produces three
`needs_human_review` outcomes for the specialised engagement types
(`consulting_opportunity`, `remote_contract`, `project_contract`):

```
precision               : 1.0000
recall                  : 0.6667
false negative rate     : 0.3333
auto approval accuracy  : 1.0
```

Precision is preserved: a disagreement never produces a publishable verdict, it
produces human review. Recall falls, which is the intended trade-off.

## 5. Per-case confidence and behaviour

| Case | Language | Classification | Confidence | Decision |
|---|---|---|---|---|
| genuine_freelance_software | en | freelance_job | 0.90 | human review (no model) |
| genuine_graphic_design_contract | en | freelance_job | 0.90 | human review |
| short_term_consulting | en | consulting_opportunity | 0.90 | human review |
| remote_contract | en | remote_contract | 0.90 | human review |
| project_contract | en | project_contract | 0.78 | human review |
| missing_budget_and_deadline | en | freelance_job | 0.90 | human review |
| amharic_freelance | am | freelance_job | 0.90 | human review |
| mixed_amharic_english | mixed | freelance_job | 0.90 | human review |
| permanent_employment | en | full_time_employment | 0.95 | auto reject |
| part_time_employment | en | part_time_employment | 0.90 | auto reject |
| internship | en | internship | 0.92 | auto reject |
| volunteering | en | volunteering | 0.92 | auto reject |
| apprenticeship | en | apprenticeship | 0.90 | auto reject |
| educational_skills_tips | en | educational_content | 0.96 | auto reject |
| course_advertisement | en | training_announcement | 0.94 | auto reject |
| general_announcement | en | general_announcement | 0.93 | auto reject |
| yekis_marketplace_promotion | en | marketplace_promotion | 0.96 | auto reject |
| news_post | en | news_and_information | 0.93 | auto reject |
| community_discussion | en | community_discussion | 0.90 | auto reject |
| job_seeker_post | en | community_discussion | 0.90 | auto reject |
| ambiguous_job | en | ambiguous | 0.45 | human review |
| prompt_injection | en | needs_human_review | 0.35 | human review |
| upfront_payment_scam | en | scam_or_fraud | 0.90 | quarantine |
| unrealistic_earnings | en | scam_or_fraud | 0.90 | quarantine |
| suspicious_unrealistic_earnings | en | suspicious_opportunity | 0.90 | quarantine |

## 6. The permanent regression case

The post that started this work:

> "In between job opportunities, we will send you some tips to improve your
> chances to land a job. Today part 4 of skills you need to have in order to
> improve your employability."

| Property | Value |
|---|---|
| Classification | `educational_content` |
| Job category | `education` |
| Publishable | false |
| Confidence | 0.96 |
| Decision | auto reject (kept in the database for audit) |
| Reason | The post teaches job seekers rather than offering work. |

It is asserted by `tests/test_classification_engine.py::test_educational_skills_tips_post_is_never_a_job`
and by `::test_educational_post_is_rejected_on_ingestion_and_kept_for_audit`,
and it is one of the dataset cases, so any future change that turns it into a
job breaks the build.

## 7. Known limitations of this evaluation

1. **The dataset is small and hand-labelled.** 27 cases cannot prove a
   production false positive rate. It proves that the known failure modes stay
   fixed. The shadow mode exists to replace this estimate with real data.
2. **The AI numbers are simulated.** No live model was called while producing
   this report, so the "model agrees" and "model disagrees" sections are
   scenario measurements, not model measurements.
3. **Amharic coverage is two cases.** The Amharic signal set covers seeking,
   deliverable, budget, duration, application path, role names, contract,
  freelance, remote, employment, internship, volunteering, apprentice,
   education, training and news. Real Amharic channel posts will contain
   vocabulary that is not yet covered.
4. **Category accuracy is not measured.** The classifier decides *what a post
   is*; the job category still comes from the extraction stage.
5. **Duplicate detection is a content hash.** Two posts with the same content
   are treated as duplicates, and two genuinely different posts with identical
   text and different metadata are also treated as duplicates.

## 8. How to improve the measurement

1. Run the collector in **shadow mode** for one week.
2. Label every `would_auto_approve` decision in the dashboard (one click).
3. Compare the labels with the decisions in `autonomy_decisions`.
4. Extend `tests/fixtures/classification_dataset.json` with every false
   positive, and add the regression test that keeps it fixed.
5. Re-run `scripts/evaluate_classification.py` and record the new numbers here.
