"""Disposable single-authority test; no remote services, model, credentials or publisher."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from uif.effect_authority import (
    AuthorityBinding, AuthorityIdentity, ClaimRequest, Completion, HandoffRequest, Operation,
    ReleaseRequest, RenewRequest, Scope, SourcePin, TerminalEffect, bind_effect_authority,
)
from uif.handoff_store import Hold
from uif.quota_handoff import LinearKey, Registry
from uif.shared_issue_coordinator import CommittedSendIntent, RegistrySharedStore, Session

WORKSPACE, PROJECT, MOVED, ISSUE, OTHER = [str(UUID(int=i)) for i in (1,2,3,4,5)]
SOURCE = SourcePin('fixture/ordinary-software','develop','1'*40,'2'*40,'3'*40)
KEY = LinearKey(WORKSPACE,PROJECT,ISSUE)
SCOPE = Scope('fixture-repository',KEY)
NATIVE = AuthorityIdentity('fixture-issued','a'*64,'b'*64,'fixture-native-session')
CLOUD = replace(NATIVE,session_id='fixture-cloud-session')
PINS = {i:str(i)*64 for i in range(1,10)}


def arun(coro):
    return asyncio.run(coro)


class ExistingJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'authority'
        self.now, self.seq = 1000, 0
        self.admitted = set()
        self.current_project = None
        self.terminals = {}
        self.reconstruction = self.quiesced = self.completion = True
        self.session_native = Session(NATIVE,'native-peer','native')
        self.session_cloud = Session(CLOUD,'cloud-peer','cloud')
        self.sessions = {s.identity.session_id:s for s in (self.session_native,self.session_cloud)}
        self.registry = Registry.initialize(self.path, workspace_id=WORKSPACE,
            actors={'morgan':{'role':'morgan','projects':[PROJECT]},
                    'native':{'role':'native','projects':[PROJECT,MOVED]},
                    'cloud':{'role':'project','projects':[PROJECT,MOVED]}},
            principals={'management-peer':['morgan'],'native-peer':['native'],'cloud-peer':['cloud']},
            morgan_actor='morgan',morgan_execution_id='fixture-morgan',morgan_principal='management-peer',
            clock=lambda:self.now,provider_verifier=lambda _:True)
        self.manage('set_stopped',stopped=False)
        self.replenish()
        self.a, self.store_a = self.peer(self.session_native)
        self.b, self.store_b = self.peer(self.session_cloud)

    def manage(self, action, **values):
        self.seq += 1
        return self.registry.invoke(principal='management-peer',actor='morgan',
          request={'action':action,'request_id':f'fixture-management-{self.seq}',
                   'coordinator':{'execution_id':'fixture-morgan','generation':1},**values})

    def replenish(self):
        self.manage('heartbeat',observed_at=self.now)
        self.registry.invoke(principal='native-peer',actor='native',
          request={'action':'provider_observation','request_id':f'fixture-available-{self.seq}',
                   'classification':'available','observed_at':self.now,'sequence':self.seq,
                   'evidence_sha256':PINS[1]})

    def peer(self, session):
        worker = Registry(self.path,clock=lambda:self.now)
        store = RegistrySharedStore(worker,authority=NATIVE,
            authenticate_peer=lambda peer:session if peer==session.identity else None,
            resolve_session=self.sessions.get,
            verify_admission=lambda access:(access.source==SOURCE and
                                           access.scope.repository_id=='fixture-repository' and
                                           (self.current_project is None or
                                            access.scope.key.project_id==self.current_project) and
                                           (not self.admitted or access.action in self.admitted)),
            verify_reconstruction=lambda _,request:self.reconstruction and request.reconstruction_sha256==PINS[2],
            verify_handoff=lambda _p,_s,request:self.quiesced and request.quiesced_sha256==PINS[3]
                                                and request.reconstruction_sha256==PINS[2],
            verify_completion=lambda _,completion:self.completion and completion.receipt_sha256==PINS[4],
            verify_terminal=lambda _,op,sha:self.terminals.get((op,sha)),
            lease_seconds=60)
        client=bind_effect_authority(mode='shared',shared_store=store,identity=session.identity,
                          verify_binding=lambda _,ident:AuthorityBinding(ident,PINS[1]))
        return client,store

    def request(self, authority=NATIVE, *, scope=SCOPE, name='fixture-issue-claim'):
        return ClaimRequest(authority,scope,SOURCE,name,1,PINS[2])

    def claim(self):
        return arun(self.a.claim(self.request())).lease

    def handoff(self,lease, *, proposer=CLOUD):
        return HandoffRequest(proposer,lease,CLOUD.session_id,'fixture-handoff',1,PINS[3],PINS[2])

    def operation(self,lease,name='fixture-ordinary-software-operation'):
        return Operation(lease.fence,name,PINS[7])

    def test_cross_project_and_two_process_claim_race_has_one_durable_original(self):
        moved = replace(SCOPE,key=replace(KEY,project_id=MOVED))
        # Use independent processes and native/flock-backed state, never a shared
        # in-memory lock supplied by these test clients.
        context=multiprocessing.get_context('fork')
        ready=context.Barrier(2)
        results=context.Queue()
        def contender(actor,request):
            ready.wait(4)
            try:
                actor = self.a if actor=='a' else self.b
                result = arun(actor.claim(request))
                results.put(('won',result.lease.fence.owner_session))
            except Hold as exc:
                results.put(('held',str(exc)))
        proposed=((self.a,self.request()),(self.b,self.request(CLOUD,scope=moved,name='other-route')))
        ps=[context.Process(target=contender,args=(label,request))
            for label,(_,request) in zip(('a','b'),proposed)]
        for p in ps:p.start()
        try:
            for p in ps:
                p.join(6)
                self.assertEqual(p.exitcode,0)
            snapshots=[results.get(timeout=2) for _ in ps]
            self.assertEqual(sorted(a for a,_ in snapshots),['held','won'])
            originals=[arun(client.observe_claim(req)) for client,req in proposed]
            self.assertEqual(sum(o is not None for o in originals),1)
            with self.registry.store.transaction(readonly=True) as (_,state,_):
                self.assertEqual(len(state['tasks']),1)
        finally:
            for p in ps:
                if p.is_alive():p.kill()
                p.join()
            results.close()

    def test_parallel_distinct_issues_through_two_registered_lanes(self):
        one=self.claim()
        two=arun(self.b.claim(self.request(CLOUD,scope=replace(SCOPE,key=replace(KEY,issue_id=OTHER)),
                                           name='fixture-other-claim'))).lease
        self.assertNotEqual(one.fence.scope.key.claim_identity,two.fence.scope.key.claim_identity)
        self.assertIsNotNone(arun(self.a.begin(self.operation(one))))
        self.assertIsNotNone(arun(self.b.begin(self.operation(two))))

    def test_legacy_claim_and_effect_cannot_bypass_shared_custody(self):
        self.claim()
        request=dict(action='claim',request_id='fixture-legacy',key=asdict(KEY),lane='cloud',
                     source=asdict(SOURCE),evidence_ref='fixture',original_operation_id='fixture-original')
        with self.assertRaisesRegex(Hold,'existing_claim'):
            self.registry.invoke(principal='cloud-peer',actor='cloud',request=request)
        with self.assertRaisesRegex(Hold,'typed_shared'):
            self.registry.perform_effect(principal='native-peer',actor='native',key=KEY,generation=1,
                operation_id='legacy-effect',payload_sha256=PINS[7],effect=lambda:self.fail('legacy effect ran'))
        with self.assertRaisesRegex(Hold,'typed_shared'):
            self.registry.invoke(principal='native-peer',actor='native',request={
                'action':'task_status','key':asdict(KEY)})
        self.assertEqual(self.registry.invoke(principal='management-peer',actor='morgan',
                                             request={'action':'status'})['tasks'],{})

    def test_renew_and_lost_claim_receipt_bound_to_exact_intent(self):
        req=self.request()
        first=arun(self.a.claim(req))
        self.assertEqual(arun(self.a.observe_claim(req)),first)
        with self.assertRaisesRegex(Hold,'original_request_changed'):
            arun(self.a.observe_claim(replace(req,reconstruction_sha256=PINS[3])))
        self.now+=1
        renewed=arun(self.a.renew(RenewRequest(NATIVE,first.lease,'fixture-renew'))).lease
        self.assertEqual(renewed.fence,first.lease.fence)
        self.assertGreater(renewed.expires_at,first.lease.expires_at)
        with self.assertRaisesRegex(Hold,'lease_changed'):
            arun(self.a.renew(RenewRequest(NATIVE,first.lease,'fixture-renew-new-original')))

    def test_missing_reconstruction_or_foreign_peer_fails_closed(self):
        self.reconstruction=False
        with self.assertRaisesRegex(Hold,'reconstruction_unverified'):
            self.claim()
        with self.assertRaisesRegex(Hold,'peer_not_authenticated'):
            arun(self.store_a.claim(CLOUD,self.request(CLOUD)))
        self.assertIsNone(arun(self.a.observe_claim(self.request())))

    def test_stale_original_and_duplicate_receive_never_pass(self):
        lease=self.claim()
        op=self.operation(lease)
        pending=arun(self.a.begin(op))
        self.assertEqual(arun(self.a.recheck(pending)),pending)
        with self.assertRaisesRegex(Hold,'uncertain'):
            arun(self.a.begin(op))
        calls=[]
        def accept(intent):
            self.assertIs(type(intent),CommittedSendIntent)
            self.assertEqual(intent.pending,pending)
            calls.append(intent)
        def observe(intent):
            self.assertIs(intent,calls[-1])
            # The installed destination reader separately returns the same
            # exact original terminal; bare callback success cannot settle.
            self.terminals[(op,PINS[5])]=TerminalEffect(op,'verified',PINS[5])
            return PINS[5]
        result=self.store_a.consume_once(NATIVE,pending,accept=accept,observe_receipt=observe)
        self.assertEqual(result,TerminalEffect(op,'verified',PINS[5]))
        with self.assertRaisesRegex(Hold,'already_consumed'):
            self.store_a.consume_once(NATIVE,pending,accept=accept,observe_receipt=observe)
        self.assertEqual(arun(self.a.begin(op)),result)  # receipt, never a fresh permit
        self.assertEqual([intent.pending for intent in calls],[pending])

    def test_expiry_no_reclaim_until_verified_quiescent_handoff(self):
        lease=self.claim()
        self.now=1061
        with self.assertRaisesRegex(Hold,'expired'):
            arun(self.a.begin(self.operation(lease)))
        with self.assertRaisesRegex(Hold,'reconciliation'):
            arun(self.b.claim(self.request(CLOUD)))
        self.quiesced=False
        with self.assertRaisesRegex(Hold,'handoff_evidence'):
            arun(self.b.handoff(self.handoff(lease)))
        self.quiesced=True
        next_=arun(self.b.handoff(self.handoff(lease))).lease
        self.assertEqual(next_.fence.generation,2)
        self.assertEqual(next_.fence.scope,lease.fence.scope)
        with self.assertRaisesRegex(Hold,'owner_not_current'):
            arun(self.a.begin(self.operation(lease)))
        self.assertIsNotNone(arun(self.b.begin(self.operation(next_))))

    def test_loss_after_effect_boundary_holds_handoff_and_other_new_original(self):
        lease=self.claim()
        op=self.operation(lease)
        pending=arun(self.a.begin(op))
        self.sends=0
        def accept(_):
            self.sends+=1
        def uncertain(_):
            raise OSError('generated receipt loss')
        with self.assertRaises(OSError):
            self.store_a.consume_once(NATIVE,pending,accept=accept,observe_receipt=uncertain)
        self.assertEqual(self.sends,1)
        self.assertEqual(arun(self.a.observe_original(op)).state,'unknown')
        with self.assertRaisesRegex(Hold,'handoff_requires_reconciliation'):
            arun(self.b.handoff(self.handoff(lease)))
        with self.assertRaisesRegex(Hold,'uncertain'):
            arun(self.a.begin(self.operation(lease,'new-original-held')))
        with self.assertRaisesRegex(Hold,'destination_not_verified'):
            arun(self.a.reconcile(op,PINS[5]))
        self.terminals[(op,PINS[5])]=TerminalEffect(op,'verified',PINS[5])
        self.assertEqual(arun(self.a.reconcile(op,PINS[5])).status,'verified')
        next_=arun(self.b.handoff(self.handoff(lease))).lease
        rebound=Operation(next_.fence,op.original_id,op.payload_sha256)
        with self.assertRaisesRegex(Hold,'original_effect_changed'):
            arun(self.b.begin(rebound))
        self.assertEqual(self.sends,1)

    def test_durable_send_intent_rechecks_expiry_before_accept(self):
        lease=self.claim()
        op=self.operation(lease)
        pending=arun(self.a.begin(op))
        store=self.store_a.store
        transaction=store.transaction
        @contextmanager
        def delayed_save(*args,**kwargs):
            with transaction(*args,**kwargs) as (reg,state,save):
                def lag():
                    save()
                    self.now=1061
                yield reg,state,lag
        store.transaction=delayed_save
        with self.assertRaisesRegex(Hold,'expired'):
            self.store_a.consume_once(NATIVE,pending,
                accept=lambda _:self.fail('expired intent accepted'),
                observe_receipt=lambda _:self.fail('receipt wait for denied intent'))
        self.assertEqual(arun(self.a.observe_original(op)).state,'unknown')

    def test_committed_stop_after_advisory_recheck_prevents_acceptance(self):
        operation=self.operation(self.claim())
        pending=arun(self.a.begin(operation))
        self.assertEqual(arun(self.a.recheck(pending)),pending)
        self.assertTrue(self.manage('set_stopped',stopped=True)['stopped'])
        with self.assertRaisesRegex(Hold,'admission_stopped'):
            self.store_a.consume_once(NATIVE,pending,
                accept=lambda _:self.fail('accepted after stop'),
                observe_receipt=lambda _:self.fail('receipt wait after stop'))
        self.assertEqual(arun(self.a.observe_original(operation)).state,'pending')

    def test_committed_hard_quota_after_advisory_recheck_prevents_acceptance(self):
        pending=arun(self.a.begin(self.operation(self.claim())))
        self.assertEqual(arun(self.a.recheck(pending)),pending)
        self.seq+=1
        self.registry.invoke(principal='native-peer',actor='native',
            request={'action':'provider_observation','request_id':'fixture-quota-revoked',
                     'classification':'hard-quota','sequence':self.seq,'observed_at':self.now,
                     'evidence_sha256':PINS[1]})
        with self.assertRaisesRegex(Hold,'provider_unavailable'):
            self.store_a.consume_once(NATIVE,pending,
                accept=lambda _:self.fail('accepted after quota revoke'),
                observe_receipt=lambda _:self.fail('receipt wait after quota revoke'))

    def test_pending_coordinator_transfer_after_recheck_prevents_acceptance(self):
        pending=arun(self.a.begin(self.operation(self.claim())))
        self.assertEqual(arun(self.a.recheck(pending)),pending)
        self.registry.coordinator_verifier=lambda _:True
        self.manage('transfer_coordinator',successor_execution_id='fixture-other-morgan',
                    successor_principal='management-peer',evidence_ref='fixture-transfer')
        with self.assertRaisesRegex(Hold,'admission_stopped'):
            self.store_a.consume_once(NATIVE,pending,
                accept=lambda _:self.fail('accepted after coordinator revoke'),
                observe_receipt=lambda _:self.fail('receipt wait after coordinator revoke'))

    def test_shared_lock_orders_irreversible_acceptance_before_concurrent_stop(self):
        op=self.operation(self.claim())
        pending=arun(self.a.begin(op))
        stop={'action':'set_stopped','request_id':'fixture-stop-at-accept','stopped':True,
              'coordinator':{'execution_id':'fixture-morgan','generation':1}}
        def try_stop():
            return self.registry.invoke(principal='management-peer',actor='morgan',request=stop)
        calls=[]
        def accept(intent):
            self.assertIs(type(intent),CommittedSendIntent)
            self.assertEqual(intent.pending,pending)
            # Separate authenticated Registry instance and real lock. This is
            # the point where the old bool guard allowed a committed stop.
            with self.assertRaisesRegex(Hold,'authority_busy'):
                try_stop()
            state,_=self.registry.store._read('state.json')
            record=state['tasks'][KEY.identity]['operations'][op.original_id]
            self.assertFalse(state['stopped'])
            self.assertEqual(record['status'],'sending')
            self.assertEqual(record['send_intent_revision'],intent.authority_revision)
            calls.append('accepted')
        def observe(_):
            # Do not keep stop/revoke blocked while awaiting the terminal.
            self.assertTrue(try_stop()['stopped'])
            calls.append('stopped')
            self.terminals[(op,PINS[5])]=TerminalEffect(op,'verified',PINS[5])
            return PINS[5]
        self.assertEqual(self.store_a.consume_once(
            NATIVE,pending,accept=accept,observe_receipt=observe),TerminalEffect(op,'verified',PINS[5]))
        self.assertEqual(calls,['accepted','stopped'])
        self.assertTrue(self.registry.invoke(principal='management-peer',actor='morgan',
                                             request={'action':'status'})['stopped'])

    def test_deferred_acceptance_is_not_permission_to_send_or_settle(self):
        op=self.operation(self.claim())
        pending=arun(self.a.begin(op))
        async def accept(_):
            self.fail('deferred sink ran')
        with self.assertRaisesRegex(Hold,'synchronous_acceptance'):
            self.store_a.consume_once(NATIVE,pending,accept=accept,
                observe_receipt=lambda _:self.fail('deferred acceptance received a receipt'))
        self.assertEqual(arun(self.a.observe_original(op)).state,'unknown')

    def test_moved_scope_neither_revives_old_receiver_nor_creates_a_second_claim(self):
        old=self.claim()
        op=self.operation(old)
        pending=arun(self.a.begin(op))
        self.current_project=MOVED
        with self.assertRaisesRegex(Hold,'admission_unverified'):
            self.store_a.consume_once(NATIVE,pending,
                accept=lambda _:self.fail('old route accepted'),
                observe_receipt=lambda _:self.fail('old route waited for a receipt'))
        moved=replace(SCOPE,key=replace(KEY,project_id=MOVED))
        with self.assertRaisesRegex(Hold,'reconciliation'):
            arun(self.b.claim(self.request(CLOUD,scope=moved,name='fixture-moved-current-route')))
        with self.registry.store.transaction(readonly=True) as (_,state,_):
            self.assertEqual(len(state['tasks']),1)

    def test_trusted_fractional_clock_emits_only_integral_wire_expiry(self):
        self.now=1000.75
        expiry=self.claim().expires_at
        self.assertIs(type(expiry),int)
        self.assertEqual(expiry,1060)

    def test_release_requires_explicit_external_completion_and_current_owner(self):
        lease=self.claim()
        complete=Completion(lease.fence,True,PINS[4])
        other=ReleaseRequest(CLOUD,complete,'not-the-owner')
        with self.assertRaisesRegex(Hold,'owner'):
            arun(self.b.release(other))
        self.completion=False
        valid=ReleaseRequest(NATIVE,complete,'fixture-release')
        with self.assertRaisesRegex(Hold,'completion_unverified'):
            arun(self.a.release(valid))
        self.completion=True
        receipt=arun(self.a.release(valid))
        self.assertEqual(receipt.completion,complete)
        self.assertEqual(arun(self.a.release(valid)),receipt)
        with self.assertRaisesRegex(Hold,'owner_not_current'):
            arun(self.a.begin(self.operation(lease)))


if __name__ == '__main__':
    unittest.main()
