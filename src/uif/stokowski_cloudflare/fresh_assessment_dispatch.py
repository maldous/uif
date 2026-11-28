"""Detached first-dispatch custody for the approved fresh assessment lane."""
from dataclasses import asdict
from pathlib import Path

from ..effect_authority import Lease
from ..handoff_store import Hold, digest, require, wire
from .intake import intake_stages
from .native_composition import (
    _handoff_directory, _publish_private_original, _retain_handoff_original,
    _retained_handoff_bytes,
)
from .remote_authority import AuthorityUnavailable

_LIMIT = 8192


def _first_dispatch_intent(grant, request, issue, run, previous, successor, item):
    return {'schema': 'uif.fresh-assessment-first-dispatch/1',
              'grant_sha256': digest(asdict(grant)), 'claim': asdict(request),
              'issue_identifier': issue.identifier, 'run': run,
              'previous': previous, 'successor': successor,
              'workspace_binding': dict(item['workspace_binding']),
              'git_config_sha256': item['git_config_sha256'],
              'prepared_tree_sha256': item['prepared_tree_sha256']}



def retain_fresh_assessment_dispatch(composition, owner, issue, run, scope_sha256, *,
                                    verify_first_dispatch_applicability):
    """One initial claim under already-established exclusive native custody.

    The existing coordinator's trusted verifier must prove explicit approved
    applicability and exclusive dispatch ownership for this exact issue (also
    across restart); no other claimant can submit the same native original
    outside this function. No runtime wiring or provider action is present.
    An unresolved intent with no exact authority original is held, not retried.
    A returned claim proves this applicability binding, not that a provider
    original is absent, retryable or authorized for send.
    """
    require(owner.project_name == 'OffMenu' and type(run) is int and run > 0,
            'assessment_dispatch_scope')
    grant = composition.assignment('OffMenu', issue.id, scope_sha256)
    stages = intake_stages({}, 'OffMenu', issue, scope_sha256, run)
    previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
    require(grant is not None and grant.previous is None and grant.scope.key.issue_id == issue.id
            and grant.attempts[0] == previous and successor in grant.attempts,
            'assessment_dispatch_grant')
    require(verify_first_dispatch_applicability(owner, issue, run, grant) is None,
            'assessment_dispatch_applicability')
    remote = composition.remote
    request = grant.claim(remote.identity)
    workspace = composition.verify_workspace(owner, issue, grant)
    item = composition.installation()['assignments'][issue.id]
    workspace_binding = dict(item['workspace_binding'])
    require(workspace == Path(workspace_binding['path'])
            and workspace_binding['original_claim_id'] == request.request_id,
            'assessment_dispatch_workspace')
    status = remote.status()
    require(not status['stopped'] and status['coordinator_epoch'] == grant.expected_coordinator_epoch,
            'assessment_dispatch_not_current')
    directory = workspace / '.git/uif-original-effects'
    name = request.request_id + '.assessment-first-intent'
    intent = _first_dispatch_intent(grant, request, issue, run, previous, successor, item)
    raw = wire(intent)
    require(len(raw) <= _LIMIT, 'assessment_dispatch_intent_size')

    created = False
    try:
        with _handoff_directory(directory) as fd:
            retained = _retained_handoff_bytes(fd, name, _LIMIT)
    except FileNotFoundError:
        require(remote.read_claim('OffMenu', issue.id, False) is None,
                'assessment_dispatch_existing_claim')
        try:
            remote.original_lease(grant)
        except Hold as error:
            if str(error) != 'authority_original_claim_unverified':
                raise
        else:
            require(False, 'assessment_dispatch_existing_original')
        composition._first_workspace(workspace, grant, None)
        require(composition.assignment('OffMenu', issue.id, scope_sha256) == grant,
                'assessment_dispatch_grant_changed')
        directory.mkdir(mode=0o700, exist_ok=True)
        with _handoff_directory(directory) as fd:
            try:
                _publish_private_original(fd, name, raw, prefix='.assessment-first-')
                created = True
            except FileExistsError:
                pass
            retained = _retained_handoff_bytes(fd, name, _LIMIT)
    require(retained == raw, 'assessment_dispatch_original_changed')

    submitted = None
    if created:
        # An fsynced intent does not freeze protected authorization or workspace
        # custody. Check immediately before the only POST; on refusal leave the
        # intent in place so subsequent attempts observe only.
        status = remote.status()
        require(not status['stopped'] and status['coordinator_epoch'] == grant.expected_coordinator_epoch,
                'assessment_dispatch_not_current')
        require(verify_first_dispatch_applicability(owner, issue, run, grant) is None,
                'assessment_dispatch_applicability')
        require(composition.assignment('OffMenu', issue.id, scope_sha256) == grant,
                'assessment_dispatch_grant_changed')
        current_item = composition.installation()['assignments'][issue.id]
        require(all(current_item.get(key) == intent[key] for key in
                    ('workspace_binding', 'git_config_sha256', 'prepared_tree_sha256'))
                and composition.verify_workspace(owner, issue, grant) == workspace,
                'assessment_dispatch_workspace_changed')
        composition._first_workspace(workspace, grant, None)
        try:
            submitted = remote._submit_initial_claim_once(grant)
        except AuthorityUnavailable:
            pass  # A lost reply can only be reconciled by observing this original.
    original = remote.original_lease(grant)
    require(submitted is None or submitted.fence == original.fence,
            'assessment_dispatch_claim_original_changed')
    current = remote.read_claim('OffMenu', issue.id, False)
    require(current is not None and current.attempt_id == previous
            and current.lease.fence == original.fence
            and remote.clock().timestamp() < current.lease.expires_at
            and composition.assignment('OffMenu', issue.id, scope_sha256) == grant,
            'assessment_dispatch_not_current')
    _retain_handoff_original(directory, request.request_id + '.assessment-first-bound',
        wire({'intent_sha256': digest(intent), 'fence': asdict(original.fence)}), _LIMIT)
    return current


