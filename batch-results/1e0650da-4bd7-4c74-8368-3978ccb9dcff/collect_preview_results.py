"""Read comparison audits and write local results; never mutate remote data."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from main import call_nhost_graphql  # noqa: E402

OUT = Path(__file__).resolve().parent
before = json.loads((OUT / 'before.json').read_text())
query = '''query PreviewComparison($baseline: uuid!, $current: uuid!, $ids: [uuid!]!) {
  jobs_aggregate { aggregate { count } }
  job_requirements_aggregate { aggregate { count } }
  job_requirement_extraction_audits_aggregate { aggregate { count } }
  baseline: job_requirement_extraction_audits(where: {batch_id: {_eq: $baseline}}, order_by: {job_id: asc}) {
    id job_id status attempt_number requirements_extracted requirements_rejected requirements_saved
    requirements_filtered save_requirements error_type error_message model_name prompt_version
  }
  baseline_selected: job_requirement_extraction_audits(where: {batch_id: {_eq: $baseline}, job_id: {_in: $ids}}, order_by: {job_id: asc}) {
    job_id job_title duration_ms extracted_requirements extraction_warnings error_message
  }
  current: job_requirement_extraction_audits(where: {batch_id: {_eq: $current}}, order_by: {job_id: asc}) {
    id batch_id job_id job_title company source status started_at completed_at duration_ms attempt_number
    requirements_extracted requirements_rejected requirements_filtered requirements_saved
    save_requirements extracted_requirements extraction_warnings audit_details error_type error_message error_detail
  }
}'''
after = call_nhost_graphql(query, {
    'baseline': 'aa04704a-0aa4-4b2f-9a60-04e1b74a0b27',
    'current': before['batch_id'], 'ids': [job['id'] for job in before['jobs']],
})
(OUT / 'after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2))
old = {row['job_id']: row for row in before['job_requirement_extraction_audits']}
rows = []
for audit in after['current']:
    details = audit['audit_details']
    prior = old[audit['job_id']]
    rows.append({
        'job_id': audit['job_id'], 'job_title': audit['job_title'], 'company': audit['company'],
        'before_status': prior['status'], 'after_status': audit['status'],
        'before_accepted': prior['requirements_extracted'], 'after_accepted': audit['requirements_extracted'],
        'before_rejected': prior['requirements_rejected'], 'after_rejected': audit['requirements_rejected'],
        'raw_items': details.get('raw_items'), 'filtered': audit['requirements_filtered'],
        'merged_duplicates': details.get('merged_duplicates'),
        'ungrounded_items': details.get('ungrounded_items'),
        'structural_rejections': details.get('structural_rejections'),
        'failure_stage': details.get('failure_stage'), 'failure_kind': details.get('failure_kind'),
        'duration_ms': audit['duration_ms'], 'requirements_saved': audit['requirements_saved'],
        'review_events': sum(event.get('action') == 'review' for event in details.get('events', [])),
        'warning_types': dict(Counter(warning['type'] for warning in audit['extraction_warnings'])),
        'category_level_conflicts': sum(warning.get('reason_type') == 'inconsistent_category_level'
                                        for warning in audit['extraction_warnings']),
        'error_message': audit['error_message'],
    })
checks = {
    'all_five_job_ids_present': {row['job_id'] for row in after['current']} == {job['id'] for job in before['jobs']},
    'one_attempt_per_job': len(after['current']) == 5 and all(row['attempt_number'] == 1 for row in after['current']),
    'production_requirement_count_unchanged': after['job_requirements_aggregate'] == before['job_requirements_aggregate'],
    'baseline_audit_fields_unchanged': after['baseline'] == before['job_requirement_extraction_audits'],
    'production_saving_disabled': all(not row['save_requirements'] and row['requirements_saved'] == 0 for row in after['current']),
    'jd_hashes_match_preflight': all(row['audit_details']['description_sha256'] == next(job['description_sha256'] for job in before['jobs'] if job['id'] == row['job_id']) for row in after['current']),
}
summary = {'complete': len(after['current']) == 5 and all(row['status'] != 'running' for row in after['current']), 'batch_id': before['batch_id'], 'checks': checks,
           'counts_after': {key: value for key, value in after.items() if key.endswith('_aggregate')},
           'rows': rows}
(OUT / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
lines = ['# Five-job preview comparison', '', f"Batch: `{before['batch_id']}`; production saving disabled.", '',
         '| Job | Previous status | New status | Previous accepted | New accepted | Rejected | Filtered | Duration (s) |',
         '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |']
for row in rows:
    lines.append(f"| {row['job_title']} ({row['company']}) | {row['before_status']} | {row['after_status']} | {row['before_accepted']} | {row['after_accepted']} | {row['after_rejected']} | {row['filtered']} | {round(row['duration_ms'] / 1000, 1) if row['duration_ms'] is not None else 'running'} |")
lines += ['', '## Verification', ''] + [f"- {key}: {value}" for key, value in checks.items()]
lines += ['', 'This is a deliberately selected five-job diagnostic cohort, not an unbiased estimate of the 113 jobs.',
          'Historical audits lack raw pre-cleanup output and JD hashes; differences cannot be attributed solely to cleanup.',
          'Current raw items, rejections, recoveries, filtering and duplicate events are retained in after.json.',
          'Historical zero filtered counts were backfilled by migration and do not establish that nothing was filtered.',
          'Acceptance counts and job status do not establish semantic accuracy, omission coverage, or production readiness.', '']
(OUT / 'comparison.md').write_text('\n'.join(lines))
print(json.dumps(summary, ensure_ascii=False, indent=2))
