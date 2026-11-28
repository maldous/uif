"""Read-only adoption of the existing authority's recipient transition."""
from copy import deepcopy
from dataclasses import asdict, replace
import unittest
from unittest.mock import Mock, patch

from test_native_remote_authority import FENCE, GRANT, NOW, OWNER, QUOTA, RECORD, make
from uif.handoff_store import Hold


class UnstartedRecoveryTest(unittest.TestCase):
    def setUp(self):
        _, self.remote = make()
        self.prior = replace(RECORD.lease, fence=replace(FENCE, owner_session='morgan', lease_id='old'))
        self.current = replace(RECORD, lease=replace(RECORD.lease, fence=replace(FENCE, generation=2)))
        self.body = dict(current=asdict(self.prior), expected_coordinator_epoch=1,
                         request_id='accept', successor_session=OWNER.session_id,
                         quiesced_sha256='7' * 64, reconstruction_sha256=GRANT.reconstruction_sha256,
                         recovery=dict(attempt_id='assess', revocation_id='revoke-original'))
        self.realm = '9' * 64
        self.binding = dict(installation_sha256=self.realm, handoff=self.body)
        self.lookup = dict(installation_sha256=self.realm, request_id='accept', session_id=OWNER.session_id)
        kind = 'uif.shared-lease.recover-unstarted/1'
        # Computed by the authority's protocol.mjs digest of this recipient request.
        self.receipt = dict(request_id='accept', request_kind=kind,
                            request_sha256='a37ac01e7ea533e0c93bc0dcfcaa7d2771ede2fea48857d2aa9db870cdc8d7a8',
                            attempt_id='assess', lease=asdict(self.current.lease))
        self.status = dict(asdict(OWNER), role='native', stopped=False, coordinator_epoch=1,
                           lease_seconds=300, quota_issuer=asdict(QUOTA),
                           commissioned_sha256=self.realm, installation_sha256=self.realm)
        self.remote.transport.request = Mock(side_effect=self.read)
        self.path = '/protected/native/recipient-original'

    def read(self, path, data=None, **options):
        if path == '/v1/status':
            return self.status
        if path == '/v1/transition/original':
            self.assertEqual(data, self.lookup)
            self.assertEqual(options, dict(allow_absent=True))
            return self.receipt
        if path == '/v1/claim/read':
            self.assertEqual(data, dict(project='OffMenu', by_attempt=True, identifier='assess'))
            return asdict(self.current) if self.current else None
        self.fail(path)

    def observe(self, grant=GRANT):
        with patch('uif.stokowski_cloudflare.remote_authority.protected_record',
                   return_value=self.binding) as protected:
            try:
                return self.remote.observe_unstarted_recovery(grant, self.path)
            finally:
                protected.assert_called_once_with(self.path)

    def test_reads_exact_original_and_renewed_current_fence(self):
        self.current = replace(self.current, lease=replace(self.current.lease,
                               expires_at=self.current.lease.expires_at + 10))
        self.assertEqual(self.observe(), self.current)
        self.assertEqual([c.args[0] for c in self.remote.transport.request.call_args_list],
                         ['/v1/status', '/v1/transition/original', '/v1/claim/read'])

    def test_rejects_changed_original_or_receipt(self):
        for path, value in [
            (('handoff', 'current', 'expires_at'), self.prior.expires_at - 1),
            (('handoff', 'current', 'fence', 'lease_id'), 'another-prior'),
            (('handoff', 'quiesced_sha256'), '8' * 64),
            (('handoff', 'recovery', 'revocation_id'), 'another-revocation'),
            (('handoff', 'recovery', 'attempt_id'), 'another-attempt'),
            (('handoff', 'successor_session'), 'other-owner'),
            (('installation_sha256',), '0' * 64),
        ]:
            with self.subTest(path=path):
                saved = self.binding
                self.binding = item = deepcopy(saved)
                for key in path[:-1]:
                    item = item[key]
                item[path[-1]] = value
                with self.assertRaises(Hold):
                    self.observe()
                self.binding = saved
        for field, value in [('request_kind', 'uif.shared-lease.handoff/3'), ('request_id', 'another'),
                             ('request_sha256', '8' * 64), ('attempt_id', 'investigate')]:
            with self.subTest(field=field), patch.dict(self.receipt, {field: value}), self.assertRaises(Hold):
                self.observe()
        self.receipt = None
        with self.assertRaisesRegex(Hold, 'recovery_receipt'):
            self.observe()

    def test_rejects_stopped_wrong_epoch_and_uncommissioned(self):
        for field, value in [('stopped', True), ('coordinator_epoch', 2), ('commissioned_sha256', '0' * 64)]:
            with self.subTest(field=field), patch.dict(self.status, {field: value}), self.assertRaises(Hold):
                self.observe()
        self.assertTrue(all(call.args == ('/v1/status',) for call in self.remote.transport.request.call_args_list))

    def test_marked_assessment_cannot_adopt_unmarked_peer_handoff(self):
        with self.assertRaisesRegex(Hold, 'recovery_assignment'):
            self.observe(replace(GRANT, first_intent_sha256='8' * 64))
        self.remote.transport.request.assert_not_called()

    def test_unprotected_input_and_transport_loss_do_not_write(self):
        with self.assertRaisesRegex(Hold, 'authority_config_path'):
            self.remote.observe_unstarted_recovery(GRANT, 'relative')
        self.remote.transport.request.assert_not_called()
        from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable
        self.remote.transport.request.side_effect = (self.status, AuthorityUnavailable('uncertain'))
        with self.assertRaisesRegex(AuthorityUnavailable, 'uncertain'):
            self.observe()
        self.assertEqual([c.args[0] for c in self.remote.transport.request.call_args_list],
                         ['/v1/status', '/v1/transition/original'])

    def test_rejects_reused_lease_different_generation_and_stale_current(self):
        fence = self.receipt['lease']['fence']
        for field, value in [('lease_id', 'old'), ('generation', 1), ('owner_session', 'morgan')]:
            with self.subTest(field=field), patch.dict(fence, {field: value}), self.assertRaises(Hold):
                self.observe()
        moved = self.current
        for stale in (replace(moved.lease, fence=replace(moved.lease.fence, lease_id='superseded')),
                      replace(moved.lease, expires_at=int(NOW.timestamp()))):
            self.current = replace(moved, lease=stale)
            with self.assertRaisesRegex(Hold, 'not_current'):
                self.observe()
        self.current = None
        with self.assertRaisesRegex(Hold, 'not_current'):
            self.observe()
