"""Single-attempt native custody for the unstarted peer recovery original."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
import stat

from ..handoff_store import Hold, digest, require, wire
from .intake import intake_identity
from .native_composition import _handoff_directory, _publish_private_original, _retained_handoff_bytes


_READS = frozenset({
    '/v1/status', '/v1/claim/read', '/v1/usage/read', '/v1/original', '/v1/transition/original',
    '/v1/recovery/observe', '/v1/attempt/attest/native', '/v1/attempt/successor/read',
    '/v1/review/read', '/v1/completion/read', '/v1/linear/receipt', '/v1/provider/receipt',
    '/v1/source/list', '/v1/source/read', '/v1/source/original', '/v1/source/receipt',
})


class _RecipientPermit:
    """Copied contexts share revocation; only the selected event-loop task may use the secret."""

    def __init__(self, claim, route, task, verify_owner, secret):
        self.claim, self.route = claim, route
        self.task, self.verify_owner, self._secret = task, verify_owner, secret

    def revoke(self):
        self._secret = None

    def authorize(self, path, data, headers):
        if path in _READS:
            return
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        require(self._secret is not None and task is self.task and not self.task.done(),
                'authority_recovery_selected_task')
        self.verify_owner(path)
        require(type(data) is dict, 'authority_recovery_egress_scope')
        expected = asdict(self.claim.lease.fence)
        if path == '/v1/renew':
            current = data.get('current')
            require(type(current) is dict and current.get('fence') == expected, 'authority_recovery_egress_scope')
            return
        request = data
        if path == '/v1/send':
            request = data.get('intent')
            require(type(request) is dict and request.get('route') == asdict(self.route),
                    'authority_recovery_provider_route')
        if path in ('/v1/send', '/v1/linear/comment', '/v1/linear/upload', '/v1/source/write'):
            parent = request.get('operation')
        elif path == '/v1/source/checkpoint':
            parent = request.get('current')
        else:
            # Settlement, transfer and later-stage admission have their own
            # native owners. None is qualified by this first-entry adapter.
            raise Hold('authority_recovery_effect_not_bound')
        require(type(parent) is dict and parent.get('fence') == expected and request.get('attempt_id') == self.claim.attempt_id,
                'authority_recovery_egress_scope')
        headers['uif-dispatch-permit'] = self._secret


@contextmanager
def recipient_first_entry(composition, owner, issue, scope_sha, attempt):
    try:
        task = asyncio.current_task()
    except RuntimeError:
        raise Hold('authority_recovery_native_owner') from None
    stage = attempt.state_name
    run = owner._issue_state_runs.get(issue.id, 1)
    require(stage in ('assess', 'investigate') and type(run) is int and run > 0,
            'authority_recovery_native_stage')
    original_attempt = 'cf-intake-' + intake_identity('OffMenu', issue.id, scope_sha, run) + '-' + stage

    def verify_owner():
        require(owner.project_name == 'OffMenu' and isinstance(task, asyncio.Task)
                and owner._tasks.get(issue.id) is task and owner.running.get(issue.id) is attempt
                and issue.id in owner.claimed and issue.id not in owner.completed
                and issue.id not in getattr(owner, '_terminal_observed', {}) and issue.id not in owner._pending_gates
                and owner.pool is not None and not owner.pool.is_paused(owner.project_name)
                and owner._issue_state_runs.get(issue.id, 1) == run
                and owner._issue_current_state.get(issue.id) == attempt.state_name == stage
                and attempt.session_id == original_attempt, 'authority_recovery_native_owner')

    verify_owner()
    claim, grant, binding, entry_sha = composition._recipient_workspace(owner, issue, scope_sha, for_entry=True)
    require(claim.attempt_id == original_attempt == grant.attempts[0], 'authority_recovery_native_attempt')
    transition = binding['original_transition']
    original = {k: transition[k] for k in ('installation_sha256', 'session_id', 'request_id', 'request_sha256')}
    name = digest(transition) + '.recipient-entry'
    workspace = Path(binding['path'])
    directory = workspace / '.git/uif-original-effects'
    raw = wire({'schema': 'uif.native-recipient-entry/1', 'original': original, 'fence': asdict(claim.lease.fence),
                'attempt_id': original_attempt, 'stage': stage, 'run': run, 'scope_sha256': scope_sha,
                'workspace_binding': binding})
    require(len(raw) <= 8192, 'authority_recovery_entry_size')
    directory.mkdir(mode=0o700, exist_ok=True)
    with _handoff_directory(directory) as fd:
        try:
            retained = _retained_handoff_bytes(fd, name, 8192)
            created = False
        except FileNotFoundError:
            try:
                _publish_private_original(fd, name, raw, prefix='.recipient-entry-', before_publish=verify_owner)
                created = True
            except FileExistsError:
                created = False
            retained = _retained_handoff_bytes(fd, name, 8192)
        require(retained == raw, 'authority_recovery_entry_original_changed')
    if created:
        parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    current, current_grant, current_binding, current_sha = composition._recipient_workspace(
        owner, issue, scope_sha, for_entry=True)
    require(current.lease.fence == claim.lease.fence and current.attempt_id == original_attempt
            and current_grant == grant and current_binding == binding and current_sha == entry_sha,
            'authority_recovery_entry_changed')
    with _handoff_directory(directory) as fd:
        require(_retained_handoff_bytes(fd, name, 8192) == raw, 'authority_recovery_entry_original_changed')
    body = {'current': asdict(current.lease), 'attempt_id': original_attempt, 'original': original}
    remote = composition.remote
    if not created:
        remote.transport.observe_recipient_entry(body)
        raise Hold('authority_recovery_entry_already_attempted')

    def verify_live(path=None):
        verify_owner()
        require(remote.clock().timestamp() < current.lease.expires_at, 'authority_recovery_entry_expired')
        installation = composition.installation()
        item = installation['assignments'].get(issue.id)
        require(type(item) is dict and digest(item) == entry_sha
                and installation['coordinator_epoch'] == grant.expected_coordinator_epoch
                and Path(installation['workspace_root']) == workspace.parent
                and owner.cfg.workspace.resolved_root().resolve(strict=True) == Path(installation['workspace_root']),
                'authority_recovery_entry_changed')
        info = workspace.lstat()
        require(workspace.resolve(strict=True) == workspace and stat.S_ISDIR(info.st_mode)
                and not info.st_mode & 0o077 and (info.st_dev, info.st_ino) == (binding['device'], binding['inode']),
                'authority_recovery_workspace_changed')
        if path in ('/v1/source/write', '/v1/source/checkpoint'):
            require(stage == 'investigate' and composition.source_spec(issue.id, original_attempt) is not None,
                    'authority_recovery_source_stage')

    with remote.transport._enter_journaled_recipient(body, current, remote.route, task, verify_live):
        yield current
