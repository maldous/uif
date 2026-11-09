"""Wire-contract tests against an isolated local fake server, never live Foo."""
import copy
import hashlib
import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('recreate', ROOT / 'tools/windmill/recreate.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class FakeServer(BaseHTTPRequestHandler):
    calls = []
    deployed = None
    drift = False
    exists = False
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(('POST', self.path, body))
        if self.path == '/api/workspaces/exists':
            result, code = self.server.exists, 200
        elif self.path == '/api/workspaces/create':
            result, code = 'created', 201
        elif self.path == '/api/w/test-foo/scripts/create':
            self.server.deployed = dict(body, hash='1234567890abcdef')
            result, code = '1234567890abcdef', 201
        else:
            result, code = 'unexpected', 400
        self.reply(code, result)
    def do_GET(self):
        self.server.calls.append(('GET', self.path, None))
        if self.path == '/api/w/test-foo/variables/exists/u/admin/foo_ssh_key_20261027':
            result = True
        elif self.path == '/api/w/test-foo/scripts/get/p/u/admin/foo_root_jobs':
            result = dict(self.server.deployed)
            if self.server.drift:
                result['content'] = 'drift'
        else:
            return self.reply(404, 'unknown')
        self.reply(200, result)
    def reply(self, code, result):
        raw = json.dumps(result).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LocalTransport:
    def __init__(self, port):
        self.port = port
    def request(self, method, path, body):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request(method, path, None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()


class RecreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Synthetic source is never imported or executed.
        self.bundle = {'version': 1, 'source_kind': 'reviewed-windmill-export', 'path': r.SCRIPT,
                       'language': 'python3', 'summary': 'synthetic fixture only', 'credential_reference': r.CREDENTIAL}
        for name, content in [('source', 'def main(action: str): return {"synthetic": True}\n'),
                              ('schema', '{"type":"object","properties":{"action":{"type":"string"}}}'),
                              ('lock', '# synthetic fixture lock, not a production resolution\nparamiko==5.1.0\nwmill==1.850.0\n')]:
            (self.root / name).write_text(content)
            self.bundle[name] = {'path': name, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeServer)
        self.server.calls, self.server.drift, self.server.exists = [], False, False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.transport = LocalTransport(self.server.server_port)
        self.journal = self.root / 'operation.json'
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()
    def run_recreation(self, binding=lambda target, reference: True):
        return r.recreate(self.root, self.bundle, 'test-foo', self.transport, binding, self.journal)
    def test_checked_in_bundle_blocked_without_resolved_lock(self):
        bundle = json.loads((ROOT / 'config/windmill/deployment-bundle.json').read_text())
        with self.assertRaises(ValueError):
            r.recreate(ROOT, bundle, 'test-foo', self.transport, lambda *args: True, self.journal)
        self.assertEqual(self.server.calls, [])
        self.assertFalse(self.journal.exists())

    def test_actual_export_cannot_substitute_empty_lock(self):
        bundle = json.loads((ROOT / 'config/windmill/deployment-bundle.json').read_text())
        for name in ('source', 'schema'):
            content = (ROOT / bundle[name]['path']).read_bytes()
            (self.root / name).write_bytes(content)
            bundle[name] = {'path': name, 'sha256': hashlib.sha256(content).hexdigest()}
        (self.root / 'empty.lock').write_text('')
        bundle['lock'] = {'path': 'empty.lock', 'sha256': hashlib.sha256(b'').hexdigest()}
        with self.assertRaises(ValueError):
            r.recreate(self.root, bundle, 'test-foo', self.transport, lambda *args: True, self.journal)
        self.assertEqual(self.server.calls, [])
        self.assertFalse(self.journal.exists())

    def test_official_contract_bindings(self):
        contract = json.loads((ROOT / 'config/windmill/api-contract.json').read_text())
        self.assertEqual(contract['source_commit'], '5d3f239bf2c114ffe0215eebd1cc5fca38435c90')
        self.assertEqual(contract['paths']['/workspaces/create']['post']['operationId'], 'createWorkspace')
        self.assertEqual(contract['paths']['/w/{workspace}/scripts/create']['post']['operationId'], 'createScript')
        self.assertTrue(set(contract['schemas']['NewScript']['required']).issubset(r.payload(self.root, self.bundle)))

    def test_supported_requests_and_verified_config(self):
        receipt = self.run_recreation()
        self.assertEqual(receipt['state'], 'script-copy-verified')
        self.assertFalse(receipt['live_recreation_verified'])
        self.assertEqual(len(self.server.calls), 5)
        self.assertNotIn('parent_hash', self.server.calls[3][2])
        with self.assertRaises(FileExistsError):
            self.run_recreation()
        self.assertEqual(len(self.server.calls), 5)
    def test_drift_blocks_receipt_and_retry(self):
        self.server.drift = True
        with self.assertRaises(ValueError):
            self.run_recreation()
        self.assertFalse(Path(str(self.journal) + '.receipt.json').exists())
        with self.assertRaises(FileExistsError):
            self.run_recreation()
    def test_existing_workspace_never_modified(self):
        self.server.exists = True
        with self.assertRaises(ValueError):
            self.run_recreation()
        self.assertEqual(len(self.server.calls), 1)
    def test_missing_external_binding_stops_before_script(self):
        with self.assertRaises(ValueError):
            self.run_recreation(lambda target, reference: False)
        self.assertEqual(len(self.server.calls), 2)
    def test_invalid_pins_before_calls(self):
        self.bundle['source']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.run_recreation()
        self.assertEqual(self.server.calls, [])
        self.assertFalse(self.journal.exists())
    def test_preserved_targets_rejected(self):
        for target in [None, 'offmenu', 'admins', '../offmenu']:
            with self.assertRaises(ValueError):
                r.recreate(self.root, self.bundle, target, self.transport, lambda *args: True, self.journal)
        self.assertEqual(self.server.calls, [])
    def test_missing_artifact_and_path_escape(self):
        self.bundle['source'] = {'path': '../outside', 'sha256': '0' * 64}
        with self.assertRaises(ValueError):
            self.run_recreation()
        self.assertEqual(self.server.calls, [])
    def test_secret_exception_sanitized(self):
        def bad(*args):
            raise RuntimeError('SECRET-SENTINEL')
        with self.assertRaises(ValueError) as e:
            self.run_recreation(bad)
        self.assertNotIn('SECRET-SENTINEL', str(e.exception))
        self.assertNotIn('SECRET-SENTINEL', self.journal.read_text())


if __name__ == '__main__':
    unittest.main()
