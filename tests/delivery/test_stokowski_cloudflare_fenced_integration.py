"""Real router/client/launcher boundaries with invented receipts and no network."""
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
            with self.assertRaises(BudgetRefused):
                await owner._run_worker(issue, SimpleNamespace())
            self.assertFalse((Path(scratch) / 'budget.sqlite').exists())
        old_dispatch.assert_not_called()
        old_eligible.assert_not_called()
        old_worker.assert_not_awaited()
        codex.assert_not_called()
        policy.assert_called_once_with(Path('/etc/owner.json'))
