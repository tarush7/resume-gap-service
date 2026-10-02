ALTER TABLE public.job_requirement_extraction_audits
ADD COLUMN IF NOT EXISTS requirements_rejected integer NOT NULL DEFAULT 0;

ALTER TABLE public.job_requirement_extraction_audits
ADD COLUMN IF NOT EXISTS extraction_warnings jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.job_requirement_extraction_audits
DROP CONSTRAINT IF EXISTS job_requirement_extraction_audits_status_check;

ALTER TABLE public.job_requirement_extraction_audits
ADD CONSTRAINT job_requirement_extraction_audits_status_check
CHECK (
    status IN (
        'running',
        'succeeded',
        'succeeded_with_warnings',
        'failed'
    )
);

ALTER TABLE public.job_requirement_extraction_audits
DROP CONSTRAINT IF EXISTS job_requirement_extraction_audits_rejected_check;

ALTER TABLE public.job_requirement_extraction_audits
ADD CONSTRAINT job_requirement_extraction_audits_rejected_check
CHECK (requirements_rejected >= 0);

ALTER TABLE public.job_requirement_extraction_audits
DROP CONSTRAINT IF EXISTS job_requirement_extraction_audits_warnings_array_check;

ALTER TABLE public.job_requirement_extraction_audits
ADD CONSTRAINT job_requirement_extraction_audits_warnings_array_check
CHECK (jsonb_typeof(extraction_warnings) = 'array');

COMMENT ON COLUMN public.job_requirement_extraction_audits.requirements_rejected IS
    'Number of model extraction items rejected while retaining valid records.';

COMMENT ON COLUMN public.job_requirement_extraction_audits.extraction_warnings IS
    'Structured reasons and model evidence for extraction items that were rejected.';
