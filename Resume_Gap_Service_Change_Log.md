# Resume Gap Service — Change Log

This is a running list of bugs found while reviewing the extraction service. Add new findings under stable IDs so we can implement and verify them later.

| ID | Change | Status |
| --- | --- | --- |
| CHG-001 | Remove the unconditional responsibilities-section classification override | Implemented — local regression tests passed |
| CHG-002 | Prevent education misclassification from broad keyword matching | Implemented — local regression tests passed |
| CHG-003 | Remove technical-background category inference from cleanup | Implemented — local regression tests passed |
| CHG-004 | Remove duplicated semantic overrides and keyword rejection in main.py | Implemented — local regression tests passed |
| CHG-005 | Audit filtered items and verify boilerplate-filter policy | Audit visibility implemented; batch review and policy decision pending |

## CHG-001 — Preserve meaning when cleaning records

**Status:** Implemented — local regression tests passed  
**Recorded:** 7 October 2026  
**Location:** `clean_requirement_records()` in the supplied LangExtract requirements module, version `job_requirements_langextract_v6_1`.

### Context

The AI produces a requirement name, category, requirement level, and source text. After grounding the evidence in the original JD, our Python cleanup function can modify the model's classification.

The current cleanup rule is:

```python
if responsibility_section is not None and evidence in responsibility_section:
    record["category"] = "responsibility"
    record["requirement_level"] = "required"
```

This rule overwrites both fields whenever evidence occurs in the detected responsibilities section. It does not inspect whether the sentence describes a qualification or explicitly states a preference.

### Bug and impact

A poorly formatted JD can place qualifications or optional requirements under a responsibilities heading. The rule can therefore turn a correct AI classification into an incorrect final record.

Example source inside that section:

> Experience supporting A/B testing is preferred.

| Field | Correct model output | Current cleanup output |
| --- | --- | --- |
| `category` | `preferred_skill` | `responsibility` |
| `requirement_level` | `preferred` | `required` |

This can inflate required-demand counts and misclassify requirements in downstream reports. Overall demand based on whether a job mentions the requirement need not change solely because these two fields change.

Grounding cannot catch this error: the evidence still comes from the JD. Pydantic's allowed-value checks cannot catch it either: both the original and overwritten values are allowed.

### Change to make

- [ ] Remove the entire unconditional responsibilities-section override shown above.
- [ ] Change the immediately following education-classification `elif` to `if`, so the remaining conditional chain remains valid Python.
- [ ] Retain the existing other cleanup rules for this scoped fix; this entry does not certify their semantic accuracy.
- [ ] Preserve the model's category and requirement level through this section-based step. Use headings as context rather than automatically replacing explicit meaning.

Removing this override prevents this particular bug. It does not guarantee that the model's classifications are always correct.

### Verification before marking complete

- [ ] A/B testing marked preferred inside a responsibilities section remains `preferred_skill` / `preferred` when the model initially classifies it correctly.
- [ ] `MBA preferred` inside a responsibilities section remains `education` / `preferred` when initially classified correctly.
- [ ] `Own and manage the product backlog` initially classified as `responsibility` / `required` retains those values.
- [ ] Source evidence remains unchanged by this fix.

## CHG-002 — Education keywords can match non-education requirements

**Status:** Implemented — local regression tests passed  
**Recorded:** 7 October 2026  
**Location:** Education branch in `clean_requirement_records()` and `FORMAL_EDUCATION_CREDENTIAL_TERMS`, version `job_requirements_langextract_v6_1`.

### Context

When the preceding responsibilities branch does not match, a record categorized as `required_skill` or `preferred_skill` is changed to `education` if any education term occurs anywhere in its combined lowercase name and evidence.

The keyword list includes the standalone string `master`. The condition uses substring matching:

```python
term in f"{record['name']} {evidence}".lower()
```

### Bug and impact

Example outside the responsibilities section:

> Ability to master SQL is required.

The model returns `name: "SQL"`, `category: "required_skill"`, and `requirement_level: "required"`. The cleanup finds `master` in the evidence and incorrectly changes the category to `education`. The requirement level and evidence are unchanged.

Here, “master” describes proficiency rather than an academic credential. Substring matching can also match words containing “master”, such as “Mastercard”. A word-boundary check alone would still incorrectly match “master SQL” and “Scrum Master”.

This can corrupt category breakdowns without changing the grounded evidence. Allowed-value validation will not detect the error because `education` is a valid category.

### Change to make

- [ ] Replace or disable the blanket keyword-triggered category override.
- [ ] If retaining automatic correction, require clear academic-credential context, such as a bachelor's degree or a master's degree; review each trigger rather than treating any keyword as conclusive.
- [ ] Preserve the original category when the context is ambiguous instead of forcing `education`.
- [ ] Preserve requirement level and source evidence during any justified category correction.
- [ ] Implement alongside CHG-001 so the final conditional chain remains valid Python.

**Proposed direction, not an implemented patch:** Explicit degree context can support a correction; generic keyword occurrence cannot establish that an item is education. Narrower rules still require verification.

### Verification before marking complete

