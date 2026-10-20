# LangExtract batch audit

The batch runner creates one audit row per `(batch_id, job_id, attempt_number)`. A successful
row stores the validated extraction JSON. A failed row stores the error type,
message, structured detail, and duration. This makes a partially completed run
inspectable and resumable.

## 1. Create and track the audit table

Run [`sql/001_create_job_requirement_extraction_audits.sql`](sql/001_create_job_requirement_extraction_audits.sql)
in the Nhost/Postgres SQL editor.

If the table does not appear in GraphQL automatically, track
`public.job_requirement_extraction_audits` in the Nhost/Hasura dashboard. The
runner uses the admin secret, so public role permissions are not required.

For both new and existing installations, also apply
`sql/002_add_extraction_warnings.sql` and
`sql/003_preserve_extraction_attempts.sql` in order. Migration 003 is required
by the current runner; it preserves existing rows and allows append-only retries.

The rollback file is
[`sql/001_drop_job_requirement_extraction_audits.sql`](sql/001_drop_job_requirement_extraction_audits.sql).

## 2. Run a one-job smoke test

The default mode writes only to the audit table and does not replace canonical
`job_requirements` rows.

```bash
.venv/bin/python batch_extract_requirements.py \
  --job-id 6b3920ff-140b-4293-b20b-440400898b32
```

Copy the `batch_id` printed in the `batch_started` event.

## 3. Inspect the smoke test

```sql
SELECT
    batch_id,
    job_id,
    status,
    duration_ms,
    requirements_extracted,
    error_type,
    error_message
FROM public.job_requirement_extraction_audits
WHERE batch_id = '<batch-id>'::uuid;
```

## 4. Run every job

```bash
.venv/bin/python batch_extract_requirements.py
```

The runner is sequential by design so two generations do not compete for the
single Ollama GPU. At 20–35 seconds per job, 113 jobs may take approximately
40–65 minutes, plus any focused retries.

## 5. Resume an interrupted batch

Use the same ID printed when the batch started:

```bash
.venv/bin/python batch_extract_requirements.py \
  --batch-id '<batch-id>'
```

Successful jobs are skipped. Retry attempts never overwrite earlier audits.
Resume requires matching JD hash, code hash, model configuration, and filter
policy; use a new batch ID for a different version or policy. Rows left in `running` receive a new attempt. Failed jobs
are skipped unless explicitly requested:

```bash
.venv/bin/python batch_extract_requirements.py \
  --batch-id '<batch-id>' \
  --retry-failed
```

## 6. Review results

Batch summary:

```sql
SELECT
    status,
    count(*) AS jobs,
    round(avg(duration_ms)) AS average_duration_ms,
    min(duration_ms) AS minimum_duration_ms,
    max(duration_ms) AS maximum_duration_ms
FROM public.job_requirement_extraction_audits
WHERE batch_id = '<batch-id>'::uuid
GROUP BY status
ORDER BY status;
```

Failures:

```sql
SELECT
    job_id,
    job_title,
    company,
    source,
    description_length,
    duration_ms,
    error_type,
    error_message,
    error_detail
FROM public.job_requirement_extraction_audits
WHERE batch_id = '<batch-id>'::uuid
  AND status = 'failed'
ORDER BY error_type, job_id;
```

Successful jobs:

```sql
SELECT
    job_id,
    job_title,
    company,
    requirements_extracted,
    duration_ms
FROM public.job_requirement_extraction_audits
WHERE batch_id = '<batch-id>'::uuid
  AND status = 'succeeded'
ORDER BY job_id;
```

Group repeated errors before changing the extractor:

```sql
SELECT
    error_type,
    error_message,
    count(*) AS affected_jobs,
    array_agg(job_id ORDER BY job_id) AS job_ids
FROM public.job_requirement_extraction_audits
WHERE batch_id = '<batch-id>'::uuid
  AND status = 'failed'
GROUP BY error_type, error_message
ORDER BY affected_jobs DESC, error_type;
```

## Optional canonical write after review

Only after the audit output has been reviewed, a new batch can also replace
canonical `job_requirements` rows:

```bash
.venv/bin/python batch_extract_requirements.py --save-requirements
```

Do not add `--save-requirements` to the first evaluation run. Successful JSON
is already preserved in `extracted_requirements` for analysis.


## Meaning-preserving cleanup and comparison (October 2026)

CHG-001 through CHG-004 remove category/level inference from Python cleanup.
The prompt version stays v6_1 because the prompt is unchanged; audits also
record `cleanup_version=meaning_preserving_v1`, Git revision and actual code hash.
Education corrections are disabled, including MBA: the model's classification
is preserved. Explicit academic degrees correctly classified by the model remain
education. Cleanup no longer guesses a correction.

