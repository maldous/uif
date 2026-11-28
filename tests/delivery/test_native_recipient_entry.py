import asyncio
from contextvars import copy_context
from dataclasses import asdict, replace
from hashlib import sha256
from threading import Event, current_thread
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

import test_native_remote_authority as fixture
import test_native_unstarted_recovery as recovery_fixture
from uif.effect_authority import Operation
from uif.handoff_store import Hold, decode, digest, wire
from uif.stokowski_cloudflare.intake import intake_identity
from uif.stokowski_cloudflare.native_assignment_projection import project_unstarted_assignment
from uif.stokowski_cloudflare.native_composition import _LinearClient, observe_clean_assignment_workspace
from uif.stokowski_cloudflare.remote_authority import AccessTransport, AuthorityUnavailable, ProviderReply
from uif.stokowski_cloudflare.shared_admission import SendIntent


class _RecipientCheckout(IsolatedAsyncioTestCase):
    stage = 'investigate'

    def setUp(self):
        self.case = case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        peer = recovery_fixture.UnstartedRecoveryTest()
        peer.setUp()
        self.issue, self.owner, self.scope = case.issue, case.owner, case.grant.scope_sha256
        original_attempt = 'cf-intake-' + intake_identity('OffMenu', self.issue.id, self.scope, 1) + '-' + self.stage
        self.attempt = SimpleNamespace(state_name=self.stage, session_id=original_attempt)
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
        self.owner._issue_state_runs, self.owner._issue_current_state = {}, {self.issue.id: self.stage}
        self.owner.running = {self.issue.id: self.attempt}
        self.owner.pool = SimpleNamespace(is_paused=lambda project: False)
        self.remote = peer.remote
        self.calls = []
        self.effect_reply = None
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
                    if path.endswith('enter'):
                        self.assertTrue(self.marker.is_file())
                        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
                        result = dict(entered=False) if self.won else dict(entered=True, permit=self.secret)
                        self.won = True
                        if self.lost:
                            raise OSError('lost reply')
                    else:
                        result = dict(entered=self.won)
                elif self.effect_reply is not None:
                    result = self.effect_reply(path, data)
                else:
                    result = {}
                reply = conn.getresponse.return_value
                reply.status = 404 if result is None else 200
                if result is None:
                    result = {'error': 'original_absent'}
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


class RecipientEntryTest(_RecipientCheckout):
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

