"""Original call identity, source CAS, review and receiver behavior, offline."""
from base64 import b64encode
from dataclasses import asdict
from hashlib import sha256
import tempfile
from pathlib import Path
import unittest

from uif.handoff_store import Hold, digest
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable
from uif.stokowski_cloudflare.remote_source import SourceTools, git_blob, pinned_members, review_diff
from uif.stokowski_cloudflare.turn import Attempt, run_turn

from test_native_remote_authority import ISSUE, RECORD, SOURCE


class Fixture:
    def __init__(self, members, originals):
        self.members, self.originals = members, originals
        self.data = dict(originals)
        self.revision = 0
        self.writes = 0
        self.records = {}
        self.sealed = False
        self.fail_after = True
        self.tool = ('replace_text', {})
        self.transport = self

    def request(self, path, data, **opts):
        action = path.rsplit('/', 1)[-1]
        if action == 'list':
            return {'paths': sorted(self.members), 'writable_paths': sorted(self.members), 'revision': self.revision,
                    'public_source_receipt_sha256': '1'*64, 'sealed': self.sealed}
        name = data.get('path')
        ref = {key: data.get(key) for key in ('attempt_id', 'provider_request_id', 'tool_call_index', 'tool_call_id')}
        key = digest({'kind': 'uif.source-tool-original/1', **ref})
        if action == 'receipt':
            return self.records.get(key)
        if action == 'read':
            raw = self.data[name]
            return {'path': name, 'revision': self.revision, 'mode': '100644' if raw is not None else None,
                    'body_base64': b64encode(raw).decode() if raw is not None else None,
                    'sha256': sha256(raw).hexdigest() if raw is not None else None}
        if action != 'write':
            raise AssertionError(action)
        op = data['operation']
        fields = {**ref, **{k: data[k] for k in ('path', 'expected_revision', 'preimage_sha256', 'postimage_sha256')}}
        assert op['fence'] == asdict(RECORD.lease.fence)
        assert op['original_id'] == key
        assert op['payload_sha256'] == digest({'kind': 'uif.native-source-write/1', **fields})
        assert self.revision == fields['expected_revision'] and not self.sealed
        from base64 import b64decode
        self.data[name] = b64decode(data['body_base64'], validate=True)
        assert sha256(self.data[name]).hexdigest() == data['postimage_sha256']
        self.revision += 1
        self.writes += 1
        tool_name, args = self.tool
        result = {'operation': op, 'path': name, 'sha256': data['postimage_sha256'], 'revision': self.revision,
                  'tool_name': tool_name, 'arguments_sha256': digest(args)}
        self.records[key] = dict(result, receipt_sha256=digest({'kind':'uif.native-source-receipt/1', **result}))
        if self.fail_after:
            raise AuthorityUnavailable('lost_response')
        return self.records[key]


