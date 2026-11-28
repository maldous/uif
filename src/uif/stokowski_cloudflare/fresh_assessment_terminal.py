"""Assessment terminal evidence join, detached from the native phase writer."""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import os
import stat
from hashlib import sha256

from ..effect_authority import Lease, TerminalEffect, pinned_sha, wire_seconds
from ..handoff_store import decode, digest, require, wire
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


def observe_consumed_fresh_assessment_successor(composition, issue, run, scope_sha256):
    """Read the confirmed native assessment-to-investigation CAS, without entering the task."""
    require(type(run) is int and run > 0, 'assessment_successor_run')
    pinned_sha(scope_sha256)
    stages = intake_stages({}, 'OffMenu', issue, scope_sha256, run)
    previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
    grant = composition.assignment('OffMenu', issue.id, scope_sha256)
    require(grant is not None and grant.previous is None and grant.first_intent_sha256 is not None
            and grant.scope.key.issue_id == issue.id and grant.scope_sha256 == scope_sha256
            and grant.attempts[0] == previous and successor in grant.attempts,
            'assessment_successor_grant')

    remote = composition.remote
    status = remote.status()
    realm = pinned_sha(status.get('installation_sha256'))
    require(not status['stopped'] and status['coordinator_epoch'] == grant.expected_coordinator_epoch,
            'assessment_successor_not_current')
    claim = remote.read_claim('OffMenu', successor, True)
    require(claim is not None and claim.attempt_id == successor, 'assessment_successor_not_current')
    lease = claim.lease
    fence = lease.fence
    require(fence.scope == grant.scope and fence.source == grant.source and fence.generation == 1
            and fence.coordinator_epoch == grant.expected_coordinator_epoch
            and remote.clock().timestamp() < lease.expires_at, 'assessment_successor_not_current')
    initial = remote.original_lease(grant)
    require(initial.fence == fence, 'assessment_successor_initial_original')

    request_id = digest({'lease': fence.lease_id, 'attempt': successor})
    lookup = {'installation_sha256': realm, 'session_id': remote.identity.session_id, 'request_id': request_id}
    receipt = remote.transport.request('/v1/transition/original', lookup, allow_absent=True)
    require(type(receipt) is dict and receipt.keys() == {'project', 'attempt_id', 'lease'}
            and receipt['project'] == 'OffMenu' and receipt['attempt_id'] == successor,
            'assessment_successor_cas_original')
    original_lease = receipt['lease']
    require(type(original_lease) is dict and original_lease.keys() == {'fence', 'expires_at'}
            and wire(original_lease['fence']) == wire(asdict(fence))
            and initial.expires_at <= wire_seconds(original_lease['expires_at']) <= lease.expires_at,
            'assessment_successor_cas_lease')
    # The current lease can have renewed since the committed CAS. Only its
    # original reply contains the lease bytes used in that request fingerprint.
    advance = {'request_id': request_id, 'current': original_lease,
               'expected_attempt_id': previous, 'attempt_id': successor}
    result = remote.transport.request('/v1/attempt/successor/read', {'current': asdict(lease), 'advance': advance})
    require(type(result) is dict and result.keys() == {'matches'} and result['matches'] is True,
            'assessment_successor_original_unverified')
    require(composition.assignment('OffMenu', issue.id, scope_sha256) == grant
            and remote.clock().timestamp() < lease.expires_at, 'assessment_successor_grant_changed')
    return advance


