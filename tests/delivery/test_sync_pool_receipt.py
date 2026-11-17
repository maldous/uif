"""Synthetic Pool receipt-shape guards; no native hooks or remote effects."""

from types import SimpleNamespace
import unittest

import test_quota_handoff as fixtures
from uif.handoff_store import Hold, digest
from uif.native_quota import NativeProfile, NativeQuotaBoundary, PoolPermit


class SyncPoolReceiptTests(unittest.TestCase):
    def scenario(self, receipt):
        fx = fixtures.QuotaHandoffTests()
        fx.setUp()
        self.addCleanup(fx.tearDown)
        fx.provider("available", 1)
        fx.claim()
        issue = SimpleNamespace(id=fx.key.issue_id)
        profile = NativeProfile(fx.key, "native-session", "native", 1,
                                fixtures.SOURCE, 3, 30000)
        calls = []

        def admit(current, operation_id):
            calls.append("pool")
            return PoolPermit(current.key, current.generation, operation_id,
                              "original-lease", receipt, fx.now)

        def launch(current_issue, permit):
            calls.append("launch")
            self.assertIs(current_issue, issue)
            self.assertEqual(permit.receipt_sha256, receipt)
            self.assertEqual(permit.lease_id, "original-lease")

        def observe(*_):
            calls.append("observe")
            return dict(status="verified", receipt_sha256=fixtures.PIN)

        boundary = NativeQuotaBoundary(
            fx.registry, profile_for=lambda _: profile, pool_admit=admit,
            outcome_read=observe,
            provider_read=lambda _: self.fail("unexpected provider acquisition"))

        def dispatch(operation_id="original-dispatch"):
            return boundary.dispatch(issue, operation_id=operation_id,
                                     dispatch_native=launch)

        return fx, calls, dispatch

    def test_invalid_receipts_refuse_before_launch_and_hold_original_intent(self):
        invalid = {
            "absent": None,
            "empty": "",
            "integer": 123,
            "bytes": b"d" * 64,
            "object": {"sha256": fixtures.PIN},
            "short": "d" * 63,
            "long": "d" * 65,
            "nonhex": "g" * 64,
            "uppercase": "D" * 64,
            "suffix": fixtures.PIN + "\n",
            "prefix": " " + fixtures.PIN,
        }
        for label, receipt in invalid.items():
            with self.subTest(receipt=label):
                fx, calls, dispatch = self.scenario(receipt)
                with self.assertRaisesRegex(Hold, "^native_pool_binding$"):
                    dispatch()
                self.assertEqual(calls, ["pool"])
                task = fx.status()["tasks"][fx.key.identity]
                self.assertEqual(task["operations"], {
                    "original-dispatch": dict(
                        status="unknown", generation=1,
                        payload_sha256=digest(dict(kind="native-dispatch", source=fixtures.SOURCE)))})
                self.assertEqual((task["state"], task["owner"], task["generation"]),
                                 ("active", "native", 1))
                self.assertEqual(task["source"], fixtures.SOURCE)
                self.assertEqual(task["original_operation_id"], "original-" + fx.key.issue_id)
                # Pool may already hold a reservation. A malformed receipt is
                # not proof of safe cleanup, terminal denial or permission to
                # replay the original or bypass it with a replacement ID.
                with self.assertRaisesRegex(Hold, "^original_operation_requires_reconciliation$"):
                    dispatch()
                with self.assertRaisesRegex(Hold, "^uncertain_operations_held$"):
                    dispatch("replacement-cannot-bypass")
                self.assertEqual(calls, ["pool"])
                self.assertEqual(fx.status()["tasks"][fx.key.identity], task)

    def test_valid_lowercase_sha_retains_once_only_dispatch_and_destination_receipt(self):
        # Shape validation is not cryptographic receipt authentication. The
        # independently observed destination receipt remains the result.
        receipt = "0123456789abcdef" * 4
        fx, calls, dispatch = self.scenario(receipt)
        original = dispatch()
        self.assertEqual(original["status"], "verified")
        self.assertEqual(original["receipt_sha256"], fixtures.PIN)
        self.assertNotEqual(original["receipt_sha256"], receipt)
        self.assertEqual(dispatch(), original)
        self.assertEqual(calls, ["pool", "launch", "observe"])
        task = fx.status()["tasks"][fx.key.identity]
        self.assertEqual(task["operations"], {"original-dispatch": original})
        self.assertEqual(task["state"], "active")


if __name__ == "__main__":
    unittest.main()
