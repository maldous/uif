"""Awaited native source contracts; no installed hooks or remote effects."""

import asyncio
from dataclasses import asdict, replace
import threading
from types import SimpleNamespace
import unittest
from uuid import UUID

import test_quota_handoff as fixtures
from uif.handoff_store import Hold
from uif.native_quota import (AsyncLinearClient, AsyncNativeQuotaBoundary, LinearWriteBinding,
                              LinearWriteObservation, NativeProfile, PoolPermit, PoolRefusal)


class AsyncEffects(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = fixtures.QuotaHandoffTests()
        self.fx.setUp()
        self.fx.provider("available", 1)
        self.fx.heartbeat()
        self.fx.claim()

    def tearDown(self):
        self.fx.tearDown()

    async def effect(self, callback, *, operation_id="async-original", reasoning=False, key=None, actor="native"):
        return await self.fx.registry.perform_effect_async(
            principal=actor + "-session", actor=actor, key=key or self.fx.key, generation=1,
            operation_id=operation_id, payload_sha256=fixtures.PIN, effect=callback, reasoning=reasoning)

    async def test_async_effect_keeps_task_fence_with_disjoint_parallelism_and_live_quota(self):
        first_started, second_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        other = fixtures.LinearKey(fixtures.WORKSPACE, fixtures.PROJECT, str(UUID(int=99)))
        self.fx.claim("cloud", key=other)

        async def first():
            first_started.set()
            await release.wait()
            return dict(status="verified", receipt_sha256=fixtures.PIN)

        async def second():
            second_started.set()
            await release.wait()
            return dict(status="verified", receipt_sha256=fixtures.PIN)

        one = asyncio.create_task(self.effect(first, reasoning=True))
        two = asyncio.create_task(self.effect(second, key=other, actor="cloud"))
        await asyncio.wait_for(asyncio.gather(first_started.wait(), second_started.wait()), 3)
        self.assertEqual(self.fx.status()["tasks"][self.fx.key.identity]["operations"]["async-original"]["status"], "pending")
        self.fx.now += .1
        self.fx.heartbeat()
        self.fx.provider("hard-quota", 2)
        with self.assertRaisesRegex(Hold, "task_effect_busy"):
            self.fx.capture()
        release.set()
        await asyncio.gather(one, two)
        with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
            await self.effect(first, operation_id="new-local", reasoning=True)
        self.fx.capture()
        self.fx.acknowledge()
        with self.assertRaisesRegex(Hold, "obsolete_claim"):
            await self.effect(first, operation_id="late-old-owner")

    async def test_cancel_and_lost_reply_preserve_original_intent_without_replay(self):
        started = asyncio.Event()
        calls = []

        async def mutation():
            calls.append("may-have-committed")
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.effect(mutation))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        operation = self.fx.status()["tasks"][self.fx.key.identity]["operations"]["async-original"]
        self.assertEqual(operation["status"], "unknown")
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            await self.effect(mutation)
        with self.assertRaisesRegex(Hold, "uncertain_operations_held"):
            await self.effect(mutation, operation_id="replacement-cannot-bypass")
        self.assertEqual(calls, ["may-have-committed"])

    async def test_sync_async_share_original_receipt_and_never_finish_task(self):
        calls = []

        async def mutation():
            calls.append(1)
            return dict(status="verified", receipt_sha256=fixtures.PIN)

        first = await self.effect(mutation)
        self.assertEqual(await self.effect(mutation), first)
        again = self.fx.registry.perform_effect(
            principal="native-session", actor="native", key=self.fx.key, generation=1,
            operation_id="async-original", payload_sha256=fixtures.PIN,
            effect=lambda: self.fail("sync replay executed"))
        self.assertEqual(again, first)
        self.assertEqual(calls, [1])
        self.assertEqual(self.fx.status()["tasks"][self.fx.key.identity]["state"], "active")

    async def test_quota_flip_during_readonly_reconstruction_refuses_before_new_intent(self):
        entered, release = threading.Event(), threading.Event()
        original = self.fx.registry.recovery_verifier

        def slow_reader(context):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(context)

        self.fx.registry.recovery_verifier = slow_reader

        async def mutation():
            self.fail("exhausted route reached effect")

        task = asyncio.create_task(self.effect(mutation, reasoning=True))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(.01)
        self.fx.provider("hard-quota", 2)
        release.set()
        with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
            await task
        self.assertNotIn("async-original", self.fx.status()["tasks"][self.fx.key.identity]["operations"])

    async def test_lost_reply_is_unknown_and_retains_original_exception(self):
        async def mutation():
            raise TimeoutError("destination reply lost")

        with self.assertRaises(TimeoutError):
            await self.effect(mutation)
        self.assertEqual(self.fx.status()["tasks"][self.fx.key.identity]["operations"]["async-original"]["status"], "unknown")

    async def test_cancelled_preflight_reader_cannot_later_admit_effect(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.fx.registry.recovery_verifier

        def slow_reader(context):
            entered.set()
            try:
                self.assertTrue(release.wait(3))
                return original(context)
            finally:
                finished.set()

        self.fx.registry.recovery_verifier = slow_reader

        async def mutation():
            self.fail("cancelled preflight launched effect")

        task = asyncio.create_task(self.effect(mutation))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        async with asyncio.timeout(3):
            while not finished.is_set():
                await asyncio.sleep(.01)
        self.assertNotIn("async-original", self.fx.status()["tasks"][self.fx.key.identity]["operations"])


class AsyncNativeClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fx = fixtures.QuotaHandoffTests()
        self.fx.setUp()
        self.fx.provider("available", 1)
        self.fx.heartbeat()
        self.fx.claim()
        self.issue = SimpleNamespace(id=self.fx.key.issue_id)
        self.profile = NativeProfile(self.fx.key, "native-session", "native", 1, fixtures.SOURCE, 3, 30000)
        self.calls = []
        self.team_id = str(UUID(int=20))
        self.label_id = str(UUID(int=21))

        async def pool(profile, operation_id):
            self.calls.append("pool")
            await asyncio.sleep(0)
            return PoolPermit(profile.key, profile.generation, operation_id, "actual-lease", fixtures.PIN, self.fx.now)

        async def outcome(*_):
            await asyncio.sleep(0)
            return dict(status="verified", receipt_sha256=fixtures.PIN)

        self.boundary = AsyncNativeQuotaBoundary(
            self.fx.registry, profile_for=lambda _: self.profile, pool_admit=pool, outcome_read=outcome,
            provider_read=lambda _: dict(classification="hard-quota", sequence=2,
                                         observed_at=self.fx.now, evidence_sha256=fixtures.PIN))

        parent = self

        class Client:
            async def post_comment(self, *args):
                parent.calls.append(("post_comment", args))
                await asyncio.sleep(0)
                return True

            async def update_issue_state(self, *args):
                parent.calls.append(("update_issue_state", args))
                return True

            async def add_label(self, *args):
                parent.calls.append(("add_label", args))
                return True

            async def create_label(self, *args):
                parent.calls.append(("create_label", args))
                return parent.label_id

            async def upload_file(self, *args):
                parent.calls.append(("upload_file", args))
                return "https://example.test/accepted-original-asset"

            async def fetch_comments(self, issue_id):
                return [{"id": "original-comment"}]

            async def fetch_issue_states_by_ids(self, ids):
                return {ids[0]: "In Progress"}

            async def fetch_team_labels(self, issue_id):
                return parent.team_id, {}, set()

        self.client = Client()
        self.observed = {}

        def binding(profile, method, payload_sha256):
            return LinearWriteBinding(profile.key, profile.generation, method + "-original", method, payload_sha256)

        async def observe(profile, bound, payload, returned, mode):
            if mode == "invoke":
                self.observed[bound.operation_id] = returned
            return LinearWriteObservation(bound, "verified", fixtures.PIN, self.observed[bound.operation_id])

        self.proxy = AsyncLinearClient(self.boundary, self.issue, self.client, team_id=self.team_id,
                                       binding_for=binding, observe=observe)

    def tearDown(self):
        self.fx.tearDown()

    async def test_explicit_writes_preserve_upstream_shapes_and_observe_replay(self):
        self.assertTrue(await self.proxy.post_comment(self.issue.id, "source result"))
        self.assertTrue(await self.proxy.post_comment(self.issue.id, "source result"))
        self.assertTrue(await self.proxy.update_issue_state(self.issue.id, "In Review"))
        self.assertTrue(await self.proxy.add_label(self.issue.id, self.label_id))
        self.assertEqual(await self.proxy.create_label(self.team_id, "verified", None), self.label_id)
        self.assertEqual(await self.proxy.upload_file("report.txt", "text/plain", b"accepted"),
                         "https://example.test/accepted-original-asset")
        self.assertEqual([c[0] for c in self.calls].count("post_comment"), 1)
        self.assertEqual(self.fx.status()["tasks"][self.fx.key.identity]["state"], "active")

    async def test_proxy_refuses_cross_issue_team_unknown_method_and_stale_generation(self):
        other = str(UUID(int=100))
        with self.assertRaisesRegex(Hold, "linear_issue_scope"):
            await self.proxy.post_comment(other, "must not be sent")
        with self.assertRaisesRegex(Hold, "linear_team_scope"):
            await self.proxy.create_label(other, "must not be created")
        with self.assertRaises(AttributeError):
            getattr(self.proxy, "_graphql")
        with self.assertRaisesRegex(Hold, "linear_issue_scope"):
            await self.proxy.fetch_comments(other)
        self.profile = replace(self.profile, generation=2)
        with self.assertRaisesRegex(Hold, "native_claim_drift"):
            await self.proxy.update_issue_state(self.issue.id, "Done")
        self.assertEqual(self.calls, [])

    async def test_native_guard_refusal_releases_only_known_ephemeral_slot(self):
        self.fx.provider("hard-quota", 2)
        released = []
        with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
            await self.boundary.dispatch(self.issue, operation_id="native-new",
                                          dispatch_native=lambda *_: self.fail("exhausted launch"),
                                          release_ephemeral_slot=lambda issue: released.append(issue.id))
        self.assertEqual(released, [self.issue.id])
        self.assertEqual(self.calls, [])

    async def test_quota_flip_while_pool_awaits_prevents_child_without_fake_pool_release(self):
        async def pool(profile, operation_id):
            await asyncio.sleep(0)
            self.fx.provider("hard-quota", 2)
            return PoolPermit(profile.key, profile.generation, operation_id, "held-real-lease", fixtures.PIN,
                              self.fx.now)

        self.boundary.pool_admit = pool
        with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
            await self.boundary.dispatch(self.issue, operation_id="admission-original",
                                          dispatch_native=lambda *_: self.fail("child launched after quota flip"),
                                          release_ephemeral_slot=lambda *_: self.fail("unproved lease release"))
        operation = self.fx.status()["tasks"][self.fx.key.identity]["operations"]["admission-original"]
        self.assertEqual(operation["status"], "unknown")

    async def test_gate_metadata_continues_without_model_pool_when_quota_exhausted(self):
        self.fx.provider("hard-quota", 2)
        async def gate(issue, permit):
            self.assertIsNone(permit)
            self.calls.append("native-gate")
            return True
        await self.boundary.dispatch_stage(self.issue, stage_type="gate", operation_id="gate-original", dispatch_native=gate)
        self.assertEqual(self.calls, ["native-gate"])

    async def test_qualified_pool_no_start_refusal_releases_ephemeral_slot_only(self):
        async def refused(profile, operation_id):
            return PoolRefusal(profile.key, profile.generation, operation_id, fixtures.PIN, self.fx.now)

        self.boundary.pool_admit = refused
        released = []
        result = await self.boundary.dispatch(
            self.issue, operation_id="pool-refused-original",
            dispatch_native=lambda *_: self.fail("refused Pool launched native worker"),
            release_ephemeral_slot=lambda issue: released.append(issue.id))
        self.assertEqual(result["status"], "denied")
        self.assertEqual(released, [self.issue.id])
        self.assertEqual(self.calls, [])

    async def test_quota_changes_during_retry_proof_never_enqueue_timer(self):
        self.fx.provider("transient", 2)
        entered, release = threading.Event(), threading.Event()
        original = self.fx.registry.recovery_verifier

        def slow_reader(context):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(context)

        self.fx.registry.recovery_verifier = slow_reader
        task = asyncio.create_task(self.boundary.schedule_retry(
            self.issue, attempt=1, delay_ms=1000,
            schedule_native=lambda *_: self.fail("exhausted retry enqueued during proof")))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(.01)
        self.fx.provider("hard-quota", 3)
        release.set()
        result = await task
        self.assertEqual(result["status"], "denied")

    async def test_callback_success_without_independent_observation_remains_unknown(self):
        self.proxy.observe = lambda *_: True
        with self.assertRaisesRegex(Hold, "linear_destination_observation"):
            await self.proxy.post_comment(self.issue.id, "actual write could succeed")
        self.assertEqual(self.fx.status()["tasks"][self.fx.key.identity]["operations"]["post_comment-original"]["status"], "unknown")
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            await self.proxy.post_comment(self.issue.id, "actual write could succeed")
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
