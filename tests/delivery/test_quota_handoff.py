"""Synthetic source tests. They neither contact Foo nor qualify live adapters."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from uuid import UUID

from uif.handoff_store import Hold, decode
from uif.quota_handoff import (CLOUD_ACTIVE, LOCAL_READY, LOCAL_RECOVERY, TRANSFER_PENDING,
                               LinearKey, RecoveryReceipt, Registry)

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = str(UUID(int=1))
PROJECT = str(UUID(int=2))
MOVED_PROJECT = str(UUID(int=20))
SOURCE = dict(repository="maldous/uif", branch="develop", base="a" * 40,
              head="b" * 40, tree="c" * 40)
PIN = "d" * 64


class QuotaHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "authority"
        self.now = 1000.0
        self.number = 0
        self.reads = []
        actors = {"morgan": dict(role="morgan", projects=[PROJECT]),
                  "native": dict(role="native", projects=[PROJECT]),
                  "cloud": dict(role="project", projects=[PROJECT, MOVED_PROJECT]),
                  "observer": dict(role="observer", projects=[])}
        principals = {"morgan-session": ["morgan"], "native-session": ["native"],
                      "next-morgan-session": ["morgan"], "cloud-session": ["cloud"],
                      f"uid:{os.geteuid()}": ["observer"]}
        self.coordinator = dict(execution_id="current-morgan", generation=1)
        self.registry = Registry.initialize(self.path, workspace_id=WORKSPACE,
                                           actors=actors, principals=principals, morgan_actor="morgan",
                                           morgan_execution_id="current-morgan", morgan_principal="morgan-session",
                                           provider_verifier=lambda _: True, handoff_verifier=lambda _: True,
                                           completion_verifier=lambda _: True, coordinator_verifier=lambda _: True,
                                           recovery_verifier=self.reconstruct, clock=lambda: self.now)
        self.key = LinearKey(WORKSPACE, PROJECT, str(UUID(int=3)))
        self.request("morgan", "set_stopped", stopped=False)

    def tearDown(self):
        self.temp.cleanup()

    def reconstruct(self, context):
        self.reads.append(context)
        self.assertIn(context.key.project_id, {PROJECT, MOVED_PROJECT})
        self.assertEqual(context.source, SOURCE)
        return RecoveryReceipt(PIN, context.evidence_ref, self.now)

    def request(self, actor, action, **fields):
        self.number += 1
        if actor == "morgan":
            fields["coordinator"] = self.coordinator
        return self.registry.invoke(principal=actor + "-session", actor=actor,
                                    request=dict(action=action, request_id="request-" + str(self.number), **fields))

    def status(self):
        return self.registry.invoke(principal="morgan-session", actor="morgan", request={"action": "status"})

    def provider(self, classification, sequence):
        return self.request("native", "provider_observation", classification=classification,
                            sequence=sequence, observed_at=self.now, evidence_sha256=PIN)

    def heartbeat(self):
        return self.request("morgan", "heartbeat", observed_at=self.now)

    def claim(self, lane="local", key=None):
        return self.request("native" if lane == "local" else "cloud", "claim", key=asdict(key or self.key),
                            lane=lane, source=SOURCE, evidence_ref="reconstruction-current",
                            original_operation_id="original-" + (key or self.key).issue_id)

    def capture(self):
        return self.request("native", "capture_handoff", key=asdict(self.key), generation=1,
                            source=SOURCE, checkpoint=dict(kind="foo-custody", reference="opaque-custody-1", sha256=PIN),
                            completed_steps=["code", "focused-checks"], child_tree_gone=True,
                            stopped_evidence_sha256=PIN, evidence_ref="handoff-current")

    def acknowledge(self):
        return self.request("cloud", "acknowledge_handoff", key=asdict(self.key), generation=1,
                            evidence_ref="cloud-current")

    def effect(self, callback, **kwargs):
        return self.registry.perform_effect(principal="native-session", actor="native", key=self.key,
                                            generation=1, operation_id="original-effect", payload_sha256=PIN,
                                            effect=callback, **kwargs)

    def prepare_transfer(self):
        self.provider("available", 1)
        self.heartbeat()
        self.claim()
        self.provider("hard-quota", 2)

    def test_two_authority_clients_racing_across_project_move_grant_one_claim(self):
        self.provider("available", 1)
        self.heartbeat()
        moved = LinearKey(WORKSPACE, MOVED_PROJECT, self.key.issue_id)
        cloud = Registry(self.path, recovery_verifier=self.reconstruct, clock=lambda: self.now)
        ready = threading.Barrier(2)
        outcomes = []

        def claim_request(actor, lane, key):
            return dict(action="claim", request_id=actor + "-race", key=asdict(key),
                        lane=lane, source=SOURCE, evidence_ref="reconstruction-current",
                        original_operation_id=actor + "-original")

        def contend(registry, actor, request):
            ready.wait(timeout=5)
            try:
                result = registry.invoke(principal=actor + "-session", actor=actor, request=request)
                outcomes.append((actor, result["status"]))
            except Hold as error:
                outcomes.append((actor, str(error)))

        contenders = {
            "native": (self.registry, claim_request("native", "local", self.key)),
            "cloud": (cloud, claim_request("cloud", "cloud", moved)),
        }
        workers = [threading.Thread(target=contend, args=(registry, actor, request))
                   for actor, (registry, request) in contenders.items()]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sum(result == "claimed" for _, result in outcomes), 1)
        loser, refusal = next((actor, result) for actor, result in outcomes if result != "claimed")
        self.assertIn(refusal, {"task_effect_busy", "existing_claim_requires_reconciliation"})
        registry, original = contenders[loser]
        with self.assertRaisesRegex(Hold, "existing_claim_requires_reconciliation"):
            registry.invoke(principal=loser + "-session", actor=loser, request=original)
        self.assertEqual(len(self.status()["tasks"]), 1)

    def test_legacy_project_conflict_holds_new_effects_but_preserves_reconciliation(self):
        self.provider("available", 1)
        self.claim()

        def lost_reply():
            raise TimeoutError("lost reply")

        with self.assertRaises(TimeoutError):
            self.effect(lost_reply)

        moved = LinearKey(WORKSPACE, MOVED_PROJECT, self.key.issue_id)
        with self.registry.store.transaction() as (_, state, save):
            task = state["tasks"][self.key.identity]
            state["tasks"][moved.identity] = dict(task, key=asdict(moved), operations={})
            save()

        with self.assertRaisesRegex(Hold, "conflicting_project_claims"):
            self.effect(lambda: self.fail("conflicting claims reached the effect boundary"))
        self.registry.operation_verifier = lambda context: context.operation_id == "original-effect"
        reconciled = self.request(
            "native", "resolve_operation", key=asdict(self.key), generation=1,
            operation_id="original-effect", status="verified", receipt_sha256=PIN)
        self.assertEqual(reconciled["status"], "reconciled")

    def test_durable_handoff_reopens_and_fences_old_owner(self):
        self.prepare_transfer()
        self.capture()
        self.assertEqual(self.acknowledge()["generation"], 2)
        reopened = Registry(self.path, recovery_verifier=self.reconstruct, clock=lambda: self.now)
        task = reopened.invoke(principal="morgan-session", actor="morgan",
                               request={"action": "status"})["tasks"][self.key.identity]
        self.assertEqual((task["lane"], task["owner"], task["generation"]), ("cloud", "cloud", 2))
        self.assertEqual(task["original_operation_id"], "original-" + self.key.issue_id)
        self.assertEqual(task["handoffs"][0]["completed_steps"], ["code", "focused-checks"])
        with self.assertRaisesRegex(Hold, "obsolete_claim"):
            self.effect(lambda: self.fail("obsolete owner reached effect"))

    def test_effect_lock_prevents_transfer_during_external_mutation(self):
        self.prepare_transfer()
        started, release = threading.Event(), threading.Event()
        output = []

        def mutation():
            started.set()
            self.assertTrue(release.wait(3))
            return dict(status="verified", receipt_sha256=PIN)

        worker = threading.Thread(target=lambda: output.append(self.effect(mutation)))
        worker.start()
        self.assertTrue(started.wait(3))
        try:
            with self.assertRaisesRegex(Hold, "task_effect_busy"):
                self.capture()
            self.assertEqual(self.status()["mode"], TRANSFER_PENDING)
            self.now += .1
            self.heartbeat()
            self.provider("hard-quota", 3)
            other = LinearKey(WORKSPACE, PROJECT, str(UUID(int=9)))
            self.claim("cloud", key=other)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(output[0]["status"], "verified")
        self.capture()
        self.acknowledge()

    def test_lost_effect_reply_retains_id_until_independent_reconciliation(self):
        self.prepare_transfer()
        calls = []

        def lost_reply():
            calls.append("destination-may-have-committed")
            raise TimeoutError("lost reply")

        with self.assertRaises(TimeoutError):
            self.effect(lost_reply)
        with self.assertRaisesRegex(Hold, "original_operation_requires_reconciliation"):
            self.effect(lambda: self.fail("unknown effect replayed"))
        self.capture()
        with self.assertRaisesRegex(Hold, "uncertain_operations_held"):
            self.acknowledge()
        fields = dict(key=asdict(self.key), generation=1, operation_id="original-effect",
                      status="verified", receipt_sha256=PIN)
        with self.assertRaisesRegex(Hold, "operation_observer_unavailable"):
            self.request("morgan", "resolve_operation", **fields)
        self.registry.operation_verifier = lambda context: (
            context.operation_id == "original-effect" and context.payload_sha256 == PIN
            and context.receipt_sha256 == PIN)
        self.request("morgan", "resolve_operation", **fields)
        self.acknowledge()
        self.assertEqual(calls, ["destination-may-have-committed"])
        task = self.status()["tasks"][self.key.identity]
        self.assertEqual(task["handoffs"][0]["unresolved"], ["original-effect"])
        self.assertEqual(task["operations"]["original-effect"]["status"], "verified")

    def test_quota_classification_and_stop_are_independent(self):
        self.provider("available", 1)
        for seq, classification in enumerate(("transient", "auth-failure", "unknown"), 2):
            result = self.provider(classification, seq)
            self.assertNotEqual(result["mode"], TRANSFER_PENDING)
            with self.assertRaisesRegex(Hold, "local_provider_unavailable"):
                self.claim()
        self.request("morgan", "set_stopped", stopped=True)
        self.provider("hard-quota", 5)
        self.assertTrue(self.status()["stopped"])
        self.provider("available", 6)
        self.assertTrue(self.status()["stopped"])
        with self.assertRaisesRegex(Hold, "service_stopped"):
            self.claim()

    def test_restored_quota_retains_active_cloud_claim_and_ramps_only_new_local_work(self):
        self.prepare_transfer()
        self.capture()
        self.acknowledge()
        self.assertEqual(self.status()["mode"], CLOUD_ACTIVE)
        self.provider("available", 3)
        state = self.status()
        self.assertEqual((state["mode"], state["local_limit"]), (LOCAL_RECOVERY, 1))
        self.assertEqual(state["tasks"][self.key.identity]["generation"], 2)
        self.assertEqual(state["tasks"][self.key.identity]["owner"], "cloud")
        with self.assertRaisesRegex(Hold, "existing_claim_requires_reconciliation"):
            self.claim()
        expected_limits = (4, 8, 16, 32, 64)
        for number, expected in enumerate(expected_limits, 10):
            key = LinearKey(WORKSPACE, PROJECT, str(UUID(int=number)))
            self.claim(key=key)
            result = self.request("native", "finish", key=asdict(key), generation=1,
                                  accepted=True, receipt_sha256=PIN)
            self.assertEqual(result["local_limit"], expected)
        self.assertEqual(self.status()["mode"], LOCAL_READY)

    def test_stale_and_future_provider_or_morgan_evidence_refuses_admission(self):
        self.provider("available", 1)
        self.heartbeat()
        self.now += 91
        with self.assertRaisesRegex(Hold, "stale_evidence"):
            self.claim()
        self.provider("available", 2)
        with self.assertRaisesRegex(Hold, "stale_evidence"):
            self.claim("cloud")
        with self.assertRaisesRegex(Hold, "stale_evidence"):
            self.request("morgan", "heartbeat", observed_at=self.now + 1)

    def test_reconstruction_is_rechecked_at_each_effect_and_transfer(self):
        self.prepare_transfer()
        self.registry.recovery_verifier = lambda _: (_ for _ in ()).throw(Hold("current_linear_changed"))
        with self.assertRaisesRegex(Hold, "current_linear_changed"):
            self.effect(lambda: self.fail("stale reconstruction reached effect"))
        self.capture()
        with self.assertRaisesRegex(Hold, "current_linear_changed"):
            self.acknowledge()
        self.assertEqual(self.status()["tasks"][self.key.identity]["generation"], 1)

    def test_no_reconstruction_adapter_or_forged_receipt_cannot_admit(self):
        self.provider("available", 1)
        self.registry.recovery_verifier = None
        with self.assertRaisesRegex(Hold, "reconstruction_adapter_unavailable"):
            self.claim()
        self.registry.recovery_verifier = lambda c: RecoveryReceipt(PIN, "wrong-binding", self.now)
        with self.assertRaisesRegex(Hold, "reconstruction_binding"):
            self.claim()
        self.registry.recovery_verifier = lambda c: RecoveryReceipt(PIN, c.evidence_ref, self.now - 301)
        with self.assertRaisesRegex(Hold, "stale_evidence"):
            self.claim()

    def test_original_request_identity_is_idempotent_and_not_repurposable(self):
        request = dict(action="provider_observation", request_id="same-original", classification="hard-quota",
                       sequence=1, observed_at=self.now, evidence_sha256=PIN)
        first = self.registry.invoke(principal="native-session", actor="native", request=request)
        again = self.registry.invoke(principal="native-session", actor="native", request=request)
        self.assertEqual(first, again)
        with self.assertRaisesRegex(Hold, "request_identity_conflict"):
            self.registry.invoke(principal="native-session", actor="native",
                                 request={**request, "classification": "available"})
        self.assertEqual(self.status()["mode"], TRANSFER_PENDING)

    def test_cli_identity_cannot_be_overridden_by_hostile_json(self):
        command = [sys.executable, str(ROOT / "tools/delivery/quota_handoff.py"),
                   "--registry", str(self.path), "--actor", "morgan"]
        request = dict(action="heartbeat", request_id="spoof", execution_id="fake", observed_at=self.now,
                       principal="morgan-session", authenticated=True)
        result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["reason"], "unauthenticated_actor")
        self.assertIsNone(self.status()["morgan"])
        duplicate = b'{"action":"status","action":"heartbeat"}'
        with self.assertRaisesRegex(Hold, "duplicate_json_key"):
            decode(duplicate)

    def test_missing_authority_is_not_replaced_or_created(self):
        missing = Path(self.temp.name) / "missing"
        result = subprocess.run([sys.executable, str(ROOT / "tools/delivery/quota_handoff.py"),
                                 "--registry", str(missing), "--actor", "observer"],
                                input='{"action":"status"}', text=True, capture_output=True)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(missing.exists())

    def test_registry_reinitialization_and_symlink_substitution_refuse(self):
        with self.assertRaises(FileExistsError):
            Registry.initialize(self.path, workspace_id=WORKSPACE,
                                actors={"morgan": dict(role="morgan", projects=[PROJECT])},
                                principals={"morgan-session": ["morgan"]}, morgan_actor="morgan",
                                morgan_execution_id="current-morgan", morgan_principal="morgan-session")
        state = self.path / "state.json"
        retained = self.path / "retained.json"
        state.rename(retained)
        state.symlink_to(retained)
        with self.assertRaises(OSError):
            self.status()

    def test_linear_alias_or_cross_project_authority_cannot_claim(self):
        with self.assertRaisesRegex(Hold, "linear_uuid_required"):
            LinearKey(WORKSPACE, PROJECT, "UIF-120")
        self.provider("available", 1)
        other = LinearKey(WORKSPACE, str(UUID(int=200)), str(UUID(int=201)))
        with self.assertRaisesRegex(Hold, "project_authority"):
            self.claim(key=other)

    def test_coordinator_succession_requires_verified_fence_and_successor_ack(self):
        self.heartbeat()
        self.registry.coordinator_verifier = None
        fields = dict(successor_execution_id="next-morgan", successor_principal="next-morgan-session",
                      evidence_ref="coordinator-original-effects")
        with self.assertRaisesRegex(Hold, "coordinator_observer_unavailable"):
            self.request("morgan", "transfer_coordinator", **fields)
        self.registry.coordinator_verifier = lambda _: True
        self.request("morgan", "transfer_coordinator", **fields)
        self.now += 1
        with self.assertRaisesRegex(Hold, "coordinator_transfer_pending"):
            self.heartbeat()
        successor = dict(execution_id="next-morgan", generation=2)
        ack = dict(action="acknowledge_coordinator", request_id="successor-ack", coordinator=successor)
        self.registry.invoke(principal="next-morgan-session", actor="morgan", request=ack)
        # Lost ACK readback reuses the exact original request, with no new owner.
        self.registry.invoke(principal="next-morgan-session", actor="morgan", request=ack)
        with self.assertRaisesRegex(Hold, "obsolete_coordinator"):
            self.heartbeat()
        self.registry.invoke(principal="next-morgan-session", actor="morgan",
                             request=dict(action="heartbeat", request_id="new-heartbeat", coordinator=successor,
                                          observed_at=self.now))
        with self.assertRaisesRegex(Hold, "obsolete_coordinator"):
            self.registry.invoke(principal="morgan-session", actor="morgan",
                                 request=dict(action="set_stopped", request_id="stale-control", stopped=True,
                                              coordinator=self.coordinator))

    def test_unverified_provider_handoff_and_completion_hashes_do_not_advance(self):
        self.registry.provider_verifier = None
        with self.assertRaisesRegex(Hold, "provider_observer_unavailable"):
            self.provider("hard-quota", 1)
        self.assertIsNone(self.status()["provider"])
        self.registry.provider_verifier = lambda _: True
        self.prepare_transfer()
        self.registry.handoff_verifier = lambda _: False
        with self.assertRaisesRegex(Hold, "handoff_evidence_unverified"):
            self.capture()
        self.assertEqual(self.status()["tasks"][self.key.identity]["state"], "active")
        self.registry.completion_verifier = None
        with self.assertRaisesRegex(Hold, "completion_observer_unavailable"):
            self.request("native", "finish", key=asdict(self.key), generation=1, accepted=True, receipt_sha256=PIN)
        self.assertEqual(self.status()["local_limit"], 0)

    def test_repeated_exhaustion_samples_preserve_acknowledged_cloud_mode(self):
        self.prepare_transfer()
        self.capture()
        self.acknowledge()
        for seq in (3, 4, 5):
            self.now += 1
            self.provider("hard-quota", seq)
            self.assertEqual(self.status()["mode"], CLOUD_ACTIVE)

    def test_failed_canary_retains_history_and_allows_fresh_qualified_attempt(self):
        self.provider("available", 1)
        self.claim()
        self.request("native", "finish", key=asdict(self.key), generation=1, accepted=False, receipt_sha256=PIN)
        self.assertEqual(self.status()["tasks"][self.key.identity]["state"], "failed")
        retry = dict(key=asdict(self.key), generation=1, lane="local", source=SOURCE,
                     evidence_ref="fresh-reconstruction", original_operation_id="second-original")
        result = self.request("native", "retry_claim", **retry)
        self.assertEqual(result["generation"], 2)
        self.assertEqual(self.status()["tasks"][self.key.identity]["attempts"][0]["completion"]["accepted"], False)
        self.registry.perform_effect(principal="native-session", actor="native", key=self.key, generation=2,
                                     operation_id="fresh-attempt-effect", payload_sha256=PIN,
                                     effect=lambda: dict(status="verified", receipt_sha256=PIN))
        self.assertEqual((self.reads[-1].origin_owner, self.reads[-1].origin_generation), ("native", 1))
        self.request("native", "finish", key=asdict(self.key), generation=2, accepted=True, receipt_sha256=PIN)
        with self.assertRaisesRegex(Hold, "failed_attempt_required"):
            self.request("native", "retry_claim", **{**retry, "generation": 2, "original_operation_id": "third"})

    def test_changed_source_retry_requires_fresh_checkpoint_qualification(self):
        self.provider("available", 1)
        self.claim()
        self.request("native", "finish", key=asdict(self.key), generation=1, accepted=False, receipt_sha256=PIN)
        changed = {**SOURCE, "head": "e" * 40, "tree": "f" * 40}
        contexts = []

        def fresh(context):
            contexts.append(context)
            return RecoveryReceipt(PIN, context.evidence_ref, self.now)

        self.registry.recovery_verifier = fresh
        self.request("native", "retry_claim", key=asdict(self.key), generation=1, lane="local", source=changed,
                     evidence_ref="fresh-source-qualified", original_operation_id="new-source-attempt")
        self.assertEqual(contexts[-1].source, changed)
        self.assertIsNone(contexts[-1].origin_owner)
        self.assertIsNone(contexts[-1].origin_generation)

    def test_expected_reader_exception_becomes_bounded_hold(self):
        self.provider("available", 1)
        self.registry.recovery_verifier = lambda _: (_ for _ in ()).throw(ValueError("private backend content"))
        with self.assertRaisesRegex(Hold, "^reconstruction_unavailable$"):
            self.claim()

    def test_checkpoint_origin_survives_first_cloud_effect_reconstruction(self):
        self.prepare_transfer()
        self.capture()
        self.acknowledge()
        self.registry.perform_effect(principal="cloud-session", actor="cloud", key=self.key, generation=2,
                                     operation_id="first-cloud-effect", payload_sha256=PIN,
                                     effect=lambda: dict(status="verified", receipt_sha256=PIN))
        self.assertEqual(self.reads[-1].generation, 2)
        self.assertEqual((self.reads[-1].origin_owner, self.reads[-1].origin_generation), ("native", 1))


if __name__ == "__main__":
    unittest.main()
