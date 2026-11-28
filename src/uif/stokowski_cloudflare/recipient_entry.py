"""Single-attempt native custody for the unstarted peer recovery original."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from hashlib import sha256
import os
import re
from pathlib import Path
import stat
from threading import Lock, current_thread

from ..handoff_store import Hold, decode, digest, require, wire
from .intake import intake_identity
from .native_composition import _handoff_directory, _publish_private_original, _retained_handoff_bytes


_READS = frozenset({
    '/v1/status', '/v1/claim/read', '/v1/usage/read', '/v1/original', '/v1/transition/original',
    '/v1/recovery/observe', '/v1/attempt/attest/native', '/v1/attempt/successor/read',
    '/v1/review/read', '/v1/completion/read', '/v1/linear/receipt', '/v1/provider/receipt',
    '/v1/source/list', '/v1/source/read', '/v1/source/original', '/v1/source/receipt',
})


def recipient_entry_body(claim, transition):
    original = {k: transition[k] for k in ('installation_sha256', 'session_id', 'request_id', 'request_sha256')}
    return {'current': asdict(claim.lease), 'attempt_id': claim.attempt_id, 'original': original}


class _RecipientPermit:
    """Copied contexts share revocation; only the selected event-loop task may use the secret."""

    def __init__(self, claim, route, task, verify_owner, secret):
        self.claim, self.route = claim, route
        self.task, self.verify_owner, self._secret = task, verify_owner, secret
        self._delegation = ContextVar('recipient_fixed_call', default=None)
        self.stage = None
        self.report = None
        self.workspace = None
        self._owner_thread = current_thread()
        self._lock = Lock()
        self._prepared = set()

    def revoke(self):
        self._secret = None

    def require_actor(self, family=None, *, owner_only=False, delegate_only=False):
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        require(self._secret is not None and not self.task.done() and not self.task.cancelling(),
                'authority_recovery_selected_task')
        require(not delegate_only or task is not self.task, 'authority_recovery_fixed_thread_required')
        if task is not self.task:
            delegation = self._delegation.get()
            require(not owner_only and task is None and delegation is not None and delegation['active']
                    and current_thread() is delegation['thread'] and family in (None, delegation['family']),
                    'authority_recovery_selected_task')
            delegation['validate']()
        self.verify_owner()

    def prepare_thread(self, family, target, require_thread):
        self.require_actor(owner_only=True)
        require(family in ('turn', 'comment', 'report-comment', 'upload') and callable(target) and callable(require_thread),
                'authority_recovery_thread_scope')
        require(family not in self._prepared, 'authority_recovery_thread_already_prepared')
        self._prepared.add(family)
        used = False
        def invoke():
            nonlocal used
            require(current_thread() is not self._owner_thread, 'authority_recovery_fixed_thread_required')
            require_thread()
            with self._lock:
                require(self._secret is not None and not used, 'authority_recovery_thread_already_used')
                used = True
            delegation = dict(thread=current_thread(), active=True, validate=require_thread, family=family)
            binding = self._delegation.set(delegation)
            try:
                self.require_actor(family)
                return target()
            finally:
                delegation['active'] = False
                self._delegation.reset(binding)
        return invoke

    def verify_tracking(self, slot, issue_id, body):
        require(self.stage == 'marked-investigation' and slot == '_announce_state'
                and issue_id == self.claim.lease.fence.scope.key.issue_id and type(body) is str,
                'authority_successor_tracking_scope')
        prefix, _, text = body.partition('\n\n')
        match = re.fullmatch(r'<!-- stokowski:state (.{1,4096}) -->', prefix)
        require(match is not None, 'authority_successor_tracking_scope')
        data = decode(match[1].encode(), limit=8192)
        timestamp = data.get('timestamp')
        expected = {'state': 'investigate', 'run': self.run, 'timestamp': timestamp}
        human = f'**[Stokowski]** Entering state: **investigate** (run {self.run})'
        if self.workflow:
            expected['workflow'] = self.workflow
            human += f' · workflow `{self.workflow}`'
        require(type(timestamp) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', timestamp)
                and type(data.get('run')) is int and data == expected and text == human,
                'authority_successor_tracking_scope')

    def verify_report(self, slot, issue_id, body):
        require(self.stage == 'marked-investigation' and slot == '_publish_run_report'
                and issue_id == self.claim.lease.fence.scope.key.issue_id
                and self.report is not None and type(body) is str and body == self.report.body,
                'authority_successor_report_scope')

    def authorize(self, path, data, headers):
        if path in _READS:
            return
        family = {'/v1/linear/comment': 'comment', '/v1/linear/upload': 'upload',
                  '/v1/source/write': 'turn', '/v1/source/checkpoint': 'turn', '/v1/send': 'turn'}.get(path)
        if self.stage == 'marked-investigation' and path == '/v1/linear/comment':
            original = data.get('operation', {}).get('original_id') if type(data) is dict else None
            report_id = digest({'attempt': self.claim.attempt_id, 'slot': '_publish_run_report',
                                'kind': 'comment', 'issue': self.claim.lease.fence.scope.key.issue_id})
            if original == report_id:
                family = 'report-comment'
        if self.stage in ('assess', 'marked-investigation'):
            require(path in ('/v1/send', '/v1/linear/comment'), 'authority_recovery_effect_not_bound')
            self.require_actor(family, delegate_only=True)
        else:
            self.require_actor(family)
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
        if self.stage in ('assess', 'marked-investigation'):
            attempt = self.claim.attempt_id
            if path == '/v1/send':
                require(parent.get('original_id') == sha256(f'{attempt}:1'.encode()).hexdigest(),
                        'authority_recovery_provider_original')
            elif self.stage == 'marked-investigation':
                issue_id = self.claim.lease.fence.scope.key.issue_id
                if family == 'report-comment':
                    self.verify_report('_publish_run_report', issue_id, request.get('body'))
                else:
                    self.verify_tracking('_announce_state', issue_id, request.get('body'))
                    require(parent.get('original_id') == digest({'attempt': attempt, 'slot': '_announce_state',
                            'kind': 'comment', 'issue': issue_id}), 'authority_successor_tracking_scope')
            else:
                issue_id = self.claim.lease.fence.scope.key.issue_id
                require(self.report is not None and request.get('body') == self.report.body
                        and parent.get('original_id') == digest({'attempt': attempt, 'slot': '_publish_run_report',
                            'kind': 'comment', 'issue': issue_id}), 'authority_assessment_report_only_effect')
        headers['uif-dispatch-permit'] = self._secret


@contextmanager
def recipient_first_entry(composition, owner, issue, scope_sha, attempt, *, expected=None):
    try:
        task = asyncio.current_task()
    except RuntimeError:
        raise Hold('authority_recovery_native_owner') from None
    stage = attempt.state_name
    run = owner._issue_state_runs.get(issue.id, 1)
    require(stage in ('assess', 'investigate') and type(run) is int and run > 0,
            'authority_recovery_native_stage')
    if stage == 'assess':
        require(expected is not None and composition._verified_assessment_report_sink is not None,
                'authority_recovery_selected_assessment_required')
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
    if expected is not None:
        require(expected.lease.fence == claim.lease.fence and expected.attempt_id == claim.attempt_id,
                'authority_recovery_selected_original_changed')
    transition = binding['original_transition']
    original = recipient_entry_body(claim, transition)['original']
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
    body = recipient_entry_body(current, transition)
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
        permit = remote.transport._recipient_permit.get()
        permit.workspace = workspace
        permit.stage = stage
        if stage != 'assess':
            # The original staged observer has no native turn/settlement binding.
            yield current
            return
        execution = composition._recipient_execution.set(permit)
        running = composition._running.set((grant, current))
        try:
            yield current
        finally:
            composition._running.reset(running)
            composition._recipient_execution.reset(execution)
