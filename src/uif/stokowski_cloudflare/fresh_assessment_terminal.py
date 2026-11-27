"""Assessment terminal evidence join, detached from the native phase writer."""
from dataclasses import asdict
from hashlib import sha256

from ..effect_authority import Lease, TerminalEffect
from ..handoff_store import decode, require, wire
from ..quota_handoff import token
from .assessment_readiness import project_assessment_readiness
from .fresh_assessment_render import render_fresh_assessment
from .intake import intake_stages


def _current_grant(composition, issue_id, scope_sha256, lease, previous, successor):
    fence = lease.fence
    grant = composition.assignment('OffMenu', issue_id, scope_sha256)
    require(grant is not None and grant.previous is None
            and grant.scope == fence.scope and grant.source == fence.source
            and grant.expected_coordinator_epoch == fence.coordinator_epoch
            and grant.attempts[0] == previous and successor in grant.attempts,
            'assessment_terminal_grant')
    status = composition.remote.status()
    current = composition.remote.read_claim('OffMenu', previous, True)
    require(not status['stopped'] and status['coordinator_epoch'] == fence.coordinator_epoch
            and current is not None and current.attempt_id == previous and current.lease == lease
            and composition.remote.clock().timestamp() < lease.expires_at,
            'assessment_terminal_claim')
    # This is the immutable claim request for the exact protected grant,
    # including reconstruction. Reading it never acquires or renews.
    original = composition.remote.original_lease(grant)
    require(original.fence == fence, 'assessment_terminal_original_claim')
    return grant


def _build_fresh_assessment_terminal(composition, issue, run, scope_sha256, lease: Lease, *,
                                    raw_report: bytes, rendered_body: str,
                                    verify_original_prerequisites):
    """Prepare canonical evidence, not an attestation, authorization or phase.

    The existing coordinator's scoped verifier (denial raises) must prove
    fresh-only applicability, prerequisites and provenance of this exact raw
    report from that execution. Deterministic rendering checks the exact
    posted body. A model or caller-provided boolean is not proof. No runtime
    wiring, report send, attest or phase write occurs.
    """
    require(type(lease) is Lease and type(rendered_body) is str and rendered_body,
            'assessment_terminal_input')
    readiness = project_assessment_readiness(raw_report, issue=issue.identifier, run=run)
    require(readiness.readiness == 'reported-ready', 'assessment_terminal_not_ready')
    stages = intake_stages({}, 'OffMenu', issue, scope_sha256, run)
    previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
    require(rendered_body == render_fresh_assessment(
        raw_report, issue=issue, run=run, scope_sha256=scope_sha256, attempt_id=previous
    ), 'assessment_terminal_render_changed')
    fence = lease.fence
    grant = _current_grant(composition, issue.id, scope_sha256, lease, previous, successor)
    require(verify_original_prerequisites(
        issue, run, scope_sha256, lease, previous, successor, raw_report, rendered_body
    ) is None, 'assessment_terminal_verifier_result')
    posted = composition.observe_stage_report(fence, previous, rendered_body)
    require(type(posted) is tuple and len(posted) == 2, 'assessment_terminal_report_unverified')
    comment_id, terminal = posted
    require(type(terminal) is TerminalEffect and terminal.status == 'verified'
            and terminal.operation.fence == fence, 'assessment_terminal_report_unverified')

    evidence = wire({
        'schema': 'uif.fresh-offmenu-assessment-terminal/1',
        'issue_id': issue.id, 'issue_identifier': issue.identifier, 'run': run,
        'state': 'assess', 'successor_state': 'investigate', 'scope_sha256': scope_sha256,
        'previous': previous, 'successor': successor, 'report_sha256': readiness.report_sha256,
        'posted_comment_id': comment_id, 'posted_effect': asdict(terminal),
    })
    require(_current_grant(composition, issue.id, scope_sha256, lease, previous, successor)
            == grant, 'assessment_terminal_grant_changed')
    return evidence, grant




def build_fresh_assessment_terminal(composition, issue, run, scope_sha256, lease: Lease, *,
                                    raw_report: bytes, rendered_body: str,
                                    verify_original_prerequisites):
    """Return verified evidence bytes only, never a claim or attestation."""
    evidence, _ = _build_fresh_assessment_terminal(
        composition, issue, run, scope_sha256, lease, raw_report=raw_report,
        rendered_body=rendered_body, verify_original_prerequisites=verify_original_prerequisites)
    return evidence

def observe_fresh_assessment_terminal(composition, issue, run, scope_sha256, lease: Lease, *,
                                      raw_report: bytes, rendered_body: str,
                                      attestation_request_id: str,
                                      verify_original_prerequisites):
    """Read an already-attested exact original through the same authenticated native transport."""
    token(attestation_request_id)
    evidence, grant = _build_fresh_assessment_terminal(
        composition, issue, run, scope_sha256, lease, raw_report=raw_report,
        rendered_body=rendered_body, verify_original_prerequisites=verify_original_prerequisites)
    outcome = decode(evidence)
    previous, successor = outcome['previous'], outcome['successor']
    require(_current_grant(composition, issue.id, scope_sha256, lease, previous, successor)
            == grant, 'assessment_terminal_grant_changed')
    result = composition.remote.transport.request('/v1/attempt/attest/native', {
        'current': asdict(lease),
        'original': {
            'request_id': attestation_request_id, 'fence': asdict(lease.fence),
            'previous': previous, 'successor': successor,
            'terminal_receipt_sha256': sha256(evidence).hexdigest(),
        },
    })
    require(type(result) is dict and result.keys() == {'matches'} and result['matches'] is True,
            'assessment_terminal_original_unverified')
    require(_current_grant(composition, issue.id, scope_sha256, lease, previous, successor)
            == grant, 'assessment_terminal_grant_changed')
    return evidence