def observe_fresh_assessment_dispatch(composition, owner, issue, run, scope_sha256, lease: Lease, *,
                                     verify_first_dispatch_applicability, expected_intent_sha256=None):
    """Read existing first-dispatch records in the active assess only.

    The existing protected coordinator still supplies an independent exclusive
    ownership and scope verification. No marker can substitute for that trust.
    Never call the claim writer, repair a record, or infer eligibility to send.
    """
    require(owner.project_name == 'OffMenu' and type(run) is int and run > 0
            and type(lease) is Lease, 'assessment_dispatch_scope')
    remote = composition.remote
    stages = intake_stages({}, 'OffMenu', issue, scope_sha256, run)
    previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']

    def observe_installed():
        grant = composition.assignment('OffMenu', issue.id, scope_sha256)
        require(grant is not None and grant.previous is None and grant.scope == lease.fence.scope
                and grant.source == lease.fence.source
                and grant.expected_coordinator_epoch == lease.fence.coordinator_epoch
                and grant.attempts[0] == previous and successor in grant.attempts,
                'assessment_dispatch_grant')
        require(verify_first_dispatch_applicability(owner, issue, run, grant) is None,
                'assessment_dispatch_applicability')
        request = grant.claim(remote.identity)
        workspace = composition.verify_workspace(owner, issue, grant)
        item = composition.installation()['assignments'][issue.id]
        require(workspace == Path(item['workspace_binding']['path'])
                and item['workspace_binding']['original_claim_id'] == request.request_id,
                'assessment_dispatch_workspace')
        return grant, workspace, request, _first_dispatch_intent(
            grant, request, issue, run, previous, successor, item)

    first = observe_installed()
    grant, workspace, request, intent = first
    directory = workspace / '.git/uif-original-effects'
    intent_name = request.request_id + '.assessment-first-intent'
    bound_name = request.request_id + '.assessment-first-bound'
    if expected_intent_sha256 is not None:
        require(digest(intent) == expected_intent_sha256, 'assessment_dispatch_authority_intent')
    expected_intent = wire(intent)
    expected_bound = wire({'intent_sha256': digest(intent), 'fence': asdict(lease.fence)})
    with _handoff_directory(directory) as fd:
        require(_retained_handoff_bytes(fd, intent_name, _LIMIT) == expected_intent
                and _retained_handoff_bytes(fd, bound_name, _LIMIT) == expected_bound,
                'assessment_dispatch_original_changed')
        status = remote.status()
        require(not status['stopped'] and status['coordinator_epoch'] == grant.expected_coordinator_epoch,
                'assessment_dispatch_not_current')
        original = remote.original_lease(grant)
        current = remote.read_claim('OffMenu', previous, True)
        require(original.fence == lease.fence and current is not None and current.attempt_id == previous
                and current.lease == lease and remote.clock().timestamp() < lease.expires_at,
                'assessment_dispatch_not_current')
        require(observe_installed() == first, 'assessment_dispatch_grant_changed')
        require(_retained_handoff_bytes(fd, intent_name, _LIMIT) == expected_intent
                and _retained_handoff_bytes(fd, bound_name, _LIMIT) == expected_bound,
                'assessment_dispatch_original_changed')
    require(remote.clock().timestamp() < lease.expires_at, 'assessment_dispatch_not_current')
    return current
