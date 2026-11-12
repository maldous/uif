"""Exercise guarded dispatch with disposable repositories and a finite supervisor."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from shutil import copyfile
import sqlite3
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from uif.dispatch import DispatchBackend, RuntimePins, read_outcome
from uif.source import SourceHold, file_digest, git
from uif.stokowski import (
    LedgerAdmission, PolledIssue, QualifiedContext,
    SelectedSource, SourceBoundary, UIF, bind_uif_lifecycle,
)


# The fixture does no coding or external work. If a pinned retained
# symphony_capacity.py is supplied, it runs the actual Pool at the same
# boundary. Otherwise write fixture data in that established receipt format.
SUPERVISOR = '''
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

os.umask(0o077)
parser = argparse.ArgumentParser()
parser.add_argument("--runtime", type=Path)
parser.add_argument("--issue")
args = parser.parse_args()
directory = args.runtime / "attempts" / args.issue
assert os.environ.get("UIF_TEST_UNAPPROVED") is None
dispatch = json.loads((directory / "dispatch-intent.json").read_text())
lease, binding = dispatch["lease_id"], dispatch["worker_binding"]
assert dispatch["issue"]["identifier"] == args.issue
assert (args.runtime / "fenced").read_text() == hashlib.sha256(
    (directory / "dispatch-intent.json").read_bytes()).hexdigest()
fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
database = Path(dispatch["pool_database"])
database.parent.mkdir(exist_ok=True)
policy = json.loads((args.runtime / "capacity-policy.json").read_text())
provider = json.loads((args.runtime / "provider-proof.json").read_text())
if Path(__file__).with_name("symphony_capacity.py").is_file():
    from symphony_capacity import Pool
    pool = Pool(database.parent, policy)
    admitted = pool.admit(lease, binding, provider,
                         dict(cpu_millis=4000, available_memory_bytes=8192, available_disk_bytes=8192))
    assert admitted["reused"] is False
else:
    pool = None
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE leases (id TEXT PRIMARY KEY, binding TEXT, fingerprint TEXT, status TEXT, outcome TEXT)")
        db.execute("INSERT INTO leases VALUES(?, ?, ?, 'running', NULL)", (lease, json.dumps(binding), fingerprint))
(directory / "intent.json").write_text(json.dumps(dict(lease=lease, binding=binding)))
if (args.runtime / "simulate-unknown").exists():
    raise SystemExit(0)
if (args.runtime / "wait-for-test").exists():
    until = time.monotonic() + 5
    while not (args.runtime / "release-test").exists():
        assert time.monotonic() < until
        time.sleep(0.01)
(directory / "receipt.json").write_text(json.dumps(dict(
    status="succeeded", child_tree_gone=True, issue=args.issue, lease=lease,
    parent_binding=binding, source_head=binding["head"], binding_sha256=fingerprint,
    parent_runtime=str(args.runtime), attempt=args.issue, attempt_path=str(directory),
    accepted_delivery=False, turn_completed=True, source_identity_changed=False, scope_violations=[],
    provider_route="codex-chatgpt", requested_model="gpt-6.1-sol", requested_effort="medium",
    resolution_status="observed", resolved_model="fixture-observed-model",
    session_id="fixture-session", usage={"input_tokens": 11, "output_tokens": 2})))
if pool:
    pool.finish(lease, True, "succeeded")
else:
    with sqlite3.connect(database) as db:
        db.execute("UPDATE leases SET status='finished', outcome='succeeded' WHERE id=?", (lease,))
'''


@dataclass
class Attempt:
    issue_id: str
    issue_identifier: str
    workspace_path: str
    attempt: int = 4
    turn_count: int = 0


def put(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return file_digest(path)


class DispatchTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.runtime = self.root / "task"
        self.coordinator = self.root / "coordinator"
        self.coordinator.mkdir()
        self.runtime.mkdir(mode=0o700)
        (self.runtime / "requests").mkdir(mode=0o700)
        self.polled = PolledIssue("UIF-900001", "1765ee92-55d6-4012-a889-95e65555711d",
                                  UIF.project_id, UIF.project_slug)
        self.issue = SimpleNamespace(id=self.polled.issue_id, identifier=self.polled.identifier)
        self.workspace = self.runtime / "workspaces" / self.issue.identifier
        self.checkout = self.root / "sealed"
        self.checkout.mkdir()
        self.branch = "feature/uif-dispatch-fixture"
        git(self.checkout, "init", "--initial-branch", self.branch)
        (self.checkout / "source").write_text("disposable source")
        git(self.checkout, "add", "source")
        git(self.checkout, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "fixture")
        self.workspace.parent.mkdir()
        git(self.checkout, "clone", "--no-local", str(self.checkout), str(self.workspace))
        for checkout in (self.checkout, self.workspace):
            checkout.chmod(0o700)
            for path in checkout.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
        head, tree = (git(self.checkout, "rev-parse", rev) for rev in ("HEAD", "HEAD^{tree}"))
        accepted, acquired = self.root / "accepted.json", self.root / "acquired.json"
        self.source = SelectedSource(UIF.repository, head, tree, self.branch, self.checkout,
                                     UIF.accepted_branch, accepted, put(accepted, {"head": head}),
                                     acquired, put(acquired, {"tree": tree}))
        self.admission = LedgerAdmission("original-store", "a" * 64, "original-row", "b" * 64,
                                         "implementation", "owner", "epoch", "original-op",
                                         "ready", "confirmed-absent", False)
        resources = dict(cpu_millis=500, memory_bytes=512, io_slots=1, output_bytes=512)
        self.manifest = dict(version=1, branch=self.branch, base_revision=head,
                             project_id=UIF.project_id, label="fixture", tasks={
                                 self.issue.identifier: dict(issue_id=self.issue.id, owner="owner",
                                                             allowed_paths=["source"], resources=resources,
                                                             purpose="fixture", acceptance=["fixture"], attempt_limit=1)})
        self.request = self.runtime / "requests" / (self.issue.identifier + ".json")
        self.provider = self.runtime / "provider-proof.json"
        self.pool = self.root / "pool"
        self.policy = dict(host=dict(cpu_reserve=0, memory_reserve_bytes=0, disk_reserve_bytes=0),
                           symphony=dict(pool_path=str(self.pool), provider_proof_ttl_seconds=60,
                                         max_concurrent_agents=2, provider_session_ceiling=2,
                                         cpu_budget_millis=2000, memory_budget_bytes=4096, io_slots=2))
        self.provider_pin = put(self.provider, dict(route="codex-chatgpt", available=True,
                                                    checked_at=time.time(), session_ceiling=2))
        self.admission_pin = put(self.runtime / "current-admission.json", dict(
            version=1, owner="owner", issue=self.issue.identifier, issue_id=self.issue.id,
            source=str(self.checkout), manifest_sha256=put(self.runtime / "manifest.json", self.manifest),
            request_sha256=put(self.request, dict(prompt="fixture prompt", timeout_seconds=12)),
            policy_sha256=put(self.runtime / "capacity-policy.json", self.policy),
            installation_sha256=put(self.runtime / "installation.json", {}), inputs={}))
        self.supervisor = self.root / "supervisor.py"
        self.supervisor.write_text(SUPERVISOR)
        self.supervisor.chmod(0o600)
        if path := os.environ.get("UIF_DISPATCH_TEST_CAPACITY"):
            copyfile(path, self.root / "symphony_capacity.py")
        self.boundary = SourceBoundary(
            runtime_root=self.root, coordinator_runtime=self.coordinator,
            qualify_context=lambda _: QualifiedContext(self.runtime, self.manifest, self.checkout, self.admission),
            selected_source=lambda *_: self.source)
        self.fenced = False
        self.recorded = None
        self.registered = None
        self.reported = []
        self.hook_owner = SimpleNamespace()
        register = bind_uif_lifecycle(SimpleNamespace(), SimpleNamespace(), self.hook_owner,
                                     boundary=self.boundary, polled_issue=lambda _: self.polled,
                                     report_attempt=self.report)

        def register_dispatch(issue, attempt, workspace, **kwargs):
            self.assertTrue(self.fenced)
            self.assertEqual(self.recorded, (kwargs["receipt_path"], kwargs["receipt_sha256"]))
            self.registered = register(issue, attempt, workspace, operation_id=kwargs["operation_id"],
                                       receipt_path=kwargs["receipt_path"], receipt_sha256=kwargs["receipt_sha256"],
                                       ledger=kwargs["admission"])
            return self.registered

        self.backend = DispatchBackend(
            boundary=self.boundary, polled_issue=lambda _: self.polled,
            runtime_pins=lambda *_: RuntimePins(self.admission_pin, self.provider_pin),
            dispatch_fence=self.fence, register_dispatch=register_dispatch,
            supervisor=self.supervisor, supervisor_sha256=file_digest(self.supervisor))
        self.attempt = Attempt(self.issue.id, self.issue.identifier, str(self.workspace))
        self.path = self.runtime / "attempts" / self.issue.identifier / "dispatch-intent.json"

    @contextmanager
    def fence(self, issue, admission):
        self.assertEqual((issue, admission), (self.polled, self.admission))
        self.assertFalse(self.fenced)
        self.fenced = True
        try:
            def record(path, digest):
                self.assertTrue(self.fenced)
                self.assertIsNone(self.recorded)
                self.assertEqual(file_digest(path), digest)
                self.recorded = (path, digest)
                (self.runtime / "fenced").write_text(digest)
            yield record
        finally:
            self.fenced = False

    async def report(self, binding):
        self.reported.append(read_outcome(binding))

    async def turn(self, on_pid=None):
        return await self.backend.owned_turn(
            "codex", SimpleNamespace(model="gpt-6.1-sol", turn_timeout_ms=12000),
            SimpleNamespace(), "fixture prompt", self.workspace, self.issue, self.attempt, on_pid=on_pid)

    async def test_supervisor_and_original_attempt_report(self):
        pids = []
        with patch.dict(os.environ, {"UIF_TEST_UNAPPROVED": "no child access"}):
            result = await self.turn(lambda pid, active: pids.append((pid, active)))
        self.assertIs(result, self.attempt)
        self.assertEqual((result.status, result.model, result.total_tokens), ("succeeded", "fixture-observed-model", 13))
        self.assertEqual(pids, [(pids[0][0], True), (pids[0][0], False)])
        intent = json.loads(self.path.read_text())
        self.assertEqual(intent["admission"]["row_id"], "original-row")
        self.assertEqual(intent["admission"]["epoch"], "epoch")
        self.assertEqual(intent["attempt"], 4)
        await self.hook_owner._publish_run_report(self.issue, self.attempt, self.workspace, "coding")
        self.assertEqual(len(self.reported), 1)
        self.assertEqual((self.reported[0].status, self.reported[0].operation_id, self.reported[0].lease_id),
                         ("succeeded", "original-op", intent["lease_id"]))
        self.assertIsNotNone(self.reported[0].worker_receipt_sha256)
        for other in (replace(self.registered, attempt_number=5),
                      replace(self.registered, operation_id="later-op")):
            with self.assertRaisesRegex(SourceHold, "uif_dispatch_report_identity"):
                read_outcome(other)
        receipt = self.reported[0].worker_receipt_path
        worker_result = json.loads(receipt.read_text())
        put(receipt, dict(worker_result, lease="different-lease"))
        self.assertEqual(read_outcome(self.registered).status, "uncertain")
        put(receipt, worker_result)
        with sqlite3.connect(self.pool / "state.sqlite") as db:
            db.execute("UPDATE leases SET status='running', outcome=NULL WHERE id=?", (intent["lease_id"],))
        self.assertEqual(read_outcome(self.registered).status, "uncertain")

    async def test_supervisor_exit_without_receipt_keeps_lease_and_blocks_replay(self):
        (self.runtime / "simulate-unknown").touch()
        result = await self.turn()
        self.assertEqual(result.error, "uncertain")
        original = self.path.read_bytes()
        with sqlite3.connect(self.pool / "state.sqlite") as db:
            self.assertEqual(db.execute("SELECT status FROM leases").fetchall(), [("running",)])
        with self.assertRaisesRegex(SourceHold, "uif_dispatch_original_attempt_retained"):
            await self.turn()
        self.assertEqual(self.path.read_bytes(), original)

    async def test_exception_during_process_creation_retains_intent(self):
        with patch("uif.dispatch.subprocess.Popen", side_effect=OSError("fixture ambiguous launch")) as spawn:
            # Don't patch subprocess globally; Git also uses Popen.
            with patch("uif.dispatch.verify_checkout"):
                with self.assertRaisesRegex(SourceHold, "uif_dispatch_spawn_requires_reconciliation"):
                    await self.turn()
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(read_outcome(self.registered).status, "uncertain")
                with self.assertRaisesRegex(SourceHold, "uif_dispatch_original_attempt_retained"):
                    await self.turn()
                self.assertEqual(spawn.call_count, 1)

    async def test_guard_refuses_stale_or_unready_inputs(self):
        (self.workspace / "unreconciled").touch()
        (self.workspace / "unreconciled").chmod(0o600)
        with self.assertRaisesRegex(SourceHold, "workspace_requires_reconciliation"):
            await self.turn()
        (self.workspace / "unreconciled").unlink()
        self.admission = replace(self.admission, combined=True)
        with self.assertRaisesRegex(SourceHold, "uif_combined_counterpart_held"):
            await self.turn()
        self.admission = replace(self.admission, combined=False)
        self.provider_pin = put(self.provider, dict(route="codex-chatgpt", available=True,
                                                    checked_at=time.time()-300, session_ceiling=2))
        with self.assertRaisesRegex(SourceHold, "uif_dispatch_provider_unavailable"):
            await self.turn()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.pool.exists())

    async def test_owner_fence_rechecks_binding_before_intent(self):
        fence = self.backend.dispatch_fence

        @contextmanager
        def changed_fence(issue, admission):
            with fence(issue, admission) as record:
                self.admission = replace(self.admission, epoch="new-owner-epoch")
                yield record

        self.backend.dispatch_fence = changed_fence
        with self.assertRaisesRegex(SourceHold, "uif_dispatch_claim_drift"):
            await self.turn()
        self.assertFalse(self.path.exists())

    async def test_authority_change_after_registration_holds_original_intent(self):
        register = self.backend.register_dispatch

        def register_then_cancel(*args, **kwargs):
            result = register(*args, **kwargs)
            (self.coordinator / "cancel.json").touch()
            return result

        self.backend.register_dispatch = register_then_cancel
        with self.assertRaisesRegex(SourceHold, "uif_dispatch_prelaunch_drift"):
            await self.turn()
        self.assertTrue(self.path.is_file())
        self.assertFalse(self.pool.exists())

    async def test_cancellation_does_not_kill_supervisor_or_release_lease(self):
        (self.runtime / "wait-for-test").touch()
        stopped = asyncio.Event()
        task = asyncio.create_task(self.turn(lambda pid, active: None if active else stopped.set()))
        worker_intent = self.path.parent / "intent.json"
        async def started():
            while not worker_intent.exists() and not task.done():
                await asyncio.sleep(0.01)
        try:
            await asyncio.wait_for(started(), timeout=4)
            self.assertTrue(worker_intent.exists())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(stopped.is_set())
            self.assertEqual(read_outcome(self.registered).status, "uncertain")
        finally:
            (self.runtime / "release-test").touch()
            await asyncio.wait_for(stopped.wait(), timeout=4)
        self.assertEqual(read_outcome(self.registered).status, "succeeded")
