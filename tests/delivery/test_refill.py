"""Verify the refill predecessor through the canonical publication and ledger."""

from dataclasses import replace
import json

from test_publication import OPERATION, PREDECESSOR, SNAPSHOT, STORE, PublicationFixture
from uif.ledger import CurrentRow, RecoveredLedger, RowKey
from uif.native_publisher import inner_response
from uif.publication import fingerprint
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


class LedgerRefillTests(PublicationFixture):
    ledger_only = True

    def test_issue_free_completion_uses_original_row_and_operation_for_refill(self):
        accepted = self.publisher.consume_boundary(self.request)
        completed = DeliveryCompletion(accepted, "completed", "4" * 64)
        refill = VerifiedRefill(STORE, {OPERATION: completed}.get, self.publisher)
        self.assertEqual(refill.verify_refill(OPERATION), RowKey(STORE, PREDECESSOR))
        self.assertNotIn("issue", accepted.request.to_record())
        self.assertEqual(accepted.request.to_record()["task"], self.request.task.to_record())
        with self.assertRaisesRegex(SourceHold, "final_outcome_required"):
            refill.verify_refill(self.request.invocation_id)
        self.assertEqual([call["mode"] for call in self.calls()], ["invoke", "observe"])

    def test_actual_owner_persists_and_refills_from_issue_free_completion(self):
        from test_owner_runtime import OwnerRuntimeTests

        fixture = OwnerRuntimeTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        key = fixture.prepare_ledger_inputs(state="reconciled-for-pickup", operation=OPERATION)
        authority = fixture.authority()
        task = authority.claim_ledger_task(key)
        admission = authority.admission.ledger_context(task).ledger
        self.request = replace(self.request, admission=admission, task=task)
        ready = self.result()["receipt"]["body"]
        inner = {**self.request.envelope(observe=False)["request"], "mode": "observe"}
        ready["intent"].update(request=inner, request_sha256=fingerprint(inner),
                               invocation_id=self.request.readiness_invocation_id)
        response = inner_response(inner, "ready")
        ready["terminal"].update(response=response, response_sha256=fingerprint(response), evidence=None)
        reference = fixture.ref("publisher-ready.json", ready)
        self.request = replace(self.request, readiness_receipt_sha256=reference["sha256"])
        self.write_reply(self.result())

        with authority.lock():
            catalogue, pin = authority.catalogue()
            catalogue["rows"][task.key.row_id]["publication_readiness"] = reference
            authority._replace_catalogue(catalogue, pin)
        publisher = replace(self.publisher, fence=authority.publication_fence)
        authority.register_publisher(publisher)
        accepted = publisher.consume_boundary(self.request)
        completed = authority.complete_publication(accepted)
        self.assertEqual(authority.read_completion(OPERATION), completed)
        self.assertEqual(authority.verify_refill(OPERATION), RowKey(key.store_id, key.row_id))

        receipt = json.loads((fixture.fixture.runtime / "delivery-completion.json").read_bytes())
        self.assertEqual(receipt["schema"], "uif-owner-delivery-completion/2")
        self.assertEqual(receipt["publication"]["request"], self.request.to_record())
        self.assertNotIn("issue", receipt["publication"]["request"])
        self.assertEqual([call["mode"] for call in self.calls()], ["invoke", "observe", "observe"])
