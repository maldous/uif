"""Verify the concrete owner catalogue and receipt adapter in disposable custody."""

from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase

from uif.admission import OwnerAdmission, _fingerprint
from uif.ledger import SNAPSHOT_SHA256, RowKey, read_snapshot
from uif.source import SourceHold
from uif.stokowski import UIF, PolledIssue

SNAPSHOT = Path(__file__).resolve().parents[2] / "README.md"
ROW = "src-059-qualification"
PREDECESSOR = "src-060-placement-decision"


class OwnerAdmissionTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.pool = self.root / "state.sqlite"
        with sqlite3.connect(self.pool) as db:
            db.execute("CREATE TABLE leases (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        self.key = RowKey("fixture-uif-store", ROW)
        self.issue = PolledIssue("UIF-900001", "1765ee92-55d6-4012-a889-95e65555711d",
                                 UIF.project_id, UIF.project_slug)
        self.source = read_snapshot(SNAPSHOT)[ROW].source_sha256
        self.active = True
        self.locked = False
        self.effect = {
            "schema": "uif-original-effect/1", "store_id": self.key.store_id,
            "row_id": ROW, "owner": "owner", "epoch": "epoch", "source_sha256": self.source,
            "runtime": str(self.runtime), "lease_ids": ["rt-original"], "state": "confirmed-absent",
        }
        self.entry = {
            "runtime": str(self.runtime), "source_sha256": self.source, "phase": "implementation",
            "disposition": "ready", "lease_ids": ["rt-original"], "issue": asdict(self.issue),
            "readiness": [], "counterparts": [],
        }

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, sort_keys=True).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        return {"path": str(path), "sha256": sha256(raw).hexdigest()}

    def guard(self):
        if not self.active:
            raise SourceHold("fixture_owner_fence_lost")

    @contextmanager
    def fence(self, issue, admission):
        self.assertEqual(issue, self.issue)
        self.assertEqual(admission.epoch, "epoch")
        self.assertFalse(self.locked)
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def reader(self, *, other_rows=None):
        catalogue = self.root / "catalogue.json"
        self.effect["lease_ids"] = self.entry["lease_ids"].copy()
        self.entry["effect"] = self.write(self.root / "effect.json", self.effect)
        ref = self.write(catalogue, {"schema": "uif-owner-catalogue/1", "store_id": self.key.store_id,
                                     "owner": "owner", "epoch": "epoch",
                                     "rows": {ROW: self.entry, **(other_rows or {})}})
        return OwnerAdmission(
            snapshot_path=SNAPSHOT, catalogue=catalogue, catalogue_sha256=ref["sha256"],
            runtime_root=self.root, pool_database=self.pool, store_id=self.key.store_id,
            owner="owner", epoch="epoch", guard=self.guard, fence=self.fence,
            verify_refill=lambda operation: RowKey(self.key.store_id, PREDECESSOR)
            if operation == "previous" else None,
        )

    def claim(self):
        current = {
            "version": 1, "owner": "owner", "issue": self.issue.identifier, "issue_id": self.issue.issue_id,
            "source": str(self.root / "checkout"), "inputs": {},
            "ledger": {"store_id": self.key.store_id, "row_id": ROW, "epoch": "epoch",
                       "snapshot_sha256": SNAPSHOT_SHA256, "source_sha256": self.source,
                       "original_operation_id": "first-op"},
        }
        values = (
            ("manifest.json", "manifest_sha256", {}),
            ("capacity-policy.json", "policy_sha256", {"symphony": {"pool_path": str(self.pool.parent)}}),
            ("installation.json", "installation_sha256", {}),
            (f"requests/{self.issue.identifier}.json", "request_sha256", {}),
        )
        for path, field, value in values:
            current[field] = self.write(self.runtime / path, value)["sha256"]
        self.entry["lease_ids"] += ["rt-" + _fingerprint({
            "runtime": str(self.runtime), "manifest_sha256": _fingerprint({}), "issue": self.issue.identifier,
        })]
        self.entry.update(disposition="claimed", claim=self.write(self.runtime / "current-admission.json", current))

    def test_qualified_first_pickup_and_refill_hold_after_pool_effect(self):
        owner = self.reader()
        self.assertEqual(owner.ledger.pickup(ROW), owner.ledger.refill(ROW, previous_operation_id="previous"))
        self.assertIsNone(owner.ledger.pickup(ROW).operation_id)
        self.assertEqual(owner.select_ready().eligible, owner.select_ready(previous_operation_id="previous").eligible)
        before = self.pool.read_bytes()
        owner.ledger.pickup(ROW)
        self.assertEqual(self.pool.read_bytes(), before)
        with sqlite3.connect(self.pool) as db:
            db.execute("INSERT INTO leases VALUES (?, ?)", ("rt-original", "finished"))
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            owner.ledger.pickup(ROW)
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            owner.ledger.refill(ROW, previous_operation_id="previous")
        self.assertEqual(owner.select_ready().held, {ROW: "uif_original_operation_requires_reconciliation"})

    def test_claim_and_fresh_intent_follow_existing_owner_fence(self):
        self.claim()
        owner = self.reader()
        admission = owner.context(self.issue).ledger
        intent = self.runtime / "attempts" / self.issue.identifier / "dispatch-intent.json"
        self.assertEqual(admission.original_operation_id, "first-op")
        with owner.dispatch_fence(self.issue, admission) as record:
            self.assertTrue(self.locked)
            receipt = self.write(intent, {"schema": "uif-dispatch-intent/1",
                                          "admission": asdict(admission), "issue": asdict(self.issue)})
            with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
                owner.context(self.issue)
            record(intent, receipt["sha256"])
            owner.ledger.verify_dispatch(admission, self.issue, operation_id="first-op",
                                         receipt_path=intent, receipt_sha256=receipt["sha256"])
        self.assertFalse(self.locked)
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            owner.ledger.verify_dispatch(admission, self.issue, operation_id="first-op",
                                         receipt_path=intent, receipt_sha256=receipt["sha256"])
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            with owner.dispatch_fence(self.issue, admission):
                self.fail("retained intent acquired a fresh dispatch fence")

    def test_catalogue_and_claim_input_drift_refuse(self):
        self.claim()
        owner = self.reader()
        self.write(self.runtime / "manifest.json", {"changed": True})
        with self.assertRaisesRegex(SourceHold, "claim_input_drift"):
            owner.context(self.issue)
        self.write(self.root / "catalogue.json", {})
        with self.assertRaisesRegex(SourceHold, "record_drift"):
            owner.context(self.issue)

    def test_historical_receipt_and_shared_hold_are_kept(self):
        self.entry["counterparts"] = ["cq"]
        owner = self.reader()
        with self.assertRaisesRegex(SourceHold, "counterpart_held"):
            owner.ledger.pickup(ROW)
        self.entry["counterparts"] = []
        owner = self.reader()
        self.write(self.runtime / "attempts" / self.issue.identifier / "receipt.json", {"result": "retained"})
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            owner.ledger.pickup(ROW)

    def test_claim_cannot_supply_missing_historical_operation(self):
        self.effect["state"] = "reconciled-for-pickup"
        owner = self.reader()
        with self.assertRaisesRegex(SourceHold, "original_operation_missing"):
            owner.ledger.pickup(ROW)
        self.claim()
        owner = self.reader()
        with self.assertRaisesRegex(SourceHold, "original_operation_missing"):
            owner.context(self.issue)
        self.effect["operation_id"] = "different-op"
        owner = self.reader()
        with self.assertRaisesRegex(SourceHold, "original_operation_drift"):
            owner.context(self.issue)
        self.effect["operation_id"] = "first-op"
        self.assertEqual(self.reader().context(self.issue).ledger.original_operation_id, "first-op")

    def test_claim_holds_if_other_row_reserves_same_operation(self):
        self.claim()
        original_admission = self.reader().context(self.issue).ledger
        runtime = self.root / "other-runtime"
        source = read_snapshot(SNAPSHOT)[PREDECESSOR].source_sha256
        effect = dict(self.effect, row_id=PREDECESSOR, runtime=str(runtime), source_sha256=source,
                      lease_ids=[], state="reconciled-for-pickup", operation_id="first-op")
        entry = dict(runtime=str(runtime), source_sha256=source, phase="implementation",
                     disposition="held", lease_ids=[], readiness=[], counterparts=[])
        entry["effect"] = self.write(runtime / "effect.json", effect)
        for reservation in ("effect", "claim"):
            with self.subTest(reservation=reservation):
                if reservation == "claim":
                    effect.update(state="confirmed-absent", operation_id=None)
                    entry["effect"] = self.write(runtime / "effect.json", effect)
                    entry["claim"] = self.write(runtime / "current-admission.json", {
                        "ledger": {"original_operation_id": "first-op"},
                    })
                owner = self.reader(other_rows={PREDECESSOR: entry})
                with self.assertRaisesRegex(SourceHold, "original_operation_ambiguous"):
                    owner.context(self.issue)
                with self.assertRaisesRegex(SourceHold, "original_operation_ambiguous"):
                    with owner.dispatch_fence(self.issue, original_admission):
                        self.fail("duplicate operation reached intent registration")
                self.assertFalse((self.runtime / "attempts").exists())
