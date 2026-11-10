"""Execute reviewed source only with injected synthetic modules; never real SSH."""
import importlib.util
import json
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'config/windmill/source/foo_root_jobs.py'


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.secret_reads = []
        self.connections = []
        self.output = b''
        self.receipt_job = None
        outer = self
        class Channel:
            def exec_command(self, command):
                outer.assertEqual(command, 'foo-root-exec-v1')
            def sendall(self, raw):
                request = json.loads(raw)
                outer.requests.append(request)
                outer.output = json.dumps({'version': 1, 'job_id': outer.receipt_job or request['job_id'], 'status': 'succeeded'}).encode()
            def shutdown_write(self): pass
            def recv_ready(self): return bool(outer.output)
            def recv(self, size):
                result, outer.output = outer.output, b''
                return result
            def recv_stderr_ready(self): return False
            def exit_status_ready(self): return True
            def recv_exit_status(self): return 0
        class Client:
            def get_host_keys(self): return self
            def add(self, host, kind, key): pass
            def set_missing_host_key_policy(self, policy): pass
            def connect(self, host, **kwargs): outer.connections.append((host, kwargs))
            def get_transport(self): return self
            def open_session(self, **kwargs): return Channel()
            def close(self): pass
        class Key:
            def __init__(self, **kwargs): pass
            @staticmethod
            def from_private_key(stream):
                outer.assertEqual(stream.read(), 'SYNTHETIC-NOT-A-KEY')
                return object()
        def resolve(reference):
            self.secret_reads.append(reference)
            return 'SYNTHETIC-NOT-A-KEY'
        with patch.dict(sys.modules, {'paramiko': types.SimpleNamespace(SSHClient=Client, Ed25519Key=Key, RejectPolicy=object),
                                     'wmill': types.SimpleNamespace(get_variable=resolve)}):
            self.main = runpy.run_path(str(SOURCE))['main']
    def test_invalid_request_precedes_credential_resolution(self):
        for kwargs in [dict(action='delete', job_id='x'), dict(job_id='../x'),
                       dict(action='run', job_id='x', command=''),
                       dict(action='run', job_id='x', command='echo test', timeout_seconds=True),
                       dict(action='run', job_id='x', command='echo test', cwd='relative'),
                       dict(action='status', job_id='x', command='echo test')]:
            with self.assertRaises(ValueError): self.main(**kwargs)
        self.assertEqual(self.secret_reads, [])
        self.assertEqual(self.connections, [])
    def test_run_and_same_id_status(self):
        result = self.main(action='run', job_id='job-1', command='synthetic command never executed')
        status = self.main(action='status', job_id='job-1')
        self.assertEqual(result, status)
        self.assertEqual([r['action'] for r in self.requests], ['run', 'status'])
        self.assertNotIn('command', self.requests[1])
        self.assertNotIn('SYNTHETIC-NOT-A-KEY', json.dumps([result, status, self.requests]))
        self.assertTrue(all(c[1]['allow_agent'] is False and c[1]['look_for_keys'] is False for c in self.connections))
    def test_wrong_receipt_id_rejected(self):
        self.receipt_job = 'other-job'
        with self.assertRaisesRegex(RuntimeError, 'ROOT_RECEIPT_INVALID'):
            self.main(action='status', job_id='job-1')


if __name__ == '__main__': unittest.main()
