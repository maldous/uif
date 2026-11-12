import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from uif.ledger import SNAPSHOT_SHA256, read_snapshot
from uif.source import SourceHold, git
from uif.stokowski import DispatchReportBinding, LedgerAdmission, PolledIssue, UIF
from uif.task_identity import LedgerTaskEnvelope, LedgerTaskKey
from uif.validation import (
    StageBinding, StageJournal, ValidationAdapter, ValidationSpec, WindmillValidationBackend,
    decode, sha, wire,
)


class Windmill:
    def __init__(self):
        self.job = None
        self.calls = []
        self.drop_ack = False

    async def get(self, path, *, follow_redirects):
        self.calls.append(("get", path))
        return self.response(404 if self.job is None else 200, self.job)

    async def post(self, path, *, params, json, follow_redirects):
        self.calls.append(("post", path, params, json))
        if self.drop_ack:
            raise TimeoutError()
        return self.response(200, params["job_id"])

    @staticmethod
    def response(status, body):
        def check():
            if status >= 400:
                raise OSError(status)

        return SimpleNamespace(status_code=status, content=wire(body), text=wire(body).decode(),
                               raise_for_status=check)


class ValidationFixture:
    def __init__(self, root):
        self.root = root
        self.candidate = root / "candidate"
        self.candidate.mkdir()
        git(self.candidate, "init", "-q", "-b", "feature/test")
        (self.candidate / "value").write_text("one\n")
        git(self.candidate, "add", "value")
        git(self.candidate, "-c", "user.name=UIF Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "fixture")
        dispatch = root / "dispatch.json"
        dispatch.write_bytes(wire({"original": "operation-1"}))
        self.owner = "author"
        self.author_binding = {"owner": self.owner}
        self.files = {"value": sha(b"one\n")}
        head = git(self.candidate, "rev-parse", "HEAD")
        self.coding = root / "coding.json"
        self.coding.write_bytes(wire(dict(origin="native-coding", status="succeeded", child_tree_gone=True,
                                         session_id="author-session", parent_binding=self.author_binding,
                                         scope_violations=[], source_head=head,
                                         lease="coding-lease", binding_sha256=sha(wire(self.author_binding)),
                                         output_sha256=self.files)))
        issue = PolledIssue("UIF-1", "a58775d8-f350-4814-8418-35b289a0cdd6", UIF.project_id, UIF.project_slug)
        ledger = LedgerAdmission("ledger", "1" * 64, "row-1", "2" * 64, "implementation", self.owner,
                                 "epoch-1", "operation-1", "ready", "confirmed-absent", False)
        original = DispatchReportBinding(issue, None, root / "workspaces" / issue.identifier,
                                         "operation-1", dispatch, sha(dispatch.read_bytes()), ledger)
        binding = StageBinding(original, self.candidate, head, head,
                               git(self.candidate, "rev-parse", "HEAD^{tree}"), "feature/test",
                               self.coding, sha(self.coding.read_bytes()))
        self.spec = ValidationSpec(binding, "5" * 64, "71ea1d75-57e9-4ce1-a7ba-520cf64736ce", "runner-1",
                                   wire(dict(action="run", job_id="runner-1", command="python -m unittest")),
                                   "3" * 64, "4" * 64, first_submission=True)
        self.epoch = "epoch-1"
        self.journal = StageJournal(root / "operations")
        self.client = Windmill()
        self.backend = WindmillValidationBackend(self.client, workspace="uif", script_path="u/uif/runner")
        self.adapter = ValidationAdapter(self.journal, self.fence, self.backend)

    @asynccontextmanager
    async def fence(self, stage, spec):
        if stage not in ("validation", "review") or self.epoch != spec.binding.dispatch.ledger.epoch:
            raise SourceHold("stale owner")
        yield

    def runner_receipt(self):
        return dict(job_id=self.spec.runner_job_id, status="succeeded", exit_code=0, child_tree_gone=True,
                    reconcile_required=False, runner_sha256=self.spec.runner_sha256,
                    capacity_policy_sha256=self.spec.capacity_policy_sha256,
                    command_sha256=self.spec.command_sha256)

    def completed(self, **updates):
        receipt = dict(self.runner_receipt(), **updates)
        self.client.job = dict(id=self.spec.windmill_job_id, workspace_id="uif", script_path="u/uif/runner",
                               type="CompletedJob", running=False, success=True, args=decode(self.spec.args_json),
                               result=dict(job_id=self.spec.runner_job_id, job_succeeded=True, receipt=receipt))
        return receipt


SNAPSHOT = Path(__file__).resolve().parents[2] / "README.md"


class LedgerValidationFixture(ValidationFixture):
    def __init__(self, root):
        from uif.dispatch import LEDGER_RECEIPT, MODEL
        from uif.stokowski import LEDGER_DISPATCH_INTENT, LEDGER_DISPATCH_INTENT_FIELDS

        super().__init__(root)
        legacy = self.spec.binding
        original = legacy.dispatch
        key = LedgerTaskKey(original.ledger.store_id, "standalone-harness-boundary-plan")
        source_sha256 = read_snapshot(SNAPSHOT)[key.row_id].source_sha256
        operation = "retained:opaque-operation/1"
        task = LedgerTaskEnvelope(key, SNAPSHOT_SHA256, source_sha256, self.owner, self.epoch, operation, SNAPSHOT)
        admission = replace(original.ledger, row_id=key.row_id, snapshot_sha256=SNAPSHOT_SHA256,
                            source_sha256=source_sha256, original_operation_id=operation)
        attempt = root / "attempts" / key.workspace_key
        attempt.mkdir(mode=0o700, parents=True)
        intent = attempt / "dispatch-intent.json"
        workspace = root / "workspaces" / key.workspace_key
        self.author_binding["head"] = legacy.base
        pins = {"admission_sha256": "a" * 64, "provider_sha256": "b" * 64}
        record = dict(schema=LEDGER_DISPATCH_INTENT, task=task.to_record(), admission=asdict(admission),
                      operation_id=operation, attempt=3, workspace=str(workspace), source=str(root / "source"),
                      source_head=legacy.base, source_tree=legacy.tree, source_branch=legacy.branch,
                      acquisition_sha256="c" * 64, accepted_sha256="d" * 64, supervisor_sha256="e" * 64,
                      requested_model=MODEL, requested_effort="medium", pins=pins, manifest_sha256="f" * 64,
                      request_sha256="0" * 64, lease_id="coding-lease", pool_database=str(root / "state.sqlite"),
                      worker_binding=self.author_binding)
        assert set(record) == LEDGER_DISPATCH_INTENT_FIELDS
        intent.write_bytes(wire(record))
        intent_sha256 = sha(intent.read_bytes())
        invocation = "1f24cc8b-36f2-4a8d-a38c-762f554f6777"
        handoff = attempt / "controller-supervisor.json"
        handoff.write_bytes(wire(dict(invocation_id=invocation, operation_id=operation, dispatch_sha256=intent_sha256)))
        (attempt / "intent.json").write_bytes(wire(dict(lease="coding-lease", binding=self.author_binding)))
        coding = attempt / "receipt.json"
        coding.write_bytes(wire(dict(
            decode(self.coding.read_bytes()),
            schema=LEDGER_RECEIPT, task=task.to_record(), workspace_key=key.workspace_key, attempt=3,
            original_operation_id=operation, invocation_id=invocation, dispatch_sha256=intent_sha256,
            admission_sha256=pins["admission_sha256"], handoff_sha256=sha(handoff.read_bytes()),
            parent_binding=self.author_binding, binding_sha256=sha(wire(self.author_binding)),
            parent_runtime=str(root), attempt_path=str(attempt), accepted_delivery=False,
            turn_completed=True, source_identity_changed=False, provider_route="codex-chatgpt",
            requested_model=MODEL, requested_effort="medium",
        )))
        with sqlite3.connect(root / "state.sqlite") as db:
            db.execute("CREATE TABLE leases (id TEXT PRIMARY KEY,binding TEXT,fingerprint TEXT,status TEXT,outcome TEXT)")
            db.execute("INSERT INTO leases VALUES (?, ?, ?, ?, ?)",
                       ("coding-lease", wire(self.author_binding).decode(), sha(wire(self.author_binding)),
                        "finished", "succeeded"))
        self.coding = coding
        self.candidate = root / "contributions" / key.workspace_key / "candidate"
        self.candidate.parent.mkdir(parents=True)
        legacy.candidate.rename(self.candidate)
        dispatch = DispatchReportBinding(None, 3, workspace, operation, intent, intent_sha256, admission, task=task)
        binding = replace(legacy, dispatch=dispatch, candidate=self.candidate, coding_receipt=coding,
                          coding_receipt_sha256=sha(coding.read_bytes()))
        self.spec = replace(self.spec, binding=binding, operation_key=binding.key("validation"))
        backend = WindmillValidationBackend(self.client, workspace="foo", script_path="u/uif/runner")
        self.adapter = ValidationAdapter(self.journal, self.adapter.fence, backend)

    def completed(self, **updates):
        receipt = super().completed(**updates)
        self.client.job["workspace_id"] = "foo"
        return receipt

class LedgerValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.f = LedgerValidationFixture(Path(temporary.name))

    async def test_ledger_validation_keeps_the_original_invocation_and_dispatch(self):
        f = self.f
        f.client.drop_ack = True
        self.assertEqual((await f.adapter.tick(f.spec)).status, "unknown")
        with self.assertRaisesRegex(SourceHold, "operation_rebind"):
            await f.adapter.tick(replace(f.spec, windmill_job_id="276d1a06-a722-432c-91e3-2701f927c4a5"))
        f.completed()
        self.assertEqual((await f.adapter.tick(f.spec)).status, "succeeded")
        proof = await f.adapter.accepted(f.spec)
        binding = proof.binding.dispatch
        pin = decode(f.adapter._pin(f.spec))
        self.assertEqual(pin["schema"], "uif-stage/2")
        self.assertEqual(pin["workspace_key"], binding.task.key.workspace_key)
        self.assertEqual(pin["spec"]["binding"]["dispatch"], binding.to_record())
        self.assertEqual(proof.windmill_job_id, f.spec.windmill_job_id)
        self.assertEqual(binding.operation_id, "retained:opaque-operation/1")
        self.assertEqual([entry[0] for entry in f.client.calls], ["get", "post", "get"])

    async def test_retained_runner_receipt_holds_without_replay_or_acceptance(self):
        f = self.f
        receipts = f.root / "runner"
        directory = receipts / f.spec.runner_job_id
        directory.mkdir(parents=True)
        (directory / "receipt.json").write_bytes(wire(f.runner_receipt()))
        backend = WindmillValidationBackend(f.client, workspace="foo", script_path="u/uif/runner",
                                            runner_receipts=receipts, runner_uid=directory.stat().st_uid)
        adapter = ValidationAdapter(f.journal, f.adapter.fence, backend)
        self.assertEqual((await adapter.tick(f.spec)).status, "unknown")
        with self.assertRaisesRegex(SourceHold, "acceptance_pending"):
            await adapter.accepted(f.spec)
        self.assertEqual([entry[0] for entry in f.client.calls], ["get"])

    async def test_failed_windmill_wrapper_cannot_qualify_a_successful_runner_receipt(self):
        f = self.f
        f.completed()
        f.client.job["success"] = False
        self.assertEqual((await f.adapter.tick(f.spec)).status, "unknown")
        self.assertEqual([entry[0] for entry in f.client.calls], ["get"])

    async def test_stale_owner_or_different_coding_receipt_prevents_validation(self):
        f = self.f
        f.epoch = "stale"
        with self.assertRaisesRegex(SourceHold, "stale owner"):
            await f.adapter.tick(f.spec)
        f.epoch = f.spec.binding.task.epoch
        f.coding.write_bytes(f.coding.read_bytes() + b"\n")
        with self.assertRaisesRegex(SourceHold, "original_coding"):
            await f.adapter.tick(f.spec)
        self.assertEqual(f.client.calls, [])


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.f = ValidationFixture(Path(temporary.name))

    async def test_lost_ack_recovers_only_the_original_job_after_restart(self):
        f = self.f
        f.client.drop_ack = True
        self.assertEqual((await f.adapter.tick(f.spec)).status, "unknown")
        restored = ValidationAdapter(StageJournal(f.journal.root), f.adapter.fence, f.backend)
        self.assertEqual((await restored.tick(f.spec)).status, "unknown")
        receipt = f.completed()
        self.assertEqual((await restored.tick(f.spec)).status, "succeeded")
        proof = await restored.accepted(f.spec)
        self.assertEqual((proof.windmill_job_id, proof.runner_job_id), (f.spec.windmill_job_id, f.spec.runner_job_id))
        self.assertEqual(proof.operation_key, f.spec.operation_key)
        self.assertEqual(proof.receipt, wire(receipt))
        self.assertEqual(proof.receipt_sha256, sha(wire(receipt)))
        self.assertEqual([call[0] for call in f.client.calls], ["get", "post", "get", "get"])
        self.assertTrue(all(call[1].endswith(f.spec.windmill_job_id) for call in f.client.calls if call[0] == "get"))

    def test_issue_journal_keeps_its_original_serialization(self):
        f = self.f
        original = json.dumps(dict(stage="validation", spec=asdict(f.spec), backend=f.adapter.backend.registration),
                              sort_keys=True, allow_nan=False,
                              default=lambda value: value.decode() if isinstance(value, bytes) else str(value))
        self.assertEqual(f.adapter._pin(f.spec), original)

    async def test_retained_missing_job_does_not_submit(self):
        f = self.f
        spec = replace(f.spec, first_submission=False)
        self.assertEqual((await f.adapter.tick(spec)).status, "unknown")
        self.assertEqual((await f.adapter.tick(spec)).status, "unknown")
        self.assertTrue(all(call[0] == "get" for call in f.client.calls))

    async def test_original_runner_receipt_reconciles_missing_wrapper(self):
        f = self.f
        receipt_dir = f.root / "runner" / f.spec.runner_job_id
        receipt_dir.mkdir(parents=True)
        original = wire(f.runner_receipt()) + b"\n"
        (receipt_dir / "receipt.json").write_bytes(original)
        backend = WindmillValidationBackend(f.client, workspace="uif", script_path="u/uif/runner",
                                            runner_receipts=receipt_dir.parent, runner_uid=receipt_dir.stat().st_uid)
        adapter = ValidationAdapter(f.journal, f.adapter.fence, backend)
        self.assertEqual((await adapter.tick(f.spec)).status, "succeeded")
        self.assertEqual((await adapter.accepted(f.spec)).receipt, original)
        self.assertEqual(len(f.client.calls), 1)

    async def test_mismatched_runner_pin_quarantines_original_operation(self):
        f = self.f
        f.completed(runner_sha256="9" * 64)
        self.assertEqual((await f.adapter.tick(f.spec)).status, "quarantined")
        with self.assertRaisesRegex(SourceHold, "acceptance_pending"):
            await f.adapter.accepted(f.spec)

    async def test_original_job_cannot_be_rebound_even_before_completion(self):
        f = self.f
        self.assertEqual((await f.adapter.tick(f.spec)).status, "submitted")
        with self.assertRaisesRegex(SourceHold, "rebind"):
            await f.adapter.tick(replace(f.spec, windmill_job_id="fb53db20-24a9-4b22-a5d1-5a079dc218a6"))

    async def test_fence_and_source_are_checked_before_publication_evidence(self):
        f = self.f
        f.completed()
        await f.adapter.tick(f.spec)
        f.epoch = "epoch-2"
        with self.assertRaisesRegex(SourceHold, "stale owner"):
            await f.adapter.accepted(f.spec)
        f.epoch = "epoch-1"
        (f.candidate / "value").write_text("changed\n")
        with self.assertRaisesRegex(SourceHold, "reconciliation"):
            await f.adapter.accepted(f.spec)

    async def test_busy_operation_holds_without_backend_effect(self):
        f = self.f
        with f.journal.lock(f.spec.binding.key("validation")):
            with self.assertRaisesRegex(SourceHold, "already_owned"):
                await f.adapter.tick(f.spec)
        self.assertEqual(f.client.calls, [])

    async def test_cancelled_submission_preserves_intent(self):
        f = self.f

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        f.client.post = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await f.adapter.tick(f.spec)
        self.assertEqual((await f.adapter.tick(f.spec)).status, "unknown")
        self.assertEqual([call[0] for call in f.client.calls], ["get", "get"])

    async def test_compact_observation_requires_registered_protocol_and_argument_pin(self):
        f = self.f
        f.completed()
        f.client.job.pop("args")
        f.client.job.update(inspect_version=1, args_sha256="9" * 64)
        backend = WindmillValidationBackend(f.client, workspace="uif", script_path="u/uif/runner",
                                            inspect_version=1)
        adapter = ValidationAdapter(f.journal, f.adapter.fence, backend)
        self.assertEqual((await adapter.tick(f.spec)).status, "quarantined")
        self.assertEqual(len(f.client.calls), 1)

    async def test_output_digest_must_match_exact_candidate(self):
        f = self.f
        receipt = decode(f.coding.read_bytes())
        receipt["output_sha256"] = {"value": "e" * 64}
        f.coding.write_bytes(wire(receipt))
        spec = replace(f.spec, binding=replace(f.spec.binding, coding_receipt_sha256=sha(wire(receipt))))
        with self.assertRaisesRegex(SourceHold, "coding_output_binding"):
            await f.adapter.tick(spec)
        self.assertEqual(f.client.calls, [])
