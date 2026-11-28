"""Exercise the native Pool boundary using detached Codex evidence fixtures."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_quota_handoff as fixtures
from uif.codex_metadata_bridge import BoundMetadataReplies, observe_signed_in_metadata
from uif.handoff_store import Hold, digest
from uif.native_quota import AsyncNativeQuotaBoundary, NativeProfile, PoolPermit, PoolRefusal
from uif.openai_quota_admission import CodexEvidence, CodexRoute, CodexWindow, allows_codex


class NativeCodexAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = fixtures.QuotaHandoffTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.fx.provider("available", 1)
        self.fx.claim()
        self.issue = SimpleNamespace(id=self.fx.key.issue_id)
        self.profile = NativeProfile(self.fx.key, "native-session", "native", 1,
                                     fixtures.SOURCE, 3, 30_000)
        self.route = CodexRoute("subscription-account", fixtures.PIN, "fixture-model",
                                ("primary", "secondary"))
        self.now = datetime.fromtimestamp(self.fx.now, timezone.utc)
        self.intents = {}
        self.reservations = {}
        self.sent = []
        self.released = []
        self.boundary = AsyncNativeQuotaBoundary(
            self.fx.registry, profile_for=lambda _: self.profile,
            pool_admit=self.admit, outcome_read=self.read_outcome,
            provider_read=lambda *_: self.fail("a Pool check is not a provider observation"),
        )

    def proof(self, remaining):
        reset = self.now + timedelta(hours=1)
        return CodexEvidence(
            self.route, self.now, self.now + timedelta(seconds=30),
            subscription_only=True, complete_binding=True,
            windows=(CodexWindow("primary", 75, 100, 2, 3, reset),
                     CodexWindow("secondary", remaining, 100, 2, 3, reset)),
        )

    def admit(self, profile, operation_id):
        self.assertEqual(profile, self.profile)
        intent, proof = self.intents[operation_id]
        receipt = digest({"fixture_operation": operation_id, "claim": profile.key.identity,
                          "generation": profile.generation})
        if not allows_codex(self.route, proof, operation=intent, now=self.now,
                            deadline=self.now + timedelta(seconds=10)):
            return PoolRefusal(profile.key, profile.generation, operation_id, receipt, self.fx.now)
        # The fixture stands in for the owner's authenticated atomic Pool. Its
        # proof already includes unresolved use and the whole next physical call.
        self.assertNotIn(operation_id, self.reservations)
        permit = PoolPermit(profile.key, profile.generation, operation_id,
                            "fixture-lease", receipt, self.fx.now)
        self.reservations[operation_id] = permit
        return permit

    def send(self, issue, permit):
        self.assertEqual(issue.id, self.fx.key.issue_id)
        self.assertIs(self.reservations[permit.operation_id], permit)
        self.sent.append(permit.operation_id)

    def read_outcome(self, profile, operation_id, permit, returned):
        self.assertEqual(profile, self.profile)
        self.assertIn(operation_id, self.sent)
        self.assertIs(self.reservations[operation_id], permit)
        return {"status": "verified", "receipt_sha256": digest({"fixture_sent": operation_id})}

    async def dispatch(self, operation_id, native=None):
        return await self.boundary.dispatch_stage(
            self.issue, stage_type="agent", operation_id=operation_id,
            dispatch_native=native or self.send,
            release_ephemeral_slot=lambda issue: self.released.append(issue.id),
        )

    async def test_codex_floors_control_native_launch_and_preserve_claim(self):
        self.intents = {
            "first-call": ("new", self.proof(21)),
            "new-at-soft-floor": ("new", self.proof(20)),
            "continue-above-hard-floor": ("continue", self.proof(16)),
            "retry-at-hard-floor": ("retry", self.proof(15)),
        }
        expected = ("verified", "denied", "verified", "denied")
        for operation, status in zip(self.intents, expected, strict=True):
            with self.subTest(operation=operation):
                self.assertEqual((await self.dispatch(operation))["status"], status)

        self.assertEqual(self.sent, ["first-call", "continue-above-hard-floor"])
        self.assertEqual(list(self.reservations), self.sent)
        self.assertEqual(self.released, [self.issue.id, self.issue.id])
        task = self.fx.status()["tasks"][self.fx.key.identity]
        self.assertEqual(task["state"], "active")
        self.assertEqual(tuple(task["operations"][operation]["status"] for operation in self.intents),
                         expected)

    async def test_same_session_metadata_cannot_substitute_for_reservation_proof(self):
        window = {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": 2000}
        port = Mock()
        port.read_bound.return_value = BoundMetadataReplies(
            self.route.account, "fixture-session", int(self.fx.now),
            b'{"account":{"type":"chatgpt"},"requiresOpenaiAuth":true}',
            json.dumps({"rateLimitsByLimitId": {"codex": {
                "limitId": "codex", "primary": window, "secondary": window,
            }}}).encode(),
        )
        diagnostic = observe_signed_in_metadata(
            port, account_id=self.route.account, session_id="fixture-session",
            limit_id="codex", clock=lambda: int(self.fx.now),
        )
        self.assertEqual(tuple(w.used_percent for w in diagnostic.windows), (1, 1))
        self.intents["diagnostic-only"] = ("new", diagnostic)
        self.assertEqual((await self.dispatch("diagnostic-only"))["status"], "denied")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.reservations, {})

    async def test_lost_ack_retains_the_original_reservation_and_prevents_replay(self):
        proof = self.proof(21)
        self.intents["single-send"] = ("new", proof)

        def send_then_lose_ack(issue, permit):
            self.send(issue, permit)
            raise TimeoutError("original fixture send acknowledgement lost")

        with self.assertRaises(TimeoutError):
            await self.dispatch("single-send", native=send_then_lose_ack)
        self.assertEqual(self.released, [])
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            await self.dispatch("single-send")

        self.assertEqual(self.sent, ["single-send"])
        self.assertEqual(list(self.reservations), self.sent)
        self.assertEqual(self.released, [self.issue.id])
        original = self.fx.status()["tasks"][self.fx.key.identity]["operations"]["single-send"]
        self.assertEqual(original["status"], "unknown")
