"""Review saved audit JSON offline; no service calls or inference."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from replay_extraction import replay_audit  # noqa: E402

OUT = Path(__file__).resolve().parent
x = json.loads((OUT / 'after.json').read_text())
old = {r['job_id']: r for r in x['baseline_selected']}
reviews = []
replays = []
for audit in x['current']:
    if audit['status'] == 'running':
        continue
    details = audit['audit_details']
    prior_records = old[audit['job_id']].get('extracted_requirements') or []
    current = audit.get('extracted_requirements') or []
    previous = {(r['name'], r['evidence']): r for r in prior_records}
    changes = []
    for record in current:
        prior = previous.get((record['name'], record['evidence']))
        if prior and any(prior[k] != record[k] for k in ('category', 'requirement_level')):
            changes.append({
                'name': record['name'], 'evidence': record['evidence'],
                'before': {k: prior[k] for k in ('category', 'requirement_level')},
                'after': {k: record[k] for k in ('category', 'requirement_level')},
            })
    pre = {(r['name'], r['evidence'], r['category'], r['requirement_level'])
           for r in details.get('pre_cleanup_records', [])}
    raw_available = bool(details.get('raw_extractions'))
    reviews.append({
        'job_id': audit['job_id'], 'job_title': audit['job_title'],
        'raw_output_available': raw_available,
        'accepted_classifications_preserved_from_pre_cleanup': (
            all((r['name'], r['evidence'], r['category'], r['requirement_level']) in pre
                for r in current) if raw_available else None
        ),
        'same_name_and_evidence_matches': sum((r['name'], r['evidence']) in previous for r in current),
        'category_changes': changes, 'recovered_items': details.get('recovered_items'),
        'raw_passes': dict(Counter(r['context_label'] for r in details.get('raw_extractions', []))),
    })
    if raw_available:
        policies = {policy: replay_audit(audit, policy) for policy in ('legacy', 'review')}
        replays.append({
            'job_id': audit['job_id'], 'job_title': audit['job_title'], 'policies': policies,
            'legacy_matches_live_accepted_records': policies['legacy'].get('requirements') == audit['extracted_requirements'],
            'review_matches_legacy_accepted_records': policies['review'].get('requirements') == policies['legacy'].get('requirements'),
        })
(OUT / 'item-review.json').write_text(json.dumps(reviews, ensure_ascii=False, indent=2))
(OUT / 'replay-comparison.json').write_text(json.dumps(replays, ensure_ascii=False, indent=2))
raw = sum(r['audit_details'].get('raw_items', 0) for r in x['current'])
ungrounded = sum(r['audit_details'].get('ungrounded_items', 0) for r in x['current'])
classification_changes = sum(len(r['category_changes']) for r in reviews)
metrics = {
    'jobs_with_captured_raw_output': len(replays),
    'raw_returned_items_from_those_jobs': raw,
    'accepted_items': sum(r.get('requirements_extracted') or 0 for r in x['current']),
    'ungrounded_items': ungrounded,
    'ungrounded_rate_among_captured_returned_items': ungrounded / raw if raw else None,
    'classification_differences_on_same_name_and_evidence': classification_changes,
    'all_available_replays_match_live_records': all(r['legacy_matches_live_accepted_records'] for r in replays),
    'no_policy_difference_in_available_replays': all(r['review_matches_legacy_accepted_records'] for r in replays),
    'all_available_accepted_classifications_match_pre_cleanup': all(r['accepted_classifications_preserved_from_pre_cleanup'] for r in reviews if r['raw_output_available']),
}
(OUT / 'review-summary.json').write_text(json.dumps(metrics, indent=2))
print(json.dumps(metrics, indent=2))