class RecipientAssessmentTest(_RecipientCheckout):
    stage = 'assess'

    async def asyncSetUp(self):
        self.owner._tasks = {self.issue.id: asyncio.current_task()}
        self.issue.title, self.issue.description = 'Fixture', 'Bounded fixture assessment'
        self.native._verified_assessment_report_sink = object()

    def enter(self):
        return self.native.recipient_first_entry(self.owner, self.issue, self.scope, self.attempt, expected=self.claim)

    def select(self):
        return self.native.recipient_assignment(self.owner, self.issue, self.scope, self.stage, 1)

    async def test_qualification_requires_pristine_selection_and_matching_registered_fence(self):
        self.owner._tasks[self.issue.id] = asyncio.current_task()
        selected = self.select()
        self.assertEqual(selected, self.claim)
        self.won = True
        with self.assertRaisesRegex(Hold, 'already_attempted'):
            self.select()
        self.won = False
        changed = replace(selected, attempt_id='other-attempt')
        with self.assertRaisesRegex(Hold, 'selected_original_changed'), self.native.recipient_first_entry(
                self.owner, self.issue, self.scope, self.attempt, expected=changed):
            self.fail('registered another original')
        self.assertFalse(self.marker.exists())
        with self.enter():
            self.assertTrue(self.native.recipient_task_active(self.issue.id, selected.attempt_id))
            self.assertEqual(self.native.recipient_workspace_for_prologue(
                self.owner, self.issue, self.scope, self.attempt), self.case.path)
            (self.case.path / '.stokowski').mkdir()
            self.assertEqual(self.native.verify_recipient_workspace(
                self.owner, self.issue, self.scope, selected.attempt_id), self.case.path)
            (self.case.path / 'guide.md').write_text('changed tracked source')
            with self.assertRaisesRegex(Hold, 'source_tree_bytes'):
                self.native.verify_recipient_workspace(self.owner, self.issue, self.scope, selected.attempt_id)
            (self.case.path / 'guide.md').write_text('original\n')
            (self.case.path / '.stokowski').rmdir()
        with self.assertRaisesRegex(Hold, 'journal_needs_reconciliation'):
            self.select()
        self.assertEqual(self.paths().count('/v1/recovery/enter'), 1)
        with patch.object(self.native, '_verified_assessment_report_sink', None):
            with self.assertRaisesRegex(Hold, 'native_report_sink'):
                self.select()
        with patch.dict(self.config['assignments'], {self.issue.id: {}}):
            self.assertIsNone(self.select())

    async def test_selected_turn_thread_and_exact_report_use_distinct_one_use_calls(self):
        self.owner._tasks[self.issue.id] = asyncio.current_task()
        claim = self.claim
        request_id = sha256(f'{claim.attempt_id}:1'.encode()).hexdigest()
        op = Operation(claim.lease.fence, request_id, 'a' * 64)
        intent = SendIntent(op, self.remote.route, claim.attempt_id, 'a' * 64, 'b' * 64, 123, 1)
        raw = wire({'summary':'fixture', 'verdict':'blocked', 'routing': {
            'issue':self.issue.identifier, 'state':'assess', 'run':1}})
        self.remote.provider_result = Mock(side_effect=lambda operation: ProviderReply(
            wire({'choices':[{'message':{'content':raw.decode()}}]}), None, 'c' * 64))
        client = _LinearClient(None, self.native)
        with self.enter():
            finisher = self.native.recipient_assessment_finisher(self.issue, {
                'scope_sha256': self.scope, 'attempt': claim.attempt_id}, 1)
            entered = None
            def validate():
                if entered is not current_thread():
                    raise Hold('wrong_executor')
            def run():
                self.assertEqual(self.native.current(self.issue.id), claim)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    with self.assertRaisesRegex(Hold, 'selected_task'):
                        executor.submit(copy_context().run, self.native.retain_intent, intent).result()
                self.native.retain_intent(intent)
                self.access.request('/v1/send', {'intent':asdict(intent)})
                self.assertEqual(self.calls[-1], ('/v1/send', self.secret))
                return finisher(request_id, raw.decode())
            bound = self.native.prepare_recipient_turn_thread(run, validate)
            with self.assertRaisesRegex(Hold, 'wrong_executor'):
                await asyncio.to_thread(bound)
            with self.assertRaisesRegex(Hold, 'already_prepared'):
                self.native.prepare_recipient_turn_thread(run, validate)
            async def copied():
                self.native.retain_intent(intent)
            with self.assertRaisesRegex(Hold, 'selected_task'):
                await asyncio.create_task(copied())
            self.assertFalse((self.marker.parent / (request_id + '.json')).exists())
            def invoke():
                nonlocal entered
                entered = current_thread()
                try:
                    return bound()
                finally:
                    entered = None
            proof = await asyncio.to_thread(invoke)
            with self.assertRaisesRegex(Hold, 'already_used'):
                await asyncio.to_thread(invoke)
            self.assertTrue(proof.path.is_file())
            self.native._slot.set('_publish_run_report')
            report = self.native._recipient_execution.get().report
            self.assertIn(claim.attempt_id, report.body)
            with self.assertRaisesRegex(Hold, 'only_effect'):
                await client.post_comment(self.issue.id, 'other body')
            with self.assertRaisesRegex(Hold, 'upload_held'):
                await client.upload_file('artifact', 'text/plain', b'data')

            def effect_reply(path, data):
                if path == '/v1/linear/receipt':
                    return None
                if path == '/v1/linear/comment':
                    self.assertEqual(data['body'], report.body)
                    return {'id': '12345678-1234-1234-1234-123456789abc', 'receipt_sha256':'d' * 64}
                if path == '/v1/original':
                    operation = data['operation']
                    return {'operation':operation, 'state':'terminal', 'terminal': {
                        'operation':operation, 'status':'verified', 'receipt_sha256':'d' * 64}}
                self.fail(path)
            self.effect_reply = effect_reply
            async def copied_comment():
                await client.post_comment(self.issue.id, report.body)
            with self.assertRaisesRegex(Hold, 'selected_task'):
                await asyncio.create_task(copied_comment())
            self.assertTrue(await client.post_comment(self.issue.id, report.body))
            with self.assertRaisesRegex(Hold, 'already_prepared'):
                await client.post_comment(self.issue.id, report.body)
            self.assertEqual(self.calls.count(('/v1/linear/comment', self.secret)), 1)
            self.assertEqual(self.paths().count('/v1/send'), 1)
            with self.assertRaisesRegex(Hold, 'later_stage_held'), self.native.settlement_context(
                    self.owner, self.issue, self.attempt):
                self.fail('unqualified subsequent stage')

    async def test_repeated_cancellation_drains_selected_linear_thread_before_exit(self):
        loop = asyncio.get_running_loop()
        started, finished = asyncio.Event(), Event()
        release = Event()
        real = self.native.linear

        def intercept(*args):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError('fixture failed to release executor')
            try:
                return real(*args)
            finally:
                finished.set()

        async def recipient():
            self.owner._tasks[self.issue.id] = asyncio.current_task()
            with self.enter():
                self.native._recipient_execution.get().report = SimpleNamespace(body='fixture')
                self.native._slot.set('_publish_run_report')
                await _LinearClient(None, self.native).post_comment(self.issue.id, 'fixture')

        with patch.object(self.native, 'linear', side_effect=intercept):
            winner = asyncio.create_task(recipient())
            try:
                await asyncio.wait_for(started.wait(), 3)
                winner.cancel()
                await asyncio.sleep(0)
                winner.cancel()
                await asyncio.sleep(0)
                self.assertFalse(winner.done())
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await winner
        self.assertTrue(finished.is_set())
        self.assertEqual(list(self.marker.parent.glob('*.linear')), [])
        self.assertNotIn('/v1/linear/comment', self.paths())
