import copy
import concurrent.futures
import threading
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('windmill_room', ROOT / 'tools/windmill/white_room.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class ContractTests(unittest.TestCase):
    def test_checked_in_contract(self):
        config = w.validate()
        self.assertEqual(config['workspace']['id'], 'foo')
        self.assertIsNone(config['recreation_workspace']['id'])
        self.assertIsNone(w.plan()['recreation_workspace']['id'])
        self.assertEqual(config['target_transport']['endpoint'], 'https://windmill.aldous.info/api/mcp/w/foo/mcp')
        self.assertIsNone(config['credential']['reference'])
        self.assertFalse(config['live_recreation_verified'])
        self.assertEqual(len(w.load('dispositions.json')['items']), 236)

    def test_mutations_fail_closed(self):
        for file, change in [
            ('desired.json', lambda x: x['target_transport'].update(workspace='offmenu')),
            ('desired.json', lambda x: x['recreation_workspace'].update(id='foo')),
            ('desired.json', lambda x: x['source'].update(mock_sha256='0' * 64)),
            ('desired.json', lambda x: x['schema'].update(sha256='0' * 64)),
            ('desired.json', lambda x: x['credential'].update(new_destination_authorized=True)),
            ('desired.json', lambda x: x.update(live_recreation_verified=True)),
            ('desired.json', lambda x: x['runner']['permissions'].append('admin')),
            ('desired.json', lambda x: x['dependencies']['external_packages'].append('unreviewed')),
            ('desired.json', lambda x: x.update(owner_repository='attacker/repo')),
            ('desired.json', lambda x: x.update(blockers=[])),
            ('dispositions.json', lambda x: x['archive_overlay'].update(hash='', source='', verified='')),
            ('dispositions.json', lambda x: x['items'].pop()),
            ('dispositions.json', lambda x: x['items'].append(x['items'][0])),
            ('dispositions.json', lambda x: x['items'][0].update(delete_authorized=True)),
        ]:
            original = w.load
            data = copy.deepcopy(original(file))
            change(data)
            with self.subTest(file=file), patch.object(w, 'load', side_effect=lambda n: data if n == file else original(n)):
                with self.assertRaises(ValueError):
                    w.validate()

    def test_paired_inventory_omission_fails(self):
        original = w.load
        observed = copy.deepcopy(original('observed-2026-11-09.json'))
        dispositions = copy.deepcopy(original('dispositions.json'))
        removed = observed['scripts'].pop()
        dispositions['items'] = [x for x in dispositions['items'] if (x['type'], x['path']) != (removed['type'], removed['path'])]
        altered = {'observed-2026-11-09.json': observed, 'dispositions.json': dispositions}
        with patch.object(w, 'load', side_effect=lambda n: altered[n] if n in altered else original(n)):
            with self.assertRaises(ValueError):
                w.validate()

    def test_request_validation(self):
        for r in [None, {}, {'action': 'delete', 'job_id': 'x'},
                  {'action': 'submit', 'job_id': '../x', 'probe': 'identity'},
                  {'action': 'submit', 'job_id': 'x', 'probe': 'shell'},
                  {'action': 'status', 'job_id': 'x', 'credential': 'secret'},
                  {'action': 'status', 'job_id': True}]:
            with self.subTest(r=r), self.assertRaises(ValueError):
                w.validate_request(r)

    def test_recreate_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'test-workspace'
            self.assertEqual(w.recreate_mock(target)['state'], 'succeeded')
            with self.assertRaises(FileExistsError):
                w.recreate_mock(target)

    def test_plan_never_mutates(self):
        self.assertEqual(w.plan()['runtime_mutations'], [])
        self.assertFalse(w.plan()['live_recreation_verified'])

    def test_durable_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'jobs.sqlite'
            t = w.MockSSHTransport()
            a = w.WhiteRoom(db, transport=t)
            request = {'action': 'submit', 'job_id': 'same-id', 'probe': 'identity'}
            first = a.handle(request)
            self.assertEqual(a.handle(request), first)
            a.close()
            b = w.WhiteRoom(db, transport=t)
            try:
                self.assertEqual(b.handle(request), first)
                self.assertEqual(b.handle({'action': 'status', 'job_id': 'same-id'}), first)
                self.assertEqual(t.calls, 1)
                self.assertEqual(b.handle({'action': 'status', 'job_id': 'absent'})['state'], 'unknown')
            finally:
                b.close()

    def test_concurrent_clients_admit_once(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'jobs.sqlite'
            initial = w.WhiteRoom(db)
            initial.close()
            transport = w.MockSSHTransport()
            barrier = threading.Barrier(2)
            def submit():
                room = w.WhiteRoom(db, transport=transport)
                try:
                    barrier.wait(timeout=5)
                    return room.handle({'action': 'submit', 'job_id': 'race', 'probe': 'identity'})
                finally:
                    room.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: submit(), range(2)))
            self.assertEqual(transport.calls, 1)
            self.assertTrue(all(r['state'] in ('uncertain', 'succeeded') for r in results))
            room = w.WhiteRoom(db)
            try:
                self.assertEqual(room.handle({'action': 'status', 'job_id': 'race'})['state'], 'succeeded')
            finally:
                room.close()

    def test_failure_no_secret_output_or_replay(self):
        class BadTransport:
            def identity(self, credential):
                raise RuntimeError('SECRET-SENTINEL')
        with tempfile.TemporaryDirectory() as d:
            a = w.WhiteRoom(Path(d) / 'jobs.sqlite', transport=BadTransport())
            request = {'action': 'submit', 'job_id': 'x', 'probe': 'identity'}
            try:
                with self.assertRaises(ValueError) as e:
                    a.handle(request)
                self.assertNotIn('SECRET-SENTINEL', str(e.exception))
                self.assertEqual(a.handle(request)['state'], 'uncertain')
                self.assertNotIn('SECRET-SENTINEL', json.dumps(a.handle(request)))
            finally:
                a.close()

    def test_wrong_identity_never_succeeds(self):
        class Wrong:
            def identity(self, credential):
                return {'host': 'other', 'protocol': 'foo-root-exec-v1', 'state': 'succeeded'}
        with tempfile.TemporaryDirectory() as d:
            a = w.WhiteRoom(Path(d) / 'jobs.sqlite', transport=Wrong())
            try:
                with self.assertRaises(ValueError):
                    a.handle({'action': 'submit', 'job_id': 'x', 'probe': 'identity'})
                self.assertEqual(a.handle({'action': 'status', 'job_id': 'x'})['state'], 'uncertain')
            finally:
                a.close()


if __name__ == '__main__':
    unittest.main()
