import asyncio
from contextvars import copy_context
from dataclasses import asdict, replace
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

import test_native_remote_authority as fixture
import test_native_unstarted_recovery as recovery_fixture
from uif.handoff_store import Hold, decode, digest, wire
from uif.stokowski_cloudflare.intake import intake_identity
from uif.stokowski_cloudflare.native_assignment_projection import project_unstarted_assignment
from uif.stokowski_cloudflare.native_composition import observe_clean_assignment_workspace
from uif.stokowski_cloudflare.remote_authority import AccessTransport, AuthorityUnavailable


class RecipientEntryTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        peer = recovery_fixture.UnstartedRecoveryTest()
        peer.setUp()
        self.issue, self.owner, self.scope = case.issue, case.owner, case.grant.scope_sha256
        original_attempt = 'cf-intake-' + intake_identity('OffMenu', self.issue.id, self.scope, 1) + '-investigate'
        self.attempt = SimpleNamespace(state_name='investigate', session_id=original_attempt)
        grant = replace(case.grant, attempts=(original_attempt,))
        peer.body['current']['fence']['source'] = asdict(grant.source)
        peer.body['recovery']['attempt_id'] = original_attempt
        self.claim = replace(peer.current, attempt_id=original_attempt, lease=replace(peer.current.lease,
                                    fence=replace(peer.current.lease.fence, source=grant.source)))
        peer.receipt.update(lease=asdict(self.claim.lease), attempt_id=original_attempt,
            request_sha256=digest(dict(kind=peer.receipt['request_kind'],
                                       request=dict(peer.body, authority=asdict(fixture.OWNER)))))
        checkout = observe_clean_assignment_workspace(case.path, grant.source, case.item['git_config_sha256'])
        entries = decode(project_unstarted_assignment(grant, fixture.OWNER, installation_sha256=peer.realm,
            submitted_handoff=wire(peer.body), review_of={}, workspace_root=case.path.parent,
            issue_identifier=case.issue.identifier, observation=checkout), limit=8192)
        self.config = dict(assignments=entries, workspace_root=str(case.path.parent), coordinator_epoch=1)
        entries[self.issue.id]['source_overlays'] = {original_attempt: {'public_source_receipt_sha256': '6' * 64}}
        transition = entries[self.issue.id]['workspace_binding']['original_transition']
        self.marker = case.path / '.git/uif-original-effects' / (digest(transition) + '.recipient-entry')
        self.expected_body = dict(current=asdict(self.claim.lease), attempt_id=original_attempt, original={
            k: transition[k] for k in ('installation_sha256', 'session_id', 'request_id', 'request_sha256')})
        self.owner.claimed, self.owner.completed, self.owner._pending_gates = {self.issue.id}, set(), {}
        self.owner._issue_state_runs, self.owner._issue_current_state = {}, {self.issue.id: 'investigate'}
        self.owner.running = {self.issue.id: self.attempt}
        self.owner.pool = SimpleNamespace(is_paused=lambda project: False)
        self.remote = peer.remote
        self.calls = []
        self.lost, self.won = False, False
        self.secret = '7' * 64

        def connect(*args, **kwargs):
            conn = Mock()
            def request(method, path, *, body, headers):
                data = decode(body, limit=65536) if body else None
                self.calls.append((path, headers.get('uif-dispatch-permit')))
                if path == '/v1/status':
                    result = peer.status
                elif path == '/v1/transition/original':
                    self.assertEqual(data, peer.lookup)
                    result = peer.receipt
                elif path == '/v1/claim/read':
                    self.assertEqual(data, dict(project='OffMenu', by_attempt=True, identifier=original_attempt))
                    result = asdict(self.claim)
                elif path in ('/v1/recovery/enter', '/v1/recovery/observe'):
                    self.assertEqual(data, self.expected_body)
                    self.assertTrue(self.marker.is_file())
                    self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
                    if path.endswith('enter'):
                        result = dict(entered=False) if self.won else dict(entered=True, permit=self.secret)
                        self.won = True
                        if self.lost:
                            raise OSError('lost reply')
                    else:
                        result = dict(entered=self.won)
                else:
                    result = {}
                reply = conn.getresponse.return_value
                reply.status = 200
                reply.getheader.return_value = 'application/json'
                reply.read.return_value = wire(result)
            conn.request.side_effect = request
            return conn
        self.enterContext(patch('uif.stokowski_cloudflare.remote_authority.HTTPSConnection', side_effect=connect))
        self.enterContext(patch('uif.stokowski_cloudflare.remote_authority.protected_record', return_value={
            'client_id': 'fixture', 'client_secret': 'fixture'}))
        self.restart()

    def restart(self):
        self.access = self.remote.transport = AccessTransport('https://fixture.example', '/unused')
        self.native = fixture.NativeComposition(self.remote, '/unused')
        self.native.installation = lambda: self.config

    def enter(self):
        return self.native.recipient_first_entry(self.owner, self.issue, self.scope, self.attempt)

    def paths(self):
        return [p for p, _ in self.calls]

    async def test_exact_task_can_enter_once_and_raw_permit_is_scoped(self):
        self.owner._tasks = {self.issue.id: asyncio.current_task()}
        self.assertFalse(self.native.selected('OffMenu', self.issue, self.scope))
        edge = dict(attempt_id=self.claim.attempt_id, operation={'fence': asdict(self.claim.lease.fence)})
        with self.enter() as current:
            self.assertEqual(current, self.claim)
            for path in ('/v1/linear/comment', '/v1/source/write'):
                self.access.request(path, edge)
                self.assertEqual(self.calls[-1], (path, self.secret))
            self.access.request('/v1/source/checkpoint', {'current': asdict(current.lease), 'attempt_id': current.attempt_id})
            self.assertEqual(self.calls[-1], ('/v1/source/checkpoint', self.secret))
            self.access.request('/v1/source/read', {})
            self.assertEqual(self.calls[-1], ('/v1/source/read', None))
            with self.assertRaisesRegex(Hold, 'egress_scope'):
                self.access.request('/v1/linear/comment', edge | {'attempt_id': 'different'})
            with self.assertRaisesRegex(Hold, 'provider_route'):
                self.access.request('/v1/send', {'intent': edge | {'route': {}}})
            with self.assertRaisesRegex(Hold, 'effect_not_bound'):
                self.access.request('/v1/claim/attempt', {})
            moved = self.case.path.with_name('moved-checkout')
            self.case.path.rename(moved)
            self.case.path.mkdir(mode=0o700)
            try:
                with self.assertRaisesRegex(Hold, 'workspace_changed'):
                    self.access.request('/v1/linear/comment', edge)
            finally:
                self.case.path.rmdir()
                moved.rename(self.case.path)
            async def copied_task():
                self.access.request('/v1/linear/comment', edge)
            with self.assertRaisesRegex(Hold, 'selected_task'):
                await asyncio.create_task(copied_task())
            with self.assertRaisesRegex(Hold, 'selected_task'):
                await asyncio.to_thread(self.access.request, '/v1/linear/comment', edge)
            context = copy_context()
        with self.assertRaisesRegex(Hold, 'selected_task'):
            context.run(self.access.request, '/v1/linear/comment', edge)
        self.assertNotIn(self.secret.encode(), self.marker.read_bytes())
        self.restart()
        with self.assertRaisesRegex(Hold, 'already_attempted'), self.enter():
            self.fail('repeat recipient entry')
        self.assertEqual(self.paths().count('/v1/recovery/enter'), 1)
        self.assertEqual(self.paths().count('/v1/recovery/observe'), 1)

    async def test_lost_acknowledgement_observes_only_across_restart(self):
        self.owner._tasks = {self.issue.id: asyncio.current_task()}
        self.lost = True
        with self.assertRaises(AuthorityUnavailable), self.enter():
            self.fail('lost reply')
        self.restart()
        with self.assertRaisesRegex(Hold, 'already_attempted'), self.enter():
            self.fail('replayed')
        self.assertEqual(self.paths().count('/v1/recovery/enter'), 1)
        self.assertEqual(self.paths().count('/v1/recovery/observe'), 2)
        self.assertTrue(all(value is None for _, value in self.calls))

    async def test_parent_sync_interruption_does_not_resend(self):
        self.owner._tasks = {self.issue.id: asyncio.current_task()}
        parent = (self.case.path / '.git').stat()
        sync = os.fsync
        def interrupt(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                raise OSError('parent fsync interrupted')
            sync(fd)
        with patch('os.fsync', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'parent fsync interrupted'), self.enter():
                self.fail('undurable entry')
        self.restart()
        with self.assertRaisesRegex(Hold, 'already_attempted'), self.enter():
            self.fail('uncertain journal retried')
        self.assertNotIn('/v1/recovery/enter', self.paths())
        self.assertEqual(self.paths().count('/v1/recovery/observe'), 1)

    async def test_unscheduled_task_and_changed_journal_cannot_request_entry(self):
        self.owner._tasks = {}
        with self.assertRaisesRegex(Hold, 'native_owner'), self.enter():
            self.fail('unselected task')
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(Hold, 'journal_required'):
            self.access.request('/v1/recovery/enter', self.expected_body)
        self.owner._tasks[self.issue.id] = asyncio.current_task()
        self.marker.parent.mkdir(mode=0o700)
        self.marker.write_bytes(b'wrong original')
        self.marker.chmod(0o600)
        with self.assertRaisesRegex(Hold, 'original_changed'), self.enter():
            self.fail('changed marker')
        self.assertNotIn('/v1/recovery/enter', self.paths())
