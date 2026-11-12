"""Exercise the actual dispatch hook through owner admission without spawning a worker."""

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import test_dispatch as dispatch_fixtures
from uif.admission import OwnerAdmission, _fingerprint
from uif.ledger import SNAPSHOT_SHA256, read_snapshot
from uif.source import SourceHold, file_digest


class OwnerDispatchIntegrationTests(IsolatedAsyncioTestCase):
    async def test_normal_dispatch_registers_original_before_a_failed_launch(self):
        fixture = dispatch_fixtures.DispatchTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        snapshot = Path(__file__).resolve().parents[2] / "README.md"
        row = "src-059-qualification"
        digest = read_snapshot(snapshot)[row].source_sha256
        store = "fixture-original-store"
        lease = "rt-" + _fingerprint({
            "runtime": str(fixture.runtime), "manifest_sha256": _fingerprint(fixture.manifest),
            "issue": fixture.polled.identifier,
        })
        fixture.pool.mkdir()
        database = fixture.pool / "state.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE leases (id TEXT PRIMARY KEY, binding TEXT, fingerprint TEXT, status TEXT, outcome TEXT)")
        database.chmod(0o600)
        claim_path = fixture.runtime / "current-admission.json"
        claim = json.loads(claim_path.read_bytes())
        claim["ledger"] = dict(store_id=store, row_id=row, epoch="epoch", snapshot_sha256=SNAPSHOT_SHA256,
                               source_sha256=digest, original_operation_id="first-op")
        claim_pin = dispatch_fixtures.put(claim_path, claim)
        effect = fixture.root / "effect.json"
        effect_pin = dispatch_fixtures.put(effect, {
            "schema": "uif-original-effect/1", "store_id": store, "row_id": row, "owner": "owner",
            "epoch": "epoch", "source_sha256": digest, "runtime": str(fixture.runtime),
            "lease_ids": [lease], "state": "confirmed-absent",
        })
        catalogue = fixture.root / "catalogue.json"
        catalogue_pin = dispatch_fixtures.put(catalogue, {
            "schema": "uif-owner-catalogue/1", "store_id": store, "owner": "owner", "epoch": "epoch",
            "rows": {row: {
                "runtime": str(fixture.runtime), "source_sha256": digest, "phase": "implementation",
                "disposition": "claimed", "lease_ids": [lease], "issue": asdict(fixture.polled),
                "claim": {"path": str(claim_path), "sha256": claim_pin}, "provider_sha256": fixture.provider_pin,
                "effect": {"path": str(effect), "sha256": effect_pin}, "readiness": [], "counterparts": [],
            }},
        })
        fenced = False

        @contextmanager
        def fence(issue, admission):
            nonlocal fenced
            self.assertFalse(fenced)
            self.assertEqual((issue, admission.original_operation_id), (fixture.polled, "first-op"))
            fenced = True
            try:
                yield
            finally:
                fenced = False

        authority = OwnerAdmission(
            snapshot_path=snapshot, catalogue=catalogue, catalogue_sha256=catalogue_pin,
            runtime_root=fixture.root, pool_database=database, store_id=store, owner="owner",
            epoch="epoch", guard=lambda: None, fence=fence, verify_refill=lambda _: None,
        )
        reports = []

        async def report(binding, outcome):
            reports.append((binding, outcome))

        orchestrator, workspace, owner = SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
        authority.bind_dispatch(
            orchestrator, workspace, owner, coordinator_runtime=fixture.coordinator,
            selected_source=lambda *_: fixture.source, polled_issue=lambda _: fixture.polled,
            report_attempt=report, supervisor=fixture.supervisor,
            supervisor_sha256=file_digest(fixture.supervisor),
        )
        popen = subprocess.Popen
        launches = []

        def unavailable(argv, *args, **kwargs):
            if argv[0] == "/usr/bin/git":
                return popen(argv, *args, **kwargs)
            self.assertTrue(fenced)
            self.assertEqual(argv[1], str(fixture.supervisor))
            self.assertTrue(fixture.path.is_file())
            launches.append(argv)
            raise OSError("disposable fixture prevents supervisor start")

        async def turn():
            return await orchestrator.run_turn(
                "codex", SimpleNamespace(model="gpt-6.1-sol", turn_timeout_ms=12000),
                SimpleNamespace(), "fixture prompt", fixture.workspace, fixture.issue, fixture.attempt,
            )

        with patch("uif.dispatch.subprocess.Popen", side_effect=unavailable):
            with self.assertRaisesRegex(SourceHold, "spawn_requires_reconciliation"):
                await turn()
            with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
                await turn()
        self.assertEqual(len(launches), 1)
        await owner._publish_run_report(fixture.issue, fixture.attempt, fixture.workspace, "coding")
        self.assertEqual(len(reports), 1)
        binding, outcome = reports[0]
        self.assertEqual(binding.ledger.row_id, row)
        self.assertEqual(binding.receipt_sha256, file_digest(fixture.path))
        self.assertEqual((outcome.status, outcome.operation_id, outcome.lease_id),
                         ("uncertain", "first-op", lease))
