"""Native first-entry and CAS restart boundaries using a real private checkout."""
import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
from threading import current_thread
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

import test_native_remote_authority as fixture
from uif.effect_authority import Operation, transition_request_sha256
from uif.handoff_store import Hold, digest, wire
from uif.stokowski_cloudflare.fresh_assessment_terminal import advance_marked_assessment
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.native_composition import _LinearClient
from uif.stokowski_cloudflare.remote_authority import AccessTransport, AuthorityUnavailable, ProviderReply
from uif.stokowski_cloudflare.shared_admission import SendIntent


class SuccessorEntryTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = case = fixture.RealCheckoutTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.issue, self.owner = case.issue, case.owner
        self.scope = case.grant.scope_sha256
        self.issue.title, self.issue.description = 'Fixture', 'Bounded investigation'
        stages = intake_stages({}, 'OffMenu', self.issue, self.scope, 1)
        self.previous, self.successor = [stages[k]['attempt'] for k in ('assess', 'investigate')]
        self.grant = replace(case.grant, attempts=(self.previous, self.successor), first_intent_sha256='7' * 64)
        case.item['fresh_assessment'] = {'attempt_id': self.previous, 'first_intent_sha256': '7' * 64}
        self.claim = replace(fixture.RECORD, attempt_id=self.previous, lease=replace(fixture.RECORD.lease,
            fence=replace(fixture.FENCE, source=self.grant.source)))
        req = self.grant.claim(fixture.OWNER)
        self.original = {'request_id':req.request_id, 'request_kind':req.kind,
                         'request_sha256':transition_request_sha256(req), 'lease':asdict(self.claim.lease)}
        self.native, self.remote = case.c, case.r
        self.native.assignment = lambda *args: self.grant
        self.native.installation = lambda: {'workspace_root':str(case.path.parent), 'coordinator_epoch':1,
                                          'assignments':{self.issue.id:case.item}}
        self.native._successor_native_worker_qualified = True
        self.native._successor_stage_qualification = lambda *_: True
        self.owner.pool = SimpleNamespace(is_paused=lambda _:False)
        self.owner._workflow_name_for = lambda _: 'offmenu'
        self.owner._issue_state_runs = {self.issue.id:1}
        self.owner._issue_current_state = {self.issue.id:'investigate'}
        self.owner.claimed, self.owner.completed, self.owner._pending_gates = {self.issue.id}, set(), {}
        self.assessment = SimpleNamespace(session_id=self.previous, settlement_record={
            'issue_id':self.issue.id, 'identifier':self.issue.identifier,'state':'assess','kind':'worker',
            'run':1,'session_id':self.previous,'phase':'report-unknown','scope_sha256':self.scope,
            'expected_report_body':'original bounded assessment report',
            'post_disposition':{'event':'complete','target':'investigate'}})
        self.native.observe_stage_report = Mock(return_value=('comment',object()))
        self.journal = case.path / '.git/uif-original-effects'
        self.journal.mkdir(mode=0o700)
        self.access = self.remote.transport = AccessTransport('https://fixture.example','/unused-fixture')
        self.calls, self.advance, self.entered, self.approved, self.fault = [], None, False, True, None
        self.secret = '7' * 64
        self.effects = {}

        def request(path, body=None, **options):
            if path in ('/v1/send', '/v1/linear/comment'):
                headers = {}
                self.access._recipient_permit.get().authorize(path,body,headers)
                self.assertEqual(headers['uif-dispatch-permit'],self.secret)
            self.calls.append(path)
            if path == '/v1/status':
                return dict(asdict(fixture.OWNER), stopped=False, role='native', coordinator_epoch=1, lease_seconds=30,
                            quota_issuer=asdict(fixture.QUOTA), installation_sha256='3'*64)
            if path == '/v1/claim/read':
                return (asdict(self.claim) if not body['by_attempt'] or body['identifier'] == self.claim.attempt_id
                        else None)
            if path == '/v1/claim/observe':
                self.assertEqual(body['first_intent_sha256'],self.grant.first_intent_sha256)
                return self.original
            if path == '/v1/attempt/attest/native':
                self.assertEqual(body, {'current':asdict(self.claim.lease),'previous':self.previous,'successor':self.successor})
                return {'matches':self.approved}
            if path == '/v1/claim/attempt':
                self.assertTrue(self.approved)
                self.assertTrue((self.journal / (body['request_id']+'.successor-cas')).exists())
                if self.fault == 'before-cas':
                    raise AuthorityUnavailable('ambiguous')
                self.advance=body
                self.claim=replace(self.claim,attempt_id=self.successor)
                if self.fault == 'after-cas':
                    raise AuthorityUnavailable('lost acknowledgement')
                return asdict(self.claim)
            if path == '/v1/transition/original':
                self.assertEqual(body['request_id'],self.advance['request_id'])
                return {'project':'OffMenu','attempt_id':self.successor,'lease':self.advance['current']}
            if path == '/v1/attempt/successor/read':
                return {'matches':body['advance'] == self.advance and body['current'] == asdict(self.claim.lease)}
            if path in ('/v1/attempt/successor/observe','/v1/attempt/successor/enter'):
                self.assertEqual(body,{'current':asdict(self.claim.lease),'advance':self.advance})
                if path.endswith('/observe'):
                    return {'entered':self.entered}
                self.assertTrue((self.journal / (self.advance['request_id']+'.successor-entry')).exists())
                if self.entered:
                    return {'entered':False}
                self.entered=True
                if self.fault == 'after-enter':
                    raise AuthorityUnavailable('lost entry reply')
                return {'entered':True,'permit':self.secret}
            if path == '/v1/linear/receipt':
                return self.effects.get(body['operation']['original_id'])
            if path == '/v1/linear/comment':
                result = {'id':fixture.ISSUE,'receipt_sha256':'d'*64}
                self.effects[body['operation']['original_id']] = result
                return result
            if path == '/v1/original':
                return {'operation':body['operation'],'state':'terminal','terminal':{
                    'operation':body['operation'],'status':'verified','receipt_sha256':'d'*64}}
            if path == '/v1/send':
                return {}
            self.fail(path)
        self.access._request = request

    def advance_claim(self):
        return advance_marked_assessment(self.native,self.owner,self.issue,self.assessment,lambda: None)

    def completed_workspace_fixture(self):
        from uif.stokowski_cloudflare.native_composition import workspace_recovery_digest
        path = self.case.path
        (path / '.stokowski').mkdir(mode=0o700)
        record = dict(self.assessment.settlement_record, phase='complete', attempted=True, report_posted=True,
                      event='complete', target='investigate', report=None, publication_event=None)
        (path / '.stokowski/settlement.json').write_bytes(wire(record))
        self.case.item['recovered_tree_sha256'] = workspace_recovery_digest(path)

    def select(self):
        return self.native.successor_assignment(self.owner,self.issue,self.scope,'investigate',1)

    def enter(self, selected):
        self.owner._tasks = {self.issue.id:asyncio.current_task()}
        self.attempt = SimpleNamespace(state_name='investigate',session_id=self.successor)
        self.owner.running = {self.issue.id:self.attempt}
        return self.native.successor_first_entry(self.owner,self.issue,self.scope,self.attempt,expected=selected)

    async def test_denied_control_or_attempted_local_phase_never_advances(self):
        self.approved=False
        with self.assertRaisesRegex(Hold,'control_original_unverified'):
            self.advance_claim()
        self.assertEqual(list(self.journal.iterdir()),[])
        self.approved=True
        self.assessment.settlement_record['attempted']=True
        with self.assertRaisesRegex(Hold,'frozen_disposition'):
            self.advance_claim()
        self.assertNotIn('/v1/claim/attempt',self.calls)

    async def test_pending_cas_prevents_a_second_post(self):
        self.fault='before-cas'
        for _ in range(2):
            with self.assertRaisesRegex(Hold,'successor_not_current'):
                self.advance_claim()
        self.assertEqual(self.calls.count('/v1/claim/attempt'),1)
        self.assertNotIn('/v1/attempt/successor/enter',self.calls)

    async def test_replaced_committed_cas_journal_holds_without_replaying(self):
        advance = self.advance_claim()
        marker = self.journal / (advance['request_id'] + '.successor-cas')
        altered = dict(advance, current=dict(advance['current'], expires_at=advance['current']['expires_at'] + 1))
        marker.write_bytes(wire(altered))
        with self.assertRaisesRegex(Hold,'cas_original'):
            self.advance_claim()
        self.assertEqual(self.calls.count('/v1/claim/attempt'),1)
        self.assertNotIn('/v1/attempt/successor/enter',self.calls)

    async def test_lost_enter_observes_only_and_secret_never_reissued(self):
        self.fault='after-cas'
        self.assertEqual(self.advance_claim(),self.advance)
        self.assertEqual(self.advance_claim(),self.advance)
        self.completed_workspace_fixture()
        selected=self.select()
        self.fault='after-enter'
        with self.assertRaisesRegex(AuthorityUnavailable,'lost entry'),self.enter(selected):
            self.fail('lost entry')
        with self.assertRaisesRegex(Hold,'already_attempted'),self.enter(selected):
            self.fail('uncertain entry')
        with self.assertRaisesRegex(Hold,'already_attempted'):
            self.select()
        self.assertEqual(self.calls.count('/v1/claim/attempt'),1)
        self.assertEqual(self.calls.count('/v1/attempt/successor/enter'),1)
        self.assertNotIn('/v1/send',self.calls)

    async def test_selected_native_turn_and_tracking_are_independent_single_originals(self):
        self.advance_claim()
        self.completed_workspace_fixture()
        selected=self.select()
        with self.enter(selected):
            self.assertEqual(self.native.successor_workspace_for_prologue(
                self.owner, self.issue, self.scope,self.attempt),self.case.path)
            self.native._slot.set('_announce_state')
            body = ('<!-- stokowski:state {"state": "investigate", "run": 1, "timestamp": "2030-01-11T12:00:00+00:00", '
                    '"workflow": "offmenu"} -->\n\n**[Stokowski]** Entering state: **investigate** (run 1) · workflow `offmenu`')
            client = _LinearClient(None,self.native)
            with self.assertRaisesRegex(Hold,'tracking_scope'):
                await client.post_comment(self.issue.id,body + ' changed')
            self.assertTrue(await client.post_comment(self.issue.id,body))
            with self.assertRaisesRegex(Hold,'already_prepared'):
                await client.post_comment(self.issue.id,body)
            request_id=sha256(f'{self.successor}:1'.encode()).hexdigest()
            op=Operation(self.claim.lease.fence,request_id,'b'*64)
            intent=SendIntent(op,self.remote.route,self.successor,'b'*64,'c'*64,2,1)
            content='{"summary":"fixture","verdict":"blocked"}'
            self.remote.provider_result=Mock(side_effect=lambda operation: ProviderReply(
                wire({'choices':[{'message':{'content':content}}]}),None,'c'*64))
            finish=self.native.successor_investigation_finisher(self.issue,{'attempt':self.successor},1)
            executor=None
            def validate():
                if executor is not current_thread():
                    raise Hold('foreign_executor')
            def fixed():
                self.native.retain_intent(intent)
                self.access.request('/v1/send',{'intent':asdict(intent)})
                finish(request_id,content)
            bound=self.native.prepare_successor_turn_thread(fixed,validate)
            def run():
                nonlocal executor
                executor=current_thread()
                try:
                    return bound()
                finally:
                    executor=None
            await asyncio.to_thread(run)
            with self.assertRaisesRegex(Hold,'already_used'):
                await asyncio.to_thread(run)
            with self.assertRaisesRegex(Hold,'upload_held'):
                await client.upload_file('file','text/plain',b'data')
            self.assertTrue((self.journal / (request_id+'.investigation-result')).exists())
            self.assertEqual(self.calls.count('/v1/send'),1)
            self.assertEqual(self.calls.count('/v1/linear/comment'),1)
        self.assertNotIn(self.secret.encode(), b''.join(f.read_bytes() for f in self.journal.iterdir()
                                                      if f.name.endswith('successor-entry')))

    async def test_cold_unattempted_original_rejoins_and_completed_phase_recovers_workspace(self):
        from uif.stokowski_cloudflare.fresh_assessment_terminal import unattempted_native_settlement
        advance = self.advance_claim()
        path = self.case.path
        (path / '.stokowski').mkdir(mode=0o700)
        phase = path / '.stokowski/settlement.json'
        record = self.assessment.settlement_record
        phase.write_bytes(wire(record))
        with self.assertRaisesRegex(RuntimeError,'stopped before native attempted'):
            with unattempted_native_settlement(path, advance, record, lambda: None):
                with self.assertRaisesRegex(ValueError,'in_progress'):
                    with unattempted_native_settlement(path, advance, record, lambda: None):
                        self.fail('concurrent owner')
                raise RuntimeError('stopped before native attempted')
        with unattempted_native_settlement(path, advance, record, lambda: None):
            record.update(phase='transition',report_posted=True,event='complete',target='investigate',
                          report=None,publication_event=None)
            phase.write_bytes(wire(record))
        with unattempted_native_settlement(path, advance, record, lambda: None):
            record['attempted'] = True
            phase.write_bytes(wire(record))
        with self.assertRaisesRegex(Hold,'already_attempted'):
            with unattempted_native_settlement(path, advance, record, lambda: None):
                self.fail('ambiguous native effect')
        with self.assertRaisesRegex(Hold,'completed_workspace_changed'):
            self.select()
        record['phase'] = 'complete'
        phase.write_bytes(wire(record))
        self.assertEqual(self.select(), self.claim)
        (path / 'unexpected.txt').write_text('external mutation')
        with self.assertRaisesRegex(Hold,'completed_workspace_changed'):
            self.select()
        self.assertNotIn('/v1/attempt/successor/enter', self.calls)
