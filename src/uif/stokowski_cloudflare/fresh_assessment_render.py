"""Fixed public-safe report body for the fresh OffMenu assessment path only."""
import re

from ..effect_authority import pinned_sha
from ..handoff_store import require, wire
from .assessment_readiness import project_assessment_readiness
from .intake import intake_stages


def render_fresh_assessment(raw: bytes, *, issue, run: int,
                            scope_sha256: str, attempt_id: str) -> str:
    """Render bounded facts only; never copy arbitrary report text into a post."""
    pinned_sha(scope_sha256)
    identifier = issue.identifier
    require(type(identifier) is str and re.fullmatch(r'[A-Z][A-Z0-9]*-[0-9]+', identifier),
            'assessment_render_issue_identifier')
    projection = project_assessment_readiness(raw, issue=identifier, run=run)
    expected = intake_stages({}, 'OffMenu', issue, scope_sha256, run)['assess']['attempt']
    require(type(attempt_id) is str and attempt_id == expected, 'assessment_render_attempt')
    binding = wire({
        'attempt_id': attempt_id, 'issue': identifier, 'run': run,
        'state': 'assess', 'scope_sha256': scope_sha256,
        'report_sha256': projection.report_sha256,
    }).decode('utf-8')
    label = {
        'reported-ready': 'Assessment report indicates readiness for bounded work.',
        'blocked': 'Assessment report indicates blocked work.',
        'deferred': 'Assessment report indicates deferred work.',
    }[projection.readiness]
    return ('<!-- stokowski:report -->\n\n'
            '### Native routing evidence\n\n'
            '```json\n' + binding + '\n```\n\n'
            '## assess — ' + identifier + '\n\n'
            + label + '\n\nThis report does not authorize a workflow transition.')
