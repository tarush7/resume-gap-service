BEGIN;

ALTER TABLE public.job_requirement_extraction_audits
    ADD COLUMN IF NOT EXISTS attempt_number integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS requirements_filtered integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audit_details jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.job_requirement_extraction_audits
    DROP CONSTRAINT IF EXISTS job_requirement_extraction_audits_batch_job_key;

CREATE UNIQUE INDEX IF NOT EXISTS job_requirement_extraction_audits_attempt_key
    ON public.job_requirement_extraction_audits (batch_id, job_id, attempt_number);

COMMENT ON COLUMN public.job_requirement_extraction_audits.audit_details IS
    'Frozen JD/hash, code/model configuration, raw LangExtract items, pre-cleanup records, filtering/recovery/merge events, stage counts and failure stage. Raw provider response text is not captured.';
COMMENT ON TABLE public.job_requirement_extraction_audits IS
    'Append-only attempts per job and batch. Select the highest attempt_number for per-job comparisons; retain earlier attempts as retry history.';

COMMIT;
