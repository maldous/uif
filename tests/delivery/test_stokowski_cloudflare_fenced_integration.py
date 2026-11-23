"""Real router/client/launcher boundaries with invented receipts and no network."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uif.stokowski_cloudflare.account_admission import AccountUsageEvidence, FreeRoute
from uif.stokowski_cloudflare.budget import BudgetRefused, ProjectAccountRouter
from uif.stokowski_cloudflare.client import MODEL, FreeAdmissionWindow, WorkersAIClient
from uif.stokowski_cloudflare.launcher import configure


ACCOUNTS = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ', 'Paid'),
                    (digit * 32 for digit in 'abcdef')))
ROUTE = FreeRoute(ACCOUNTS['OffMenu'], MODEL, None, 'a' * 64)
REQUEST = dict(project='OffMenu', attempt_id='attempt', request_id='attempt/1',
               active_projects={'OffMenu'}, messages=[{'role': 'user', 'content': 'fixture'}],
               tools=[], timeout=15)


class FencedClientTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.database = Path(scratch.name) / 'ledger.sqlite'
        self.router = ProjectAccountRouter(self.database, ACCOUNTS,
                                           strict_routes={'OffMenu': ROUTE})
        self.token = Mock(side_effect=AssertionError('legacy token resolver called'))
        self.native_https = self.enterContext(patch(
            'uif.stokowski_cloudflare.client.HTTPSConnection',
            side_effect=AssertionError('unfenced network attempted')))
        self.now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        self.enterContext(patch('uif.stokowski_cloudflare.client._utc_now', return_value=self.now))
        self.enterContext(patch('uif.stokowski_cloudflare.budget._utc_time',
                                side_effect=lambda now=None: self.now if now is None else now))
        self.end = self.now + timedelta(seconds=30)
        self.snapshot = AccountUsageEvidence(ROUTE, self.now.date().isoformat(),
                                             self.now, self.end, 0, 0, True, True)

    def read_window(self, timeout):
        self.assertEqual(timeout, REQUEST['timeout'])
        return FreeAdmissionWindow(self.end, {'OffMenu': self.snapshot})

    def test_missing_ports_refuse_before_reservation_and_old_transport(self):
        for extras in ({}, {'read_account_usage': self.read_window},
                       {'open_fenced_connection': Mock()}):
            client = WorkersAIClient(self.router, self.token, **extras)
            with self.assertRaises(BudgetRefused):
                client.complete(**REQUEST)
        self.assertFalse(self.database.exists())
        self.token.assert_not_called()
        self.native_https.assert_not_called()

    def test_receipt_from_wrong_credential_epoch_never_opens_connection(self):
        opener = Mock(side_effect=AssertionError('wrong route reached egress'))
        self.snapshot = replace(self.snapshot, route=replace(ROUTE,
                                                         credential_epoch_sha256='c' * 64))
        client = WorkersAIClient(self.router, self.token, read_account_usage=self.read_window,
                                 open_fenced_connection=opener)
        with self.assertRaises(BudgetRefused):
            client.complete(**REQUEST)
        opener.assert_not_called()

    def test_actual_client_reserves_before_fenced_receiver_only(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = (
            b'{"model":"@cf/openai/gpt-oss-20b","choices":[{}],"usage":{"neurons":0.01}}')

        def open_fenced(call):
            self.assertEqual((call.project, call.attempt_id, call.request_id, call.route,
                              call.request_not_after),
                             ('OffMenu', 'attempt', 'attempt/1', ROUTE, self.end))
            with sqlite3.connect(self.database) as db:
                self.assertEqual(db.execute('SELECT account,settled FROM requests').fetchone(),
                                 (ROUTE.account_id, 0))
                self.assertEqual(db.execute('SELECT request_sha256 FROM routed_requests').fetchone(),
                                 (call.request_sha256,))
            return connection

        client = WorkersAIClient(self.router, self.token, read_account_usage=self.read_window,
                                 open_fenced_connection=open_fenced)
        client.complete(**REQUEST)
        (method, path), kwargs = connection.request.call_args
        self.assertEqual(method, 'POST')
        self.assertIn(ROUTE.account_id, path)
        self.assertEqual(kwargs['headers'], {'Content-Type': 'application/json'})
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT request_sha256 FROM routed_requests').fetchone(),
                             (sha256(kwargs['body']).hexdigest(),))
            self.assertEqual(db.execute('SELECT amount,settled FROM requests').fetchone(), (10, 1))
        connection.request.assert_called_once()
        self.token.assert_not_called()
        self.native_https.assert_not_called()

    def test_expiry_during_binding_closes_without_sending(self):
        conn = Mock()

        def opener(_):
            self.now = self.end
            return conn

        patched_clock = patch('uif.stokowski_cloudflare.client._utc_now',
                              side_effect=lambda: self.now)
        client = WorkersAIClient(self.router, self.token, read_account_usage=self.read_window,
                                 open_fenced_connection=opener)
        with patched_clock, self.assertRaisesRegex(BudgetRefused, 'deadline elapsed'):
            client.complete(**REQUEST)
        conn.request.assert_not_called()
        conn.close.assert_called_once()
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT amount,settled FROM requests').fetchone(),
                             (client.request_maximum, 0))

    def test_lost_owner_after_reservation_does_not_fall_back_or_refund(self):
        opener = Mock(return_value=None)
        client = WorkersAIClient(self.router, self.token, read_account_usage=self.read_window,
                                 open_fenced_connection=opener)
        with self.assertRaisesRegex(BudgetRefused, 'effect fence'):
            client.complete(**REQUEST)
        with self.assertRaisesRegex(BudgetRefused, 'already routed'):
            client.complete(**REQUEST)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT amount,settled FROM requests').fetchone(),
                             (client.request_maximum, 0))
        opener.assert_called_once()
        self.token.assert_not_called()
        self.native_https.assert_not_called()


class ParkedNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_unpaused_issue_is_held_if_authority_or_quota_ports_absent(self):
        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: False)
            _dispatch = Mock()
            _run_worker = AsyncMock()
            _release_slot = Mock()
            _is_eligible = Mock(return_value=True)
            running = {}
            _tasks = {}

        old_dispatch, old_worker, old_eligible = Owner._dispatch, Owner._run_worker, Owner._is_eligible
        codex = Mock(side_effect=AssertionError('started Codex'))
        runner = SimpleNamespace(run_turn=codex)
        orchestration = SimpleNamespace(run_turn=codex, Orchestrator=Owner,
                                        MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
                                        logger=Mock())
        config = dict(accounts=ACCOUNTS, projects=['OffMenu'], credentials='/etc/dormant',
                      workers_ai_strict_free_routes={'OffMenu': ROUTE.credential_epoch_sha256},
                      paid_workers_ai_exclusive=True,
                      admissions={'wanted': {'investigate': {'project': 'OffMenu',
                                                             'scope_sha256': 'fixture'}}})
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json',
                    side_effect=lambda path: config if path == Path('/etc/owner.json')
                    else self.fail('parked startup opened a credential file')) as policy):
            configure(runner, orchestration, SimpleNamespace(), policy=Path('/etc/owner.json'),
                      database=Path(scratch) / 'budget.sqlite')
            owner, issue = Owner(), SimpleNamespace(id='wanted')
            self.assertFalse(owner._is_eligible(issue))
            self.assertIsNone(owner._dispatch(issue))
            self.assertIsNone(await owner._run_worker(issue, SimpleNamespace()))
            self.assertFalse((Path(scratch) / 'budget.sqlite').exists())
        old_dispatch.assert_not_called()
        old_eligible.assert_not_called()
        old_worker.assert_not_awaited()
        codex.assert_not_called()
        policy.assert_called_once_with(Path('/etc/owner.json'))

    async def check_native_worker_handoff(self, *, allowed=False, successor=False,
                                          retry=False, pending_gate=False, settling=False,
                                          late_reader_error=None, failed_warning=False):
        issue = SimpleNamespace(id='wanted', identifier='FIXTURE-1', state='Active')
        claim = True
        failure = None
        ledger_start = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        window_end = ledger_start + timedelta(minutes=15)
        prior_retry = SimpleNamespace(attempt=8)
        newer_retry = SimpleNamespace(attempt=9)
        old_original = AsyncMock()
        writes = Mock(side_effect=AssertionError('revoked worker must not produce effects'))

        class Owner:
            project_name = 'OffMenu'
            pool = SimpleNamespace(is_paused=lambda _: False)

            def __init__(self):
                self.running = {}
                self._tasks = {}
                self.claimed = {issue.id, 'unrelated'}
                self.retry_attempts = {issue.id: prior_retry, 'unrelated': object()}
                self._slot_held = {issue.id, 'unrelated'}
                self._pending_gates = {}
                self._issue_current_state = {}
                self.releases = []

            def _is_eligible(self, *_args, **_kwargs):
                return True

            def _states_for(self, _):
                return {'investigate': SimpleNamespace(type='agent')}

            def _entry_state_for(self, _):
                return 'investigate'

            def _release_slot(self, issue_id):
                if issue_id in self._slot_held:
                    self._slot_held.discard(issue_id)
                    self.releases.append(issue_id)

            def _dispatch(self, current, attempt_num=None):
                # Native 610cbf97... lines 1680–1681 and 1722–1724: consume the
                # original retry; register current attempt and task; no done hook.
                self.retry_attempts.pop(current.id, None)
                self.claimed.add(current.id)
                attempt = SimpleNamespace(attempt=attempt_num)
                self.running[current.id] = attempt
                task = asyncio.create_task(self._run_worker(current, attempt))
                self._tasks[current.id] = task
                return task

            async def _run_worker(self, current, attempt):
                # Only the authorized native path may begin the original worker.
                assert self.running[current.id] is attempt
                assert self._tasks[current.id] is asyncio.current_task()
                assert current.id in self.claimed
                await old_original(current, attempt)

            def _on_worker_exit(self, *_args):
                writes('worker exit / settlement')

            def _schedule_retry(self, *_args):
                writes('schedule a retry')

        codex = Mock(side_effect=AssertionError('started Codex'))
        opener = Mock(side_effect=AssertionError('opened inference connection'))
        runner = SimpleNamespace(run_turn=codex)
        orchestration = SimpleNamespace(run_turn=codex, Orchestrator=Owner,
                                        MultiOrchestrator=type('Multi', (), {'start': AsyncMock()}),
                                        logger=Mock(), state_key=lambda value: value.lower())
        config = dict(accounts=ACCOUNTS, projects=['OffMenu'], credentials='/etc/dormant',
                      workers_ai_strict_free_routes={'OffMenu': ROUTE.credential_epoch_sha256},
                      admissions={issue.id: {'investigate': {'project': 'OffMenu',
                                                             'attempt': 'original-attempt',
                                                             'scope_sha256': 'fixture'}}})
        if failed_warning:
            orchestration.logger.warning.side_effect = RuntimeError('synthetic logger failure')

        def read_claim(project, issue_id):
            if failure is not None:
                raise failure
            return claim and (project, issue_id) == ('OffMenu', issue.id)

        owner = Owner()
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json',
                    side_effect=lambda path: config if path == Path('/etc/owner.json')
                    else self.fail('strict run opened a credential file')),
              patch('uif.stokowski_cloudflare.budget._utc_time',
                    side_effect=lambda now=None: ledger_start if now is None else now)):
            usage = AccountUsageEvidence(ROUTE, ledger_start.date().isoformat(),
                                         ledger_start, window_end, 0, 0, True, True)
            configure(runner, orchestration, SimpleNamespace(scope=lambda _: 'fixture'),
                      policy=Path('/etc/owner.json'), database=Path(scratch) / 'budget.sqlite',
                      read_account_usage=lambda _: FreeAdmissionWindow(window_end, {'OffMenu': usage}),
                      open_fenced_connection=opener,
                      allows_native_dispatch=read_claim)
            task = owner._dispatch(issue)
            self.assertIsNotNone(task)
            original = owner.running[issue.id]
            self.assertNotIn(issue.id, owner.retry_attempts)
            self.assertIn(issue.id, owner.claimed)
            self.assertIs(owner._tasks[issue.id], task)
            claim = allowed
            failure = late_reader_error
            if retry:
                owner.retry_attempts[issue.id] = newer_retry
            if pending_gate:
                owner._pending_gates[issue.id] = object()
            if settling:
                original.settlement_record = {'phase': 'unknown'}
            if successor:
                newer = SimpleNamespace(attempt=10)
                newer_task = object()
                owner.running[issue.id] = newer
                owner._tasks[issue.id] = newer_task
            if isinstance(late_reader_error, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await task
                old_original.assert_not_awaited()
                opener.assert_not_called()
                writes.assert_not_called()
                orchestration.logger.warning.assert_not_called()
                return
            await task
            if late_reader_error is not None:
                orchestration.logger.warning.assert_called_once_with(
                    'Workers AI native authority check unavailable; holding dispatch')
            if allowed:
                old_original.assert_awaited_once_with(issue, original)
                self.assertIs(owner.running[issue.id], original)
                self.assertIn(issue.id, owner.claimed)
                self.assertEqual(owner.releases, [])
            else:
                old_original.assert_not_awaited()
                if successor:
                    self.assertIs(owner.running[issue.id], newer)
                    self.assertIs(owner._tasks[issue.id], newer_task)
                    self.assertIn(issue.id, owner.claimed)
                    self.assertEqual(owner.releases, [])
                elif settling:
                    self.assertIs(owner.running[issue.id], original)
                    self.assertIs(owner._tasks[issue.id], task)
                    self.assertIn(issue.id, owner.claimed)
                    self.assertEqual(owner.releases, [])
                else:
                    self.assertNotIn(issue.id, owner.running)
                    self.assertNotIn(issue.id, owner._tasks)
                    self.assertNotIn(issue.id, owner._slot_held)
                    self.assertEqual(owner.releases, [issue.id])
                    self.assertEqual(issue.id in owner.claimed, retry or pending_gate)
                if retry:
                    self.assertIs(owner.retry_attempts[issue.id], newer_retry)
            self.assertIn('unrelated', owner.claimed)
            self.assertIn('unrelated', owner.retry_attempts)
            self.assertIn('unrelated', owner._slot_held)
        codex.assert_not_called()
        opener.assert_not_called()
        writes.assert_not_called()

    async def test_late_revocation_releases_only_original_volatile_slot_and_claim(self):
        await self.check_native_worker_handoff()

    async def test_same_authority_uses_original_registered_worker_and_retains_custody(self):
        await self.check_native_worker_handoff(allowed=True)

    async def test_stale_worker_cannot_release_successor_or_its_retry_or_slot(self):
        await self.check_native_worker_handoff(successor=True, retry=True)

    async def test_revoked_worker_preserves_existing_retry_and_its_claim_without_rescheduling(self):
        await self.check_native_worker_handoff(retry=True)

    async def test_revoked_worker_does_not_discard_pending_gate_claim_or_uncertain_settlement(self):
        await self.check_native_worker_handoff(pending_gate=True)
        await self.check_native_worker_handoff(settling=True)

    async def test_late_authority_reader_exception_refuses_and_releases_exact_worker(self):
        await self.check_native_worker_handoff(late_reader_error=RuntimeError('private fixture detail'))

    async def test_authority_evaluation_does_not_swallow_async_cancellation(self):
        await self.check_native_worker_handoff(late_reader_error=asyncio.CancelledError())

    async def test_diagnostics_cannot_prevent_late_revocation_cleanup(self):
        await self.check_native_worker_handoff(
            late_reader_error=RuntimeError('private fixture detail'), failed_warning=True)
