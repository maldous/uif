"""Synthetic collector-to-actual-Worker recovery tests, with no network socket.

The Node process calls the real Worker and migrations against local SQLite. This
is not deployed D1, HTTPS/TLS, daemon/source qualification, or public acceptance.
"""
import http.client
import json
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import unittest

from tools.morgan_monitor.events import make_event
from tools.morgan_monitor.sources import Observation
from tools.morgan_monitor.store import Store
from tools.morgan_monitor.transport import HTTPSReceiver, flush

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('receiver_fixture.mjs')
WORKER = ROOT / 'services/morgan-monitor/worker.mjs'
NOW = 1794240000
TOKEN = 'synthetic-test-token-not-real-32chars'
PRIVATE = 'synthetic-private-record-not-for-publication'


class Bridge:
    def __init__(self, path):
        self.path = path
        self.process = None
        self.start()

    def start(self):
        self.process = subprocess.Popen(['node', '--no-warnings', str(FIXTURE), str(self.path)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, cwd=ROOT)

    def call(self, command):
        self.process.stdin.write(json.dumps(command) + '\n')
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 10)[0]:
            raise AssertionError('Offline receiver fixture timed out')
        response = self.process.stdout.readline()
        if not response:
            raise AssertionError('Offline receiver fixture exited unexpectedly')
        return json.loads(response)

    def close(self):
        if self.process is None:
            return
        process, self.process = self.process, None
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            process.stdout.close()
            process.stderr.close()


class WorkerResponse:
    def __init__(self, result, lose_ack=False):
        self.status, self.body = result['status'], result['body'].encode()
        self.lose_ack = lose_ack
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit):
        if self.lose_ack:
            raise http.client.IncompleteRead(b'synthetic-private-response-fragment', 10)
        return self.body[:limit]


class WorkerOpener:
    def __init__(self, bridge):
        self.bridge, self.now, self.lose_ack = bridge, NOW, False
        self.requests, self.responses = [], []

    def open(self, request, timeout):
        self.requests.append(request.data)
        result = self.bridge.call({'path': '/v1/ingest', 'method': request.method,
                                  'headers': dict(request.header_items()),
                                  'body': request.data.decode(), 'now': self.now * 1000})
        self.responses.append(result)
        lose_ack, self.lose_ack = self.lose_ack, False
        return WorkerResponse(result, lose_ack)


