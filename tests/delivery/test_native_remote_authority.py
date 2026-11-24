"""Changed contracts of the native Cloudflare authority composition, offline."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
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
                                                       install_sinks, workspace_recovery_digest)
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
        receipt=self.c.publisher_handoff(self.path,self.issue,accepted)
        import json
        data=json.loads(receipt.read_text())
        self.assertEqual((data['parent'],data['tree']),(self.base,tree))
        bundle=receipt.parent / data['bundle']
        self.assertEqual(sha256(bundle.read_bytes()).hexdigest(),data['bundle_sha256'])
        self.assertIn(head,self.git('bundle','list-heads',str(bundle)))
        self.assertEqual(self.git('branch','--show-current'),'review-fixture')

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

    def test_completed_native_callback_preserves_success_after_receiving_failure(self):
        class Native:
            async def _reconcile(self):
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
        attempt = SimpleNamespace(state_name='independent-review',
                                  settlement_record={'phase': 'original', 'target': 'publish', 'run': 1})
        self.assertIs(asyncio.run(native._resume_settlement(self.issue, attempt)), True)
        self.assertEqual(attempt.settlement_record['phase'], 'complete')
        self.c.send_handoff.assert_awaited_once()
        self.c.record_settled.assert_not_called()

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
        self.c.send_handoff = AsyncMock(side_effect=[Hold('receiver_unavailable'),exported,exported])
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
