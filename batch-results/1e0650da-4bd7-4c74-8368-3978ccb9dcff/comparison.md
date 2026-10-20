# Five-job preview comparison

Batch: `1e0650da-4bd7-4c74-8368-3978ccb9dcff`; production saving disabled.

| Job | Previous status | New status | Previous accepted | New accepted | Rejected | Filtered | Duration (s) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Technology Product Manager AI & Data Strategy (Citi) | succeeded_with_warnings | succeeded_with_warnings | 68 | 68 | 38 | 0 | 219.0 |
| Staff Technical Program Manager (Kaseya) | succeeded_with_warnings | succeeded_with_warnings | 42 | 42 | 43 | 0 | 228.5 |
| Principal Product Manager – Agentic AI Platform (G-P) | succeeded | succeeded | 36 | 36 | 0 | 0 | 129.7 |
| Solution Category Owner – Consultation Solutions (ZEISS Group) | failed | succeeded | None | 92 | 0 | 0 | 284.9 |
| Engineering & Delivery Lead - Data Services (Citi) | failed | failed | None | 0 | 0 | 0 | 182.7 |

## Verification

- all_five_job_ids_present: True
- one_attempt_per_job: True
- production_requirement_count_unchanged: True
- baseline_audit_fields_unchanged: True
- production_saving_disabled: True
- jd_hashes_match_preflight: True

This is a deliberately selected five-job diagnostic cohort, not an unbiased estimate of the 113 jobs.
Historical audits lack raw pre-cleanup output and JD hashes; differences cannot be attributed solely to cleanup.
Current raw items, rejections, recoveries, filtering and duplicate events are retained in after.json.
Historical zero filtered counts were backfilled by migration and do not establish that nothing was filtered.
Acceptance counts and job status do not establish semantic accuracy, omission coverage, or production readiness.

## Observations

- Five unique jobs, five attempts, no retries. Two clean successes, two successes
  with grounding warnings, one parsing failure. The selected baseline had one
  clean success, two warning successes and two failures.
- Production requirement rows remain 125. Audit rows increased from 239 to 244.
  The 113 baseline audit rows match all fields captured before this preview.
- The ZEISS job returned 92 accepted records in 284.864 seconds; its earlier
  attempt timed out. This single success does not establish that timeouts are fixed.
- Citi Engineering & Delivery Lead again failed with the same unterminated-JSON
  error at line 532, column 26, character 16403. The cleanup changes do not fix
  this parsing failure, which occurs before requirements are converted.
- The three previously successful jobs retained exactly the same names, evidence
  and accepted counts (146 matched records). Classification differs for 48 of
  those records: 23 Citi AI/Data, 4 Kaseya and 21 G-P. These differences are
  consistent with removing the old unconditional section classification.
- Targeted source review: Citi explicitly says SQL and Python are an added
  advantage; the new records retain preferred_skill/preferred, whereas the old
  output stored responsibility/required. Its 8+ years qualification now retains
  experience rather than being forced to responsibility. This is a targeted
  example review, not a full semantic or coverage audit of all accepted records.
- Across four jobs with captured model items: 319 returned items = 238 accepted
  + 81 rejected for ungrounded evidence + zero filtered + zero merged duplicates.
  The grounding rejection rate is 81/319 (25.39%) in those four selected jobs.
  No returned-item denominator is available for the parsing failure. These
  rejections are not individually established hallucinations. There were zero
  structural rejections or category/level conflicts among the returned items.
- All 238 accepted records preserve their captured pre-cleanup category and level.
  Offline replay reproduces accepted output for all four parseable jobs, under
  both legacy and review filter policies. No filtering/review events occurred, so
  these JDs do not resolve the boilerplate policy question.
- The full 113-job comparison, representative coverage checklists, broader
  boilerplate review and production promotion remain pending.
