"""Verify the refill predecessor through the canonical publication and ledger."""

from dataclasses import replace

from test_publication import OPERATION, PREDECESSOR, SNAPSHOT, STORE, PublicationFixture
from uif.ledger import CurrentRow, RecoveredLedger, RowKey
from uif.refill import DeliveryCompletion, VerifiedRefill
from uif.source import SourceHold
from uif.stokowski import UIF

SUCCESSOR = "src-059-qualification"


class RefillTests(PublicationFixture):
    def setUp(self):
        super().setUp()
        accepted = self.publisher.consume_boundary(self.request)
        self.completion = DeliveryCompletion(accepted, "completed", "4" * 64)
        self.refill = VerifiedRefill(STORE, lambda original: self.completion, self.publisher)
        self.ledger = RecoveredLedger(SNAPSHOT, store_id=STORE, read_current=self.read_current,
                                      verify_refill=self.refill.verify_refill)
        self.counterparts = ()

    def read_current(self, key):
        return CurrentRow(
            key=key, source_sha256=self.ledger.rows[key.row_id].source_sha256,
            project_id=UIF.project_id, phase="implementation", disposition="ready",
            prior_effect="confirmed-absent", effect_evidence_sha256="a" * 64,
            operation_id=None, owner=None, epoch=None, counterparts=self.counterparts,
        )

    def test_returns_predecessor_and_qualifies_successor_via_same_ledger(self):
        self.assertEqual(self.refill.verify_refill(OPERATION), RowKey(STORE, PREDECESSOR))
        self.assertEqual(self.ledger.refill(SUCCESSOR, previous_operation_id=OPERATION),
                         self.ledger.pickup(SUCCESSOR))
        with self.assertRaisesRegex(SourceHold, "original_replay"):
            self.ledger.refill(PREDECESSOR, previous_operation_id=OPERATION)
        self.counterparts = ("cq",)
        with self.assertRaisesRegex(SourceHold, "combined_counterpart_held"):
            self.ledger.refill(SUCCESSOR, previous_operation_id=OPERATION)
        self.assertEqual([call["mode"] for call in self.calls()], ["invoke"] + ["observe"] * 4)

    def test_requires_terminal_outcome_and_exact_original_operation_and_store(self):
        with self.assertRaisesRegex(SourceHold, "completion_binding"):
            self.refill.verify_refill("later-operation")
        with self.assertRaisesRegex(SourceHold, "completion_binding"):
            replace(self.refill, store_id="another-store").verify_refill(OPERATION)
        self.completion = replace(self.completion, outcome="publication-observed")
        with self.assertRaisesRegex(SourceHold, "final_outcome_required"):
            self.refill.verify_refill(OPERATION)
        self.assertEqual(len(self.calls()), 1)

    def test_refill_holds_if_accepted_publication_no_longer_reads_back(self):
        self.completion = replace(self.completion, publication=replace(
            self.completion.publication, accepted_source_sha256="5" * 64))
        with self.assertRaisesRegex(SourceHold, "publication_drift"):
            self.refill.verify_refill(OPERATION)
        self.assertEqual([call["mode"] for call in self.calls()], ["invoke", "observe"])
