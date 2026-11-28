"""Changed contracts of the native Cloudflare authority composition, offline."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uif.effect_authority import (AuthorityIdentity, ClaimFence, Completion, Lease, OriginalEffect,
                                  ReleaseReceipt, ReleaseRequest, RenewRequest, Scope, SourcePin,
                                  TerminalEffect, transition_request_sha256)
from uif.handoff_store import Hold, digest
from uif.quota_handoff import LinearKey
from uif.stokowski_cloudflare.account_admission import FreeRoute
from uif.stokowski_cloudflare.client import GLM_MODEL
from uif.stokowski_cloudflare.native_composition import (NativeComposition, _COMMENT_SLOTS, _LinearClient,
                                                       install_sinks, load_guarded_startup,
                                                       workspace_recovery_digest)
from uif.stokowski_cloudflare.remote_authority import AccessTransport, NativeAssignment, RemoteAuthority, _origin
from uif.stokowski_cloudflare.shared_admission import NativeClaim

OWNER = AuthorityIdentity('realm', 'a'*64, 'b'*64, 'native-session')
QUOTA = AuthorityIdentity('quota', 'a'*64, 'b'*64, 'quota-session')
SCOPE = Scope('repository-1', LinearKey(*(f'00000000-0000-4000-8000-{n:012d}' for n in (1,2,3))))
SOURCE = SourcePin('maldous/offmenu', 'develop', '1'*40, '2'*40, '3'*40)
GRANT = NativeAssignment(SCOPE, SOURCE, '4'*64, '5'*64, 1, ('assess', 'investigate', 'review'))
NOW = datetime(2030, 1, 11, 12, tzinfo=timezone.utc)
ROUTE = FreeRoute('c'*32, GLM_MODEL, 'fixture', '6'*64)
SECOND = replace(ROUTE, account_id='d'*32)
FENCE = ClaimFence(SCOPE,SOURCE,OWNER.session_id,1,1,'lease',OWNER.authority_id,OWNER.deployment_sha256,OWNER.policy_sha256)
RECORD = NativeClaim('OffMenu','assess',Lease(FENCE,int(NOW.timestamp())+200))
ISSUE = SCOPE.key.issue_id

class Worker:
    def __init__(self):
        self.calls = []
        self.original, self.claim, self.completion = None, None, None
        self.effects = {}

    def request(self, path, data=None, **options):
        self.calls.append((path, data, options))
        if path == '/v1/status':
            return dict(asdict(OWNER), stopped=False, role='native', coordinator_epoch=1,
                        lease_seconds=300, quota_issuer=asdict(QUOTA))
        if path == '/v1/claim/read':
            return asdict(self.claim) if self.claim else None
        if path == '/v1/claim/observe':
            return self.original
        if path == '/v1/claim':
            self.claim = RECORD
            request = GRANT.claim(OWNER)
            self.original = dict(request_id=data['request_id'], request_kind=request.kind,
                         request_sha256=transition_request_sha256(request),lease=asdict(RECORD.lease))
            return self.original
        if path == '/v1/claim/attempt':
            if data['expected_attempt_id'] != self.claim.attempt_id:
                raise Hold('stale_attempt')
            self.claim = replace(self.claim, attempt_id=data['attempt_id'])
            return asdict(self.claim)
        if path == '/v1/renew':
            request = RenewRequest(OWNER,self.claim.lease,data['request_id'])
            self.claim = replace(self.claim, lease=replace(self.claim.lease, expires_at=self.claim.lease.expires_at+1))
            return dict(request_id=request.request_id,request_kind=request.kind,
                request_sha256=transition_request_sha256(request),lease=asdict(self.claim.lease))
        if path == '/v1/usage/read':
            return dict(issuer=asdict(QUOTA), receipt_sha256='7'*64,source_receipt_sha256='8'*64,
                        admission_binding_sha256='9'*64,projection=dict(route=data['route'],utc_day='2030-01-11',
                            covered_through=int(NOW.timestamp()),valid_until=int(NOW.timestamp())+200,
                            upper=1234,gap_upper=2400,completeness_verified=True,free_standard_no_fallback=True))
        if path == '/v1/startup/read':
            return dict(data,**asdict(OWNER),coordinator_epoch=1,expires_at=int(NOW.timestamp())+5)
        if path == '/v1/completion/read':
            return asdict(self.completion) if self.completion else None
        if path == '/v1/release':
            req = ReleaseRequest(OWNER,self.completion,data['request_id'])
            return asdict(ReleaseReceipt(req.kind,req.request_id,transition_request_sha256(req),self.completion))
        if path == '/v1/linear/receipt':
            previous = self.effects.get(data['operation']['original_id'])
            if previous:
                if previous[0] != data['operation']:
                    raise Hold('different_original')
                return previous[1]
            return None
        if path in ('/v1/linear/comment','/v1/linear/upload'):
            result = {'receipt_sha256':'f'*64}
            if path.endswith('comment'):
                result['id'] = '10000000-0000-4000-8000-000000000001'
            else:
                result['asset_url'] = 'https://uploads.linear.app/example/one'
            self.effects[data['operation']['original_id']] = (data['operation'],result)
            return result
        if path == '/v1/original':
            return {'operation':data['operation'],'state':'terminal','terminal':{
                'operation':data['operation'],'status':'verified','receipt_sha256':'f'*64}}
        raise AssertionError(path)


def make():
    worker = Worker()
    remote = RemoteAuthority(worker,native=OWNER,quota_issuer=QUOTA,route=ROUTE,reviewer_route=SECOND,
            repository_id=SCOPE.repository_id,workspace_id=SCOPE.key.workspace_id,
            project_id=SCOPE.key.project_id,clock=lambda:NOW)
    return worker, remote


class NativeRemoteTest(unittest.TestCase):
    def test_optional_identity_pair_reuses_bound_native_status_only(self):
        from uif.service_identity_readback import IdentityReadbackExpectation
        from uif.service_identity_readback import IdentityReadbackUnavailable

        remote = object.__new__(RemoteAuthority)
        remote.identity, remote.quota_issuer = OWNER, QUOTA
        remote.transport = Mock()
        current = dict(asdict(OWNER), role="native", stopped=False, coordinator_epoch=1,
                       lease_seconds=30, quota_issuer=asdict(QUOTA),
                       installation_sha256="c"*64, commissioned_sha256="c"*64)
        remote.transport.request.return_value = current
        expected = IdentityReadbackExpectation("native", OWNER, 1, "c"*64, False)
        finish = remote.begin_identity_readback(expected)
        self.assertEqual(remote.transport.request.call_count, 1)
        self.assertEqual(finish(), dict(role="native", stopped=False, coordinator_epoch=1))
        self.assertEqual(remote.transport.request.call_count, 2)
        self.assertTrue(all(c.args == ("/v1/status",) for c in remote.transport.request.call_args_list))
        remote.transport.request.side_effect = Hold("redacted test sentinel")
        with self.assertRaisesRegex(IdentityReadbackUnavailable, "^same_runtime_role_read_unavailable$"):
            finish()

    def test_claim_original_fingerprint_and_attempt_cas(self):
        w,r=make()
        self.assertEqual(r.acquire(GRANT,'assess'),RECORD)
        self.assertEqual(r.acquire(GRANT,'assess'),RECORD)
        self.assertEqual(r.acquire(GRANT,'investigate').attempt_id,'investigate')
        self.assertEqual([p for p,_,_ in w.calls].count('/v1/claim'),1)
        call=next(d for p,d,_ in w.calls if p.endswith('/attempt'))
        self.assertEqual(call['expected_attempt_id'],'assess')
        self.assertNotIn('authority',next(d for p,d,_ in w.calls if p=='/v1/claim'))
        self.assertGreater(r.renew(w.claim).lease.expires_at,RECORD.lease.expires_at)

    def test_preexisting_unverified_reconstruction_and_changed_receipt_hold(self):
        w,r=make()
        w.claim=RECORD
        with self.assertRaisesRegex(Hold,'original_claim_unverified'):
            r.acquire(GRANT,'assess')
        w.claim=None
        r.acquire(GRANT,'assess')
        w.original['request_sha256']='9'*64
        with self.assertRaisesRegex(Hold,'request_binding'):
            r.acquire(GRANT,'investigate')
        self.assertFalse(any(p=='/v1/claim/attempt' for p,_,_ in w.calls))

    def test_verified_usage_per_account_and_short_deadline(self):
        _,r=make()
        c=NativeComposition(r,'/unused')
        w=c.usage(600)
        self.assertEqual(set(w.usage_snapshots),{'UIF','OffMenu'})
        self.assertEqual(w.request_not_after.timestamp(),NOW.timestamp()+90)
        c._running.set((GRANT,RECORD))
        c.current=Mock(return_value=RECORD)
        c.installation=lambda:{'assignments':{ISSUE:{'review_of':{'review':'investigate'}}}}
        self.assertEqual(set(c.usage(600).usage_snapshots),{'OffMenu'})
        c.current.return_value=replace(RECORD,attempt_id='review')
        self.assertEqual(set(c.usage(600).usage_snapshots),{'UIF'})

    def test_challenge_binds_same_request_object_and_deadline(self):
        w,r=make()
        c=NativeComposition(r,'/unused')
        c.installation=lambda:{'unit':'stokowski-foo.service','stage':'stage','coordinator_epoch':1}
        args=c.startup_arguments()
        deadline=time.monotonic_ns()+5_000_000_000
        req=('uif.stokowski.native-start/1',args['startup_binding'],deadline)
        receipt=args['startup_verifier'](req)
        self.assertIs(receipt[0],req)
        self.assertEqual(receipt[1],args['startup_binding'])
        self.assertLessEqual(receipt[3],deadline)
        self.assertGreater(receipt[3],time.monotonic_ns())
        self.assertTrue(w.calls[-1][1]['nonce'])
        with self.assertRaisesRegex(Hold,'start_challenge'):
            args['startup_verifier']((req[0],req[1],0))

    def test_idle_start_uses_only_authenticated_stopped_status_for_the_exact_installation(self):
        unit, stage = 'stokowski-foo.service', 'staged-idle'
        config = dict(schema='uif.stokowski-remote-idle/1', unit=unit, stage=stage,
                      origin='https://authority.example.test',
                      access_credential='/etc/fixture-access.json', native_identity=asdict(OWNER),
                      coordinator_epoch=1, hold_reason='free_quota_unavailable')
        target = '/protected/remote-authority.json'
        module = 'uif.stokowski_cloudflare.native_composition'
        with (
            patch(module + '.protected_record', return_value=config) as local,
            patch(module + '.AccessTransport') as make_transport,
            patch(module + '.load_composition') as normal,
        ):
            transport = make_transport.return_value
            readback = dict(asdict(OWNER), role='native', stopped=True, coordinator_epoch=1)
            transport.request.return_value = readback
            composition, args = load_guarded_startup(target, unit, stage)
            self.assertIsNone(composition)
            self.assertEqual(args['idle_reason'], config['hold_reason'])
            verify = args['startup_verifier']
            deadline = time.monotonic_ns() + 5_000_000_000
            req = ('uif.stokowski.native-start/1', args['startup_binding'], deadline)
            receipt = verify(req)
            self.assertIs(receipt[0], req)
            self.assertEqual(receipt[1:], (args['startup_binding'], 'stopped_idle', deadline))
            transport.request.assert_called_once_with('/v1/status', timeout=4)
            normal.assert_not_called()
            for drift in ({'stopped': False}, {'role': 'morgan'}, {'coordinator_epoch': 2},
                          {'policy_sha256': 'e'*64}):
                transport.request.return_value = readback | drift
                with self.assertRaisesRegex(Hold, 'idle_status_binding'):
                    verify(req)
            transport.request.reset_mock()
            with self.assertRaisesRegex(Hold, 'idle_challenge'):
                verify((req[0], req[1], 0))
            local.return_value = config | {'hold_reason': 'free_route_unavailable'}
            with self.assertRaisesRegex(Hold, 'idle_challenge'):
                verify(req)
            transport.request.assert_not_called()
            with self.assertRaisesRegex(Hold, 'idle_installation'):
                load_guarded_startup(target, unit, 'wrong-stage')
            local.return_value = {'schema': 'uif.stokowski-remote-authority/1'}
            composition, args = load_guarded_startup(target, unit, stage)
            normal.assert_called_once_with(target)
            self.assertIs(composition, normal.return_value)
            self.assertIs(args, composition.startup_arguments.return_value)

    def test_commissioned_authority_hold_retries_only_read_and_reports_no_effects(self):
        from uif.stokowski_cloudflare import launcher
        from unittest.mock import patch
        verify = Mock(side_effect=[False, False, True])
        with patch.object(launcher, '_idle_notify') as notify, patch.object(launcher.time, 'sleep') as sleep:
            launcher.wait_for_normal_authority(verify)
        self.assertEqual(verify.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        first, last = [call.args[0] for call in notify.call_args_list]
        self.assertIn('authority_unavailable; admissions and effects closed', first)
        self.assertNotIn('READY=1', first)
        self.assertIn('checking normal admission', last)

    def test_managed_idle_reports_hold_to_supervisor_and_stops_on_sigterm(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(
                socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            address = str(Path(directory) / 'notify')
            notify.bind(address)
            notify.settimeout(5)
            env = os.environ | {'NOTIFY_SOCKET': address,
                                'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}
            command = [sys.executable, '-c',
                       'from uif.stokowski_cloudflare.launcher import serve_supervised_idle; '
                       'serve_supervised_idle("free_route_unavailable")']
            with subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as process:
                try:
                    ready = notify.recv(512).decode()
                    self.assertEqual(ready, 'READY=1\nSTATUS=Stokowski idle: free_route_unavailable; '
                                            'admissions and effects closed')
                    self.assertIsNone(process.poll())
                    process.terminate()
                    self.assertEqual(notify.recv(512).decode(),
                                     'STOPPING=1\nSTATUS=Stokowski idle shutdown')
                    out, err = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, err)
                    self.assertIn('Stokowski idle shutdown', out)
                finally:
                    if process.poll() is None:
                        process.kill()

    def test_linear_reuses_verified_original_and_conflicting_content_holds(self):
        w,r=make()
        c=NativeComposition(r,'/unused')
        c.current=lambda issue:RECORD
        c._slot.set('_publish_run_report')
        c._running.set((GRANT, RECORD))
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent)
            (path / '.git').mkdir()
            c.installation = lambda: {'assignments': {ISSUE: {'workspace_binding': {'path': str(path)}}}}
            self.check_linear_receiving(c, w)

    def check_linear_receiving(self, c, w):
        self.assertTrue(c.linear('comment', ISSUE, {'body':'review'}))
        self.assertTrue(c.linear('comment', ISSUE, {'body':'review'}))
        with self.assertRaisesRegex(Hold,'different_original_payload'):
            c.linear('comment', ISSUE, {'body':'changed'})
        self.assertEqual([p for p,_,_ in w.calls].count('/v1/linear/comment'),1)
        self.assertEqual(c.upload('sum.txt','text/plain',b'abc'),'https://uploads.linear.app/example/one')
        self.assertEqual(c.upload('sum.txt','text/plain',b'abc'),'https://uploads.linear.app/example/one')
        self.assertEqual([p for p,_,_ in w.calls].count('/v1/linear/upload'),1)
        self.assertFalse(asyncio.run(_LinearClient(None,c).update_issue_state(ISSUE,'Done')))

    def test_stage_report_observation_uses_exact_original_without_sending(self):
        worker, remote = make()
        composition = NativeComposition(remote, '/unused')
        composition._slot.set('_publish_run_report')
        composition.current = lambda issue_id: RECORD
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent)
            (path / '.git').mkdir()
            composition.installation = lambda: {'assignments': {ISSUE: {'workspace_binding': {'path': str(path)}}}}
            self.assertTrue(composition.linear('comment', ISSUE, {'body': 'report'}))
        sent_op, result = next(iter(worker.effects.values()))
        worker.calls.clear()
        comment_id, terminal = composition.observe_stage_report(FENCE, 'assess', 'report')
        self.assertEqual(comment_id, result['id'])
        self.assertEqual(asdict(terminal.operation), sent_op)
        self.assertEqual(terminal.status, 'verified')
        self.assertEqual(terminal.receipt_sha256, result['receipt_sha256'])
        self.assertIsNone(composition.observe_stage_report(FENCE, 'different-attempt', 'report'))
        with self.assertRaisesRegex(Hold, 'different_original'):
            composition.observe_stage_report(FENCE, 'assess', 'report\n')
        with self.assertRaisesRegex(Hold, 'different_original'):
            composition.observe_stage_report(replace(FENCE, lease_id='different'), 'assess', 'report')
        result['receipt_sha256'] = 'a'*64
        with self.assertRaisesRegex(Hold, 'authority_linear_effect_unsettled'):
            composition.observe_stage_report(FENCE, 'assess', 'report')
        self.assertEqual({path for path, _, _ in worker.calls}, {'/v1/linear/receipt', '/v1/original'})

    def test_release_requires_original_independent_completion(self):
        w,r=make()
        self.assertIsNone(r.release_verified(RECORD.lease))
        self.assertFalse(any(p=='/v1/release' for p,_,_ in w.calls))
        w.completion=Completion(FENCE,True,'a'*64)
        self.assertEqual(r.release_verified(RECORD.lease).completion,w.completion)
        w.completion=Completion(replace(FENCE,lease_id='another'),True,'a'*64)
        with self.assertRaisesRegex(Hold,'completion_changed'):
            r.release_verified(RECORD.lease)

    def test_proxy_404_or_redirect_never_proves_absence(self):
        for value in ('http://example.com','https://example.com:443','https://u@example.com','https://example.com/v1'):
            with self.assertRaises(Hold):
                _origin(value)
        http=Mock()
        def reply(code, raw):
            response=Mock(status=code)
            response.getheader.return_value='application/json'
            response.read.return_value=raw
            http.return_value.getresponse.return_value=response
        with patch('uif.stokowski_cloudflare.remote_authority.HTTPSConnection',http), \
             patch('uif.stokowski_cloudflare.remote_authority.protected_record',return_value={
                'client_id':'fixture','client_secret':'fixture-value'}):
            transport=AccessTransport('https://authority.example','/unused')
            for code,obj in ((302,b'{}'),(404,b'{"error":"unknown_route"}')):
                reply(code,obj)
                with self.assertRaises(Hold):
                    transport.request('/v1/claim/read',{},allow_absent=True)
            reply(404,b'{"error":"claim_absent"}')
            self.assertIsNone(transport.request('/v1/claim/read',{},allow_absent=True))
            self.assertEqual(http.call_count,3)

    def test_usage_cooldown_preserves_valid_http_hints_without_retrying_any_request(self):
        from uif.provider_discovery import ProviderReadCooldown

        with patch('uif.stokowski_cloudflare.remote_authority.HTTPSConnection') as http, \
             patch('uif.stokowski_cloudflare.remote_authority.protected_record', return_value={
                 'client_id': 'fixture', 'client_secret': 'fixture-value'}), \
             patch('uif.stokowski_cloudflare.remote_authority.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 28, 10, tzinfo=timezone.utc)
            reply = http.return_value.getresponse.return_value
            transport = AccessTransport('https://authority.example', '/unused')
            for status, header, seconds in ((429, '600', 600),
                    (503, 'Sat, 28 Nov 2026 10:02:00 GMT', 120)):
                reply.status, reply.getheader.return_value = status, header
                with self.assertRaises(ProviderReadCooldown) as cooldown:
                    transport.request('/v1/usage/read', {})
                self.assertEqual(cooldown.exception.delay_seconds, seconds)
            reply.status, reply.getheader.return_value = 429, '1, 300'
            with self.assertRaisesRegex(Hold, 'authority_http_429'):
                transport.request('/v1/usage/read', {})
            reply.getheader.return_value = '600'
            with self.assertRaisesRegex(Hold, 'authority_http_429'):
                transport.request('/v1/send', {}, provider=True)
            self.assertEqual(http.return_value.request.call_count, 4)
            reply.read.assert_not_called()

    def test_raw_recovery_digest_detects_untracked_retained_and_index_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'.git').mkdir()
            (p/'.git/index').write_bytes(b'index')
            (p/'.stokowski').mkdir()
            (p/'.stokowski/settlement.json').write_bytes(b'{}')
            a=workspace_recovery_digest(p)
            (p/'.stokowski/settlement.json').write_bytes(b'{"original":2}')
            b=workspace_recovery_digest(p)
            (p/'untracked').write_bytes(b'extra')
            c=workspace_recovery_digest(p)
            (p/'.git/index').write_bytes(b'another-index')
            self.assertEqual(len({a,b,c,workspace_recovery_digest(p)}),4)

class RealCheckoutTest(unittest.TestCase):
    def setUp(self):
        import subprocess
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'UIF-1'
        self.path.mkdir(mode=0o700)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.path), *args], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        self.git = git
        git('init', '-b', 'develop')
        git('config','user.email','fixture@example.invalid')
        git('config','user.name','Fixture')
        git('remote','add','origin','https://github.com/maldous/offmenu.git')
        (self.path/'guide.md').write_text('original\n')
        git('add','guide.md')
        git('commit','-m','fixture original')
        self.base = git('rev-parse','HEAD')
        self.grant = replace(GRANT, source=SourcePin('maldous/offmenu','develop',self.base,self.base,
                                                    git('rev-parse','HEAD^{tree}')))
        self.worker, self.r=make()
        self.c=NativeComposition(self.r,'/unused')
        self.issue=SimpleNamespace(id=ISSUE,identifier=self.path.name)
        self.owner=SimpleNamespace(project_name='OffMenu',running={},
                    cfg=SimpleNamespace(workspace=SimpleNamespace(resolved_root=lambda:self.path.parent)))
        st=self.path.stat()
        self.item={'workspace_binding':{'path':str(self.path),'device':st.st_dev,'inode':st.st_ino,
                    'original_claim_id':self.grant.claim(OWNER).request_id},
                    'git_config_sha256':sha256((self.path/'.git/config').read_bytes()).hexdigest(),
                    'prepared_tree_sha256':workspace_recovery_digest(self.path),
                    'publisher':'offmenu-cloud','publisher_target':{'repository':'maldous/offmenu','base':'develop'}}
        self.c.installation=lambda:{'assignments':{ISSUE:self.item},'workspace_root':str(self.path.parent)}

    def test_clean_workspace_observation_matches_runtime_without_issuing_authority(self):
        from uif.stokowski_cloudflare.native_composition import observe_clean_assignment_workspace

        path, source = self.path, self.grant.source
        config = self.item['git_config_sha256']
        before = workspace_recovery_digest(path)
        observed = observe_clean_assignment_workspace(path, source, config)
        self.assertEqual(observed, {**{k: self.item['workspace_binding'][k]
                                      for k in ('path', 'device', 'inode')},
            'git_config_sha256': config, 'prepared_tree_sha256': before})
        self.assertEqual(workspace_recovery_digest(path), before)
        self.assertEqual(self.worker.calls, [])
        with self.assertRaisesRegex(Hold, 'private_git_config'):
            observe_clean_assignment_workspace(path, source, 'e'*64)
        (path / 'unexpected').write_text('unexpected')
        with self.assertRaisesRegex(Hold, 'dirty_workspace'):
            observe_clean_assignment_workspace(path, source, config)
        (path / 'unexpected').unlink()
        self.git('update-index', '--assume-unchanged', 'guide.md')
        (path / 'guide.md').write_text('hidden mutation')
        with self.assertRaisesRegex(Hold, 'source_tree_bytes'):
            observe_clean_assignment_workspace(path, source, config)

    def test_workspace_observation_rejects_config_drift_and_ignores_ambient_git(self):
        from uif.stokowski_cloudflare import native_composition as module

        path, source = self.path, self.grant.source
        config = self.item['git_config_sha256']
        with patch.dict(os.environ, {'GIT_DIR': '/nonexistent', 'GIT_WORK_TREE': '/nonexistent'}):
            observed = module.observe_clean_assignment_workspace(path, source, config)
        self.assertEqual(observed['git_config_sha256'], config)

        real_inventory = module.workspace_recovery_digest
        def observe_then_mutate(candidate):
            result = real_inventory(candidate)
            with (candidate / '.git/config').open('a') as out:
                out.write('# changed between checks\n')
            return result

        with patch.object(module, 'workspace_recovery_digest', side_effect=observe_then_mutate):
            with self.assertRaisesRegex(Hold, 'private_git_config'):
                module.observe_clean_assignment_workspace(path, source, config)

    def test_clean_raw_tree_and_hidden_tracked_change(self):
        self.c.verify_workspace(self.owner,self.issue,self.grant)
        self.c._first_workspace(self.path,self.grant,None)
        self.git('update-index','--assume-unchanged','guide.md')
        (self.path/'guide.md').write_text('untracked-by-status\n')
        self.assertEqual(self.git('status','--porcelain'),'')
        with self.assertRaisesRegex(Hold,'tree_bytes'):
            self.c._first_workspace(self.path,self.grant,None)

    def test_author_baseline_review_child_and_single_commit_publisher_handoff(self):
        self.git('switch','-c','review-fixture')
        (self.path/'guide.md').write_text('reviewed\n')
        self.git('add','guide.md')
        self.git('commit','-m','reviewed candidate')
        head=self.git('rev-parse','HEAD')
        tree=self.git('rev-parse','HEAD^{tree}')
        with self.assertRaisesRegex(Hold,'source_changed'):
            self.c.verify_workspace(self.owner,self.issue,self.grant)
        self.c.verify_workspace(self.owner,self.issue,self.grant,review_postimage=(head,tree,'review-fixture'))
        claim=NativeClaim('OffMenu','review',Lease(replace(FENCE,source=self.grant.source),RECORD.lease.expires_at))
        self.c._running.set((self.grant,claim))
        self.c.current=lambda _:claim
        accepted={'binding':{'repo':'maldous/offmenu','base':'develop','kind':'git','scope_sha256':self.grant.scope_sha256,
                         'issue_id':ISSUE,'candidate':head,'branch':'review-fixture'},
                  'accepted_review':{'original':'accepted example'}}
        self.c.assignment = lambda *args: self.grant
        self.worker.claim = claim
        original = self.c.publisher_handoff(self.path, self.issue, accepted)
        receipt = original.path
        observed = self.c.observe_owner_candidate(self.path, self.issue, head, original)
        self.assertEqual(observed.raw, receipt.read_bytes())
        self.assertFalse(observed.raw.endswith(b'\n'))
        self.assertEqual(observed.handoff_sha256, sha256(observed.raw).hexdigest())
        self.assertEqual(observed.fence, claim.lease.fence)
        self.assertEqual([path for path, _, _ in self.worker.calls], ['/v1/status', '/v1/claim/read'])
        self.worker.claim = replace(claim, lease=replace(claim.lease,
            fence=replace(claim.lease.fence, generation=2)))
        with self.assertRaisesRegex(Hold, 'original_lease_changed'):
            self.c.observe_owner_candidate(self.path, self.issue, head, original)
        self.worker.claim = claim
        receipt.write_bytes(observed.raw + b' ')
        with self.assertRaisesRegex(Hold, 'receipt_bytes_changed'):
            self.c.observe_owner_candidate(self.path, self.issue, head, original)
        receipt.write_bytes(observed.raw)
        import json
        data=json.loads(receipt.read_text())
        self.assertEqual((data['parent'],data['tree']),(self.base,tree))
        bundle=receipt.parent / data['bundle']
        self.assertEqual(sha256(bundle.read_bytes()).hexdigest(),data['bundle_sha256'])
        self.assertIn(head,self.git('bundle','list-heads',str(bundle)))
        self.assertEqual(self.git('branch','--show-current'),'review-fixture')

    def test_real_git_handoff_uses_one_bounded_digest_for_note_and_outcome(self):
        self.git('switch', '-c', 'digest-fixture')
        (self.path/'guide.md').write_text('reviewed candidate\n')
        self.git('add', 'guide.md')
        self.git('commit', '-m', 'retained candidate')
        head = self.git('rev-parse', 'HEAD')
        claim = NativeClaim('OffMenu', 'review',
            Lease(replace(FENCE, source=self.grant.source), RECORD.lease.expires_at))
        self.c.current = lambda _: claim
        self.c._running.set((self.grant, claim))
        accepted = {'binding': {'repo': 'maldous/offmenu', 'base': 'develop', 'kind': 'git',
                    'scope_sha256': self.grant.scope_sha256, 'issue_id': ISSUE,
                    'candidate': head, 'branch': 'digest-fixture'},
                    'accepted_review': {'original': 'accepted fixture'}}
        note = Mock(return_value=True)
        self.c.linear = note  # Existing receipt/fence pathway tested separately.
        result = asyncio.run(self.c.send_handoff(self.path, self.issue, accepted))
        receipt = self.path/'.git/uif-owner-handoffs'/(head + '.json')
        self.assertEqual(result, sha256(receipt.read_bytes()).hexdigest())
        self.assertIn('SHA-256 ' + result, note.call_args.args[2]['body'])
        self.assertRegex(result, '^[a-f0-9]{64}$')
        receipt.chmod(0o644)
        note.reset_mock()
        with self.assertRaisesRegex(Hold, 'receipt_custody'):
            asyncio.run(self.c.send_handoff(self.path, self.issue, accepted))
        note.assert_not_called()
        receipt.chmod(0o600)
        producer = self.c.publisher_handoff

        def replace_original(*args):
            original = producer(*args)
            alternate = receipt.with_suffix('.replacement')
            alternate.write_bytes(b'different same-owner regular bytes')
            alternate.chmod(0o600)
            alternate.replace(receipt)
            return original
        self.c.publisher_handoff = replace_original
        with self.assertRaisesRegex(Hold, 'handoff_bytes_changed'):
            asyncio.run(self.c.send_handoff(self.path, self.issue, accepted))
        note.assert_not_called()

        self.c.publisher_handoff = producer
        receipt.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(Hold, 'receipt_custody'):
            asyncio.run(self.c.send_handoff(self.path, self.issue, accepted))
        note.assert_not_called()

        self.c.publisher_handoff = lambda *args: receipt
        with self.assertRaisesRegex(Hold, 'receipt_target'):
            asyncio.run(self.c.send_handoff(self.path, self.issue, accepted))
        note.assert_not_called()

    def test_cold_original_review_settlement_uses_verified_git_child(self):
        self.git('switch', '-c', 'review-recovery')
        (self.path / 'guide.md').write_text('previously reviewed candidate\n')
        self.git('add', 'guide.md')
        self.git('commit', '-m', 'original review candidate')
        reviewer = 'review'
        receipt = {'issue_id': ISSUE, 'kind': 'git', 'state': 'independent-review', 'run': 1,
                   'scope_sha256': self.grant.scope_sha256, 'candidate': self.git('rev-parse', 'HEAD'),
                   'branch': 'review-recovery'}
        (self.path / '.stokowski').mkdir()
        (self.path / '.stokowski/review-binding.json').write_text(json.dumps(receipt))
        self.item['recovered_tree_sha256'] = workspace_recovery_digest(self.path)
        self.item['review_of'] = {reviewer: 'investigate'}
        claim = NativeClaim('OffMenu', reviewer, Lease(replace(FENCE, source=self.grant.source),
                                                       RECORD.lease.expires_at))
        candidate = {'issue_id': ISSUE, 'source': asdict(self.grant.source),
                     'scope_sha256': self.grant.scope_sha256, 'candidate_tree': self.git('rev-parse', 'HEAD^{tree}')}
        self.c.assignment = lambda *args: self.grant
        self.r.read_claim = lambda *args: claim
        self.r.original_lease = lambda *args: claim.lease
        self.r.transport.request = Mock(return_value={'fence': asdict(claim.lease.fence),
                        'candidate': candidate, 'candidate_sha256': digest(candidate)})
        attempt = SimpleNamespace(session_id=reviewer, state_name='independent-review',
                    settlement_record=dict(receipt, session_id=reviewer))
        self.owner.running = {ISSUE: attempt}
        self.owner._issue_state_runs = {ISSUE: 1}
        with self.c.settlement_context(self.owner, self.issue, attempt) as binding:
            self.assertEqual(binding, (self.grant, claim))
        self.r.transport.request.assert_called_once_with('/v1/review/read', {
            'current': asdict(claim.lease), 'author_attempt_id': 'investigate', 'review_attempt_id': reviewer})
        (self.path / 'guide.md').write_text('unrecognized postimage\n')
        with self.assertRaisesRegex(Hold, 'dirty_workspace_needs_original_reconstruction'):
            with self.c.settlement_context(self.owner, self.issue, attempt):
                self.fail('A modified candidate is unavailable for original settlement')

    def test_marked_assess_settlement_keeps_original_claim_for_investigation(self):
        from uif.stokowski_cloudflare.intake import intake_stages

        issue = SimpleNamespace(**vars(self.issue), title='Example', description='Bound work')
        stages = intake_stages({}, 'OffMenu', issue, self.grant.scope_sha256, 1)
        previous = stages['assess']['attempt']
        successor = stages['investigate']['attempt']
        grant = replace(self.grant, attempts=(previous, successor))
        request = grant.claim(OWNER)
        self.c.assignment = lambda *args: grant
        self.item['workspace_binding']['original_claim_id'] = request.request_id
        self.item['fresh_assessment'] = {'attempt_id': previous, 'first_intent_sha256': 'e' * 64}
        lease = replace(RECORD.lease, fence=replace(FENCE, source=grant.source))
        claim = NativeClaim('OffMenu', previous, lease)
        self.worker.claim = claim
        self.worker.original = {'request_id': request.request_id, 'request_kind': request.kind,
                                'request_sha256': transition_request_sha256(request), 'lease': asdict(lease)}

        class Native:
            async def _resume_settlement(owner, issue, attempt):
                attempt.settlement_record['phase'] = 'complete'
                return True

            def _settlement_path(owner, issue):
                return self.path

        for name in (*_COMMENT_SLOTS, '_reconcile'):
            setattr(Native, name, AsyncMock())
        install_sinks(SimpleNamespace(Orchestrator=Native, LinearClient=object, logger=Mock()), self.c)
        native = Native()
        native.project_name, native.cfg = self.owner.project_name, self.owner.cfg
        native._issue_state_runs = {ISSUE: 1}
        record = {'issue_id': ISSUE, 'run': 1, 'state': 'assess', 'session_id': previous,
                  'scope_sha256': grant.scope_sha256, 'report_posted': True, 'event': 'complete',
                  'target': 'investigate', 'phase': 'transition'}
        attempt = SimpleNamespace(state_name='assess', session_id=previous, settlement_record=record)
        native.running = {ISSUE: attempt}
        self.c._seen_fences[ISSUE] = lease.fence
        self.c._running.set((grant, claim))
        before = record.copy()
        self.assertFalse(asyncio.run(native._resume_settlement(issue, attempt)))
        self.assertEqual(record, before)
        record.update(phase='report-unknown', expected_report_body='exact original body')
        before = record.copy()
        with patch.object(self.c, 'observe_stage_report', return_value=('comment', object())) as observe:
            self.assertFalse(asyncio.run(native._resume_settlement(issue, attempt)))
        observe.assert_called_once_with(lease.fence, previous, 'exact original body')
        self.assertEqual(record, before)
        record['phase'] = 'complete'  # Previously settled native original.
        self.assertTrue(asyncio.run(native._resume_settlement(issue, attempt)))
        self.c._running.set(None)

        with self.assertRaisesRegex(Hold, 'marked_successor_first_entry_required'):
            with self.c.bind_dispatch(native, issue, grant.scope_sha256, stages['investigate'], stage='investigate'):
                self.fail('The generic binding must not consume a marked successor')
        paths = [path for path, _, _ in self.worker.calls]
        self.assertFalse(set(paths) & {'/v1/claim/attempt', '/v1/claim', '/v1/completion/read', '/v1/release'})

    def test_completed_native_callback_preserves_success_after_receiving_failure(self):
        class Native:
            async def _reconcile(self):
                order.append('native')
                return None

            async def _resume_settlement(self, issue, attempt):
                attempt.settlement_record['phase'] = 'complete'
                return True

            def _settlement_path(self, issue):
                return self.path

            def _states_for(self, issue):
                return {'publish': SimpleNamespace(publication='target')}

            def _delivery_authority(self, destination):
                return {'role': 'publish'}

            async def _accepted_publication(self, *args):
                return {'binding': {'kind': 'git'}}

        for name in _COMMENT_SLOTS:
            setattr(Native, name, AsyncMock())
        native = Native()
        native.path = self.path
        @contextmanager
        def current(*args):
            yield self.grant, RECORD
        self.c.settlement_context = current
        self.c.requires_publisher = Mock(return_value=True)
        self.c.send_handoff = AsyncMock(side_effect=Hold('receipt_not_yet_available'))
        self.c.record_settled = Mock()
        module = SimpleNamespace(Orchestrator=Native, LinearClient=object, logger=Mock())
        install_sinks(module, self.c)
        order = []
        async def receive(_):
            order.append('handoff')
        def late(_):
            order.append('release')
        self.c.receive_pending = receive
        self.c.receive_late_completions = late
        self.assertIsNone(asyncio.run(native._reconcile()))
        self.assertEqual(order, ['handoff', 'release', 'native'])
        order.clear()
        async def failed_receive(_):
            order.append('handoff')
            raise Hold('held handoff channel')
        self.c.receive_pending = failed_receive
        self.assertIsNone(asyncio.run(native._reconcile()))
        self.assertEqual(order, ['handoff', 'release', 'native'])
        module.logger.warning.assert_called_once_with('Native shared handoff observation unavailable')
        attempt = SimpleNamespace(state_name='independent-review',
                                  settlement_record={'phase': 'original', 'target': 'publish', 'run': 1})
        self.assertIs(asyncio.run(native._resume_settlement(self.issue, attempt)), True)
        self.assertEqual(attempt.settlement_record['phase'], 'complete')
        self.c.send_handoff.assert_awaited_once()
        self.c.record_settled.assert_not_called()

    def test_retained_completions_isolate_each_issues_custody_and_grant(self):
        candidate = self.path.resolve()
        first, second = 'earlier', 'later'
        assignment = {'scope_sha256': 'fixture', 'workspace_binding': {'path': str(candidate)}}
        config = {'workspace_root': str(candidate.parent), 'assignments': {
            first: assignment, second: assignment}}
        native = SimpleNamespace(project_name='OffMenu', running={})
        with patch.object(self.c, 'installation', return_value=config), \
                patch.object(self.c, 'assignment', side_effect=[Hold('held grant'), self.grant]), \
                patch.object(self.c, 'try_release') as release:
            self.c.receive_late_completions(native)
            release.assert_called_once_with(self.grant, candidate)
        config['assignments'][first] = dict(assignment, workspace_binding={
            'path': str(candidate.parent.parent / 'foreign')})
        with patch.object(self.c, 'installation', return_value=config), \
                patch.object(self.c, 'assignment', return_value=self.grant), \
                patch.object(self.c, 'try_release') as release:
            self.c.receive_late_completions(native)
            release.assert_called_once_with(self.grant, candidate)

    def test_cold_receiving_rejoins_original_and_requires_real_publisher_outcome(self):
        grant = self.grant
        claim = NativeClaim('OffMenu', 'review', Lease(replace(FENCE, source=grant.source), RECORD.lease.expires_at))
        self.item['review_of'] = {'review':'investigate'}
        self.item['scope_sha256'] = grant.scope_sha256
        self.c.assignment = lambda *args: grant
        self.c._running.set(None)
        self.r.read_claim = lambda *args: claim
        self.r.original_lease = lambda *args: claim.lease
        candidate = {'source':asdict(grant.source), 'issue_id':ISSUE, 'scope_sha256':grant.scope_sha256}
        fake = {'candidate':candidate,'candidate_sha256':digest(candidate),'fence':asdict(claim.lease.fence)}
        outcome = {}
        def receiver(path, data, **kwargs):
            if path == '/v1/review/read':
                return fake
            if path == '/v1/completion/read':
                return asdict(Completion(claim.lease.fence, True, digest(outcome))) if outcome else None
            raise AssertionError(path)
        self.r.transport.request = receiver
        native = self.owner
        native._issue_state_runs = {ISSUE:1}
        native.cfg.tracker = SimpleNamespace(project_slug='offmenu')
        native._ensure_linear_client = lambda: SimpleNamespace(fetch_issue=AsyncMock(return_value=self.issue))
        native._accepted_publication = AsyncMock(return_value={'binding':{'kind':'git'}})
        native._delivery_authority = lambda name: {'role':'publish'}
        exported = self.path / '.git/owner.fixture'
        exported.write_bytes(b'exact accepted candidate fixture')
        self.c.send_handoff = AsyncMock(side_effect=[Hold('receiver_unavailable'),sha256(exported.read_bytes()).hexdigest(), sha256(exported.read_bytes()).hexdigest()])
        self.c.record_settled = Mock()
        self.c.prepare_handoff(self.path, grant, claim, {'run':1}, 'publish')
        original = self.path / '.git/uif-original-effects' / (grant.claim(OWNER).request_id + '.receiving')
        self.assertTrue(original.is_file())
        asyncio.run(self.c.receive_pending(native))
        self.c.record_settled.assert_not_called()
        self.c._receiving_after.clear()
        asyncio.run(self.c.receive_pending(native))
        # A successful export still lacks the actual sole publisher's receipt.
        self.c.record_settled.assert_not_called()
        h = sha256(exported.read_bytes()).hexdigest()
        outcome.update(schema='uif.offmenu-publisher-outcome/1', publisher='offmenu-cloud',
                       handoff_sha256=h, fence=asdict(claim.lease.fence), publication_original_id='publisher-fixture',
                       repository=grant.source.repository, base=grant.source.branch, publication_receipt_sha256='e'*64)
        self.item['publisher_outcomes'] = {h:outcome}
        self.c._receiving_after.clear()
        asyncio.run(self.c.receive_pending(native))
        self.c.record_settled.assert_called_once_with(grant,claim,self.path)
        self.assertTrue(original.with_suffix('.received').is_file())
        self.assertIsNone(self.c._running.get())

        # Crash window: .received is durable but the original callback never
        # reaches the .settled write. A fresh composition must not re-present.
        received = original.with_suffix('.received')
        before = received.read_bytes()
        restarted = NativeComposition(self.r, '/unread-fixture')
        restarted.installation = self.c.installation
        restarted.assignment = lambda *a: grant
        restarted.send_handoff = AsyncMock(side_effect=AssertionError('must not re-present'))
        self.r.release_verified = Mock(return_value=None)
        received.write_bytes(before + b' ')
        asyncio.run(restarted.receive_pending(native))
        self.assertFalse(original.with_suffix('.settled').exists())
        self.r.release_verified.assert_not_called()
        received.write_bytes(before)
        restarted._receiving_after.clear()
        stale = replace(claim, lease=replace(claim.lease, fence=replace(claim.lease.fence,
                                                                      generation=claim.lease.fence.generation + 1)))
        self.r.read_claim = lambda *a: stale
        intent_before = original.read_bytes()
        asyncio.run(restarted.receive_pending(native))
        self.assertFalse(original.with_suffix('.settled').exists())
        self.assertEqual((original.read_bytes(), received.read_bytes()), (intent_before, before))
        self.r.release_verified.assert_not_called()
        self.r.read_claim = lambda *a: claim
        restarted._receiving_after.clear()
        asyncio.run(restarted.receive_pending(native))
        self.assertEqual((original.read_bytes(), received.read_bytes()), (intent_before, before))
        settled = original.with_suffix('.settled')
        retained_settlement = settled.read_bytes()
        restarted.send_handoff.assert_not_called()
        reconstructed = NativeComposition(self.r, '/unread-fixture')
        reconstructed.installation = self.c.installation
        reconstructed.assignment = lambda *a: grant
        reconstructed.send_handoff = restarted.send_handoff
        asyncio.run(reconstructed.reconcile_originals(native, Mock()))
        self.assertEqual(settled.read_bytes(), retained_settlement)
        reconstructed.send_handoff.assert_not_called()
        # Neither observation found a terminal release receipt; the later
        # tick may only reconcile the same original lease, never re-export.
        from unittest.mock import call
        self.assertEqual(self.r.release_verified.call_args_list, [call(claim.lease)] * 2)

    def test_release_ack_loss_recovers_same_authenticated_original_and_keeps_exact_receipt(self):
        from uif.handoff_store import wire
        from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable

        claim = self.grant.claim(OWNER)
        lease = replace(RECORD.lease, fence=replace(FENCE, source=self.grant.source))
        self.worker.original = dict(request_id=claim.request_id, request_kind=claim.kind,
            request_sha256=transition_request_sha256(claim), lease=asdict(lease))
        self.worker.completion = Completion(lease.fence, True, 'a'*64)
        journal = self.path / '.git/uif-original-effects'
        journal.mkdir(mode=0o700)
        original = journal / (claim.request_id + '.settled')
        original.write_bytes(wire(asdict(lease)))
        original.chmod(0o600)
        released = original.with_suffix('.released')
        self.item['source_overlays'] = {'fixture': True}
        orig_bytes = original.read_bytes()
        history, mutations = {}, []
        transport = self.worker.request

        def request(path, body, **kwargs):
            if path != '/v1/release':
                return transport(path, body, **kwargs)
            mutations.append(body)
            key = body['request_id']
            if key not in history:
                history[key] = (body, transport(path, body, **kwargs))
                raise AuthorityUnavailable('fixture-lost-release-ack')
            prior, reply = history[key]
            self.assertEqual(body, prior)
            return reply

        self.worker.request = request
        self.c.try_release(self.grant, self.path)
        self.assertFalse(mutations)  # Source publication is still held.
        self.c.prepare_handoff(self.path, self.grant,
            NativeClaim('OffMenu', self.grant.attempts[0], lease), {'run':1}, 'publish')
        received = original.with_suffix('.received')
        received.write_bytes(original.with_suffix('.receiving').read_bytes())
        received.chmod(0o600)
        with self.assertRaises(AuthorityUnavailable):
            self.c.try_release(self.grant, self.path)
        self.assertFalse(released.exists())
        received_before = received.read_bytes()
        reboot = NativeComposition(self.r, '/unused')
        reboot.installation = self.c.installation
        self.item['scope_sha256'] = self.grant.scope_sha256
        reboot.assignment = lambda project, issue, scope: self.grant
        native = SimpleNamespace(project_name='OffMenu', running={})
        asyncio.run(reboot.reconcile_originals(native, Mock()))
        self.assertEqual(len(history), 1)
        self.assertEqual(mutations[0], mutations[1])
        self.assertEqual(released.read_bytes(), wire(next(iter(history.values()))[1]))
        self.assertEqual(released.stat().st_mode & 0o777, 0o600)
        self.assertEqual(released.stat().st_nlink, 1)
        reboot.try_release(self.grant, self.path)
        self.assertEqual(len(mutations), 2)
        self.assertEqual((original.read_bytes(), received.read_bytes()), (orig_bytes, received_before))

    def test_release_receipt_collision_is_bounded_and_retains_original(self):
        from uif.handoff_store import wire

        claim = self.grant.claim(OWNER)
        lease = replace(RECORD.lease, fence=replace(FENCE, source=self.grant.source))
        self.worker.original = dict(request_id=claim.request_id, request_kind=claim.kind,
            request_sha256=transition_request_sha256(claim), lease=asdict(lease))
        self.worker.completion = Completion(lease.fence, True, 'a'*64)
        journal = self.path / '.git/uif-original-effects'
        journal.mkdir(mode=0o700)
        original = journal / (claim.request_id + '.settled')
        original.write_bytes(wire(asdict(lease)))
        released = original.with_suffix('.released')
        transport = self.worker.request
        oversized = b'x'*8193

        def request(path, body, **kwargs):
            reply = transport(path, body, **kwargs)
            if path == '/v1/release':
                released.write_bytes(oversized)
                released.chmod(0o600)
            return reply

        original_read = Path.read_bytes
        def refuse_unbounded(path):
            if path == released:
                raise AssertionError('must not use unbounded Path.read_bytes on release collision')
            return original_read(path)

        self.worker.request = request
        with patch.object(Path, 'read_bytes', refuse_unbounded):
            with self.assertRaisesRegex(Hold, 'authority_handoff_receipt_custody'):
                self.c.try_release(self.grant, self.path)
        self.assertEqual(released.read_bytes(), oversized)
        self.assertEqual(original.read_bytes(), wire(asdict(lease)))

    def test_original_provider_bytes_recovered_without_second_send(self):
        from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable, ProviderReply
        from uif.stokowski_cloudflare.shared_admission import SendIntent
        from uif.effect_authority import Operation
        op=Operation(FENCE,'d'*64,'e'*64)
        intent=SendIntent(op,ROUTE,'assess','1'*64,'2'*64,int(NOW.timestamp())+80,100)
        self.c._running.set((self.grant, RECORD))
        calls=[]
        def receiver(path,data,**kwargs):
            calls.append(path)
            if path=='/v1/send': raise AuthorityUnavailable('lost_ack')
            if path=='/v1/provider/receipt': return ProviderReply(b'result','original','f'*64)
            if path=='/v1/original':
                return asdict(OriginalEffect(op,'terminal',TerminalEffect(op,'verified','f'*64)))
            raise AssertionError(path)
        self.r.transport.request=receiver
        self.assertEqual(self.r.send(intent,b'body').read(),b'result')
        self.assertEqual(calls,['/v1/send','/v1/provider/receipt','/v1/original'])
        self.assertTrue((self.path/'.git/uif-original-effects'/(op.original_id+'.json')).is_file())

    def test_fresh_assessment_finisher_retains_only_original_provider_evidence(self):
        from uif.stokowski_cloudflare.intake import intake_stages
        from uif.stokowski_cloudflare.remote_authority import ProviderReply
        from uif.handoff_store import decode, wire
        record = {'scope_sha256': self.grant.scope_sha256}
        self.issue.title, self.issue.description = 'Fixture assessment', 'Bounded fixture scope'
        attempt = intake_stages({}, 'OffMenu', self.issue, self.grant.scope_sha256, 1)['assess']['attempt']
        record['attempt'] = attempt
        grant = replace(self.grant, attempts=(attempt, *self.grant.attempts[1:]))
        fence = replace(RECORD.lease.fence, source=grant.source)
        claim = replace(RECORD, attempt_id=attempt, lease=replace(RECORD.lease, fence=fence))
        self.c._running.set((grant, claim))
        self.c.assignment = lambda *args: grant
        self.item['workspace_binding']['original_claim_id'] = grant.claim(self.r.identity).request_id
        self.r.read_claim = Mock(return_value=claim)
        request_id = 'provider-original'
        directory = self.path / '.git/uif-original-effects'
        directory.mkdir(mode=0o700)
        operation = {'fence':asdict(fence),'original_id':request_id,'payload_sha256':'a'*64}
        original = directory / (request_id + '.json')
        original.write_bytes(wire({'operation':operation,'attempt_id':attempt,
                                    'route':asdict(self.r.routes['OffMenu'])}))
        original.chmod(0o600)
        from uif.stokowski_cloudflare.native_composition import digest
        evidence = directory/(digest({'assess':attempt})+'.assessment')
        raw = wire({'summary':'private fixture','verdict':'blocked',
                    'routing':{'issue':self.issue.identifier,'state':'assess','run':1}})
        body = wire({'choices':[{'message':{'content':raw.decode()}}]})
        self.r.provider_result = Mock(side_effect=lambda op: ProviderReply(body,None,'b'*64))
        callback = self.c.retain_assessment_result(self.issue, record, 1)
        with self.assertRaisesRegex(Hold,'provider_content'):
            callback(request_id, 'substituted')
        self.assertFalse(evidence.exists())
        stale = replace(claim, lease=replace(claim.lease, fence=replace(fence,generation=fence.generation+1)))
        self.r.read_claim.side_effect = [claim, stale]
        with self.assertRaisesRegex(Hold,'assessment_claim_changed'):
            callback(request_id, raw.decode())
        self.assertFalse(evidence.exists())
        self.r.read_claim.side_effect = None
        binding = self.item['workspace_binding'].copy()
        def replace_workspace_selection(op):
            self.item['workspace_binding'] = {**binding, 'path':str(self.path.parent/'substitute')}
            return ProviderReply(body,None,'b'*64)
        self.r.provider_result.side_effect = replace_workspace_selection
        with self.assertRaisesRegex(Hold,'assessment_workspace_binding'):
            callback(request_id, raw.decode())
        self.assertFalse(evidence.exists())
        self.item['workspace_binding'] = binding
        moved = directory.with_name('old-uif-original-effects')
        def replace_journal(op):
            directory.rename(moved)
            directory.mkdir(mode=0o700)
            original.write_bytes((moved/original.name).read_bytes())
            original.chmod(0o600)
            return ProviderReply(body,None,'b'*64)
        self.r.provider_result.side_effect = replace_journal
        with self.assertRaisesRegex(Hold,'assessment_provider_intent_changed'):
            callback(request_id, raw.decode())
        self.assertFalse(evidence.exists())
        self.assertFalse((moved/evidence.name).exists())
        original.unlink()
        directory.rmdir()
        moved.rename(directory)
        self.r.provider_result.side_effect = lambda op: ProviderReply(body,None,'b'*64)
        callback(request_id, raw.decode())
        before = evidence.stat().st_ino
        callback(request_id, raw.decode())
        receipt = decode(evidence.read_bytes())
        self.assertEqual(receipt['operation'], operation)
        self.assertEqual(receipt['receipt_sha256'], 'b'*64)
        self.assertEqual(receipt['report_sha256'],sha256(raw).hexdigest())
        self.assertEqual(receipt['provider_body_sha256'],sha256(body).hexdigest())
        self.assertEqual(evidence.stat().st_ino,before)
        self.assertNotIn(raw,evidence.read_bytes())
        self.assertEqual(self.worker.calls,[])

        # Fresh runtime handoff uses the ordinal-1 native provider original,
        # then existing comment and terminal receipt, not Attempt.result_text.
        from types import SimpleNamespace
        from uif.stokowski_cloudflare.fresh_assessment_render import render_fresh_assessment
        from uif.stokowski_cloudflare.native_composition import _FreshAssessmentExecution
        from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable
        execution = _FreshAssessmentExecution(claim, self.path)
        self.c._fresh_assessment.set(execution)
        self.c._slot.set('_publish_run_report')
        with self.assertRaisesRegex(Hold, 'fresh_provider_original'):
            callback(request_id, raw.decode())
        evidence.unlink()  # isolate this second, independently bound fixture
        first = sha256(f'{attempt}:1'.encode()).hexdigest()
        first_intent = directory / (first + '.json')
        first_intent.write_bytes(wire({'operation': operation | {'original_id': first},
                                      'attempt_id': attempt, 'route': asdict(self.r.routes['OffMenu'])}))
        first_intent.chmod(0o600)
        finish = self.c.fresh_assessment_finisher(self.issue, record, 1)
        state = SimpleNamespace(status='succeeded', session_id=attempt, state_name='assess',
                                result_text='untrusted fallback not used')
        with self.assertRaisesRegex(Hold, 'assessment_report_original'):
            self.c.verified_fresh_assessment_report(self.issue, state, self.path, 'assess')
        self.assertEqual(self.worker.calls, [])
        finish(first, raw.decode())
        expected = render_fresh_assessment(raw, issue=self.issue, run=1,
            scope_sha256=record['scope_sha256'], attempt_id=attempt)
        expected_op = self.c._linear_operation(fence, attempt, '_publish_run_report', 'comment',
                                              {'body': expected})
        proof = self.c.verified_fresh_assessment_report(self.issue, state, self.path, 'assess')
        from uif.stokowski_cloudflare.native import _native_report
        self.assertEqual(proof(self.issue.id, attempt, 1), (_native_report(raw.decode()), decode(raw), expected))
        with self.assertRaisesRegex(Hold, 'assessment_report_binding'):
            proof(self.issue.id, attempt, 2)
        with self.assertRaisesRegex(Hold, 'assessment_report_only_effect'):
            self.c.linear('comment', self.issue.id, {'body': 'substituted'})
        post = self.r.transport.request

        def lose_comment_ack(path, data=None, **opts):
            result = post(path, data, **opts)
            if path == '/v1/linear/comment':
                self.assertEqual(data, {'operation': asdict(expected_op), 'attempt_id': attempt,
                                        'body': expected})
                raise AuthorityUnavailable('fixture lost reply')
            return result

        self.r.transport.request = lose_comment_ack
        wrong = replace(claim, lease=replace(claim.lease, fence=replace(fence, generation=2)))
        self.r.read_claim.return_value = wrong
        with self.assertRaises(Hold):
            self.c.linear('comment', self.issue.id, {'body': expected})
        self.assertEqual(self.worker.calls, [])
        self.r.read_claim.return_value = claim
        self.assertTrue(self.c.linear('comment', self.issue.id, {'body': expected}))
        self.assertTrue(self.c.linear('comment', self.issue.id, {'body': expected}))
        self.assertEqual([call[0] for call in self.worker.calls].count('/v1/linear/comment'), 1)
        self.assertNotIn('/v1/linear/upload', [call[0] for call in self.worker.calls])
        self.assertNotIn('private fixture', expected)
