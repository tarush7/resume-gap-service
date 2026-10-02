# LangExtract batch audit

The batch runner creates one audit row per `(batch_id, job_id)`. A successful
row stores the validated extraction JSON. A failed row stores the error type,
message, structured detail, and duration. This makes a partially completed run
inspectable and resumable.

## 1. Create and track the audit table

Run [`sql/001_create_job_requirement_extraction_audits.sql`](sql/001_create_job_requirement_extraction_audits.sql)
in the Nhost/Postgres SQL editor.

If the table does not appear in GraphQL automatically, track
`public.job_requirement_extraction_audits` in the Nhost/Hasura dashboard. The
runner uses the admin secret, so public role permissions are not required.

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

Successful jobs are skipped. Rows left in `running` are retried. Failed jobs
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

## Optional canonical write

Only after the audit output has been reviewed, a new batch can also replace
canonical `job_requirements` rows:

```bash
.venv/bin/python batch_extract_requirements.py --save-requirements
```

Do not add `--save-requirements` to the first evaluation run. Successful JSON
is already preserved in `extracted_requirements` for analysis.