class RemoteSourceTest(unittest.TestCase):
    def setUp(self):
        self.raw = b'line\n' * 23000 + b'feature\n'
        self.base = {'app.js': self.raw, 'new.js': None}
        self.members = {k: {'mode': None if v is None else '100644',
                            'base_sha256': None if v is None else sha256(v).hexdigest(),
                            'base_git_blob': None if v is None else git_blob(v), 'writable': True}
                        for k, v in self.base.items()}
        self.fixture = Fixture(self.members, self.base)
        self.tools = SourceTools(self.fixture, lambda issue: RECORD, ISSUE, RECORD.attempt_id,
                                 self.members, '1'*64)
        self.src = dict(public_source_receipt_sha256='1'*64, members=self.members)

    def test_large_source_and_originals_survive_lost_ack(self):
        self.assertEqual(self.members, pinned_members(self.src, ['app.js', 'new.js']))
        tools, executor = self.tools.tools()
        self.assertIn('read_range', {x['function']['name'] for x in tools})
        kwargs = dict(request_id='2'*64, ordinal=0, call_id='tool1')
        self.assertEqual(executor.execute_original('read_range', {'path':'app.js','offset':len(self.raw)-8}, 30, **kwargs),
                         'feature\n')
        args = {'path': 'app.js', 'old': 'feature', 'replacement': 'café'}
        self.fixture.tool = ('replace_text', args)
        self.assertIn('revision 1', executor.execute_original('replace_text', args, 30, **kwargs))
        self.assertIn('revision 1', executor.execute_original('replace_text', args, 30, **kwargs))
        with self.assertRaisesRegex(Hold, 'changed_source_tool_payload'):
            executor.execute_original('replace_text', dict(args, replacement='different'), 30, **kwargs)
        self.assertEqual(self.fixture.writes, 1)
        self.assertIn('café', self.fixture.data['app.js'].decode())
        self.assertEqual(self.base['app.js'], self.raw)
        new = {'path': 'new.js', 'content': 'new file\n'}
        self.fixture.tool = ('create_file', new)
        executor.execute_original('create_file', new, 30, **dict(kwargs, ordinal=1, call_id='tool2'))
        self.assertEqual(self.fixture.writes, 2)
        self.assertIsNone(self.base['new.js'])

    def test_frozen_diff_rejects_substituted_bytes_and_unselected_file(self):
        raw, change = self.raw, self.raw + b'changed\n'
        file = {'path':'app.js', 'mode':'100644', 'base_mode':'100644', 'base_git_blob': git_blob(raw),
                'preimage_sha256':sha256(raw).hexdigest(), 'base_base64':b64encode(raw).decode(),
                'postimage_sha256':sha256(change).hexdigest(), 'body_base64':b64encode(change).decode()}
        with self.subTest('verified'):
            self.assertIn('+changed', review_diff({'files':[file]}, self.members))
        with self.assertRaisesRegex(Hold, 'member_bytes'):
            review_diff({'files':[dict(file, body_base64=b64encode(b'changed-again').decode())]}, self.members)
        with self.assertRaisesRegex(Hold, 'candidate_scope'):
            review_diff({'files':[dict(file, path='other.js')]}, self.members)

    def test_runner_threads_original_request_and_call_index_to_receiver(self):
        args = {'path': 'new.js', 'content': 'ok'}
        self.fixture.tool = ('create_file', args)
        import json
        class Client:
            def __init__(self):
                self.ids = []
            def complete(self, **data):
                self.ids.append(data['request_id'])
                if len(self.ids) == 1:
                    return {'choices':[{'message': {'role':'assistant', 'tool_calls':[{'id':'one', 'type':'function',
                            'function':{'name':'create_file', 'arguments':json.dumps(args)}}]}}]}
                return {'choices':[{'message':{'role':'assistant','content':'complete'}}]}
        client = Client()
        tools, execute = self.tools.tools()
        accepted = []
        result = run_turn(client, Attempt('OffMenu', RECORD.attempt_id), 'test', tools=tools, execute_tool=execute,
                          active_projects=lambda:{'OffMenu'}, cancelled=lambda:False, timeout=30,
                          finish_original=lambda request, value: accepted.append((request,value)))
        self.assertEqual(result, 'complete')
        self.assertEqual(accepted, [(client.ids[1], result)])
        self.assertEqual(self.fixture.writes, 1)
        self.assertIn(digest({'kind':'uif.source-tool-original/1','attempt_id':RECORD.attempt_id,
                              'provider_request_id':client.ids[0],'tool_call_index':0,'tool_call_id':'one'}),
                      self.fixture.records)

    def test_original_checkpoint_then_review_compares_complete_frozen_bytes(self):
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        from dataclasses import replace
        from test_native_remote_authority import GRANT
        from types import SimpleNamespace
        author = dict(attempt='assess', write_roots=['app.js','new.js'], read_paths=[])
        c = NativeComposition(self.fixture, '/unused')
        c.installation = lambda: {'assignments':{ISSUE:{'source_overlays':{'assess':self.src}}}}
        c.current = lambda issue: replace(RECORD, attempt_id='review')
        self.fixture.revision = 1
        self.fixture.sealed = True
        changed = self.raw + b'author delta\n'
        file = {'path':'app.js', 'base_mode':'100644','mode':'100644','base_git_blob':git_blob(self.raw),
                'preimage_sha256':sha256(self.raw).hexdigest(), 'base_base64':b64encode(self.raw).decode(),
                'postimage_sha256':sha256(changed).hexdigest(),'body_base64':b64encode(changed).decode()}
        artifact = {'schema':'uif.source-candidate/1', 'source':asdict(SOURCE), 'fence':asdict(RECORD.lease.fence),
                    'author_attempt_id':'assess','revision':1,'public_source_receipt_sha256':'1'*64,'files':[file]}
        original = self.fixture.request
        def request(path,data,**options):
            if path.endswith('/checkpoint'):
                self.assertEqual(data['current'],asdict(RECORD.lease))
                self.assertEqual(data['attempt_id'],'assess')
                return {'candidate':artifact,'candidate_sha256':digest(artifact)}
            return original(path,data,**options)
        self.fixture.request = request
        record = c.prepare_source_review(ISSUE,author,{'prompt':'review exactly'})
        self.assertEqual(record['source_candidate_sha256'],digest(artifact))
        self.assertIn('+author delta',record['prompt'])
        self.assertNotIn('candidate_tree',record)
        file['postimage_sha256'] = '0'*64
        with self.assertRaisesRegex(Hold, 'member_bytes'):
            c.prepare_source_review(ISSUE,author,{'prompt':'review exactly'})
