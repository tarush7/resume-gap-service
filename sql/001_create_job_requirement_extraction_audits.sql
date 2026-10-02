CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.job_requirement_extraction_audits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL,
    job_id uuid NOT NULL,
    job_title text,
    company text,
    source text,
    description_length integer,
    extractor text NOT NULL,
    prompt_version text NOT NULL,
    model_name text NOT NULL,
    save_requirements boolean NOT NULL DEFAULT false,
    status text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    duration_ms integer,
    requirements_extracted integer,
    requirements_saved integer,
    extracted_requirements jsonb,
    error_type text,
    error_message text,
    error_detail jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_requirement_extraction_audits_status_check
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT job_requirement_extraction_audits_batch_job_key
        UNIQUE (batch_id, job_id)
);

CREATE INDEX IF NOT EXISTS
    job_requirement_extraction_audits_batch_status_idx
    ON public.job_requirement_extraction_audits (batch_id, status);

CREATE INDEX IF NOT EXISTS
    job_requirement_extraction_audits_job_id_idx
    ON public.job_requirement_extraction_audits (job_id);

COMMENT ON TABLE public.job_requirement_extraction_audits IS
    'One LangExtract attempt per job and batch. Stores successful output or structured failure details.';

COMMENT ON COLUMN public.job_requirement_extraction_audits.job_id IS
    'Intentionally not a foreign key so audit history survives deletion of a source job.';

COMMENT ON COLUMN public.job_requirement_extraction_audits.extracted_requirements IS
    'Validated, source-grounded extraction output. This does not imply promotion to job_requirements.';
