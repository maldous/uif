"""Generated shared-port replies plus real legacy Registry compatibility."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID
import unittest

import test_quota_handoff as legacy
from uif.effect_authority import (
    AuthorityBinding, AuthorityEpoch, AuthorityIdentity, ClaimFence, ClaimRequest,
    Completion, HandoffRequest, Lease, LeaseReceipt, MAX_WIRE_SECONDS, Operation,
    ObservationBinding, ObservationReceipt, ObservationRoute, ObserverIdentity,
    OriginalEffect, PendingEffect, ReleaseReceipt, ReleaseRequest, RenewRequest,
    Scope, SharedAtomicObserver, SharedAtomicStore, SharedEffectObserver, SourcePin,
    TerminalEffect, bind_effect_authority, lease_request_sha256, observation_lookup_sha256,
    observation_original_sha256, transition_request_sha256,
)
from uif.handoff_store import Hold
from uif.native_quota import NativeProfile, NativeQuotaBoundary, PoolPermit
from uif.quota_handoff import LinearKey

PIN = "d" * 64


class ScriptedStore(SharedAtomicStore):
    """No state or network: verifies the shape of supported client calls only."""

    def __init__(self):
        self.replies = {}
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        reply = self.replies[name]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def claim(self, *args): return self.call("claim", args)
    async def observe_claim(self, *args): return self.call("observe_claim", args)
    async def renew(self, *args): return self.call("renew", args)
    async def handoff(self, *args): return self.call("handoff", args)
    async def begin(self, *args): return self.call("begin", args)
    async def recheck(self, *args): return self.call("recheck", args)
    async def observe_original(self, *args): return self.call("observe_original", args)
    async def reconcile(self, *args): return self.call("reconcile", args)
    async def release(self, *args): return self.call("release", args)


class SharedBindingTests(unittest.TestCase):
    def setUp(self):
        self.identity = AuthorityIdentity("configured-shared", "a" * 64, "b" * 64, "verified-session")
        self.store = ScriptedStore()

    def bind(self, *, store=None, verify=None, **changes):
        options = dict(mode="shared", shared_store=self.store if store is None else store,
                       identity=self.identity,
                       verify_binding=verify or (lambda _, i: AuthorityBinding(i, PIN)))
        options.update(changes)
        return bind_effect_authority(**options)

    def test_shared_never_falls_back_to_local_or_accepts_unqualified_store(self):
        with self.assertRaisesRegex(Hold, "shared_atomic_authority_unavailable"):
            bind_effect_authority(mode="shared")
        with self.assertRaisesRegex(Hold, "shared_authority_configuration"):
            self.bind(local=object())
        with self.assertRaisesRegex(Hold, "shared_atomic_authority_unavailable"):
            self.bind(store=SimpleNamespace(claim=self.store.claim))
        with self.assertRaisesRegex(Hold, "shared_authority_configuration"):
            self.bind(mode="ambient")
        with self.assertRaises(TypeError):
            SharedAtomicStore()
        self.assertEqual(self.store.calls, [])

    def test_independent_binding_uses_actual_configured_store_and_full_identity(self):
        invocations = []
        def observe(store, identity):
            invocations.append((store, identity))
            return AuthorityBinding(identity, PIN)
        self.bind(verify=observe)
        self.assertEqual(invocations, [(self.store, self.identity)])
        for proof in (
            True, {"identity":self.identity, "observation_sha256":PIN},
            AuthorityBinding(replace(self.identity, session_id="other"), PIN),
            AuthorityBinding(replace(self.identity, policy_sha256="9" * 64), PIN),
        ):
            with self.subTest(proof=type(proof).__name__):
                with self.assertRaisesRegex(Hold, "shared_authority_binding"):
                    self.bind(verify=lambda *_: proof)
        def offline(*_):
            raise ConnectionError("private observer diagnostics must not be returned")
        with self.assertRaisesRegex(Hold, "^shared_authority_binding_unavailable$"):
            self.bind(verify=offline)
        self.assertEqual(self.store.calls, [])


class SharedCallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = ScriptedStore()
        self.identity = AuthorityIdentity("configured-shared","a"*64,"b"*64,"verified-session")
        self.port = bind_effect_authority(mode="shared", shared_store=self.store, identity=self.identity,
            verify_binding=lambda _, pin: AuthorityBinding(pin,PIN))
        self.scope = Scope("gh-repo:123", LinearKey(*(str(UUID(int=n)) for n in (1,2,3))))
        self.source = SourcePin("maldous/uif","develop","a"*40,"b"*40,"c"*40)
        self.fence = ClaimFence(self.scope,self.source,self.identity.session_id,7,4,"issued-lease",
                                self.identity.authority_id,self.identity.deployment_sha256,
                                self.identity.policy_sha256)
        self.lease = Lease(self.fence,48)
        self.claim_lease = replace(self.lease,fence=replace(self.fence,generation=1))
        self.claim_request = ClaimRequest(self.identity,self.scope,self.source,"original-claim",7,PIN)
        self.renew_request = RenewRequest(self.identity,self.lease,"original-renew")
        self.handoff_request = HandoffRequest(self.identity,self.lease,"next-session","original-handoff",8,PIN,"9"*64)
        self.op = Operation(self.fence,"original-op",PIN)
        self.pending = PendingEffect(self.op,"issued-dispatch")
        self.terminal = TerminalEffect(self.op,"verified","e"*64)
        self.complete = Completion(self.fence,True,"f"*64)
        self.release_request = ReleaseRequest(self.identity,self.complete,"original-release")

    @staticmethod
    def receipt(request, lease):
        # Test observer presents the result recorded under this exact request.
        return LeaseReceipt(request.kind,request.request_id,lease_request_sha256(request),lease)

    async def test_claim_uses_only_scoped_intent_and_rejects_rebound_receipt(self):
        request = self.claim_request
        receipt = self.receipt(request,self.claim_lease)
        self.store.replies["claim"] = receipt
        self.assertIs(await self.port.claim(request),receipt)
        self.assertEqual(self.store.calls,[("claim",(self.identity,request))])
        fence = self.claim_lease.fence
        for bad_fence in (replace(fence,owner_session="wrong-holder"),
                          replace(fence,scope=replace(self.scope,repository_id="gh-repo:456")),
                          replace(fence,source=replace(self.source,head="9"*40)),
                          replace(fence,coordinator_epoch=8),replace(fence,generation=2)):
            self.store.replies["claim"] = self.receipt(request,replace(self.claim_lease,fence=bad_fence))
            with self.assertRaisesRegex(Hold,"shared_claim_(binding|owner)"):
                await self.port.claim(request)
        self.store.replies["claim"] = self.claim_lease  # legacy bare response has no identity
        with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
            await self.port.claim(request)

    async def test_original_claim_read_is_not_claim_or_replay(self):
        request = self.claim_request
        self.store.replies.update(claim=TimeoutError("simulated lost reply"),observe_claim=None)
        with self.assertRaisesRegex(Hold,"^shared_authority_outcome_unknown$"):
            await self.port.claim(request)
        self.assertIsNone(await self.port.observe_claim(request))
        receipt = self.receipt(request,self.claim_lease)
        self.store.replies["observe_claim"] = receipt
        self.assertIs(await self.port.observe_claim(request),receipt)
        self.assertEqual(self.store.calls,[(kind,(self.identity,request))
                                           for kind in ("claim","observe_claim","observe_claim")])

    async def test_claim_and_observe_hold_both_different_id_and_same_id_proof_drift(self):
        a = self.claim_request
        returned = self.receipt(a,self.claim_lease)
        self.store.replies.update(claim=returned,observe_claim=returned)
        for request in (replace(a,request_id="new-request"),replace(a,reconstruction_sha256="9"*64),
                        replace(a,expected_coordinator_epoch=9)):
            self.assertNotEqual(lease_request_sha256(a),lease_request_sha256(request))
            for method in (self.port.claim,self.port.observe_claim):
                with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
                    await method(request)
        self.store.replies["claim"] = replace(returned,request_kind=RenewRequest.kind)
        with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
            await self.port.claim(a)
        prior_calls = self.store.calls[:]
        with self.assertRaisesRegex(Hold,"shared_request_authority"):
            await self.port.observe_claim(replace(a,authority=replace(self.identity,session_id="other")))
        self.assertEqual(self.store.calls,prior_calls)

    async def test_crossed_concurrent_claim_reply_never_binds_new_request(self):
        a = self.claim_request
        b = replace(a,request_id="other-original",reconstruction_sha256="8"*64)
        recorded_a = self.receipt(a,self.claim_lease)
        started, finish = asyncio.Event(), asyncio.Event()

        async def receiver(identity, request):
            self.assertIs(identity,self.identity)
            if request == a:
                started.set()
                await finish.wait()
            return recorded_a  # B receives A's earlier persisted result.
        self.store.claim = receiver
        delayed = asyncio.create_task(self.port.claim(a))
        try:
            await asyncio.wait_for(started.wait(),timeout=2)
            with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
                await self.port.claim(b)
        finally:
            finish.set()
        accepted_history = await asyncio.wait_for(delayed,timeout=2)
        self.assertIs(accepted_history,recorded_a)  # exact original result is still a receipt
        # The actual authority advanced while A was delayed. An original
        # historical receipt never lets the caller bypass current begin.
        self.store.replies["begin"] = Hold("obsolete_claim")
        with self.assertRaisesRegex(Hold,"obsolete_claim"):
            await self.port.begin(Operation(accepted_history.lease.fence,"old-owner-effect",PIN))

    async def test_renewal_requires_same_request_prior_lease_fence_and_increasing_expiry(self):
        request = self.renew_request
        accepted = self.receipt(request,replace(self.lease,expires_at=70))
        self.store.replies["renew"] = accepted
        self.assertIs(await self.port.renew(request),accepted)
        for drift in (replace(request,request_id="new-renew"),
                      replace(request,current=replace(self.lease,expires_at=54))):
            with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
                await self.port.renew(drift)
        for invalid in (self.lease,
                        replace(self.lease,expires_at=70,fence=replace(self.fence,lease_id="other"))):
            self.store.replies["renew"] = self.receipt(request,invalid)
            with self.assertRaisesRegex(Hold,"shared_renewal_binding"):
                await self.port.renew(request)
        self.assertEqual(self.store.calls[0],("renew",(self.identity,request)))

    async def test_handoff_pins_exact_prior_lease_both_proofs_and_current_epoch(self):
        request = self.handoff_request
        newer = replace(self.fence,owner_session="next-session",generation=5,lease_id="new-lease",
                        coordinator_epoch=8)
        approved = self.receipt(request,replace(self.lease,fence=newer))
        self.store.replies["handoff"] = approved
        self.assertIs(await self.port.handoff(request),approved)
        for drift in (replace(request,request_id="other-handoff"),
                      replace(request,quiesced_sha256="a"*64),
                      replace(request,reconstruction_sha256="b"*64),
                      replace(request,current=replace(self.lease,expires_at=65)),
                      replace(request,expected_coordinator_epoch=9)):
            with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
                await self.port.handoff(drift)
        for f in (replace(newer,generation=4),replace(newer,lease_id=self.fence.lease_id),
                  replace(newer,coordinator_epoch=7),replace(newer,coordinator_epoch=9),
                  replace(newer,scope=replace(self.scope,repository_id="gh-repo:9"))):
            self.store.replies["handoff"] = self.receipt(request,replace(self.lease,fence=f))
            with self.assertRaisesRegex(Hold,"shared_handoff_binding"):
                await self.port.handoff(request)
        with self.assertRaisesRegex(Hold,"shared_claim_owner"):
            await self.port.begin(replace(self.op,fence=newer))
        self.assertTrue(all(name=="handoff" for name,_ in self.store.calls))

    async def test_another_authority_even_with_same_scope_and_session_cannot_reuse_fence(self):
        altered = replace(self.fence,deployment_sha256="0"*64)
        foreign = replace(self.op,fence=altered)
        for call in (lambda:self.port.begin(foreign),
                     lambda:self.port.recheck(replace(self.pending,operation=foreign)),
                     lambda:self.port.observe_original(foreign),
                     lambda:self.port.reconcile(foreign,self.terminal.receipt_sha256),
                     lambda:self.port.handoff(replace(self.handoff_request,
                                                     current=replace(self.lease,fence=altered))),
                     lambda:self.port.release(replace(self.release_request,completion=replace(self.complete,fence=altered)))):
            with self.assertRaisesRegex(Hold,"shared_fence_authority"):
                await call()
        self.assertEqual(self.store.calls, [])
        wrong = replace(self.claim_lease.fence,deployment_sha256="0"*64)
        self.store.replies["claim"] = self.receipt(self.claim_request,replace(self.claim_lease,fence=wrong))
        with self.assertRaisesRegex(Hold,"shared_fence_authority"):
            await self.port.claim(self.claim_request)

    async def test_ambiguous_renew_handoff_and_release_each_hold_without_replay(self):
        self.store.replies.update(renew=TimeoutError("no response"),
                                  handoff=ConnectionError("no response"),
                                  release=OSError("no response"))
        for method, request in ((self.port.renew,self.renew_request),
                                (self.port.handoff,self.handoff_request),
                                (self.port.release,self.release_request)):
            with self.assertRaisesRegex(Hold,"^shared_authority_outcome_unknown$"):
                await method(request)
        self.assertEqual([name for name,_ in self.store.calls],["renew","handoff","release"])

    async def test_effect_observation_and_terminal_receipt_bind_original_operation_id(self):
        other_op = replace(self.op,original_id="effect-sibling")
        wrong_result = replace(self.terminal,operation=other_op)
        self.store.replies.update(observe_original=OriginalEffect(other_op,"terminal",wrong_result),
                                  reconcile=wrong_result)
        with self.assertRaisesRegex(Hold,"shared_original_binding"):
            await self.port.observe_original(self.op)
        with self.assertRaisesRegex(Hold,"shared_terminal_binding"):
            await self.port.reconcile(self.op,self.terminal.receipt_sha256)

    async def test_pending_begin_and_recheck_bind_the_same_dispatch_fence(self):
        self.store.replies.update(begin=self.pending,recheck=self.pending)
        self.assertIs(await self.port.begin(self.op),self.pending)
        self.assertIs(await self.port.recheck(self.pending),self.pending)
        self.store.replies["recheck"] = replace(self.pending,dispatch_fence="wrong-fence")
        with self.assertRaisesRegex(Hold,"shared_dispatch_fence"):
            await self.port.recheck(self.pending)
        for changed in (replace(self.pending,operation=replace(self.op,payload_sha256="a"*64)),
                        {"operation":self.op,"dispatch_fence":"opaque"}):
            self.store.replies["begin"] = changed
            with self.assertRaisesRegex(Hold,"shared_operation_binding"):
                await self.port.begin(self.op)

    async def test_ambiguous_effect_has_no_retry_or_dispatch_permit_on_read(self):
        self.store.replies.update(begin=ConnectionError("internal details"),
                                  observe_original=OriginalEffect(self.op,"pending"))
        with self.assertRaisesRegex(Hold,"^shared_authority_outcome_unknown$"):
            await self.port.begin(self.op)
        observation = await self.port.observe_original(self.op)
        self.assertEqual(observation.state,"pending")
        self.assertFalse(hasattr(observation,"dispatch_fence"))
        self.assertEqual([n for n,_ in self.store.calls],["begin","observe_original"])
        self.store.replies["observe_original"] = self.pending
        with self.assertRaisesRegex(Hold,"shared_original_binding"):
            await self.port.observe_original(self.op)
        self.store.replies["observe_original"] = OriginalEffect(self.op,"terminal",self.terminal)
        self.assertIs((await self.port.observe_original(self.op)).terminal,self.terminal)

    async def test_reconciliation_and_owner_release_keep_terminal_ids(self):
        request = self.release_request
        completed = ReleaseReceipt(request.kind,request.request_id,transition_request_sha256(request),self.complete)
        self.store.replies.update(reconcile=self.terminal,release=completed)
        self.assertIs(await self.port.reconcile(self.op,self.terminal.receipt_sha256),self.terminal)
        self.assertIs(await self.port.release(request),completed)
        self.store.replies["reconcile"] = replace(self.terminal,receipt_sha256=PIN)
        with self.assertRaisesRegex(Hold,"shared_terminal_binding"):
            await self.port.reconcile(self.op,self.terminal.receipt_sha256)
        self.store.replies["release"] = replace(completed,completion=replace(self.complete,accepted=False))
        with self.assertRaisesRegex(Hold,"shared_completion_binding"):
            await self.port.release(request)
        with self.assertRaisesRegex(Hold,"shared_claim_owner"):
            await self.port.release(replace(request,completion=replace(self.complete,
                                                  fence=replace(self.fence,owner_session="different"))))
        self.store.replies["release"] = replace(completed,request_id="another-original")
        with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
            await self.port.release(request)
        self.assertEqual([n for n,_ in self.store.calls],["reconcile","release","reconcile","release","release"])


class ScriptedReader(SharedAtomicObserver):
    def __init__(self):
        self.calls = []
        self.result = None
        self.after_read = lambda: None

    async def lookup(self, binding, original):
        self.calls.append((binding,original))
        self.after_read()
        if isinstance(self.result,BaseException):
            raise self.result
        return ObservationReceipt(observation_lookup_sha256(binding,original),self.result)


class AuthenticatedObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = SharedCallTests()
        self.fx.setUp()
        self.reader = ScriptedReader()
        self.observer_identity = ObserverIdentity("independent-reader","a"*64,"b"*64,"reader-session")
        self.known_binding = None
        self.now = 20

    def original_epoch(self, epoch):
        a = self.fx.identity
        return AuthorityEpoch(a.authority_id,a.deployment_sha256,a.policy_sha256,epoch)

    def route_for(self, original, *, historical=False, current=None):
        f = self.fx.fence
        epoch = original.expected_coordinator_epoch if type(original) is HandoffRequest else f.coordinator_epoch
        retained = self.original_epoch(epoch)
        return ObservationRoute("historical" if historical else "current",self.fx.identity,
                                current or retained,retained,f.scope,f.source,
                                observation_original_sha256(original))

    def bind(self, original, *, route=None, identity=None, reader=None):
        route = route or self.route_for(original)
        identity = identity or self.observer_identity
        self.known_binding = ObservationBinding(identity,route,10,40,PIN)
        return SharedEffectObserver(reader or self.reader,identity,route,
                                    verify_binding=lambda *_:self.known_binding,clock=lambda:self.now)

    async def test_mutation_lost_ack_reconciles_original_via_reader_no_second_write(self):
        fx = self.fx
        successor = replace(fx.fence,owner_session="next-session",generation=5,lease_id="new-lease",
                            coordinator_epoch=8)
        cases = (
            ("renew",fx.renew_request,fx.receipt(fx.renew_request,replace(fx.lease,expires_at=61))),
            ("handoff",fx.handoff_request,fx.receipt(fx.handoff_request,replace(fx.lease,fence=successor))),
            ("release",fx.release_request,
             ReleaseReceipt(fx.release_request.kind,fx.release_request.request_id,
                            transition_request_sha256(fx.release_request),fx.complete)),
        )
        for method, request, recorded in cases:
            with self.subTest(method=method):
                writes = []
                self.reader.result = None
                observer = self.bind(request)
                self.assertIsNone((await observer.lookup(request)).result)

                async def planned_once(identity, original):
                    self.assertIs(identity,fx.identity)
                    self.assertIs(original,request)
                    writes.append(original)
                    self.reader.result = recorded
                    raise TimeoutError("synthetic accepted original then lost ACK")

                setattr(fx.store,method,planned_once)
                with self.assertRaisesRegex(Hold,"^shared_authority_outcome_unknown$"):
                    await getattr(fx.port,method)(request)
                outcome = await observer.lookup(request)
                self.assertIs(outcome.result,recorded)
                self.assertEqual(await observer.lookup(request),outcome)
                self.assertEqual(writes,[request])
                self.assertFalse(hasattr(observer,"begin"))
                self.assertFalse(hasattr(observer,"release"))
                self.assertFalse(hasattr(outcome,"dispatch_fence"))

    async def test_separate_reader_authentication_and_configured_grant_are_mandatory(self):
        op = self.fx.op
        route = self.route_for(op)
        with self.assertRaisesRegex(Hold,"shared_observer_unavailable"):
            SharedEffectObserver(self.fx.store,self.observer_identity,route,
                                 verify_binding=lambda *_:None,clock=lambda:self.now)
        with self.assertRaisesRegex(Hold,"shared_observer_unavailable"):
            SharedEffectObserver(self.reader,self.fx.identity,route,
                                 verify_binding=lambda *_:None,clock=lambda:self.now)
        with self.assertRaisesRegex(Hold,"shared_observer_distinct"):
            self.bind(op,identity=replace(self.observer_identity,session_id=self.fx.identity.session_id))
        observer = self.bind(op)
        self.known_binding = replace(self.known_binding,
                                     identity=replace(self.observer_identity,session_id="other-reader"))
        with self.assertRaisesRegex(Hold,"shared_observer_binding"):
            await observer.lookup(op)
        self.assertEqual(self.reader.calls,[])

    async def test_same_immutable_issue_in_new_project_is_not_an_observer_alias(self):
        op = self.fx.op
        route = self.route_for(op)
        observer = self.bind(op)
        moved_key = replace(op.fence.scope.key,project_id=str(UUID(int=998)))
        moved_scope = replace(op.fence.scope,key=moved_key)
        moved = replace(op,fence=replace(op.fence,scope=moved_scope))
        self.assertEqual(moved.fence.scope.key.issue_id,op.fence.scope.key.issue_id)
        self.assertEqual(moved.fence.scope.key.workspace_id,op.fence.scope.key.workspace_id)
        with self.assertRaisesRegex(Hold,"shared_observation_original"):
            await observer.lookup(moved)
        moved_route = replace(route,scope=moved_scope)
        moved_observer = self.bind(op,route=moved_route)
        with self.assertRaisesRegex(Hold,"shared_observation_scope"):
            await moved_observer.lookup(op)
        changed_scope = replace(op.fence.scope,repository_id="gh-repo:999")
        repo_observer = self.bind(op,route=replace(route,scope=changed_scope))
        with self.assertRaisesRegex(Hold,"shared_observation_scope"):
            await repo_observer.lookup(op)
        self.assertEqual(self.reader.calls,[])

    async def test_lookup_expiry_checks_both_before_and_after_await(self):
        op = self.fx.op
        self.reader.result = OriginalEffect(op,"pending")
        observer = self.bind(op)
        self.now = 40
        with self.assertRaisesRegex(Hold,"shared_observer_expiry"):
            await observer.lookup(op)
        self.assertEqual(self.reader.calls,[])
        self.now = 39
        self.reader.after_read = lambda:setattr(self,"now",40)
        with self.assertRaisesRegex(Hold,"shared_observer_expiry"):
            await observer.lookup(op)
        self.assertEqual(len(self.reader.calls),1)

    async def test_epoch_rotation_needs_explicit_historical_reader_and_never_grants_effect(self):
        op = self.fx.op
        old_route = self.route_for(op)
        new_current = replace(old_route.current,epoch=old_route.current.epoch+1,
                              deployment_sha256="1"*64,policy_sha256="2"*64)
        with self.assertRaisesRegex(Hold,"shared_observation_epoch"):
            replace(old_route,current=new_current)
        with self.assertRaisesRegex(Hold,"shared_observation_route"):
            self.route_for(op,historical=True,current=replace(new_current,authority_id="wrong"))
        old_reader = self.bind(op)
        historical_route = self.route_for(op,historical=True,current=new_current)
        self.reader.after_read = lambda:setattr(self,"known_binding",replace(self.known_binding,route=historical_route))
        self.reader.result = OriginalEffect(op,"terminal",self.fx.terminal)
        with self.assertRaisesRegex(Hold,"shared_observer_binding"):
            await old_reader.lookup(op)
        self.reader.after_read = lambda:None
        history = self.bind(op,route=historical_route)
        self.assertIs((await history.lookup(op)).result,self.reader.result)
        live_identity = replace(self.fx.identity,deployment_sha256=new_current.deployment_sha256,
                                policy_sha256=new_current.policy_sha256)
        live = bind_effect_authority(mode="shared",shared_store=self.fx.store,identity=live_identity,
                                     verify_binding=lambda _,i:AuthorityBinding(i,PIN))
        for call in (lambda:live.begin(op),lambda:live.observe_original(op),
                     lambda:live.reconcile(op,self.fx.terminal.receipt_sha256)):
            with self.assertRaisesRegex(Hold,"shared_fence_authority"):
                await call()
        self.assertEqual(self.fx.store.calls,[])

    async def test_receipt_binding_and_relevant_transition_are_rechecked_after_lookup(self):
        req = self.fx.renew_request
        observer = self.bind(req)
        lease = replace(self.fx.lease,expires_at=67)
        wrong_req = replace(req,request_id="same-scope-not-original")
        self.reader.result = self.fx.receipt(wrong_req,lease)
        with self.assertRaisesRegex(Hold,"shared_lease_request_binding"):
            await observer.lookup(req)
        self.reader.result = self.fx.receipt(req,replace(lease,fence=replace(self.fx.fence,generation=9)))
        with self.assertRaisesRegex(Hold,"shared_renewal_binding"):
            await observer.lookup(req)
        self.reader.result = None
        self.assertIsNone((await observer.lookup(req)).result)
        async def old_reader(binding, original):
            alien = replace(binding,identity=replace(binding.identity,session_id="different-bound-reader"))
            return ObservationReceipt(observation_lookup_sha256(alien,original),None)
        self.reader.lookup = old_reader
        with self.assertRaisesRegex(Hold,"shared_observation_receipt_binding"):
            await observer.lookup(req)

    def test_canonical_wire_time_has_one_integer_encoding(self):
        lease = self.fx.lease
        for bad in (True,48.0,float("nan"),-1,MAX_WIRE_SECONDS+1):
            with self.assertRaisesRegex(Hold,"shared_wire_seconds"):
                replace(lease,expires_at=bad)
        self.assertEqual(replace(lease,expires_at=MAX_WIRE_SECONDS).expires_at,MAX_WIRE_SECONDS)
        r = self.fx.renew_request
        self.assertEqual(transition_request_sha256(r),transition_request_sha256(replace(r,current=replace(lease,expires_at=48))))
        self.assertNotEqual(transition_request_sha256(r),transition_request_sha256(replace(r,current=replace(lease,expires_at=49))))
        with self.assertRaisesRegex(Hold,"shared_lease_receipt"):
            LeaseReceipt("uif.shared-lease.renew/2",r.request_id,transition_request_sha256(r),lease)
        with self.assertRaisesRegex(Hold,"shared_wire_seconds"):
            ObservationBinding(self.observer_identity,self.route_for(r),20.0,30,PIN)


class ExactLocalCompatibility(unittest.TestCase):
    def test_real_native_boundary_keeps_original_local_registry_and_no_replay(self):
        fx = legacy.QuotaHandoffTests()
        fx.setUp()
        self.addCleanup(fx.tearDown)
        fx.provider("available",1)
        fx.claim()
        local = bind_effect_authority(mode="local", local=fx.registry)
        self.assertIs(local, fx.registry)
        with self.assertRaisesRegex(Hold,"shared_authority_configuration"):
            bind_effect_authority(mode="shared", local=fx.registry)
        profile = NativeProfile(fx.key,"native-session","native",1,legacy.SOURCE,3,30000)
        seen = []
        def pool(p, oid):
            seen.append(("pool",oid))
            return PoolPermit(p.key,p.generation,oid,"legacy-lease",PIN,fx.now)
        def run(_,permit):
            seen.append(("dispatch",permit.operation_id))
        def observe(_,oid,*args):
            seen.append(("receipt",oid))
            return {"status":"verified","receipt_sha256":PIN}
        native = NativeQuotaBoundary(local, profile_for=lambda _:profile, pool_admit=pool,
            outcome_read=observe,provider_read=lambda _:self.fail("unexpected provider read"))
        issue = SimpleNamespace(id=fx.key.issue_id)
        first = native.dispatch(issue,operation_id="existing-original",dispatch_native=run)
        second = native.dispatch(issue,operation_id="existing-original",dispatch_native=run)
        self.assertEqual(first,second)
        self.assertEqual([name for name,_ in seen],["pool","dispatch","receipt"])
        self.assertEqual(first["status"],"verified")
