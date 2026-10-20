# Five-job preview comparison

Batch: `1e0650da-4bd7-4c74-8368-3978ccb9dcff`; production saving disabled.

| Job | Previous status | New status | Previous accepted | New accepted | Rejected | Filtered | Duration (s) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Technology Product Manager AI & Data Strategy (Citi) | succeeded_with_warnings | succeeded_with_warnings | 68 | 68 | 38 | 0 | 219.0 |
| Staff Technical Program Manager (Kaseya) | succeeded_with_warnings | succeeded_with_warnings | 42 | 42 | 43 | 0 | 228.5 |
| Principal Product Manager – Agentic AI Platform (G-P) | succeeded | succeeded | 36 | 36 | 0 | 0 | 129.7 |
| Solution Category Owner – Consultation Solutions (ZEISS Group) | failed | running | None | None | 0 | 0 | running |

## Verification

- all_five_job_ids_present: False
- one_attempt_per_job: False
- production_requirement_count_unchanged: True
- baseline_audit_fields_unchanged: True
- production_saving_disabled: False
- jd_hashes_match_preflight: True

This is a deliberately selected five-job diagnostic cohort, not an unbiased estimate of the 113 jobs.
Historical audits lack raw pre-cleanup output and JD hashes; differences cannot be attributed solely to cleanup.
Current raw items, rejections, recoveries, filtering and duplicate events are retained in after.json.
Historical zero filtered counts were backfilled by migration and do not establish that nothing was filtered.
Acceptance counts and job status do not establish semantic accuracy, omission coverage, or production readiness.