@unittest.skipUnless(shutil.which('node') and WORKER.exists(), 'Receiver source and Node 24 required')
class ReceiverRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'collector' / 'state.sqlite3')
        self.bridge = Bridge(self.root / 'receiver.sqlite3')
        self.opener = WorkerOpener(self.bridge)
        self.receiver = HTTPSReceiver({
            'enabled': True, 'url': 'https://receiver.example/v1/ingest',
            'allowedUrls': ['https://receiver.example/v1/ingest'],
            'tokenEnvs': {'codex_turns': 'SYNTHETIC_TOKEN'}, 'timeoutSeconds': 1,
        }, environment={'SYNTHETIC_TOKEN': TOKEN}, opener=self.opener)

    def tearDown(self):
        self.bridge.close()
        self.store.close()
        self.temp.cleanup()

    def save(self, now=NOW, health='ok'):
        obs = Observation('codex_turns', now, health, 'historical_registry',
                          [{'key': PRIVATE, 'record_at': now, 'state': 'inProgress', 'liveness': 'unknown'}]
                          if health == 'ok' else [], 1 if health == 'ok' else 0,
                          health == 'ok', None if health == 'ok' else 'source_unavailable')
        bindings = [{'sourceId': 'codex_turns', 'recordKey': PRIVATE, 'alias': '1',
                     'topicKeys': ['monitoring'], 'blockerKeys': []}]
        return self.store.save(obs, lambda seq: make_event(obs, seq, bindings))

    def status(self, now=NOW, path='/api/status'):
        result = self.bridge.call({'path': path, 'now': now * 1000})
        self.assertEqual(result['status'], 200)
        return json.loads(result['body'])

    def restart(self):
        self.bridge.close()
        self.bridge.start()
        self.store.close()
        self.store = Store(self.root / 'collector' / 'state.sqlite3')

    def test_committed_lost_ack_retries_exactly_after_both_restarts(self):
        event = self.save()
        self.opener.lose_ack = True
        self.assertEqual(flush(self.store, self.receiver, NOW), {'sent': 0, 'failed': 1})
        self.assertEqual(self.opener.responses[0]['status'], 202)
        self.assertEqual(self.store.status()['pending'], 1)
        self.assertEqual(self.store.db.execute('SELECT last_error_code FROM outbox').fetchone()[0], 'delivery_failed')
        self.restart()
        self.assertEqual(flush(self.store, self.receiver, NOW + 4), {'sent': 0, 'failed': 0})
        self.opener.now = NOW + 5
        self.assertEqual(flush(self.store, self.receiver, NOW + 5), {'sent': 1, 'failed': 0})
        self.assertEqual(self.opener.requests[0], self.opener.requests[1])
        self.assertEqual(self.opener.responses[1]['status'], 200)
        self.assertTrue(json.loads(self.opener.responses[1]['body'])['duplicate'])
        self.assertEqual(self.store.status()['pending'], 0)
        stats = self.bridge.call({'action': 'stats'})
        self.assertEqual(stats, {'count': 1, 'head': {'sequence': 1, 'observed_at': NOW * 1000, 'received_at': NOW * 1000}})
        public = self.status(NOW + 5)
        slot = next(row for row in public['slots'] if row['id'] == 'foo-1')
        self.assertEqual(slot['availability'], 'fresh')
        self.assertIsNone(slot['displayState'])
        for private in (PRIVATE, TOKEN, 'synthetic-private-response-fragment'):
            self.assertNotIn(private, json.dumps(public))
            self.assertNotIn(private, json.dumps(event))
            self.assertNotIn(private, json.dumps(self.store.snapshot()))

    def test_backlog_stays_old_and_outside_clock_expires_fresh_recovery(self):
        self.save()
        self.opener.now = NOW + 3600
        self.assertEqual(flush(self.store, self.receiver, NOW + 3600), {'sent': 1, 'failed': 0})
        self.assertEqual(self.status(NOW + 3600)['sources'][0]['status'], 'outage')
        self.save(NOW + 3602)
        self.opener.now = NOW + 3602
        self.assertEqual(flush(self.store, self.receiver, NOW + 3602), {'sent': 1, 'failed': 0})
        fresh = self.status(NOW + 3602)
        self.assertEqual(fresh['sources'][0]['status'], 'fresh')
        expired = self.bridge.call({'action': 'scheduled', 'now': (NOW + 4202) * 1000})
        self.assertEqual(expired['sources'][0]['status'], 'outage')
        self.assertNotEqual(expired['revision'], fresh['revision'])
        historical = self.status(NOW + 4202, '/api/snapshots/' + fresh['revision'])
        self.assertEqual(historical, fresh)

    def test_source_failure_never_renews_last_good_evidence(self):
        self.save()
        self.assertEqual(flush(self.store, self.receiver, NOW), {'sent': 1, 'failed': 0})
        self.save(NOW + 30, 'unavailable')
        self.opener.now = NOW + 30
        self.assertEqual(flush(self.store, self.receiver, NOW + 30), {'sent': 1, 'failed': 0})
        public = self.status(NOW + 30)
        self.assertEqual(public['sources'][0]['status'], 'error')
        self.assertEqual(public['sources'][0]['lastSuccessAt'], '2026-11-09T16:00:00.000Z')
        self.assertEqual(self.store.snapshot()['sources'][0]['lastSuccessAt'], NOW)
        expired = self.bridge.call({'action': 'scheduled', 'now': (NOW + 600) * 1000})
        self.assertEqual(expired['sources'][0]['status'], 'outage')
