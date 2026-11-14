"""Native seam source checks; upstream/installed lifecycle remains unmodified."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

import test_quota_handoff as fixtures
from uif.handoff_store import Hold
from uif.native_quota import NativeProfile, NativeQuotaBoundary, PoolPermit


class NativeQuotaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QuotaHandoffTests()
        self.fixture.setUp()
        self.fixture.provider("available", 1)
        self.fixture.claim()
        self.issue = SimpleNamespace(id=self.fixture.key.issue_id)
        self.profile = NativeProfile(self.fixture.key, "native-session", "native", 1,
                                     fixtures.SOURCE, 3, 30000)
        self.calls = []

        def admit(profile, operation):
            self.calls.append("real-pool-adapter")
            return PoolPermit(profile.key, profile.generation, operation, "original-lease", fixtures.PIN,
                              self.fixture.now)

        self.boundary = NativeQuotaBoundary(self.fixture.registry, profile_for=lambda _: self.profile,
                                            pool_admit=admit,
                                            outcome_read=lambda *_: dict(status="verified", receipt_sha256=fixtures.PIN),
                                            provider_read=lambda _: dict(classification="hard-quota", sequence=2,
                                                                         observed_at=self.fixture.now,
                                                                         evidence_sha256=fixtures.PIN))

    def tearDown(self):
        self.fixture.tearDown()

    def test_dispatch_uses_existing_pool_then_native_once(self):
        def native(issue, permit):
            self.calls.append("native-dispatch")
            self.assertEqual(permit.lease_id, "original-lease")
        self.boundary.dispatch(self.issue, operation_id="native-original", dispatch_native=native)
        self.boundary.dispatch(self.issue, operation_id="native-original", dispatch_native=native)
        self.assertEqual(self.calls, ["real-pool-adapter", "native-dispatch"])

    def test_verified_exhaustion_closes_dispatch_and_suppresses_native_retry(self):
        self.boundary.observe_provider(self.issue, request_id="provider-event-original")
        with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
            self.boundary.dispatch(self.issue, operation_id="no-launch", dispatch_native=lambda *_: self.fail())
        result = self.boundary.schedule_retry(self.issue, attempt=1, delay_ms=1000,
                                              schedule_native=lambda *_: self.fail("quota retry scheduled"))
        self.assertFalse(result["scheduled"])
        self.assertEqual(self.calls, [])

    def test_wrong_pool_binding_or_unobserved_dispatch_is_retained_unknown(self):
        self.boundary.pool_admit = lambda profile, operation: PoolPermit(
            profile.key, 99, operation, "wrong-lease", fixtures.PIN, self.fixture.now)
        with self.assertRaisesRegex(Hold, "native_pool_binding"):
            self.boundary.dispatch(self.issue, operation_id="held-original", dispatch_native=lambda *_: self.fail())
        task = self.fixture.status()["tasks"][self.fixture.key.identity]
        self.assertEqual(task["operations"]["held-original"]["status"], "unknown")

    def test_profile_source_and_generation_drift_refuses_before_metadata(self):
        self.profile = replace(self.profile, generation=2)
        with self.assertRaisesRegex(Hold, "native_claim_drift"):
            self.boundary.guarded_effect(self.issue, operation_id="metadata", payload_sha256=fixtures.PIN,
                                         effect=lambda: self.fail("obsolete metadata reached core"))

    def test_retry_retains_native_budget_and_effect_receipt(self):
        self.fixture.provider("transient", 2)
        result = self.boundary.schedule_retry(
            self.issue, attempt=1, delay_ms=1000,
            schedule_native=lambda *_: dict(status="verified", receipt_sha256=fixtures.PIN))
        self.assertEqual(result["status"], "verified")
        with self.assertRaisesRegex(Hold, "native_retry_budget"):
            self.boundary.schedule_retry(self.issue, attempt=4, delay_ms=1000,
                                          schedule_native=lambda *_: self.fail())


if __name__ == "__main__":
    unittest.main()