- [ ] “Ability to master SQL is required” retains `required_skill` when initially classified correctly.
- [ ] “Scrum Master skills are preferred” is not changed to education.
- [ ] “Experience with Mastercard payment products is required” is not changed to education.
- [ ] “MBA preferred” can still be corrected from `preferred_skill` to `education` while preserving `preferred`, if credential correction is retained.
- [ ] An explicit master's-degree requirement is treated as education.
- [ ] Source evidence remains unchanged.

## CHG-003 — Technical-background cleanup guesses the category

**Status:** Implemented — local regression tests passed  
**Recorded:** 12 October 2026  
**Location:** Final classification branch of `clean_requirement_records()`.

If a record is classified as education, contains “technical background” in its evidence, and contains no term in `FORMAL_EDUCATION_TERMS`, the rule changes its category to preferred_skill when its level is preferred, otherwise required_skill. That includes a level of unspecified.

This is another automatic semantic override. “Technical background” can describe different kinds of qualifications; absence of one of our keywords does not establish its category. The level itself is not changed in this function, but main.py subsequently forces required_skill to required, so an unspecified item can become required across the two functions.

- [ ] Remove this automatic category replacement along with CHG-001 and CHG-002.
- [ ] Preserve Qwen's category and level; flag uncertain interpretations for review when needed.
- [ ] Verify an unspecified level is not promoted to required by either layer.

## CHG-004 — main.py repeats semantic assumptions

**Status:** Implemented — local regression tests passed  
**Recorded:** 12 October 2026  
**Location:** `validate_job_requirements()` in the supplied main.py snapshot.

The shared validator rejects skill names containing broad education substrings such as “master”. For example, a skill named “Mastercard expertise” can be rejected even when its source evidence is valid. This remains a problem after removing the education override in the LangExtract module.

The validator also silently sets required_skill to level required and preferred_skill to level preferred. A mismatch between category and level should be identified explicitly, rather than changing a field without a record of the change.

- [ ] Remove the broad education-keyword rejection.
- [ ] Remove silent requirement-level reassignment.
- [ ] Retain structural and source-grounding validation.
- [ ] Define how inconsistent category/level pairs are flagged, reviewed, or rejected per item; do not silently invent the intended meaning.
- [ ] Verify these checks across the API path and the batch path; the supplied main.py does not include the batch runner, so its behavior still needs inspection.

## CHG-005 — Verify boilerplate filtering before deciding its policy

**Status:** Audit visibility implemented; batch review and policy decision pending  
**Recorded:** 12 October 2026  
**Location:** `find_ignored_boilerplate_ranges()` and the filtering branch of `clean_requirement_records()`.

### Context and confirmed audit gap

The cleanup function drops a record when its evidence text occurs inside a recognized ignored section such as Disclaimer. The drop uses `continue` without creating a filtering event. A grounded requirement can therefore disappear without being counted as a grounding or schema rejection.

Example: two grounded records enter cleanup; one falls in Disclaimer; one record leaves, with no new warning describing the filtered record.

### Decision to make using batch evidence

Keep, narrow, or remove the section-based filter after reviewing its behavior on actual JDs and regression examples. A heading alone does not establish that all its contents are irrelevant. Do not treat filtered items as proven hallucinations.

- [ ] Record a separate filtered-item count and event containing the original record, evidence, matched section, and reason.
- [ ] Review whether generic company policies are appropriately excluded.
- [ ] Check that SQL explicitly required under a misleading Disclaimer heading is not silently lost from the employer-demand checklist.
- [ ] Include role-specific certification, security, or compliance requirements under misleading headings.
- [ ] Include the same evidence text appearing in both a requirement section and an ignored section; inspect its actual source occurrence rather than assuming any occurrence in an ignored range establishes irrelevance.
- [ ] Preserve enough pre-filter output to inspect or replay removed records.
- [ ] Decide the filtering policy after the preview comparison; record the decision and examples supporting it before production ingestion.

## Batch testing verification — required before promoting results

**Status:** Five-job diagnostic preview completed; full 113-job comparison pending
**Recorded:** 12 October 2026  
**Purpose:** Confirm that cleanup fixes preserve source meaning and measure extraction issues separately from database-saving failures.

### Fair comparison

- [ ] Freeze the same job IDs and JD text for before/after comparison. Record JD hashes, extractor/code revision, prompt version, model, parameters, and batch ID.
- [ ] Keep baseline audits intact. Start a separate comparison batch with `save_requirements=false`.
- [ ] Start with about five varied JDs and regression examples for CHG-001 through CHG-005; broaden to the existing 113-job cohort after these pass.
- [ ] For cleanup-only comparisons, replay captured pre-cleanup model output when available to avoid unnecessary GPU calls. Existing post-cleanup records cannot recover requirements or values already discarded or overwritten; rerun extraction when that original output is unavailable.
- [ ] Do an end-to-end preview smoke test as well as cleanup replay, so shared main.py validation is exercised.

### Audit evidence and accounting

