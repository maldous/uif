"""Synthetic retry proof-window changes, without timers or remote effects."""

from types import SimpleNamespace
import unittest

import test_quota_handoff as fixtures
from uif.handoff_store import Hold, digest
from uif.native_quota import NativeProfile, NativeQuotaBoundary


class SyncNativeRetryTests(unittest.TestCase):
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
        self.change_during_proof = lambda: None
        original = self.fx.registry.recovery_verifier

        def reconstruct(context):
            self.change_during_proof()
            return original(context)

        self.fx.registry.recovery_verifier = reconstruct
        self.boundary = NativeQuotaBoundary(
            self.fx.registry, profile_for=lambda _: self.profile,
            pool_admit=lambda *_: self.fail("retry reserved a Pool lease"),
            outcome_read=lambda *_: self.fail("retry observed a native dispatch"),
            provider_read=lambda _: self.fail("unexpected provider acquisition"))

    def schedule(self, *_):
        self.calls.append("timer")
        return dict(status="verified", receipt_sha256=fixtures.PIN)

    def retry(self):
        return self.boundary.schedule_retry(self.issue, attempt=1, delay_ms=1000,
                                            schedule_native=self.schedule)

    def assert_refused(self, reason):
        result = self.retry()
        self.assertEqual(result["status"], "denied")
        self.assertEqual(self.calls, [])
        state = self.fx.status()
        expected = digest(dict(
            schema="uif-native-retry-refusal/1", key=self.fx.key.identity,
            generation=1, attempt=1, delay_ms=1000, provider=state["provider"],
            stopped=state["stopped"], reason=reason))
        self.assertEqual(result["receipt_sha256"], expected)
        self.assertEqual(state["tasks"][self.fx.key.identity]["operations"]["retry-1"], result)
        return result

    def test_hard_quota_during_proof_records_no_start_and_cannot_replay_after_restore(self):
        self.change_during_proof = lambda: self.fx.provider("hard-quota", 2)
        original = self.assert_refused("provider-unavailable")
        self.fx.provider("available", 3)
        self.change_during_proof = lambda: self.fail("terminal timer repeated reconstruction")
        self.assertEqual(self.retry(), original)
        self.assertEqual(self.calls, [])

    def test_auth_failure_during_proof_does_not_schedule(self):
        self.change_during_proof = lambda: self.fx.provider("auth-failure", 2)
        self.assert_refused("provider-unavailable")

    def test_unknown_provider_during_proof_does_not_schedule(self):
        self.change_during_proof = lambda: self.fx.provider("unknown", 2)
        self.assert_refused("provider-unavailable")

    def test_provider_expiry_during_proof_does_not_schedule(self):
        def expire():
            self.fx.now += 91

        self.change_during_proof = expire
        self.assert_refused("provider-evidence-stale")

    def test_transient_retry_retains_existing_timer_and_original_receipt_once(self):
        self.fx.provider("transient", 2)
        original = self.retry()
        self.assertEqual(original["status"], "verified")
        self.assertEqual(self.retry(), original)
        self.assertEqual(self.calls, ["timer"])

    def test_stop_during_proof_still_refuses_before_timer_intent(self):
        self.change_during_proof = lambda: self.fx.request("morgan", "set_stopped", stopped=True)
        with self.assertRaisesRegex(Hold, "service_stopped"):
            self.retry()
        self.assertEqual(self.calls, [])
        self.assertNotIn("retry-1", self.fx.status()["tasks"][self.fx.key.identity]["operations"])


if __name__ == "__main__":
    unittest.main()
