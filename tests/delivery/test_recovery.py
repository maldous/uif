"""Original-effect recovery and exact latest LangGraph checkpoint compatibility."""

from dataclasses import replace
from types import SimpleNamespace

from test_publication import PublicationFixture
from uif.recovery import CheckpointPin, LangGraphCheckpoint, Recovery
from uif.source import SourceHold


class ExistingSaver:
    def __init__(self, record):
        self.record = record
        self.reads = []

    def get_tuple(self, config):
        self.reads.append(config)
        return self.record


class RecoveryTests(PublicationFixture):
    def test_lost_reply_reconciles_original_without_second_effect(self):
        self.lose_reply.touch()
        with self.assertRaisesRegex(SourceHold, "outcome_unknown"):
            self.publisher.consume_boundary(self.request)
        result = Recovery(self.publisher).observe(self.request)
        self.assertEqual(result.publication.head, self.merge)
        self.assertIsNone(result.checkpoint)
        self.assertEqual(self.calls(), [self.request.envelope(observe=False), self.request.envelope(observe=True)])

    def test_missing_or_unknown_original_cannot_create_or_replace_intent(self):
        recovery = Recovery(self.publisher)
        with self.assertRaisesRegex(SourceHold, "original_intent"):
            recovery.observe(self.request)
        self.owner.intent = self.request
        self.write_reply({"version": 1, "status": "not-found"})
        with self.assertRaises(SourceHold):
            recovery.observe(self.request)
        self.assertEqual(self.calls(), [self.request.envelope(observe=True)])

    def test_latest_checkpoint_must_match_version_owner_steps_and_original_receipts(self):
        publication = self.publisher.consume_boundary(self.request)
        pin = CheckpointPin("thread", "", "checkpoint", "run", "a" * 64, "b" * 64,
                            "c" * 64, ("publish",), ("complete",), "d" * 64, 2)
        record = SimpleNamespace(
            config={"configurable": {"thread_id": "thread", "checkpoint_ns": "", "checkpoint_id": "checkpoint"}},
            checkpoint={"id": "checkpoint", "v": 2},
            metadata={"uif_recovery": pin.metadata(self.request, publication)},
            pending_writes=[],
        )
        saver = ExistingSaver(record)
        recovery = Recovery(self.publisher, LangGraphCheckpoint(
            saver, pin.graph_sha256, pin.schema_sha256, pin.code_sha256, pin.wire_version,
        ))
        self.assertEqual(recovery.observe(self.request, pin).checkpoint, pin)
        self.assertEqual(saver.reads, [{"configurable": {"thread_id": "thread", "checkpoint_ns": ""}}])
        for changed in (replace(pin, checkpoint_id="old"), replace(pin, wire_version=1),
                        replace(pin, schema_sha256="e" * 64), replace(pin, pending=("dispatch",))):
            with self.subTest(pin=changed), self.assertRaisesRegex(SourceHold, "checkpoint_incompatible"):
                recovery.observe(self.request, changed)
        record.metadata["uif_recovery"]["epoch"] = "older-epoch"
        with self.assertRaisesRegex(SourceHold, "checkpoint_incompatible"):
            recovery.observe(self.request, pin)
        self.assertTrue(all(call["mode"] == "observe" for call in self.calls()[1:]))
