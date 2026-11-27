"""Existing authenticated native release original, no outbound test effects."""
from dataclasses import asdict
import unittest

from test_native_remote_authority import FENCE, OWNER, RECORD, make
from uif.effect_authority import Completion
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable


class NativeReleaseObservationTest(unittest.TestCase):
    def fixture(self, mode):
        worker, remote = make()
        worker.completion = Completion(FENCE, True, '7'*64)
        original_transport = worker.request
        claims = []
        receipt = None

        def request(path, body=None, **kwargs):
            nonlocal receipt
            claims.append(path)
            if path == '/v1/release':
                self.assertIsNone(receipt, 'unexpected second release mutation')
                receipt = original_transport(path, body, **kwargs)
                raise AuthorityUnavailable('fixture-lost-ack')
            if path == '/v1/status':
                status = original_transport(path, body, **kwargs)
                return status | {'installation_sha256':'1'*64,
                                 'commissioned_sha256':('2' if mode == 'drift' else '1')*64}
            if path == '/v1/transition/original':
                self.assertEqual(kwargs, {'allow_absent':True})
                self.assertEqual(body, {'installation_sha256':'1'*64, 'session_id':OWNER.session_id,
                                        'request_id':receipt['request_id']})
                if mode == 'unavailable':
                    raise AuthorityUnavailable('fixture-observe-unavailable')
                if mode == 'absent':
                    return None
                if mode == 'wrong_digest':
                    return receipt | {'request_sha256':'0'*64}
                if mode == 'wrong_fence':
                    changed = receipt['completion'] | {'fence':asdict(FENCE) | {'generation':99}}
                    return receipt | {'completion':changed}
                if mode == 'wrong_type':
                    return receipt | {'request_kind':'uif.shared-lease.claim/3'}
                return receipt
            return original_transport(path, body, **kwargs)

        worker.request = request
        return worker, remote, claims

    def test_exact_readback_resolves_ack_loss_without_second_mutation(self):
        worker, remote, calls = self.fixture('verified')
        out = remote.release_verified(RECORD.lease)
        self.assertEqual(out.completion, worker.completion)
        self.assertEqual(calls, ['/v1/completion/read','/v1/release','/v1/status','/v1/transition/original'])

    def test_absence_unreadable_or_unbound_receipt_stays_uncertain_no_repeat(self):
        for mode in ('drift','unavailable','absent','wrong_digest','wrong_fence','wrong_type'):
            with self.subTest(mode=mode):
                _, remote, calls = self.fixture(mode)
                with self.assertRaisesRegex(AuthorityUnavailable,'fixture-lost-ack'):
                    remote.release_verified(RECORD.lease)
                self.assertEqual(calls.count('/v1/release'),1)
                self.assertEqual(calls.count('/v1/transition/original'),0 if mode == 'drift' else 1)