The shared validator rejects `required_skill` unless its level is `required`,
and `preferred_skill` unless its level is `preferred`. This includes `unspecified`:
it is flagged rather than promoted. A strict API request fails with a structured
`inconsistent_category_level` error including the item. The batch rejects only
that item, retains its original values in the warning, and continues with other
valid items. Other categories can retain `unspecified`.

The boilerplate policy is still **pending representative review**. `legacy`
retains the existing section filter with explicit events. `review` retains
flagged items to measure what that filter would remove and is preview-only.
Neither a filtered item nor a retained item under review is counted as a model
rejection. Jobs with either event are audit-only, even with `--save-requirements`.
The strict API returns an error with review events rather than silently omitting
filtered items. Aligned evidence uses its actual occurrence; unaligned evidence
with multiple occurrences is retained for review if any occurrence is outside
ignored sections.

Freeze exactly the same jobs and JD text before comparing versions/policies:

```bash
.venv/bin/python batch_extract_requirements.py --max-jobs 5 --freeze-to /tmp/five-jobs.json
.venv/bin/python batch_extract_requirements.py --jobs-file /tmp/five-jobs.json --filter-policy legacy
.venv/bin/python batch_extract_requirements.py --jobs-file /tmp/five-jobs.json --filter-policy review
```

Each extraction command without `--batch-id` creates a separate batch. Neither
command writes production requirements. Freezing only reads jobs and writes a
local snapshot; it runs no extraction and creates no audit rows. Snapshot files
are created exclusively to prevent overwriting an earlier cohort. For the full
cohort, freeze the selected 113 IDs, then reuse that snapshot for each version.
Existing baseline audits lack pre-cleanup output and may lack frozen JD text;
post-cleanup output cannot reconstruct removed items or overwritten values.

New audits capture LangExtract-returned extraction text, attributes, intervals,
per-pass source text, pre-cleanup records, evidence recoveries and filter/merge
events. They do **not** capture raw provider response text or unparsed responses.
A parse/timeout failure can therefore have no returned-item denominator; do not
interpret missing items as zero rejections. JD hashes, model name, generation and
alignment parameters are included; model weights/digest are not pinned.

Export one complete new audit row (including `job_title` and `audit_details`) as
JSON, then replay cleanup and shared validation without inference or DB writes:

```bash
.venv/bin/python replay_extraction.py /tmp/audit.json --output /tmp/replay-comparison.json
```

Replay compares legacy filtering against retaining flagged records. It is not
a v6_1 baseline implementation: use preserved baseline audits/code for before/
after semantics comparisons. Missing pre-cleanup or focused-retry output is
reported as requiring fresh extraction. Output files are never overwritten.

Use the latest attempt per job for the main comparison; keep attempts separately:

```sql
WITH chosen AS (
    SELECT DISTINCT ON (job_id) *
    FROM public.job_requirement_extraction_audits
    WHERE batch_id = '<batch-id>'::uuid
    ORDER BY job_id, attempt_number DESC
)
SELECT job_id, status, requirements_extracted, requirements_rejected,
       requirements_filtered, requirements_saved,
       audit_details->>'raw_items' AS raw_returned_items,
       audit_details->>'merged_duplicates' AS merged_duplicates,
       audit_details->>'extraction_status' AS extraction_status,
       audit_details->>'failure_stage' AS failure_stage,
       audit_details->>'failure_kind' AS failure_kind
FROM chosen;
```

`raw_items` counts returned items across the full-JD pass and any responsibility
retry; each pass's source/context is retained. Rejected, filtered and merged items
are separate stages. Final accepted records are stored in `extracted_requirements`
and `audit_details.accepted_records`, including when a later save fails.
`requirements_saved=0` is explicit in previews. `failure_stage` distinguishes
input, extraction, validation, saving and audit persistence; `failure_kind` further identifies
recognizable timeout, parsing, grounding, filtering and structural failures.
`extraction_status=succeeded` survives a saving failure. `save_status` separately
records whether saving was requested, skipped for review, succeeded, or failed. Batch summaries count
one latest attempt per unique job and report total attempts and retries separately.

For quality rates, divide `ungrounded_items` and `structural_rejections` by
`raw_items` for the same chosen attempts/stage. Category/level conflicts are
semantic review failures, not structural failures or proven hallucinations.
Do not mix duplicate merges or filtering into rejection rates. Use manually
reviewed per-JD checklists to assess omissions and responsibility coverage;
more rows or fewer rejections alone cannot establish better quality.

Local regression and mocked API preview tests do not replace the five-JD review,
113-job comparison, coverage review, or final filter-policy decision. No production
ingestion should be promoted on the strength of those local tests alone.
