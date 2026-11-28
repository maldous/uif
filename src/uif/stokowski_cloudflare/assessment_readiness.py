"""Offline projection of the current UIF assess-only prompt, not a verdict authority."""
from dataclasses import dataclass
from hashlib import sha256
import re

from ..handoff_store import decode, require


@dataclass(frozen=True)
class AssessmentReadiness:
    issue: str
    run: int
    readiness: str
    report_sha256: str


def parse_assessment_report(raw: bytes) -> dict:
    require(type(raw) is bytes and 0 < len(raw) <= 64_000, 'assessment_report_size')
    fenced = re.fullmatch(rb'\s*```json[ \t]*\r?\n(.*?)\r?\n```\s*', raw, re.IGNORECASE | re.DOTALL)
    return decode(fenced.group(1) if fenced else raw, limit=64_000)


def project_assessment_readiness(raw: bytes, *, issue: str, run: int) -> AssessmentReadiness:
    """Describe the report's own readiness claim; never admit a workflow stage."""
    require(type(issue) is str and bool(issue) and type(run) is int and run > 0,
            'assessment_expected_binding')
    report = parse_assessment_report(raw)
    routing = report.get('routing')
    require(type(routing) is dict and routing.get('issue') == issue and routing.get('state') == 'assess'
            and type(routing.get('run')) is int and routing['run'] == run,
            'assessment_report_binding')
    verdict = report.get('verdict')
    require(isinstance(report.get('summary'), str) and bool(report['summary'].strip())
            and verdict in ('approve', 'blocked', 'deferred'), 'assessment_report_verdict')
    if verdict == 'approve':
        require(all(routing.get(field) is value for field, value in {
            'actionable': True, 'ownership_clear': True, 'deferred': False,
            'owner_pause': False, 'uncertain_effects': False,
        }.items()) and routing.get('missing_inputs') == [],
            'assessment_report_readiness_unsubstantiated')
    return AssessmentReadiness(issue, run, 'reported-ready' if verdict == 'approve' else verdict,
                               sha256(raw).hexdigest())
