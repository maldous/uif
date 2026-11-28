"""Exact optional account debt readback, exercised through the existing transport."""

from dataclasses import asdict
import unittest
from unittest.mock import Mock

from test_native_remote_authority import NOW, QUOTA, ROUTE, make
from uif.handoff_store import Hold
from uif.stokowski_cloudflare.shared_admission import AccountReservationSnapshot


class RemoteUsageReservationTests(unittest.TestCase):
    def setUp(self):
        worker, self.remote = make()
        self.raw = worker.request('/v1/usage/read', {'route': asdict(ROUTE)})
        self.remote.transport = Mock()
        self.remote.transport.request.return_value = self.raw
        self.current = dict(receipt_sha256=self.raw['receipt_sha256'], observed_at=int(NOW.timestamp()),
                            debt_millineurons=456, ceiling_breached=False)

    def test_present_snapshot_attaches_to_the_exact_existing_readback(self):
        self.raw['reservation_snapshot'] = self.current
        reading = self.remote.read_usage(ROUTE)
        self.assertEqual(reading.reservation_snapshot, AccountReservationSnapshot(**self.current))
        self.assertEqual(reading.receipt_sha256, self.current['receipt_sha256'])
        self.assertTrue(self.remote.verify_usage(QUOTA, reading))
        self.remote.transport.request.assert_called_once_with('/v1/usage/read', {'route': asdict(ROUTE)})

    def test_old_receiver_can_omit_optional_snapshot(self):
        reading = self.remote.read_usage(ROUTE)
        self.assertIsNone(reading.reservation_snapshot)
        self.assertTrue(self.remote.verify_usage(QUOTA, reading))

    def test_wrong_and_unsafe_additional_evidence_is_refused(self):
        invalid = (None, {}, dict(self.current, receipt_sha256='a' * 64),
                   dict(self.current, observed_at=int(NOW.timestamp()) - 1),
                   dict(self.current, observed_at=int(NOW.timestamp()) + 91),
                   dict(self.current, observed_at=int(NOW.timestamp()) + 200),
                   dict(self.current, observed_at=float(int(NOW.timestamp()))),
                   dict(self.current, debt_millineurons=-1), dict(self.current, debt_millineurons=2**53),
                   dict(self.current, debt_millineurons=True), dict(self.current, ceiling_breached=1),
                   dict(self.current, original_id='private'))
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                self.raw['reservation_snapshot'] = snapshot
                with self.assertRaisesRegex(Hold, '^authority_quota_reservation_binding$'):
                    self.remote.read_usage(ROUTE)
        self.raw['reservation_snapshot'] = self.current
        self.raw['projection']['utc_day'] = '2030-01-12'
        with self.assertRaisesRegex(Hold, '^authority_quota_reservation_binding$'):
            self.remote.read_usage(ROUTE)
