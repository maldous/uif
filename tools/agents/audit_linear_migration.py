"""Audit frozen README-to-Linear scope accounting without contacting services.

Input is an immutable historical README plus full issue-description readbacks.
This never selects work, updates issues or interprets source IDs as live intake.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

IMPORT_MARKER = '<!-- readme-to-linear-20261114-v1 -->'
ANCILLARY_HEADING = '## Complete ancillary source accounting'
SOURCE_BOUNDARY = '## 11 nonadditive references'


def audit(source, issues):
    if SOURCE_BOUNDARY not in source:
        raise ValueError('Historical README is missing the nonadditive boundary')
    action_source, references = source.split(SOURCE_BOUNDARY, 1)
    action_ids = re.findall(r'^- \*\*([^*\n]+)\*\*:', action_source, re.M)
    reference_ids = re.findall(r'^- \*\*([^*\n]+)\*\*:', references, re.M)
    if not action_ids or len(set(action_ids)) != len(action_ids):
        raise ValueError('Historical action IDs must be nonempty and unique')
    if len(set(reference_ids)) != len(reference_ids):
        raise ValueError('Historical reference IDs must be unique')
    if not isinstance(issues, list):
        raise ValueError('Expected complete issue-readback objects')
    evidence, occurrences, seen = [], defaultdict(list), set()
    for issue in issues:
        if not isinstance(issue, dict) or not isinstance(issue.get('id'), str) or not isinstance(issue.get('description'), str):
            raise ValueError('Expected issue id and full description')
        identity, description = issue['id'], issue['description']
        if identity in seen:
            raise ValueError('Duplicate issue readback')
        seen.add(identity)
        count = description.count(IMPORT_MARKER)
        if count > 1:
            raise ValueError('Ambiguous repeated primary-import marker')
        if not count:
            continue
        primary = description.split(IMPORT_MARKER, 1)[1].split(ANCILLARY_HEADING, 1)[0]
        normalized = re.sub(r'<issue\b[^>]*\blabel="([^"]+)"[^>]*/>', r'\1', primary).replace('**', '')
        matches = []
        for line in normalized.splitlines():
            for action in action_ids:
                if line.startswith('* ' + action + ':'):
                    matches.append(action)
                    occurrences[action].append(identity)
                    break
        evidence.append({
            'issue': identity, 'url': issue.get('url'), 'updated_at': issue.get('updatedAt'),
            'description_sha256': hashlib.sha256(description.encode()).hexdigest(),
            'primary_scope_occurrences': len(matches),
        })
    missing = sorted(set(action_ids) - occurrences.keys())
    return {
        'schema': 'uif-linear-migration-audit-v1',
        'purpose': 'Frozen historical scope-accounting audit; not a work queue, allocator or runtime readiness proof',
        'source_readme_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'source_action_count': len(action_ids),
        'source_nonadditive_reference_count': len(reference_ids),
        'issue_readbacks_checked': len(issues),
        'receiving_issue_count': sum(item['primary_scope_occurrences'] > 0 for item in evidence),
        'primary_unique_action_count': len(occurrences),
        'primary_scope_occurrences': sum(map(len, occurrences.values())),
        'missing_action_ids': missing,
        'multiple_primary_occurrences': [
            {'source_id': action, 'issues': owners}
            for action, owners in sorted(occurrences.items()) if len(owners) > 1
        ],
        'receiving_readback_evidence': sorted(
            (item for item in evidence if item['primary_scope_occurrences']), key=lambda item: item['issue']),
        'scope_accounting_complete': not missing,
        'limits': [
            'Issue reads are non-atomic; refresh actual issue state before admission.',
            'Primary imports alone count; the archival whole-README copy is excluded.',
            'Repeated original and later wording within one issue is not additional work.',
            'Coverage does not establish semantic acceptance, installation, atomic claims, execution or refill.',
            'Historical IDs are provenance only; live intake remains native Linear issues.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-readme', type=Path, required=True)
    parser.add_argument('--issue-readbacks', type=Path, required=True,
                        help='JSON array with full id/description readbacks, never abbreviated list results')
    args = parser.parse_args(argv)
    try:
        result = audit(args.source_readme.read_text(encoding='utf-8'),
                       json.loads(args.issue_readbacks.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result['scope_accounting_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
