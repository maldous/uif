from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from uif.dispatch import _encode
from uif.ledger import SNAPSHOT_SHA256, read_snapshot
from uif.source import SourceHold
from uif.stokowski import PolledIssue
from uif.task_identity import LedgerTaskEnvelope, LedgerTaskKey, SCHEMA

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "README.md"
ROW_ID = "standalone-harness-boundary-plan"
OTHER_ROW_ID = "uif217-r055-independent-custody"


class LedgerTaskIdentityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = read_snapshot(SNAPSHOT)

    def setUp(self):
        self.record = {
            "schema": SCHEMA, "store_id": "fixture-store", "row_id": ROW_ID,
            "snapshot_sha256": SNAPSHOT_SHA256, "source_sha256": self.rows[ROW_ID].source_sha256,
            "owner": "fixture-owner", "epoch": "fixture-epoch", "original_operation_id": "fixture-original",
        }

    def load(self, **changes):
        return LedgerTaskEnvelope.from_record({**self.record, **changes}, snapshot_path=SNAPSHOT)

    def test_round_trip_binds_the_original_row(self):
        envelope = self.load()
        self.assertEqual(envelope.key, LedgerTaskKey("fixture-store", ROW_ID))
        self.assertEqual(envelope.to_record(), self.record)
        restored = LedgerTaskEnvelope.from_record(json.loads(_encode(envelope.to_record())), snapshot_path=SNAPSHOT)
        self.assertEqual(restored, envelope)

    def test_workspace_key_depends_only_on_the_stable_store_and_row(self):
        original = self.load()
        updated = self.load(owner="next-owner", epoch="next-epoch", original_operation_id="next-operation")
        self.assertEqual(original.key.workspace_key,
                         "ledger-5c6f49037e933475097e16635cfb43a44c05a13ccb3b624f0e605bec2c3bea89")
        self.assertEqual(original.key.workspace_key, updated.key.workspace_key)
        self.assertNotEqual(original.key.workspace_key, self.load(store_id="other/store").key.workspace_key)
        another = self.load(row_id=OTHER_ROW_ID, source_sha256=self.rows[OTHER_ROW_ID].source_sha256)
        self.assertNotEqual(original.key.workspace_key, another.key.workspace_key)

    def test_rejects_issue_and_nonadditive_identities_as_original_rows(self):
        for row_id in ("UIF-137", "offmenu-retained-source-account"):
            with self.subTest(row_id=row_id), self.assertRaisesRegex(SourceHold, "unknown_original_row"):
                self.load(row_id=row_id)

    def test_rejects_snapshot_and_original_row_digest_drift(self):
        with self.assertRaisesRegex(SourceHold, "snapshot_pin"):
            self.load(snapshot_sha256="0" * 64)
        with self.assertRaisesRegex(SourceHold, "row_pin"):
            self.load(source_sha256=self.rows[OTHER_ROW_ID].source_sha256)
        with TemporaryDirectory() as temp:
            altered = Path(temp) / "README.md"
            altered.write_bytes(SNAPSHOT.read_bytes() + b"\nchanged\n")
            with self.assertRaisesRegex(SourceHold, "snapshot_pin"):
                LedgerTaskEnvelope.from_record(self.record, snapshot_path=altered)
            with self.assertRaisesRegex(SourceHold, "snapshot_pin"):
                LedgerTaskEnvelope.from_record(
                    {**self.record, "snapshot_sha256": sha256(altered.read_bytes()).hexdigest()},
                    snapshot_path=altered)

    def test_rejects_issue_fields_and_caller_supplied_effect_or_disposition(self):
        for field in ("issue", "issue_id", "identifier", "project_id", "project_slug", "linear",
                      "prior_effect", "disposition", "workspace_key"):
            with self.subTest(field=field), self.assertRaisesRegex(SourceHold, "task_record"):
                self.load(**{field: "invented"})

    def test_missing_original_operation_does_not_qualify_an_envelope(self):
        for value in (None, "", " ", " leading", "trailing\n"):
            with self.subTest(value=value), self.assertRaisesRegex(SourceHold, "operation_unbound"):
                self.load(original_operation_id=value)
        missing = self.record.copy()
        del missing["original_operation_id"]
        with self.assertRaisesRegex(SourceHold, "task_record"):
            LedgerTaskEnvelope.from_record(missing, snapshot_path=SNAPSHOT)

    def test_rejects_malformed_keys_and_authority_bindings(self):
        for field, value in (("store_id", ""), ("row_id", "../row"), ("row_id", 1)):
            with self.subTest(field=field), self.assertRaisesRegex(SourceHold, "task_key"):
                self.load(**{field: value})
        for field, value in (("owner", None), ("epoch", " ")):
            with self.subTest(field=field), self.assertRaisesRegex(SourceHold, "task_binding"):
                self.load(**{field: value})

    def test_schema_has_the_exact_closed_record_shape(self):
        schema = json.loads((ROOT / "config/delivery/ledger-task.schema.json").read_text())
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), self.record.keys())
        self.assertEqual(set(schema["properties"]), self.record.keys())
        self.assertEqual(schema["properties"]["schema"]["const"], SCHEMA)

    def test_polled_issue_encoding_is_unchanged(self):
        issue = PolledIssue("UIF-137", "fixture-issue-id", "fixture-project-id", "fixture-slug")
        self.assertEqual(_encode(asdict(issue)),
                         b'{"identifier":"UIF-137","issue_id":"fixture-issue-id",'
                         b'"project_id":"fixture-project-id","project_slug":"fixture-slug"}')
