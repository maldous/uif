"""Typed offline two-principal entry; no ingress, credentials or provider."""
from dataclasses import replace
import unittest

import test_shared_issue_coordinator as fixtures
from uif.effect_authority import AuthorityBinding, bind_effect_authority
from uif.handoff_store import Hold
from uif.shared_peer_entry import PeerGrant, PeerRegistration, SharedPeerEntry, VerifiedTransportPeer


class SharedPeerEntryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExistingJournalTests('test_stale_original_and_duplicate_receive_never_pass')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.native, self.cloud = object(), object()
        self.connected = {
            self.native: VerifiedTransportPeer('native-peer', fixtures.NATIVE.session_id),
            self.cloud: VerifiedTransportPeer('cloud-peer', fixtures.CLOUD.session_id),
        }
        self.granted = lambda _: True
        literal = PeerGrant(fixtures.SCOPE, fixtures.SOURCE,
                            frozenset({'claim','observe_claim','begin','recheck','observe_original',
                                       'renew','handoff','reconcile','release'}))
        self.peers = (
            PeerRegistration(self.fixture.session_native, (literal,)),
            PeerRegistration(self.fixture.session_cloud,
                (replace(literal, actions=literal.actions | {'receive_handoff'}),)),
        )

    def entry(self, **overrides):
        options = dict(authority=fixtures.NATIVE, peers=self.peers,
            authenticate_transport=self.connected.get, verify_current_grant=lambda access:self.granted(access),
            verify_reconstruction=lambda _,r:r.reconstruction_sha256==fixtures.PINS[2],
            verify_handoff=lambda _,__,r:r.quiesced_sha256==fixtures.PINS[3],
            verify_completion=lambda _,c:c.receipt_sha256==fixtures.PINS[4],
            verify_terminal=lambda _,op,sha:self.fixture.terminals.get((op,sha)))
        return SharedPeerEntry(self.fixture.registry, **(options | overrides))

    @staticmethod
    def client(store, identity):
        return bind_effect_authority(mode='shared', shared_store=store, identity=identity,
            verify_binding=lambda _,i:AuthorityBinding(i, fixtures.PINS[1]))

    def state(self):
        return self.fixture.registry.store._read('state.json')[1]

    def test_two_verified_principals_share_journal_and_only_successor_can_act(self):
        entry = self.entry()
        a = self.client(entry.bind(self.native), fixtures.NATIVE)
        b = self.client(entry.bind(self.cloud), fixtures.CLOUD)
        original = self.fixture.request()
        claim = fixtures.arun(a.claim(original))
        self.assertEqual(fixtures.arun(a.observe_claim(original)), claim)
        rival = self.fixture.request(fixtures.CLOUD,name='fixture-other-client')
        with self.assertRaisesRegex(Hold, 'issue_needs_reconciliation'):
            fixtures.arun(b.claim(rival))
        self.assertIsNone(fixtures.arun(b.observe_claim(rival)))
        handed = fixtures.arun(b.handoff(self.fixture.handoff(claim.lease)))
        op = self.fixture.operation(handed.lease)
        with self.assertRaisesRegex(Hold, 'owner_not_current'):
            fixtures.arun(a.begin(self.fixture.operation(claim.lease)))
        pending = fixtures.arun(b.begin(op))
        self.assertEqual(fixtures.arun(b.recheck(pending)), pending)
        self.assertEqual(fixtures.arun(b.observe_original(op)).state, 'pending')

    def test_unknown_missing_forged_or_crossed_principal_cannot_bind(self):
        entry = self.entry()
        before = self.state()
        for supplied in (None, object(), 'native-peer', self.connected[self.native], {'principal':'native-peer'}):
            with self.subTest(supplied=type(supplied)), self.assertRaisesRegex(Hold, 'not_authenticated'):
                entry.bind(supplied)
        for principal in ('fixture-unregistered', 'cloud-peer'):
            self.connected[self.native] = VerifiedTransportPeer(principal, fixtures.NATIVE.session_id)
            with self.assertRaisesRegex(Hold, 'not_authenticated'):
                entry.bind(self.native)
        self.assertEqual(self.state(), before)

    def test_payload_identity_cannot_select_peer_or_arbitrary_call(self):
        a = self.entry().bind(self.native)
        before = self.state()
        with self.assertRaisesRegex(Hold, 'identity_mismatch'):
            fixtures.arun(a.claim(fixtures.CLOUD, self.fixture.request(fixtures.CLOUD)))
        with self.assertRaisesRegex(Hold, 'claim_request'):
            fixtures.arun(a.claim(fixtures.NATIVE, self.fixture.request(fixtures.CLOUD)))
        with self.assertRaisesRegex(Hold, 'typed_request'):
            fixtures.arun(a.claim(fixtures.NATIVE, {'principal':'native-peer','action':'claim'}))
        for absent in ('invoke','status','consume_once','lookup','request','provider','open'):
            self.assertFalse(hasattr(a, absent))
        self.assertEqual(self.state(), before)

    def test_wrong_issue_route_repository_or_source_denied_before_any_lock(self):
        original = self.fixture.request()
        a = self.entry().bind(self.native)
        before = self.state(), set(self.fixture.path.iterdir())
        for other in (
            replace(original, scope=replace(fixtures.SCOPE, key=replace(fixtures.KEY, issue_id=fixtures.OTHER))),
            replace(original, scope=replace(fixtures.SCOPE, key=replace(fixtures.KEY, project_id=fixtures.MOVED))),
            replace(original, scope=replace(fixtures.SCOPE, repository_id='other-repo')),
            replace(original, source=replace(fixtures.SOURCE, head='9'*40)),
        ):
            with self.subTest(other=other), self.assertRaisesRegex(Hold, 'scope_or_grant'):
                fixtures.arun(a.claim(fixtures.NATIVE, other))
        self.assertEqual((self.state(), set(self.fixture.path.iterdir())), before)

    def test_action_and_successor_grants_are_independent_of_caller(self):
        limited = replace(self.peers[0], grants=(replace(self.peers[0].grants[0], actions=frozenset({'claim'})),))
        a = self.entry(peers=(limited,self.peers[1])).bind(self.native)
        original = self.fixture.request()
        claim = fixtures.arun(a.claim(fixtures.NATIVE, original))
        before = self.state()
        with self.assertRaisesRegex(Hold, 'scope_or_grant'):
            fixtures.arun(a.observe_claim(fixtures.NATIVE, original))
        entry = self.entry()
        b = entry.bind(self.cloud)
        self.granted = lambda access: access.action != 'receive_handoff'
        with self.assertRaisesRegex(Hold, 'admission_unverified'):
            fixtures.arun(b.handoff(fixtures.CLOUD, self.fixture.handoff(claim.lease)))
        self.assertEqual(self.state(), before)

    def test_transport_revocation_after_bind_denies_reads_and_mutations(self):
        a = self.entry().bind(self.native)
        original = self.fixture.request()
        fixtures.arun(a.claim(fixtures.NATIVE, original))
        before = self.state()
        del self.connected[self.native]
        for action in (a.observe_claim, a.claim):
            with self.assertRaisesRegex(Hold, 'not_authenticated'):
                fixtures.arun(action(fixtures.NATIVE, original))
        self.assertEqual(self.state(), before)

    def test_transport_rebound_and_grant_revoked_between_checks_have_no_commit(self):
        before = self.state()
        answers = iter((self.connected[self.native],self.connected[self.native],self.connected[self.cloud]))
        a = self.entry(authenticate_transport=lambda _:next(answers)).bind(self.native)
        with self.assertRaisesRegex(Hold, 'peer_not_authenticated'):
            fixtures.arun(a.claim(fixtures.NATIVE, self.fixture.request()))
        a = self.entry().bind(self.native)
        allowed = iter((True,False))
        self.granted = lambda _:next(allowed)
        with self.assertRaisesRegex(Hold, 'admission_unverified'):
            fixtures.arun(a.claim(fixtures.NATIVE, self.fixture.request()))
        self.assertEqual(self.state(), before)

    def test_missing_verifiers_ambiguous_registrations_and_wildcards_refused(self):
        for override in ({'authenticate_transport':None}, {'verify_current_grant':None}, {'peers':()}):
            with self.assertRaisesRegex(Hold, 'entry_unconfigured'):
                self.entry(**override)
        for ambiguous in (
            replace(self.peers[1],session=replace(self.peers[1].session,principal='native-peer')),
            replace(self.peers[1],session=replace(self.peers[1].session,identity=fixtures.NATIVE)),
        ):
            with self.assertRaisesRegex(Hold, 'registration_ambiguous'):
                self.entry(peers=(self.peers[0],ambiguous))
        for bad in ({'*'}, {'accept'}, set()):
            with self.assertRaisesRegex(Hold, 'exact_grant'):
                PeerGrant(fixtures.SCOPE,fixtures.SOURCE,frozenset(bad))

    def test_configured_but_not_registered_in_the_journal_cannot_claim(self):
        impostor = replace(self.peers[0],session=replace(self.peers[0].session, actor='cloud'))
        a = self.entry(peers=(impostor,self.peers[1])).bind(self.native)
        before = self.state()
        with self.assertRaisesRegex(Hold,'unauthenticated_actor'):
            fixtures.arun(a.claim(fixtures.NATIVE, self.fixture.request()))
        self.assertEqual(self.state(), before)


if __name__ == '__main__':
    unittest.main()
