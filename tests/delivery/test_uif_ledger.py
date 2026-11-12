"""Exercise the original ledger reader through the UIF admission and hook boundary."""

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

import test_stokowski_control as fixtures
from uif.ledger import (
    SNAPSHOT_SHA256, Counterpart, CurrentDispatchIntent, CurrentRow, RecoveredLedger, RowKey,
    bind_recovered_ledger_lifecycle, read_snapshot,
)
from uif.source import SourceHold, file_digest
from uif.stokowski import UIF, LedgerAdmission, Workspace

SNAPSHOT = Path(__file__).resolve().parents[2] / "README.md"
ROW = "src-059-qualification"
PREDECESSOR = "src-060-placement-decision"


@dataclass
class Attempt:
    issue_id: str
    issue_identifier: str
    workspace_path: str
    attempt: int | None = None


class LedgerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.reads = []
        self.warranted = {}

        def read_current(key):
            self.reads.append(key)
            return self.current

        self.reader = RecoveredLedger(SNAPSHOT, store_id="fixture-original-uif-store",
                                      read_current=read_current, verify_refill=self.warranted.get)
        self.key = RowKey(self.reader.store_id, ROW)
        self.current = CurrentRow(
            key=self.key, source_sha256=self.reader.rows[ROW].source_sha256,
            project_id=UIF.project_id, phase="implementation", disposition="ready",
            prior_effect="confirmed-absent", effect_evidence_sha256="a" * 64,
            operation_id=None, owner=None, epoch=None, counterparts=(),
        )

    def test_pinned_reader_counts_original_rows_and_refuses_altered_source(self):
        self.assertEqual(Counter(row.section for row in self.reader.rows.values()),
                         {"uif": 73, "cq": 26, "cix": 19, "offmenu": 54, "anqr": 7, "discovery": 34})
        self.assertIn("UIF-214:dirty-postimage-R185", self.reader.rows)
        for row_id in ("shared.publication.UIF-218", "UIF94", "UIF-137", "UIF-106"):
            with self.assertRaisesRegex(SourceHold, "unknown_original_row"):
                self.reader.pickup(row_id)
        changed = self.root / "README.md"
        changed.write_bytes(SNAPSHOT.read_bytes() + b"\n")
        with self.assertRaisesRegex(SourceHold, "snapshot_pin"):
            read_snapshot(changed)
        self.assertEqual(self.reads, [])

    def test_pickup_does_not_promote_planning_or_unresolved_effects(self):
        ready = self.current
        self.assertIs(self.reader.pickup(ROW), ready)
        for change, reason in (
            ({"source_sha256": "b" * 64}, "current_binding"),
            ({"effect_evidence_sha256": ""}, "requires_reconciliation"),
            ({"prior_effect": "unknown"}, "requires_reconciliation"),
            ({"prior_effect": "reconciled-for-pickup"}, "original_operation_missing"),
            ({"operation_id": "already-known"}, "first_claim_binding"),
            ({"owner": "still-owned"}, "pickup_held"),
            ({"disposition": "completed"}, "pickup_held"),
            ({"disposition": "superseded"}, "pickup_held"),
            ({"counterparts": None}, "allocation_unknown"),
        ):
            with self.subTest(change=change):
                self.current = replace(ready, **change)
                with self.assertRaisesRegex(SourceHold, reason):
                    self.reader.pickup(ROW)
        self.current = replace(ready, prior_effect="reconciled-for-pickup", operation_id="original-effect")
        self.assertEqual(self.reader.pickup(ROW).operation_id, "original-effect")
        self.assertTrue(all(key == self.key for key in self.reads))

    def test_shared_readiness_checks_only_relevant_counterpart(self):
        self.current = replace(self.current, counterparts=("cq",))
        with self.assertRaisesRegex(SourceHold, "counterpart_held"):
            self.reader.pickup(ROW)
        cq = Counterpart("cq", True, True, "b" * 64)
        self.current = replace(self.current, readiness=(replace(cq, project="cix"),))
        with self.assertRaisesRegex(SourceHold, "counterpart_held"):
            self.reader.pickup(ROW)
        self.current = replace(self.current, readiness=(replace(cq, working=False),))
        with self.assertRaisesRegex(SourceHold, "counterpart_held"):
            self.reader.pickup(ROW)
        self.current = replace(self.current, readiness=(cq,))
        self.assertEqual(self.reader.pickup(ROW).key, self.key)

    def test_refill_requires_warrant_and_rejects_replay_of_prior_row(self):
        with self.assertRaisesRegex(SourceHold, "refill_not_warranted"):
            self.reader.refill(ROW, previous_operation_id="previous")
        self.warranted["previous"] = self.key
        with self.assertRaisesRegex(SourceHold, "refill_original_replay"):
            self.reader.refill(ROW, previous_operation_id="previous")
        self.warranted["previous"] = RowKey(self.reader.store_id, PREDECESSOR)
        self.assertEqual(self.reader.refill(ROW, previous_operation_id="previous"), self.reader.pickup(ROW))
        self.current = replace(self.current, disposition="uncertain")
        with self.assertRaisesRegex(SourceHold, "pickup_held"):
            self.reader.refill(ROW, previous_operation_id="previous")

    async def test_production_bootstrap_checks_claim_for_workspace_dispatch_and_report(self):
        fixture = fixtures.SourceControlTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.acquire()
        source = fixture.boundary()
        reports = []
        coordinator = SimpleNamespace()
        workspace = SimpleNamespace(WorkspaceResult=Workspace)
        owner = SimpleNamespace()
        issue = SimpleNamespace(id=fixture.issue.issue_id, identifier=fixture.issue.identifier)
        ready = self.reader.pickup(ROW)

        # The test supplies the readback of the existing claim. The production
        # bootstrap consumes that binding and never creates a claim itself.
        self.current = replace(ready, issue=fixture.issue, disposition="claimed",
                               operation_id="first-op", owner="owner", epoch="epoch")
        admission = LedgerAdmission(
            self.key.store_id, SNAPSHOT_SHA256, self.key.row_id, ready.source_sha256,
            "implementation", "owner", "epoch", "first-op", "ready", "confirmed-absent", False,
        )

        async def reporter(binding):
            reports.append(binding)

        hooks = bind_recovered_ledger_lifecycle(
            coordinator, workspace, owner, ledger=self.reader, runtime_root=fixture.root,
            coordinator_runtime=fixture.coordinator,
            qualify_context=lambda selected: replace(source.qualify_context(selected), ledger=admission),
            selected_source=source.selected_source, polled_issue=lambda identifier: fixture.issue,
            report_attempt=reporter,
        )
        with self.assertRaisesRegex(SourceHold, "pickup_held"):
            hooks.pickup(ROW)
        claimed = self.current
        self.current = replace(claimed, epoch="different-epoch")
        with self.assertRaisesRegex(SourceHold, "claim_drift"):
            await coordinator.ensure_workspace(fixture.coordinator / "workspaces", issue.identifier, {})
        self.current = claimed
        result = await coordinator.ensure_workspace(fixture.coordinator / "workspaces", issue.identifier, {})
        self.assertTrue(result.created)
        receipt = fixture.runtime / "dispatch-intent.json"
        receipt.write_text("fixture first dispatch receipt")
        receipt.chmod(0o600)
        attempt = Attempt(issue.id, issue.identifier, str(result.path))
        pin = file_digest(receipt)

        def register(selected_admission=admission, operation="first-op", receipt_pin=pin):
            return hooks.register_dispatch(issue, attempt, result.path, admission=selected_admission,
                                           operation_id=operation, receipt_path=receipt,
                                           receipt_sha256=receipt_pin)

        with self.assertRaisesRegex(SourceHold, "dispatch_intent_drift"):
            register()
        self.current = replace(claimed, dispatch_intent=CurrentDispatchIntent("first-op", receipt, pin))
        intended = self.current
        with self.assertRaisesRegex(SourceHold, "dispatch_already_intended"):
            await coordinator.ensure_workspace(fixture.coordinator / "workspaces", issue.identifier, {})
        for change in ({"prior_effect": "unknown"}, {"effect_evidence_sha256": ""}):
            self.current = replace(intended, **change)
            with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
                register()
        self.current = intended
        with self.assertRaisesRegex(SourceHold, "admission_held"):
            register(replace(admission, disposition="superseded"))
        with self.assertRaisesRegex(SourceHold, "dispatch_operation_drift"):
            register(operation="later-op")
        with self.assertRaisesRegex(SourceHold, "dispatch_intent_drift"):
            register(receipt_pin="b" * 64)
        binding = register()
        self.current = replace(claimed, prior_effect="unknown", disposition="superseded")
        with self.assertRaisesRegex(SourceHold, "claim_drift"):
            register()
        await owner._publish_run_report(issue, attempt, result.path, "coding")
        self.assertEqual(reports, [binding])
        self.assertEqual(binding.ledger, admission)
        self.assertEqual(binding.operation_id, "first-op")
        await workspace.remove_workspace(result.path.parent, issue.identifier, {})
        self.assertTrue(result.path.is_dir())
        self.assertEqual(receipt.read_text(), "fixture first dispatch receipt")
