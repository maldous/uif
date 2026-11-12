"""Exercise the persisted owner authority at the coordinator's real hook boundaries."""

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID

import test_dispatch as dispatch_fixtures
import test_validation as validation_fixtures
import test_publication as publication_fixtures
from uif.admission import _fingerprint
from uif.ledger import RowKey, SNAPSHOT_SHA256, read_snapshot
from uif.native_publisher import inner_response
from uif.publication import AcceptedPublication, PublicationRequest, fingerprint
from uif.owner_runtime import LEDGER_PUBLICATION_REQUEST, OwnerAuthority, bind_owner_coordinator
from uif.source import SourceHold, file_digest
from uif.stokowski import DispatchReportBinding, LEDGER_DISPATCH_INTENT, LEDGER_DISPATCH_REPORT, UIF, Workspace
from uif.task_identity import LedgerTaskEnvelope, LedgerTaskKey


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

    def prepare_ledger_inputs(self, *, state="confirmed-absent", operation=None, **request_fields):
        f = self.fixture
        key = LedgerTaskKey("fixture-uif-store", ROW)
        target = dict(store_id=key.store_id, row_id=key.row_id,
                      snapshot_sha256=SNAPSHOT_SHA256, source_sha256=self.entry["source_sha256"])
        task = {name: value for name, value in f.manifest["tasks"][f.issue.identifier].items()
                if name != "issue_id"}
        manifest = {**f.manifest, "tasks": {key.workspace_key: {**task, "ledger_task": target}}}
        inputs = json.loads(Path(self.entry["admission_inputs"]["path"]).read_bytes())
        inputs["manifest_sha256"] = dispatch_fixtures.put(f.runtime / "manifest.json", manifest)
        inputs["request_sha256"] = dispatch_fixtures.put(
            f.runtime / "requests" / (key.workspace_key + ".json"),
            dict(prompt="fixture prompt", timeout_seconds=12, ledger_task=target, **request_fields))
        lease = "rt-" + _fingerprint(dict(runtime=str(f.runtime), manifest_sha256=_fingerprint(manifest),
                                          ledger_task=key.workspace_key))
        effect = json.loads(Path(self.entry["effect"]["path"]).read_bytes())
        effect.update(lease_ids=[lease], state=state, operation_id=operation)
        self.entry.update(issue=None, lease_ids=[lease], effect=self.ref("effect.json", effect),
                          admission_inputs=self.ref("admission-inputs.json", inputs))
        self.set_catalogue()
        return key

    def test_selected_ledger_intake_claims_once_and_acquires_exact_workspace(self):
        f = self.fixture
        key = self.prepare_ledger_inputs()
        owner, orchestrator = SimpleNamespace(), SimpleNamespace()
        intake = bind_owner_coordinator(self.authority(), orchestrator, None, owner, intake="ledger")
        selected, = intake.select_ready().eligible
        self.assertEqual(selected.key, RowKey(key.store_id, key.row_id))
        self.assertIsNone(selected.issue)
        self.assertIsNone(selected.operation_id)
        self.assertFalse(self.claim_file.exists())
        task = intake.claim(key)
        self.assertIsInstance(task, LedgerTaskEnvelope)
        self.assertEqual(task.key, key)
        self.assertEqual(str(UUID(task.original_operation_id)), task.original_operation_id)
        claim = json.loads(self.claim_file.read_bytes())
        self.assertEqual((claim["version"], claim["ledger"]), (2, task.to_record()))
        self.assertFalse({"issue", "issue_id"} & claim.keys())
        authority = self.authority()
        self.assertEqual(authority.admission.ledger_context(task).ledger.original_operation_id,
                         task.original_operation_id)
        self.assertFalse(authority.select_ledger_tasks().eligible)
        with self.assertRaisesRegex(SourceHold, "pickup_held"):
            authority.claim_ledger_task(key)
        self.assertEqual(json.loads(self.claim_file.read_bytes()), claim)
        forged = LedgerTaskEnvelope.from_record(
            {**task.to_record(), "original_operation_id": "invented"}, snapshot_path=SNAPSHOT)
        lock_paths = set(authority.operation_locks_path.iterdir())
        with self.assertRaisesRegex(SourceHold, "task_claim_drift"):
            authority.ensure_ledger_workspace(forged, f.coordinator / "workspaces")
        self.assertEqual(set(authority.operation_locks_path.iterdir()), lock_paths)
        with authority.lock(operation=task.original_operation_id):
            with self.assertRaisesRegex(SourceHold, "operation_busy"):
                authority.ensure_ledger_workspace(task, f.coordinator / "workspaces")
        workspace = intake.ensure_workspace(task, f.coordinator / "workspaces")
        self.assertEqual((workspace.identifier, workspace.path),
                         (key.workspace_key, f.runtime / "workspaces" / key.workspace_key))
        self.assertEqual((workspace.path / "source").read_text(), "disposable source")
        self.assertFalse(authority.ensure_ledger_workspace(task, f.coordinator / "workspaces").created)
        self.assertEqual(vars(orchestrator), {})

    def test_ledger_intake_preserves_reconciled_operation(self):
        key = self.prepare_ledger_inputs(state="reconciled-for-pickup", operation="retained-original")
        with patch("uif.owner_runtime.uuid4", side_effect=AssertionError("original must be reused")):
            self.assertEqual(self.authority().claim_ledger_task(key).original_operation_id, "retained-original")

    async def test_ledger_dispatch_carrier_and_original_intent_fence(self):
        f = self.fixture
        key = self.prepare_ledger_inputs(state="reconciled-for-pickup", operation="opaque-original")
        authority = self.authority()
        task = authority.claim_ledger_task(key)
        admission = authority.admission.ledger_context(task).ledger
        legacy = DispatchReportBinding(f.polled, 3, f.workspace, "legacy-original", f.path, "0" * 64)
        self.assertIsNone(legacy.task)
        self.assertEqual(tuple(asdict(legacy)), ("issue", "attempt_number", "workspace",
                                                "operation_id", "receipt_path", "receipt_sha256", "ledger"))
        pins = authority.admission.runtime_pins(None, admission, task=task)
        claim = json.loads(self.claim_file.read_bytes())
        workspace = f.runtime / "workspaces" / key.workspace_key
        receipt = f.runtime / "attempts" / key.workspace_key / "dispatch-intent.json"
        record = dict(schema=LEDGER_DISPATCH_INTENT, task=task.to_record(), admission=asdict(admission),
                      operation_id=task.original_operation_id, attempt=3, workspace=str(workspace),
                      source=f.source.repository, source_head=f.source.head, source_tree=f.source.tree,
                      source_branch=f.source.branch, acquisition_sha256=f.source.acquisition_sha256,
                      accepted_sha256=f.source.accepted_sha256, supervisor_sha256=file_digest(f.supervisor),
                      requested_model="gpt-6.1-sol", requested_effort="medium", pins=asdict(pins),
                      manifest_sha256=claim["manifest_sha256"], request_sha256=claim["request_sha256"],
                      lease_id=self.entry["lease_ids"][0], pool_database=str(authority.pool_database_path),
                      worker_binding={})
        with authority.admission.dispatch_fence(None, admission, task=task) as register:
            receipt.parent.mkdir(parents=True, mode=0o700)
            wrong_digest = dispatch_fixtures.put(receipt, {**record, "issue": None})
            with self.assertRaisesRegex(SourceHold, "intent_drift"):
                register(receipt, wrong_digest)
            for field in ("source_head", "source", "source_tree", "source_branch",
                          "acquisition_sha256", "accepted_sha256"):
                wrong_digest = dispatch_fixtures.put(receipt, {**record, field: "0" * len(record[field])})
                with self.assertRaisesRegex(SourceHold, "intent_drift"):
                    register(receipt, wrong_digest)
                with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
                    authority.admission.ledger.verify_dispatch(
                        admission, None, operation_id=task.original_operation_id,
                        receipt_path=receipt, receipt_sha256=wrong_digest, task=task)
            digest = dispatch_fixtures.put(receipt, record)
            register(receipt, digest)
            authority.admission.ledger.verify_dispatch(
                admission, None, operation_id=task.original_operation_id,
                receipt_path=receipt, receipt_sha256=digest, task=task)
            binding = DispatchReportBinding(None, 3, workspace, task.original_operation_id,
                                            receipt, digest, admission, task=task)
            wire = binding.to_record()
            self.assertEqual((wire["schema"], wire["task"], wire["operation_id"]),
                             (LEDGER_DISPATCH_REPORT, task.to_record(), "opaque-original"))
            self.assertNotIn("issue", wire)
            self.assertEqual(DispatchReportBinding.from_ledger_record(wire, snapshot_path=SNAPSHOT).task, task)
            with self.assertRaisesRegex(SourceHold, "report_shape"):
                DispatchReportBinding.from_ledger_record({**wire, "issue": None}, snapshot_path=SNAPSHOT)
        authority = self.authority()
        self.assertEqual(authority.catalogue()[0]["rows"][ROW]["dispatch_intent"],
                         {"path": str(receipt), "sha256": digest})
        with sqlite3.connect(authority.pool_database_path) as db:
            db.execute("INSERT INTO leases VALUES (?, ?, ?, ?, ?)",
                       (record["lease_id"], "{}", "fixture", "finished", "succeeded"))
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            authority.admission.ledger_context(task)
        async with authority.stage_fence("validation", SimpleNamespace(binding=SimpleNamespace(dispatch=binding))):
            with self.assertRaisesRegex(SourceHold, "operation_busy"):
                with self.authority().lock(operation=task.original_operation_id):
                    self.fail("stage released the original operation lock")
        other_runtime = f.runtime.parent / "other-runtime"
        for forged, error in (
            (replace(binding, receipt_sha256="9" * 64, task=task), "dispatch_intent_drift"),
            (replace(binding, ledger=replace(admission, prior_effect="confirmed-absent"), task=task),
             "dispatch_intent_drift"),
            (replace(binding, workspace=other_runtime / "workspaces" / key.workspace_key,
                     receipt_path=other_runtime / "attempts" / key.workspace_key / receipt.name, task=task),
             "stage_workspace"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(SourceHold, error):
                async with authority.stage_fence(
                        "validation", SimpleNamespace(binding=SimpleNamespace(dispatch=forged))):
                    self.fail("unexpected stage admission")

    def test_ledger_report_requires_exact_path_spelling(self):
        key = self.prepare_ledger_inputs()
        authority = self.authority()
        task = authority.claim_ledger_task(key)
        admission = authority.admission.ledger_context(task).ledger
        runtime = self.fixture.runtime
        workspace = runtime / "workspaces" / key.workspace_key
        receipt = runtime / "attempts" / key.workspace_key / "dispatch-intent.json"
        wire = DispatchReportBinding(None, 3, workspace, task.original_operation_id, receipt,
                                     "1" * 64, admission, task=task).to_record()
        self.assertEqual(DispatchReportBinding.from_ledger_record(wire, snapshot_path=SNAPSHOT).to_record(), wire)
        for field, segment in (("receipt_path", "/attempts/"), ("workspace", "/workspaces/")):
            for spelling in (segment + "/", segment + "./"):
                with self.subTest(field=field, spelling=spelling):
                    alias = {**wire, field: wire[field].replace(segment, spelling)}
                    with self.assertRaisesRegex(SourceHold, "report_shape"):
                        DispatchReportBinding.from_ledger_record(alias, snapshot_path=SNAPSHOT)

    async def test_ledger_stage_requires_registered_dispatch_receipt(self):
        key = self.prepare_ledger_inputs()
        authority = self.authority()
        task = authority.claim_ledger_task(key)
        admission = authority.admission.ledger_context(task).ledger
        runtime = self.fixture.runtime
        workspace = runtime / "workspaces" / key.workspace_key
        receipt = runtime / "attempts" / key.workspace_key / "dispatch-intent.json"
        dispatch = DispatchReportBinding(None, 3, workspace, task.original_operation_id, receipt,
                                         "1" * 64, admission, task=task)
        spec = SimpleNamespace(binding=SimpleNamespace(dispatch=dispatch), policy=runtime / "capacity-policy.json",
                               policy_sha256="2" * 64)
        selection = SimpleNamespace(spec=spec, installation_sha256="3" * 64,
                                    capacity_policy_sha256="4" * 64, provider_proof_sha256="5" * 64)
        with self.subTest("validation"):
            with self.assertRaisesRegex(SourceHold, "dispatch_intent_drift"):
                async with authority.stage_fence("validation", spec):
                    pass
        with self.subTest("review"):
            record = {name: value for name, value in vars(selection).items() if name != "spec"}
            record["spec"] = {}
            with (patch.object(authority, "ledger_review_record", return_value=record),
                  patch("uif.owner_runtime.review_spec_record", return_value={})):
                with self.assertRaisesRegex(SourceHold, "dispatch_intent_drift"):
                    with authority.review_fence(selection):
                        pass

    def test_ledger_intake_preserves_original_after_incomplete_catalogue_write(self):
        key = self.prepare_ledger_inputs()
        authority = self.authority()
        with patch.object(authority, "_replace_catalogue", side_effect=SourceHold("fixture interrupted write")):
            with self.assertRaisesRegex(SourceHold, "interrupted write"):
                authority.claim_ledger_task(key)
        original = self.claim_file.read_bytes()
        self.assertTrue(json.loads(original)["ledger"]["original_operation_id"])
        self.assertIn("requires_reconciliation", self.authority().select_ledger_tasks().held[ROW])
        with patch("uif.owner_runtime.uuid4", side_effect=AssertionError("must not allocate again")):
            with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
                self.authority().claim_ledger_task(key)
        self.assertEqual(self.claim_file.read_bytes(), original)

    def test_ledger_completion_recovers_exact_task_with_registered_snapshot(self):
        @dataclass(frozen=True)
        class LedgerRequestFixture:
            admission: object
            task: LedgerTaskEnvelope
            issue: None = None

            @property
            def key(self):
                return RowKey(self.task.key.store_id, self.task.key.row_id)

            def to_record(self):
                return dict(schema=LEDGER_PUBLICATION_REQUEST, admission=asdict(self.admission),
                            task=self.task.to_record())

        key = self.prepare_ledger_inputs()
        authority = self.authority()
        task = authority.claim_ledger_task(key)
        request = LedgerRequestFixture(authority.admission.ledger_context(task).ledger, task)
        publication = AcceptedPublication(request, "1" * 40, "2" * 40, "fixture", "3" * 64, "4" * 64)
        record = authority._completion_record(publication)
        self.assertEqual(record["schema"], "uif-owner-delivery-completion/2")
        self.assertEqual(record["publication"]["request"], request.to_record())
        path = self.fixture.runtime / "delivery-completion.json"
        digest = dispatch_fixtures.put(path, record)
        with authority.lock():
            catalogue, pin = authority.catalogue()
            catalogue["rows"][ROW].update(
                disposition="completed", publication_intent=request.to_record(),
                completion=dict(operation_id=task.original_operation_id, receipt=dict(path=str(path), sha256=digest)))
            authority._replace_catalogue(catalogue, pin)
        with patch.object(PublicationRequest, "from_ledger_record", create=True, return_value=request) as decode:
            with authority.publication_fence(request, "recovery") as transaction:
                self.assertEqual(transaction.read_intent(request.key, task.original_operation_id), request)
            recovered = authority.read_completion(task.original_operation_id)
        self.assertEqual(recovered.publication, publication)
        self.assertEqual(decode.call_count, 2)
        for call in decode.call_args_list:
            self.assertEqual(call.args, (request.to_record(),))
            self.assertEqual(call.kwargs, {"snapshot_path": authority.snapshot_path})

    def test_ledger_review_record_keeps_exact_task_and_original_operation(self):
        key = self.prepare_ledger_inputs()
        authority = self.authority()
        task = authority.claim_ledger_task(key)
        runtime = self.fixture.runtime
        policy = runtime / "capacity-policy.json"
        record = dict(schema="uif-owner-review/2", store_id=key.store_id, row_id=key.row_id,
                      operation_id=task.original_operation_id, owner=task.owner, epoch=task.epoch,
                      runtime=str(runtime), task=task.to_record(), policy_path=str(policy),
                      policy_sha256=file_digest(policy), spec={}, installation_sha256="1" * 64,
                      capacity_policy_sha256="2" * 64, provider_proof_sha256="3" * 64)
        with authority.lock(operation=task.original_operation_id), authority.lock():
            catalogue, pin = authority.catalogue()
            catalogue["rows"][ROW]["review"] = self.ref("ledger-review.json", record)
            authority._replace_catalogue(catalogue, pin)
            self.assertEqual(authority.ledger_review_record(
                task, runtime=runtime, policy=policy, policy_sha256=record["policy_sha256"]), record)
            forged = LedgerTaskEnvelope.from_record(
                {**task.to_record(), "original_operation_id": "wrong"}, snapshot_path=SNAPSHOT)
            with self.assertRaisesRegex(SourceHold, "review_binding"):
                authority.ledger_review_record(forged, runtime=runtime, policy=policy,
                                               policy_sha256=record["policy_sha256"])

    def test_ledger_intake_holds_unknown_effect_and_shared_row(self):
        key = self.prepare_ledger_inputs(state="unknown")
        authority = self.authority()
        self.assertIn("requires_reconciliation", authority.select_ledger_tasks().held[ROW])
        with self.assertRaisesRegex(SourceHold, "requires_reconciliation"):
            authority.claim_ledger_task(key)
        key = self.prepare_ledger_inputs()
        self.entry["disposition"] = "held"
        self.set_catalogue()
        self.assertIn("pickup_held", authority.select_ledger_tasks().held[ROW])
        with self.assertRaisesRegex(SourceHold, "pickup_held"):
            authority.claim_ledger_task(key)
        self.entry["disposition"] = "ready"
        self.entry["counterparts"] = ["another-project"]
        self.set_catalogue()
        self.assertIn("counterpart_held", authority.select_ledger_tasks().held[ROW])
        with self.assertRaisesRegex(SourceHold, "counterpart_held"):
            authority.claim_ledger_task(key)
        self.assertFalse(self.claim_file.exists())

    def test_ledger_intake_does_not_bypass_registered_refill(self):
        key = self.prepare_ledger_inputs()
        catalogue = json.loads(self.catalogue.read_bytes())
        catalogue["refill_from"] = "unverified-predecessor"
        dispatch_fixtures.put(self.catalogue, catalogue)
        authority = self.authority()
        with self.assertRaisesRegex(SourceHold, "completion_unregistered"):
            authority.select_ledger_tasks()
        with self.assertRaisesRegex(SourceHold, "completion_unregistered"):
            authority.claim_ledger_task(key)
        self.assertFalse(self.claim_file.exists())

    def test_ledger_claim_rejects_issue_association_and_fabricated_issue_input(self):
        authority = self.authority()
        key = LedgerTaskKey(authority.store_id, ROW)
        self.assertIn("intake_held", authority.select_ledger_tasks().held[ROW])
        with self.assertRaisesRegex(SourceHold, "effect_held"):
            authority.claim_ledger_task(key)
        self.prepare_ledger_inputs(issue_id="invented")
        with self.assertRaisesRegex(SourceHold, "task_input_binding"):
            authority.claim_ledger_task(key)
        self.assertFalse(self.claim_file.exists())

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
