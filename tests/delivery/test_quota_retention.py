"""New retention/crash cases only; no live authority or accepted-suite replay."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import uif.handoff_store as store
from uif.handoff_store import Hold, MAX_STATE_BYTES, CONTROL_RESERVE_BYTES
from uif.quota_handoff import Registry, LinearKey, RecoveryReceipt

W, P = str(UUID(int=1)), str(UUID(int=2))
PIN = "d" * 64
SOURCE = dict(repository="maldous/uif", branch="develop", base="a" * 40, head="b" * 40, tree="c" * 40)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "authority"
        self.now, self.number = 1000.0, 0
        self.coordinator = dict(execution_id="morgan-current", generation=1)
        self.registry = Registry.initialize(
            self.root, workspace_id=W,
            actors={"morgan": dict(role="morgan", projects=[P]), "native": dict(role="native", projects=[P])},
            principals={"morgan-session": ["morgan"], "native-session": ["native"]},
            morgan_actor="morgan", morgan_execution_id="morgan-current", morgan_principal="morgan-session",
            recovery_verifier=lambda context: RecoveryReceipt(PIN, context.evidence_ref, self.now),
            operation_verifier=lambda context: True, provider_verifier=lambda context: True,
            completion_verifier=lambda context: True, clock=lambda: self.now)
        self.key = LinearKey(W, P, str(UUID(int=3)))
        self.call("morgan", "set_stopped", stopped=False)
        self.call("native", "provider_observation", classification="available", sequence=1,
                  observed_at=self.now, evidence_sha256=PIN)

    def request(self, actor, action, **fields):
        self.number += 1
        if actor == "morgan":
            fields["coordinator"] = self.coordinator
        return dict(action=action, request_id="request-" + str(self.number), **fields)

    def invoke(self, actor, request):
        return self.registry.invoke(principal=actor + "-session", actor=actor, request=request)

    def call(self, actor, action, **fields):
        return self.invoke(actor, self.request(actor, action, **fields))

    def pulse(self):
        self.now += .1
        return self.call("morgan", "heartbeat", observed_at=self.now)

    def claim(self, key=None):
        return self.call("native", "claim", key=asdict(key or self.key), lane="local", source=SOURCE,
                         evidence_ref="accepted-reconstruction", original_operation_id="original-admission")

    def finish(self, accepted=False, generation=1):
        return self.call("native", "finish", key=asdict(self.key), generation=generation,
                         accepted=accepted, receipt_sha256=PIN)

    def raw(self):
        return json.loads((self.root / "state.json").read_text())

    def task(self):
        return self.invoke("morgan", dict(action="task_status", key=asdict(self.key)))

    def small_hot(self):
        self.patch_requests = patch.object(store, "HOT_REQUESTS", 2)
        self.patch_events = patch.object(store, "HOT_EVENTS", 2)
        self.patch_requests.start()
        self.patch_events.start()
        self.addCleanup(self.patch_requests.stop)
        self.addCleanup(self.patch_events.stop)

    def test_permanent_request_result_and_conflict_lookup_after_hot_prune(self):
        self.small_hot()
        self.now += 1
        original = self.request("morgan", "heartbeat", observed_at=self.now)
        result = self.invoke("morgan", original)
        for _ in range(6):
            self.pulse()
        state = self.raw()
        self.assertLessEqual(len(state["requests"]), 2)
        self.assertLessEqual(len(state["events"]), 2)
        self.assertNotIn("morgan:" + original["request_id"], state["requests"])
        self.assertEqual(self.invoke("morgan", original), result)
        with self.assertRaisesRegex(Hold, "request_identity_conflict"):
            self.invoke("morgan", dict(original, observed_at=original["observed_at"] + .1))
        with self.registry.store.transaction() as (_, state, save):
            state["coordinator"].update(generation=2, execution_id="successor-morgan")
            state["morgan"] = None
            save()
        with self.assertRaisesRegex(Hold, "obsolete_coordinator"):
            self.invoke("morgan", original)  # a cold heartbeat never revives its old epoch

    def test_legacy_8192_receipts_do_not_block_first_stop_and_compaction(self):
        with self.registry.store.transaction() as (_, state, save):
            state["requests"] = {"native:retained-" + str(i):
                                 dict(sha256=PIN, result=dict(status="retained", revision=i))
                                 for i in range(8192)}
            save()
        self.assertEqual(len(self.raw()["requests"]), 8192)  # newly prepared fixture records stay hot
        self.call("morgan", "set_stopped", stopped=True)
        self.assertTrue(self.raw()["stopped"])
        self.assertLessEqual(len(self.raw()["requests"]), store.HOT_REQUESTS)
        self.assertEqual(self.registry.store.request_receipt("native:retained-0")["sha256"], PIN)

    def test_cold_commit_before_hot_replace_retains_only_previously_committed_results(self):
        self.small_hot()
        old = self.request("morgan", "heartbeat", observed_at=self.now)
        old_result = self.invoke("morgan", old)
        before = (self.root / "state.json").read_bytes()
        pending = self.request("morgan", "set_stopped", stopped=True)
        replace = store.os.replace

        def fail_hot(source, target):
            if Path(target).name == "state.json":
                raise OSError("synthetic loss before hot commit")
            return replace(source, target)

        with patch.object(store.os, "replace", side_effect=fail_hot):
            with self.assertRaises(OSError):
                self.invoke("morgan", pending)
        self.assertEqual((self.root / "state.json").read_bytes(), before)
        self.assertIsNone(self.registry.store.request_receipt("morgan:" + pending["request_id"]))
        self.assertEqual(self.invoke("morgan", old), old_result)
        self.assertTrue(self.invoke("morgan", pending)["stopped"])
        self.assertTrue(self.raw()["stopped"])

    def test_terminal_tombstone_blocks_fresh_claim_and_keeps_failed_lineage(self):
        self.small_hot()
        self.claim()
        self.registry.perform_effect(principal="native-session", actor="native", key=self.key, generation=1,
                                     operation_id="original-denied", payload_sha256=PIN,
                                     effect=lambda: dict(status="denied", receipt_sha256=PIN))
        self.finish()
        self.pulse()
        self.assertNotIn(self.key.identity, self.raw()["tasks"])
        retained = self.task()
        self.assertEqual(retained["operations"]["original-denied"]["status"], "denied")
        with self.assertRaisesRegex(Hold, "existing_claim_requires_reconciliation"):
            self.claim()
        result = self.call("native", "retry_claim", key=asdict(self.key), generation=1,
                           lane="local", source=SOURCE, evidence_ref="accepted-retry",
                           original_operation_id="second-admission")
        self.assertEqual(result["generation"], 2)
        task = self.task()
        self.assertEqual(task["attempts"][0]["operations"], retained["operations"])
        self.assertEqual(task["checkpoint_origin"], dict(owner="native", generation=1))
        with self.assertRaisesRegex(Hold, "historical_operation_requires_reconciliation"):
            self.registry.perform_effect(principal="native-session", actor="native", key=self.key, generation=2,
                                         operation_id="original-denied", payload_sha256=PIN,
                                         effect=lambda: self.fail("old denied effect replayed"))

    def test_unknown_operation_and_its_owner_never_archived(self):
        self.small_hot()
        self.claim()

        def uncertain():
            raise TimeoutError("lost original reply")

        with self.assertRaises(TimeoutError):
            self.registry.perform_effect(principal="native-session", actor="native", key=self.key, generation=1,
                                         operation_id="original-unknown", payload_sha256=PIN, effect=uncertain)
        for _ in range(4):
            self.pulse()
        self.assertEqual(self.raw()["tasks"][self.key.identity]["operations"]["original-unknown"]["status"], "unknown")
        self.assertIsNone(self.registry.store.archived_task(self.key.identity))
        self.assertEqual(self.task()["owner"], "native")

    def test_headroom_holds_new_work_but_stop_resolve_finish_and_archive_io_fallback_work(self):
        self.claim()

        def uncertain():
            raise TimeoutError("lost original reply")

        with self.assertRaises(TimeoutError):
            self.registry.perform_effect(principal="native-session", actor="native", key=self.key, generation=1,
                                         operation_id="original-unknown", payload_sha256=PIN, effect=uncertain)
        with self.registry.store.transaction() as (_, state, save):
            state["fixture_padding"] = "x" * (MAX_STATE_BYTES - CONTROL_RESERVE_BYTES)
            save()
        other = LinearKey(W, P, str(UUID(int=4)))
        with self.assertRaisesRegex(Hold, "registry_admission_headroom"):
            self.claim(other)
        with patch.object(self.registry.store, "_compact", side_effect=OSError("archive volume temporarily unavailable")):
            self.call("morgan", "set_stopped", stopped=True)
            self.call("morgan", "set_stopped", stopped=False)
            self.call("native", "resolve_operation", key=asdict(self.key), generation=1,
                      operation_id="original-unknown", status="failed", receipt_sha256=PIN)
            self.finish()
        self.assertEqual(self.raw()["archive_degraded"], "archive_write_unavailable")
        self.assertEqual(self.raw()["tasks"][self.key.identity]["state"], "failed")

    def test_missing_or_stale_archive_fails_closed_after_pruning(self):
        self.small_hot()
        for _ in range(3):
            self.pulse()
        cold = self.root / "cold-archive.sqlite3"
        previous = cold.read_bytes()
        old_revision = self.raw()["cold_archive"]["minimum_revision"]
        for _ in range(3):
            self.pulse()
        self.assertGreater(self.raw()["cold_archive"]["minimum_revision"], old_revision)
        cold.write_bytes(previous)
        with self.assertRaisesRegex(Hold, "archive_revision_rollback"):
            self.pulse()
        cold.unlink()
        with self.assertRaisesRegex(Hold, "required_archive_unavailable"):
            self.pulse()

    def test_status_does_not_create_archive_or_upgrade_legacy_state(self):
        original = (self.root / "state.json").read_bytes()
        self.invoke("morgan", dict(action="status"))
        self.assertEqual((self.root / "state.json").read_bytes(), original)
        self.assertFalse((self.root / "cold-archive.sqlite3").exists())

    def test_interrupted_empty_archive_publication_leaves_no_canonical_trap(self):
        self.small_hot()
        replace = store.os.replace

        def fail_archive(source, target):
            if Path(target).name == "cold-archive.sqlite3":
                raise OSError("loss before archive publication")
            return replace(source, target)

        with patch.object(store.os, "replace", side_effect=fail_archive):
            self.pulse()
        self.assertFalse((self.root / "cold-archive.sqlite3").exists())
        self.pulse()
        self.assertTrue((self.root / "cold-archive.sqlite3").exists())
        self.assertNotIn("archive_degraded", self.raw())


if __name__ == "__main__":
    unittest.main()
