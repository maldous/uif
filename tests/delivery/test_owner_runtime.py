"""Exercise the persisted owner authority at the coordinator's real hook boundaries."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import test_dispatch as dispatch_fixtures
import test_validation as validation_fixtures
import test_publication as publication_fixtures
from uif.admission import _fingerprint
from uif.ledger import RowKey, read_snapshot
from uif.native_publisher import inner_response
from uif.publication import fingerprint
from uif.owner_runtime import OwnerAuthority, bind_owner_coordinator
from uif.source import SourceHold, file_digest
from uif.stokowski import UIF, Workspace


ROW = "src-059-qualification"
SNAPSHOT = Path(__file__).resolve().parents[2] / "README.md"


class OwnerRuntimeTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = f = dispatch_fixtures.DispatchTests()
        self.addCleanup(f.doCleanups)
        f.setUp()
        for directory in (f.coordinator, f.workspace.parent):
            directory.chmod(0o700)
        f.pool.mkdir()
        database = f.pool / "state.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE leases (id TEXT PRIMARY KEY,binding TEXT,fingerprint TEXT,status TEXT,outcome TEXT)")
        database.chmod(0o600)
        lock = f.root / "catalogue.lock"
        lock.touch(mode=0o600)
        locks = f.root / "operation-locks"
        locks.mkdir(mode=0o700)
        self.claim_file = f.runtime / "current-admission.json"
        inputs = json.loads(self.claim_file.read_bytes())
        for field in ("owner", "issue", "issue_id", "version"):
            del inputs[field]
        self.claim_file.unlink()
        lease = "rt-" + _fingerprint(dict(runtime=str(f.runtime), manifest_sha256=_fingerprint(f.manifest),
                                          issue=f.issue.identifier))
        source_sha = read_snapshot(SNAPSHOT)[ROW].source_sha256
        store = "fixture-uif-store"
        effect = dict(schema="uif-original-effect/1", store_id=store, row_id=ROW, owner="owner",
                      epoch="epoch", source_sha256=source_sha, runtime=str(f.runtime),
                      lease_ids=[lease], state="confirmed-absent")
        source = {name: str(value) if isinstance(value, Path) else value
                  for name, value in asdict(f.source).items()}
        self.entry = dict(runtime=str(f.runtime), source_sha256=source_sha, phase="implementation",
                          disposition="ready", lease_ids=[lease], issue=asdict(f.polled),
                          effect=self.ref("effect.json", effect), provider_sha256=f.provider_pin,
                          selected_source=self.ref("selected.json", source),
                          admission_inputs=self.ref("admission-inputs.json", inputs),
                          readiness=[], counterparts=[])
        self.catalogue = f.root / "catalogue.json"
        self.set_catalogue()
        self.registration = self.ref("registration.json", dict(
            schema="uif-owner-runtime/1", store_id=store, owner="owner", epoch="epoch",
            catalogue=str(self.catalogue), lock=str(lock), operation_locks=str(locks),
            snapshot=str(SNAPSHOT), runtime_root=str(f.root), coordinator_runtime=str(f.coordinator),
            pool_database=str(database),
        ))

    def ref(self, name, value):
        path = self.fixture.root / name
        return dict(path=str(path), sha256=dispatch_fixtures.put(path, value))

    def set_catalogue(self):
        dispatch_fixtures.put(self.catalogue, dict(schema="uif-owner-catalogue/1", store_id="fixture-uif-store",
                                 owner="owner", epoch="epoch", rows={ROW: self.entry}))

    def authority(self):
        return OwnerAuthority.load(self.registration["path"], self.registration["sha256"])

    async def test_poll_dispatch_claim_and_original_report_survive_restart(self):
        f, authority = self.fixture, self.authority()
        calls = []

        async def fetch(slug, states):
            self.assertEqual(slug, UIF.project_slug)
            return [f.issue, SimpleNamespace(identifier="UIF-999999", id="another")]

        client = SimpleNamespace(fetch_candidate_issues=fetch)
        coordinator = SimpleNamespace(
            _ensure_linear_client=lambda: client,
            _dispatch=lambda issue, attempt_num=None: calls.append(issue),
            _release_slot=lambda issue_id: None, claimed=set(),
        )
        orchestrator = SimpleNamespace()
        bind_owner_coordinator(authority, orchestrator, SimpleNamespace(WorkspaceResult=Workspace), coordinator,
                               supervisor=f.supervisor, supervisor_sha256=file_digest(f.supervisor))
        selected, = await coordinator._ensure_linear_client().fetch_candidate_issues(UIF.project_slug, ["Todo"])
        self.assertFalse(self.claim_file.exists())
        coordinator._dispatch(selected)
        self.assertEqual(calls, [f.issue])
        workspace = await orchestrator.ensure_workspace(f.coordinator / "workspaces", f.issue.identifier, {})
        self.assertEqual(workspace.path, f.workspace)
        claimed = self.authority().admission.context(f.polled).ledger
        self.assertEqual(claimed.row_id, ROW)
        self.assertEqual(json.loads(self.claim_file.read_bytes())["ledger"]["original_operation_id"],
                         claimed.original_operation_id)
        popen = subprocess.Popen

        def refuse_supervisor(argv, *args, **kwargs):
            if argv[0] == "/usr/bin/git":
                return popen(argv, *args, **kwargs)
            self.assertTrue(f.path.is_file())
            raise OSError("disposable test stops before supervisor start")

        with patch("uif.dispatch.subprocess.Popen", side_effect=refuse_supervisor):
            with self.assertRaisesRegex(SourceHold, "spawn_requires_reconciliation"):
                await orchestrator.run_turn("codex", SimpleNamespace(model="gpt-6.1-sol", turn_timeout_ms=12000),
                                            SimpleNamespace(), "fixture prompt", f.workspace, f.issue, f.attempt)
        await coordinator._publish_run_report(f.issue, f.attempt, f.workspace, "coding")
        record = self.authority().catalogue()[0]["rows"][ROW]["coding_outcome"]
        self.assertEqual((record["status"], record["operation_id"], record["dispatch_sha256"]),
                         ("uncertain", claimed.original_operation_id, file_digest(f.path)))
        self.assertEqual(await client.fetch_candidate_issues(UIF.project_slug, ["Todo"]), [])

    async def test_combined_hold_or_orphaned_claim_cannot_be_dispatched(self):
        f = self.fixture
        self.entry["counterparts"] = ["another-project"]
        self.set_catalogue()
        with self.assertRaisesRegex(SourceHold, "counterpart_held"):
            self.authority().claim(ROW, f.polled)
        self.assertFalse(self.claim_file.exists())
        self.entry["counterparts"] = []
        self.set_catalogue()
        original = dispatch_fixtures.put(self.claim_file, {"owner": "owner", "ledger": {"original_operation_id": "retained"}})
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            self.authority().claim(ROW, f.polled)
        self.assertEqual(file_digest(self.claim_file), original)

    async def test_operation_locks_isolate_distinct_originals(self):
        authority = self.authority()
        with authority.lock(operation="first"):
            with self.assertRaisesRegex(SourceHold, "operation_busy"):
                with self.authority().lock(operation="first"):
                    self.fail("same original operation obtained a second fence")
            with self.authority().lock(operation="second"):
                self.assertEqual(authority.store_id, "fixture-uif-store")

    async def test_foo_backend_observes_exact_original_windmill_job(self):
        runtime = self.fixture.root / "validation-task"
        runtime.mkdir(mode=0o700)
        f = validation_fixtures.ValidationFixture(runtime)
        f.completed()
        f.client.job["workspace_id"] = "foo"
        authority = self.authority()

        class Transport:
            async def get(self, path, **kwargs):
                self_path.append(path)
                return await f.client.get(path, **kwargs)

        self_path = []
        adapter = authority.validation_adapter(runtime, Transport(), workspace="foo", script_path="u/uif/runner")
        observed = await adapter.backend.inspect(f.spec)
        self.assertEqual(observed.status, "succeeded")
        self.assertEqual(self_path, ["/w/foo/jobs_u/get/" + f.spec.windmill_job_id])
        f.client.job["workspace_id"] = "another"
        self.assertEqual((await adapter.backend.inspect(f.spec)).status, "quarantined")
        with self.assertRaisesRegex(SourceHold, "validation_binding"):
            authority.validation_adapter(runtime, Transport(), workspace="another", script_path="u/uif/runner")

    def publication(self):
        authority = self.authority()
        admission = authority.claim(ROW, self.fixture.polled)
        fixture = publication_fixtures.PublicationFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.request = request = replace(fixture.request, admission=admission, issue=self.fixture.polled)
        readiness = fixture.result()["receipt"]["body"]
        inner = {**request.envelope(observe=False)["request"], "mode": "observe"}
        response = inner_response(inner, "ready")
        readiness["intent"].update(request=inner, request_sha256=fingerprint(inner),
                                   invocation_id=request.readiness_invocation_id)
        readiness["terminal"].update(response=response, response_sha256=fingerprint(response))
        reference = self.ref("original-ready.json", readiness)
        fixture.request = request = replace(request, readiness_receipt_sha256=reference["sha256"])
        fixture.write_reply(fixture.result())
        with authority.lock():
            catalogue, digest = authority.catalogue()
            catalogue["rows"][ROW]["publication_readiness"] = reference
            authority._replace_catalogue(catalogue, digest)
        publisher = replace(fixture.publisher, fence=authority.publication_fence)
        authority.register_publisher(publisher)
        return authority, publisher, fixture, request

    async def test_completion_reconciles_partial_commit_and_qualifies_refill(self):
        authority, publisher, fixture, request = self.publication()
        publication = publisher.consume_boundary(request)
        operation = request.admission.original_operation_id
        self.assertIsNone(authority.read_completion(operation))
        path = self.fixture.runtime / "delivery-completion.json"
        with patch.object(authority, "_replace_catalogue", side_effect=OSError("interrupted after receipt")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                authority.complete_publication(publication)
        receipt_sha = file_digest(path)
        self.assertIsNone(authority.read_completion(operation))

        recovered = self.authority()
        recovered.register_publisher(replace(publisher, fence=recovered.publication_fence))
        completed = recovered.complete_publication(publication)
        self.assertEqual(completed.outcome_receipt_sha256, receipt_sha)
        self.assertEqual(recovered.read_completion(operation), completed)
        self.assertEqual(recovered.complete_publication(publication), completed)
        self.assertEqual(recovered.verify_refill(operation), RowKey(recovered.store_id, ROW))
        self.assertEqual(recovered.catalogue()[0]["refill_from"], operation)
        self.assertEqual(recovered.catalogue()[0]["rows"][ROW]["disposition"], "completed")
        with self.assertRaisesRegex(SourceHold, "refill_original_replay"):
            recovered.admission.ledger.refill(ROW, previous_operation_id=operation)

        successor_id = "src-060-placement-decision"
        runtime = self.fixture.root / "successor"
        runtime.mkdir(mode=0o700)
        source = read_snapshot(SNAPSHOT)[successor_id].source_sha256
        effect = dict(schema="uif-original-effect/1", store_id=recovered.store_id, row_id=successor_id,
                      owner=recovered.owner, epoch=recovered.epoch, source_sha256=source,
                      runtime=str(runtime), lease_ids=[], state="confirmed-absent")
        with recovered.lock():
            catalogue, digest = recovered.catalogue()
            catalogue["rows"][successor_id] = dict(
                runtime=str(runtime), source_sha256=source, phase="implementation", disposition="ready",
                lease_ids=[], effect=self.ref("successor-effect.json", effect), counterparts=[], readiness=[],
            )
            recovered._replace_catalogue(catalogue, digest)
        successor, = recovered.admission.select_ready(previous_operation_id=operation).eligible
        self.assertEqual(successor.key, RowKey(recovered.store_id, successor_id))
        self.assertEqual(file_digest(path), receipt_sha)
        self.assertEqual([call["mode"] for call in fixture.calls()].count("invoke"), 1)

    async def test_completion_holds_without_original_canonical_readback(self):
        authority, publisher, fixture, request = self.publication()
        publication = publisher.consume_boundary(request)
        path = self.fixture.runtime / "delivery-completion.json"
        wrong = replace(publication, accepted_source_sha256="8" * 64)
        with self.assertRaisesRegex(SourceHold, "completion_readback"):
            authority.complete_publication(wrong)
        self.assertFalse(path.exists())
        fixture.write_reply(fixture.result(stopped=False))
        with self.assertRaisesRegex(SourceHold, "terminal_proof"):
            authority.complete_publication(publication)
        self.assertFalse(path.exists())
        fixture.write_reply(fixture.result())
        publication_fixtures.git(fixture.bare, "update-ref", "refs/heads/develop", request.source_base)
        with self.assertRaisesRegex(SourceHold, "remote_readback"):
            authority.complete_publication(publication)
        self.assertFalse(path.exists())
        self.assertNotIn("refill_from", authority.catalogue()[0])
