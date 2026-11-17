"""Synthetic synchronous admission races; no installed hooks or remote effects."""

from types import SimpleNamespace
import unittest

import test_quota_handoff as fixtures
from uif.handoff_store import Hold
from uif.native_quota import NativeProfile, NativeQuotaBoundary, PoolPermit


class SyncNativeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.QuotaHandoffTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.fx.provider("available", 1)
        self.fx.claim()
        self.issue = SimpleNamespace(id=self.fx.key.issue_id)
        self.profile = NativeProfile(self.fx.key, "native-session", "native", 1,
                                     fixtures.SOURCE, 3, 30000)
        self.calls = []
        self.after_reservation = lambda: None
        self.boundary = NativeQuotaBoundary(
            self.fx.registry, profile_for=lambda _: self.profile,
            pool_admit=self.admit, outcome_read=self.observe,
            provider_read=lambda _: self.fail("unexpected provider acquisition"))

    def admit(self, profile, operation_id):
        self.calls.append("pool")
        self.after_reservation()
        return PoolPermit(profile.key, profile.generation, operation_id,
                          "original-lease", fixtures.PIN, self.fx.now)

    def launch(self, issue, permit):
        self.calls.append("launch")
        self.assertIs(issue, self.issue)
        self.assertEqual(permit.lease_id, "original-lease")

    def observe(self, *_):
        self.calls.append("observe")
        return dict(status="verified", receipt_sha256=fixtures.PIN)

    def dispatch(self, operation_id="original-dispatch"):
        return self.boundary.dispatch(self.issue, operation_id=operation_id,
                                      dispatch_native=self.launch)

    def operation(self):
        task = self.fx.status()["tasks"][self.fx.key.identity]
        return task["operations"]["original-dispatch"]

    def test_quota_observed_during_pool_reservation_refuses_child_launch(self):
        self.after_reservation = lambda: self.fx.provider("hard-quota", 2)
        with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
            self.dispatch()
        self.assertEqual(self.calls, ["pool"])
        self.assertEqual(self.operation()["status"], "unknown")
        # The real Pool may already have reserved resources. Restoration cannot
        # replay it or admit a replacement until the original is reconciled.
        self.fx.provider("available", 3)
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            self.dispatch()
        with self.assertRaisesRegex(Hold, "uncertain_operations_held"):
            self.dispatch("replacement-cannot-bypass")
        self.assertEqual(self.calls, ["pool"])

    def test_stop_observed_during_pool_reservation_refuses_child_launch(self):
        self.after_reservation = lambda: self.fx.request("morgan", "set_stopped", stopped=True)
        with self.assertRaisesRegex(Hold, "service_stopped"):
            self.dispatch()
        self.assertEqual(self.calls, ["pool"])
        self.assertEqual(self.operation()["status"], "unknown")
        self.fx.request("morgan", "set_stopped", stopped=False)
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            self.dispatch()
        self.assertEqual(self.calls, ["pool"])

    def test_provider_expiring_during_pool_reservation_refuses_child_launch(self):
        def expire_provider():
            self.fx.now += 91

        self.after_reservation = expire_provider
        with self.assertRaisesRegex(Hold, "stale_evidence"):
            self.dispatch()
        # The freshly returned permit does not refresh provider availability.
        self.assertEqual(self.calls, ["pool"])
        self.assertEqual(self.operation()["status"], "unknown")

    def test_unchanged_admission_launches_and_observes_original_only_once(self):
        first = self.dispatch()
        self.assertEqual(first["status"], "verified")
        self.assertEqual(self.dispatch(), first)
        self.assertEqual(self.calls, ["pool", "launch", "observe"])
        self.assertEqual(self.fx.status()["tasks"][self.fx.key.identity]["state"], "active")


if __name__ == "__main__":
    unittest.main()