- [ ] Preserve original model text/attributes, final accepted records, rejection reasons, recoveries, and any filtering or transformations needed for the comparison. Mark missing audit instrumentation as work to do; do not assume the existing audit captures all stages.
- [ ] Report failures per job: parsing, timeout, grounding, schema, and saving failures separately.
- [ ] Count raw returned items, accepted items, rejected items, filtered items, and merged duplicates at their respective stages. Explain count changes rather than assuming extracted + rejected accounts for every item.
- [ ] Report unique jobs separately from attempts. Use one chosen attempt per job and version for the main comparison; report retries separately.
- [ ] Keep extraction success independent from save success. Preview batches should save zero production requirement rows.

### Quality checks

- [ ] Ungrounded-item rate = items rejected for ungrounded evidence / raw model-returned items at the same stage. Do not label all grounding failures as confirmed hallucinations.
- [ ] Structural rejection rate = items rejected for invalid structure/allowed values / raw model-returned items at the same stage.
- [ ] Review a fixed, representative set of JDs and all flagged examples. Compare model meaning with source meaning, including category and requirement level. State the reviewed denominator; an intentionally selected flagged subset is not an unbiased estimate of the whole batch.
- [ ] Distinguish invented requirements from paraphrases, alignment failures, and incorrect classifications.
- [ ] Verify boilerplate filtering with normal and misleading headings, role-specific requirements, and repeated evidence text. Record every removal separately from model rejection and decide the filter policy before production ingestion.
- [ ] Use manually reviewed requirement checklists to measure coverage and omissions. More extracted rows alone does not prove better quality.
- [ ] Verify responsibilities coverage separately; finding one responsibility does not establish that all duties were extracted.
- [ ] Compare duration and failure rates without treating lower rejection counts as sufficient proof of improvement.

### Completion criteria

- [ ] All recorded regression examples pass, source evidence remains traceable, and no cleanup or shared validator silently changes classifications.
- [ ] Representative reviewed output preserves employer intent and reveals any remaining omissions.
- [ ] Batch results and errors are inspectable, with explicit denominators and separate attempt/job counts.
- [ ] Promote results into `job_requirements` only after reviewing this comparison and resolving or explicitly retaining known limitations.

## Future findings

Add the next confirmed bug as **CHG-006**, including its code location, example, impact, proposed change, and verification criteria. Keep proposed changes marked pending until they are implemented and checked.


## Implementation notes — 20 October 2026

- CHG-001/002/003: all category/level inference removed from cleanup. The optional
  academic-credential correction was disabled entirely; no keyword list is used.
- CHG-004: skill-name education rejection removed. Category/level conflicts are
  rejected explicitly per item in batch and as a structured API error. No level
  is reassigned, including unspecified.
- CHG-005: filtering/review events retain the record, evidence, section and source
  occurrence. Legacy filtering remains pending actual-JD review. A preview-only
  review mode retains flagged requirements; no claim that filtered items are
  hallucinations is made. Strict API and batch saving block flagged results.
- Batch audits now retain raw LangExtract items, pre-cleanup records, recoveries,
  filtering and merge events, frozen JD/hash, code/configuration metadata, stage
  counts, and separate extraction/save outcomes. Retries append new attempts.
- Required audit schema changes were applied by the user before the five-job
  preview; the updated runner successfully wrote and retrieved the new audit fields.
- Local replay and a frozen-cohort workflow are documented in
  `BATCH_EXTRACTION_GUIDE.md`. Raw provider responses and model weights/digests
  are not captured; unavailable pre-cleanup output requires a fresh extraction.
- The five-job diagnostic preview is complete. Representative manual review,
  coverage checklists, the full 113-job preview, filter-policy decision and
  production promotion remain pending.

Validation: 33 local tests passed, including mocked FastAPI preview, offline replay,
filter accounting and append-only retry checks. Ruff lint/format and whitespace
checks passed. The tests mock external services and do not constitute actual-JD
batch verification.

## Five-job diagnostic preview — 20 October 2026

Batch `1e0650da-4bd7-4c74-8368-3978ccb9dcff` used `save_requirements=false`
and the same five selected baseline job IDs. Two clean successes, two successes
with warnings, one parsing failure; five attempts, zero retries. Production
requirements stayed at 125 rows; audits increased from 239 to 244. The captured
fields of all 113 baseline audits remain unchanged.

Across four parseable jobs: 319 raw items, 238 accepted, 81 grounding rejections,
zero filtered, zero merged duplicates. The fifth job has no returned-item
denominator because inference output could not be parsed. All accepted category
and level values match captured pre-cleanup output. Four offline replays match
live accepted output. Of 146 records matching older output on name/evidence,
48 classifications differ, consistent with removal of the section override.
Targeted JD review confirms SQL/Python preferences and an experience requirement
are preserved for Citi AI/Data; this does not establish full cohort accuracy.

ZEISS succeeded after its previous timeout. Citi Engineering & Delivery Lead
still fails with the same unterminated-JSON error. No filter-policy events were
observed, so actual-JD boilerplate policy verification remains pending.

Evidence: `batch-results/1e0650da-4bd7-4c74-8368-3978ccb9dcff/comparison.md`,
`after.json`, `item-review.json`, `replay-comparison.json`, and `run.log`.
