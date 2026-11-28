"""Public report projection of one retained investigation; never transition authority."""
from hashlib import sha256
import json
import re

from ..effect_authority import pinned_sha
from ..handoff_store import require
from .assessment_readiness import parse_assessment_report
from .intake import intake_stages


def render_investigation_report(raw: bytes, *, issue, run: int,
                                scope_sha256: str, attempt_id: str) -> str:
    pinned_sha(scope_sha256)
    require(type(issue.identifier) is str and re.fullmatch(r'[A-Z][A-Z0-9]*-[0-9]+', issue.identifier)
            and type(run) is int and run > 0, 'investigation_report_binding')
    expected = intake_stages({}, 'OffMenu', issue, scope_sha256, run)['investigate']['attempt']
    require(type(attempt_id) is str and attempt_id == expected, 'investigation_report_attempt')
    report = parse_assessment_report(raw)
    summary, verdict = report.get('summary'), report.get('verdict')
    require(type(summary) is str and re.sub(r'[\x09-\x0d\x20]', '', summary) != ''
            and not any(0xd800 <= ord(c) <= 0xdfff for c in summary), 'investigation_report_summary')
    require(len(summary.encode('utf-8')) <= 2048 and verdict in ('approve', 'blocked', 'deferred'),
            'investigation_report_verdict')
    if 'routing' in report:
        routing = report['routing']
        require(type(routing) is dict and routing.get('issue') == issue.identifier
                and routing.get('state') == 'investigate' and type(routing.get('run')) is int
                and routing['run'] == run, 'investigation_report_routing')
    binding = json.dumps({'attempt_id': attempt_id, 'issue': issue.identifier, 'run': run,
                    'state': 'investigate', 'scope_sha256': scope_sha256,
                    'report_sha256': sha256(raw).hexdigest(), 'summary': summary,
                    'verdict': verdict}, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    for char, escaped in (('`', r'\u0060'), ('<', r'\u003c'), ('>', r'\u003e'), ('@', r'\u0040')):
        binding = binding.replace(char, escaped)
    label = {'approve': 'Investigation report indicates proposed completion.',
             'blocked': 'Investigation report indicates blocked work.',
             'deferred': 'Investigation report indicates deferred work.'}[verdict]
    return ('<!-- stokowski:report -->\n\n### Native routing evidence\n\n```json\n' + binding
            + '\n```\n\n## investigate — ' + issue.identifier + '\n\n' + label
            + '\n\nIndependent review is required before a workflow transition.')