def _retain_first_request(directory, name, body, before_publish):
    """Only the creator of a durably retained request may make its first POST."""
    from .native_composition import _handoff_directory, _publish_private_original, _retained_handoff_bytes
    raw = wire(body)
    require(len(raw) <= 8192, 'assessment_successor_request_size')
    # This directory already contains the marked first assessment's originals.
    with _handoff_directory(directory) as fd:
        try:
            _publish_private_original(fd, name, raw, prefix='.successor-', before_publish=before_publish)
            created = True
        except FileExistsError:
            created = False
        retained = decode(_retained_handoff_bytes(fd, name, 8192), limit=8192)
        os.fsync(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return created, retained


def advance_marked_assessment(composition, owner, issue, attempt, verify_owner):
    """Consume only this frozen native disposition and the independently approved control edge."""
    from .remote_authority import AuthorityUnavailable
    record = attempt.settlement_record
    run, scope = record['run'], record['scope_sha256']
    stages = intake_stages({}, 'OffMenu', issue, scope, run)
    previous, successor = (stages[k]['attempt'] for k in ('assess', 'investigate'))
    disposition = record.get('post_disposition')
    require(record.get('phase') in ('report-unknown', 'transition') and not record.get('attempted')
            and record.get('kind') == 'worker' and record.get('state') == 'assess'
            and record.get('issue_id') == issue.id and record.get('identifier') == issue.identifier
            and record.get('session_id') == attempt.session_id == previous and isinstance(disposition, dict)
            and disposition.get('event') == 'complete' and disposition.get('target') == 'investigate'
            and (record['phase'] != 'transition' or record.get('event') == 'complete'
                 and record.get('target') == 'investigate' and record.get('report_posted') is True),
            'assessment_successor_frozen_disposition')
    verify_owner()
    grant = composition.assignment('OffMenu', issue.id, scope)
    require(grant is not None and grant.first_intent_sha256 is not None and grant.previous is None
            and grant.attempts[0] == previous and successor in grant.attempts, 'assessment_successor_grant')
    remote = composition.remote
    current = remote.read_claim('OffMenu', issue.id, False)
    require(current is not None and current.attempt_id in (previous, successor), 'assessment_successor_not_current')
    initial = remote.original_lease(grant)
    require(initial.fence == current.lease.fence and initial.fence.generation == 1,
            'assessment_successor_initial_original')
    workspace = composition.verify_workspace(owner, issue, grant)
    directory = workspace / '.git/uif-original-effects'
    fence = current.lease.fence
    body = record.get('expected_report_body')
    require(type(body) is str and body and composition.observe_stage_report(fence, previous, body) is not None,
            'assessment_successor_report_unverified')
    request_id = digest({'lease': fence.lease_id, 'attempt': successor})
    advance = {'request_id': request_id, 'current': asdict(current.lease), 'expected_attempt_id': previous,
               'attempt_id': successor}
    if current.attempt_id == previous:
        def current_approval():
            verify_owner()
            require(composition.assignment('OffMenu', issue.id, scope) == grant
                    and remote.read_claim('OffMenu', issue.id, False) == current
                    and remote.clock().timestamp() < current.lease.expires_at, 'assessment_successor_not_current')
            approved = remote.transport.request('/v1/attempt/attest/native',
                {'current': asdict(current.lease), 'previous': previous, 'successor': successor})
            require(type(approved) is dict and approved.keys() == {'matches'} and approved['matches'] is True,
                    'assessment_successor_control_original_unverified')

        current_approval()
        created, saved = _retain_first_request(directory, request_id + '.successor-cas', advance, current_approval)
        require(saved['request_id'] == request_id and saved['current']['fence'] == asdict(fence)
                and saved['expected_attempt_id'] == previous and saved['attempt_id'] == successor,
                'assessment_successor_cas_original')
        if created:
            current_approval()
            try:
                remote.transport.request('/v1/claim/attempt', saved)
            except AuthorityUnavailable:
                pass  # The exact retained CAS is the only way to recover.
    observed = observe_consumed_fresh_assessment_successor(composition, issue, run, scope)
    verify_retained_successor_cas(directory, observed)
    verify_owner()
    return observed


def verify_retained_successor_cas(directory, advance):
    from .native_composition import _handoff_directory, _retained_handoff_bytes
    with _handoff_directory(directory) as fd:
        try:
            raw = _retained_handoff_bytes(fd, advance['request_id'] + '.successor-cas', 8192)
        except FileNotFoundError:
            # An independent committed CAS still has its immutable authority
            # receipt. Local selection separately requires prospective entry.
            pass
        else:
            require(raw == wire(advance), 'assessment_successor_cas_original')


def _saved_native_settlement(workspace):
    from .native_composition import _clock_file_identity
    path = workspace / '.stokowski/settlement.json'
    require(path == path.resolve(strict=True), 'assessment_successor_saved_disposition')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
                and 0 < info.st_size <= 1024 * 1024, 'assessment_successor_saved_disposition')
        saved = decode(stream.read(info.st_size + 1), limit=1024 * 1024)
        require(_clock_file_identity(info) == _clock_file_identity(os.fstat(stream.fileno()))
                == _clock_file_identity(path.lstat()), 'assessment_successor_saved_disposition')
        return saved


def _settlement_subject(workspace, advance, record):
    from .native_composition import workspace_recovery_digest
    disposition = record['post_disposition']
    normalized = dict(record, phase='transition', report_posted=True, event=disposition['event'],
                      target=disposition.get('target'), report=disposition.get('report'),
                      publication_event=disposition.get('publication_event'))
    normalized.pop('attempted', None)
    return {'advance': advance, 'disposition_sha256': digest(normalized),
            'workspace_except_settlement': workspace_recovery_digest(workspace, exclude_native_settlement=True)}


def completed_successor_workspace(workspace, directory_fd, advance):
    from .native_composition import _retained_handoff_bytes
    try:
        raw = _retained_handoff_bytes(directory_fd, advance['request_id'] + '.successor-settlement', 8192)
    except FileNotFoundError:
        return False
    saved = _saved_native_settlement(workspace)
    disposition = saved.get('post_disposition')
    require(type(disposition) is dict and saved.get('phase') == 'complete' and saved.get('attempted') is True
            and saved.get('report_posted') is True and saved.get('event') == disposition.get('event') == 'complete'
            and saved.get('target') == disposition.get('target') == 'investigate'
            and saved.get('report') == disposition.get('report')
            and saved.get('publication_event') == disposition.get('publication_event')
            and raw == wire(_settlement_subject(workspace, advance, saved)),
            'authority_successor_completed_workspace_changed')
    return True


@contextmanager
def unattempted_native_settlement(workspace, advance, record, verify):
    """Serialize recovery across processes until the pinned native writer leaves its durable preimage."""
    from .native_composition import _clock_file_identity, _handoff_directory, _retained_handoff_bytes
    subject = _settlement_subject(workspace, advance, record)
    directory = workspace / '.git/uif-original-effects'
    name = advance['request_id'] + '.successor-settlement'
    _, saved = _retain_first_request(directory, name, subject, verify)
    require(saved == subject, 'assessment_successor_settlement_original_changed')
    with _handoff_directory(directory) as parent:
        with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent), 'rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('assessment_successor_settlement_in_progress') from None
            require(_clock_file_identity(os.fstat(lock.fileno())) ==
                    _clock_file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    and _retained_handoff_bytes(parent, name, 8192) == wire(subject),
                    'assessment_successor_settlement_original_changed')
            verify()
            saved = _saved_native_settlement(workspace)
            require(wire(saved) == wire(record) and saved['phase'] in ('report-unknown', 'transition')
                    and not saved.get('attempted'), 'assessment_successor_settlement_already_attempted')
            yield
