from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, digest
from uif.stokowski_cloudflare.fresh_assessment_terminal import observe_consumed_fresh_assessment_successor
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.native_composition import NativeComposition
import test_native_remote_authority as fixture


class ConsumedAssessmentSuccessorTest(unittest.TestCase):
    def setUp(self):
        self.worker, self.remote = fixture.make()
        self.composition = NativeComposition(self.remote, '/unused-fixture')
        self.issue = SimpleNamespace(id=fixture.ISSUE, identifier='OFF-1', title='Test', description='Test')
        self.scope = fixture.GRANT.scope_sha256
        stages = intake_stages({}, 'OffMenu', self.issue, self.scope, 1)
        self.previous = stages['assess']['attempt']
        self.successor = stages['investigate']['attempt']
        self.grant = replace(fixture.GRANT, attempts=(self.previous, self.successor), first_intent_sha256='d' * 64)
        self.composition.assignment = Mock(return_value=self.grant)
        now = int(fixture.NOW.timestamp())
        initial = replace(fixture.RECORD.lease, expires_at=now - 20)
        self.cas_lease = replace(initial, expires_at=now - 5)
        self.current = replace(initial, expires_at=now + 100)
        self.worker.claim = replace(fixture.RECORD, attempt_id=self.successor, lease=self.current)
        request = self.grant.claim(fixture.OWNER)
        self.worker.original = dict(request_kind=request.kind, request_id=request.request_id,
                                    request_sha256=transition_request_sha256(request), lease=asdict(initial))
        self.cas = dict(project='OffMenu', attempt_id=self.successor, lease=asdict(self.cas_lease))
        self.advance = dict(request_id=digest({'lease': initial.fence.lease_id, 'attempt': self.successor}),
                            current=asdict(self.cas_lease), expected_attempt_id=self.previous, attempt_id=self.successor)
        self.check = {'matches': True}
        self.status = {'installation_sha256': '3' * 64}
        self.calls = []
        self.after_match = lambda: None
        underlying = self.worker.request

        def send(path, data=None, **options):
            self.calls.append(path)
            if path == '/v1/status':
                return underlying(path, data, **options) | self.status
            if path == '/v1/claim/read':
                self.assertEqual(data, dict(project='OffMenu', identifier=self.successor, by_attempt=True))
                return underlying(path, data, **options)
            if path == '/v1/claim/observe':
                self.assertEqual(data['first_intent_sha256'], self.grant.first_intent_sha256)
                return underlying(path, data, **options)
            if path == '/v1/transition/original':
                self.assertEqual(data, dict(installation_sha256=self.status['installation_sha256'],
                                            session_id=fixture.OWNER.session_id, request_id=self.advance['request_id']))
                self.assertIs(options['allow_absent'], True)
                return self.cas
            self.assertEqual(path, '/v1/attempt/successor/read')
            self.assertEqual(data, {'current': asdict(self.current), 'advance': self.advance})
            self.after_match()
            return self.check

        self.remote.transport.request = send

    def observe(self):
        return observe_consumed_fresh_assessment_successor(self.composition, self.issue, 1, self.scope)

    def test_confirmed_original_uses_original_expiry_after_renewal(self):
        self.assertEqual(self.observe(), self.advance)
        self.assertEqual(self.calls, ['/v1/status', '/v1/claim/read', '/v1/claim/observe',
                                      '/v1/transition/original', '/v1/attempt/successor/read'])

    def test_missing_or_changed_cas_cannot_be_reconstructed_from_live_claim(self):
        good = self.cas
        for bad in [None, self.cas | {'attempt_id': self.previous}, self.cas | {'project':'UIF'},
                    self.cas | {'lease': asdict(replace(self.cas_lease, fence=replace(fixture.FENCE, lease_id='old')))},
                    self.cas | {'lease': asdict(replace(self.current, expires_at=self.current.expires_at + 1))}]:
            with self.subTest(receipt=bad):
                self.calls.clear()
                self.cas = bad
                with self.assertRaises(Hold):
                    self.observe()
                self.assertNotIn('/v1/attempt/successor/read', self.calls)
        self.cas = good
        self.worker.original = None
        with self.assertRaisesRegex(Hold, 'authority_original_claim_unverified'):
            self.observe()

    def test_missing_retained_control_or_inexact_remote_response_holds(self):
        for reply in [None, {'matches': False}, {'matches': 1}, {'matches': True, 'extra': 'unbound'}]:
            with self.subTest(reply=reply):
                self.check = reply
                with self.assertRaisesRegex(Hold, 'successor_original_unverified'):
                    self.observe()

    def test_old_owner_epoch_unmarked_or_changed_grant_holds(self):
        for status in [{'stopped': True}, {'coordinator_epoch': 2}]:
            with self.subTest(status=status):
                self.status.update(status)
                with self.assertRaisesRegex(Hold, 'successor_not_current'):
                    self.observe()
                self.status.pop(next(iter(status)))
        saved = self.worker.claim
        self.worker.claim = replace(saved, lease=replace(self.current, fence=replace(fixture.FENCE, owner_session='other')))
        with self.assertRaisesRegex(Hold, 'authority_native_claim_binding'):
            self.observe()
        self.worker.claim = saved
        unmarked = replace(self.grant, first_intent_sha256=None)
        self.composition.assignment.return_value = unmarked
        with self.assertRaisesRegex(Hold, 'successor_grant'):
            self.observe()
        self.composition.assignment.side_effect = [self.grant, unmarked]
        with self.assertRaisesRegex(Hold, 'successor_grant_changed'):
            self.observe()

    def test_lease_expired_while_reading_never_confirms(self):
        def expire():
            self.remote.clock = lambda: fixture.NOW + timedelta(seconds=100)
        self.after_match = expire
        with self.assertRaisesRegex(Hold, 'successor_grant_changed'):
            self.observe()
