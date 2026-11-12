"""Exercise the UIF supervisor with finite local child processes and receipts."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch

from uif.dispatch import MODEL, SCHEMA, _digest, _lease_id, read_outcome
from uif.source import SourceHold, file_digest, git
from uif.stokowski import DispatchReportBinding, LedgerAdmission, PolledIssue, UIF
from uif.task_identity import LedgerTaskKey
from uif import role_broker, supervisor
import test_guard_dropper as guard_fixtures


class TypedSupervisorBindingTests(TestCase):
    def test_owner_pinned_typed_binding_shares_ordinary_authority_and_refuses_drift(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        runtime, pool, coordinator = (root / name for name in ("row", "pool", "coordinator"))
        for directory in (runtime, pool, coordinator):
            directory.mkdir()

        def write(path, value):
            path.write_text(json.dumps(value))
            path.chmod(0o600)
            return dict(path=str(path), sha256=file_digest(path))

        authority = SimpleNamespace(runtime_root_path=root, coordinator_runtime_path=coordinator,
                                    pool_database_path=pool / "state.sqlite", operation_locks_path=root / "locks")
        role_config = dict(
            schema=role_broker.SCHEMA, attempt_isolation=role_broker.ATTEMPT_ISOLATION,
            controller_uid=os.getuid(), controller_gid=os.getegid(), controller_root=str(root),
            pool_database=str(authority.pool_database_path), operation_locks=str(authority.operation_locks_path),
            roles={name: {} for name in ("coding", "review", "neutral")},
        )
        roles = write(root / "roles.json", role_config)
        typed_roles = write(root / "typed-roles.json", dict(role_config, roles={
            **role_config["roles"], "coding": {"guard": {"sha256": "c" * 64}},
        }))
        ordinary_path, typed_path = root / "ordinary.json", root / "typed.json"
        ordinary_helper = dict(path=str(root / "neutral-helper"), sha256="a" * 64)
        typed_helper = dict(path=str(root / "typed-helper"), sha256="b" * 64)
        ordinary = dict(schema="uif-coding-supervisor/2", uid=os.getuid(), runtime_root=str(root),
                        coordinator_runtime=str(coordinator), pool_root=str(pool), role_policy=roles,
                        capacity=write(root / "capacity.py", {}),
                        owner_registration=write(root / "owner.json", {}), role_helper=ordinary_helper)
        if os.getuid() == 0:
            self.skipTest("requires a non-root supervisor fixture")
        ordinary_pin = write(ordinary_path, ordinary)["sha256"]
        typed = dict(ordinary, role_helper=typed_helper, role_policy=typed_roles)
        typed_pin = write(typed_path, typed)["sha256"]
        with (patch.object(supervisor, "POLICY", ordinary_path), patch.object(supervisor, "TYPED_POLICY", typed_path),
              patch.object(supervisor, "TYPED_HELPER", Path(typed_helper["path"])),
              patch.object(role_broker, "POLICY", Path(roles["path"])),
              patch.object(role_broker, "TYPED_POLICY", Path(typed_roles["path"])),
              patch.object(supervisor, "_trusted", side_effect=Path),
              patch("uif.owner_runtime.OwnerAuthority.load", return_value=authority),
              patch.object(role_broker, "RoleBroker") as broker):
            supervisor._installed(runtime, {"supervisor_policy_sha256": ordinary_pin})
            broker.assert_called_once_with(ordinary_helper["path"], ordinary_helper["sha256"])
            broker.reset_mock()
            supervisor._installed(runtime, {"supervisor_policy_sha256": typed_pin}, typed=True)
            broker.assert_called_once_with(typed_helper["path"], typed_helper["sha256"])
            broker.reset_mock()
            with self.assertRaisesRegex(SourceHold, "supervisor_policy_pin"):
                supervisor._installed(runtime, {"supervisor_policy_sha256": ordinary_pin}, typed=True)
            for change, reason in (({"pool_root": str(root / "other")}, "shared_binding"),
                                   ({"owner_registration": ordinary["capacity"]}, "shared_binding"),
                                   ({"role_policy": roles}, "role_policy_path"),
                                   ({"role_helper": ordinary_helper}, "typed_helper_path")):
                pin = write(typed_path, dict(typed, **change))["sha256"]
                with self.assertRaisesRegex(SourceHold, reason):
                    supervisor._installed(runtime, {"supervisor_policy_sha256": pin}, typed=True)
            drifted = write(Path(typed_roles["path"]), dict(role_config, roles={
                **role_config["roles"], "coding": {"uid": 999},
            }))
            pin = write(typed_path, dict(typed, role_policy=drifted))["sha256"]
            with self.assertRaisesRegex(SourceHold, "shared_binding"):
                supervisor._installed(runtime, {"supervisor_policy_sha256": pin}, typed=True)
            typed_path.unlink()
            with self.assertRaises(FileNotFoundError):
                supervisor._installed(runtime, {"supervisor_policy_sha256": typed_pin}, typed=True)
            broker.assert_not_called()


CHILD = r"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

marker, mode, behavior = sys.argv[1:]
assert mode in {"workspace-write", "read-only"}
if behavior in {"orphan", "linger"}:
    child = subprocess.Popen([sys.executable, "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(45)"],
        start_new_session=True)
    Path(marker).write_text(str(child.pid))
if behavior == "linger":
    time.sleep(45)
if behavior == "orphan":
    Path("source").write_text("retained coding output")
if behavior == "dirty":
    Path("outside-scope").write_text("fixture change")
print(json.dumps({"type":"thread.started", "thread_id":"fixture-session"}), flush=True)
print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":3,"output_tokens":5}}), flush=True)
"""

DRIVER = r"""
import json
from pathlib import Path
import sqlite3
import sys

from uif import supervisor

root, issue, behavior, capacity = sys.argv[1:]
root = Path(root)
runtime = root / "runtime"
cfg = dict(runtime_root=root, coordinator_runtime=root / "coordinator", pool_root=root / "pool",
           auth_reference=root / "auth-reference", capacity=Path(capacity) if capacity else None)

class FixturePool:
    def __init__(self, path, policy):
        self.db = path / "state.sqlite"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, binding TEXT, fingerprint TEXT, status TEXT, outcome TEXT)")
        self.db.chmod(0o600)

    def admit(self, lease, binding, provider, host):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO leases VALUES (?, ?, ?, 'running', NULL)",
                       (lease, json.dumps(binding), supervisor._digest(binding)))
        return dict(reused=False)

    def finish(self, lease, gone, status):
        assert gone is True
        assert (runtime / "attempts" / issue / "receipt.json").is_file()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE leases SET status=?, outcome=? WHERE id=?",
                       ("finished" if status == "succeeded" else "failed", status, lease))

class FixtureCapacity:
    Pool = FixturePool
    host_snapshot = staticmethod(lambda path: {})
    host_pressure = staticmethod(lambda path, policy: None)

supervisor._installed = lambda *args, **kwargs: cfg
if not capacity:
    supervisor.load_capacity = lambda cfg: FixtureCapacity
supervisor._command = lambda cfg, ws, directory, home, mode: [
    sys.executable, str(root / "child.py"), str(root / "orphan"), mode, behavior]
if behavior == "uncertain":
    def cannot_prove(child):
        child.wait(timeout=3)
        return False
    supervisor._cleanup = cannot_prove
if behavior == "finish-failure":
    write_then_finish = supervisor.finish_attempt
    def fail_after_receipt(pool, *args):
        def unavailable(*args):
            raise RuntimeError("fixture finish interrupted")
        pool.finish = unavailable
        write_then_finish(pool, *args)
    supervisor.finish_attempt = fail_after_receipt
if behavior == "review":
    request = dict(prompt="review fixture", timeout_seconds=4)
    supervisor.prepare_guarded_runtime(runtime, {})
    result = supervisor.run_guarded_turn(cfg, runtime=runtime, issue=issue,
        workspace=runtime / "workspaces" / issue, directory=runtime / "attempts" / ("review-" + issue),
        request=request, mode="read-only", stop=lambda: False, resource_check=lambda: None)
    print(json.dumps(result))
else:
    try:
        result = supervisor.execute(runtime, issue)
    except (ValueError, OSError, RuntimeError) as error:
        print(type(error).__name__)
        raise SystemExit(2)
    print(json.dumps(result))
"""


def _put(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return file_digest(path)


class LedgerGuardTests(TestCase):
    @skipUnless(not getattr(guard_fixtures.GuardTests, "__unittest_skip__", False),
                "requires an unprivileged Linux x86_64 C toolchain")
    def test_native_guard_requires_exact_ledger_key_and_coding_paths(self):
        fixture = guard_fixtures.GuardTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        key = LedgerTaskKey("fixture-store", "row:one").workspace_key
        other_key = LedgerTaskKey("fixture-store", "row:two").workspace_key
        attempt = fixture.attempt.with_name(key)
        workspace = fixture.workspace.with_name(key)
        workspace.mkdir(mode=0o700)
        attempt.mkdir(mode=0o700)
        fixture.paths("workspace-write", workspace=workspace, attempt=attempt)
        for changed in (key[:-1], key + "-suffix", key.upper(), key[:-1] + "g"):
            with self.subTest(changed=changed):
                fixture.paths("workspace-write", workspace=workspace.with_name(changed),
                              attempt=attempt.with_name(changed), expected=126)
        fixture.paths("workspace-write", workspace=fixture.workspace, attempt=attempt, expected=126)
        fixture.paths("workspace-write", workspace=workspace.with_name(other_key), attempt=attempt, expected=126)
        fixture.paths("workspace-write", workspace=fixture.root / "other/workspaces" / key,
                      attempt=attempt, expected=126)
        fixture.paths("read-only", workspace=workspace, attempt=attempt.with_name("review-" + key), expected=126)
        slot = "a" * 64
        review = fixture.root / slot / "attempts" / ("review-" + key + "-" + slot[:16])
        projection = fixture.root / slot / "workspaces" / key
        fixture.paths("read-only", workspace=projection, attempt=review)
        fixture.paths("workspace-write", workspace=projection, attempt=review, expected=126)
        fixture.paths("read-only", workspace=projection,
                      attempt=review.with_name(review.name[:-1] + "b"), expected=126)
        fixture.paths("read-only", workspace=workspace, attempt=review, expected=126)
        fixture.invoke("dir", workspace)
        workspace.chmod(0o770)
        fixture.invoke("dir", workspace, expected=126)


class SupervisorTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.runtime = self.root / "runtime"
        self.issue = "UIF-900001"
        self.workspace = self.runtime / "workspaces" / self.issue
        self.directory = self.runtime / "attempts" / self.issue
        for path in (self.workspace, self.directory, self.runtime / "requests",
                     self.root / "coordinator", self.root / "pool"):
            path.mkdir(mode=0o700, parents=True)
        self.auth = self.root / "auth-reference"
        self.auth.touch(mode=0o600)
        self.branch = "feature/uif-supervisor-fixture"
        git(self.workspace, "init", "--initial-branch", self.branch)
        (self.workspace / "source").write_text("disposable source")
        git(self.workspace, "add", "source")
        git(self.workspace, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "fixture")
        for path in self.runtime.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        head, tree = (git(self.workspace, "rev-parse", x) for x in ("HEAD", "HEAD^{tree}"))
        self.polled = PolledIssue(self.issue, "1765ee92-55d6-4012-a889-95e65555711d",
                                  UIF.project_id, UIF.project_slug)
        self.ledger = LedgerAdmission("store", "a" * 64, "row", "b" * 64, "implementation",
                                      "owner", "epoch", "original-op", "ready", "confirmed-absent", False)
        policy = dict(
            host=dict(cpu_reserve=0, memory_reserve_bytes=0, disk_reserve_bytes=0),
            symphony=dict(pool_path=str(self.root / "pool"), provider_proof_ttl_seconds=60,
                          max_concurrent_agents=2, provider_session_ceiling=2, cpu_budget_millis=1000,
                          memory_budget_bytes=512 * 1024**2, io_slots=2),
            windmill=dict(computation_subprocesses_per_job=12))
        task = dict(issue_id=self.polled.issue_id, owner=self.ledger.owner, allowed_paths=["source"],
                    resources=dict(cpu_millis=100, memory_bytes=128 * 1024**2, io_slots=1, output_bytes=512))
        manifest = dict(version=1, branch=self.branch, base_revision=head, project_id=UIF.project_id,
                        label="fixture", tasks={self.issue: task})
        request = dict(prompt="fixture prompt", timeout_seconds=8)
        provider = dict(route="codex-chatgpt", available=True, checked_at=time.time(), session_ceiling=2)
        request_sha = _put(self.runtime / "requests" / (self.issue + ".json"), request)
        manifest_sha = _put(self.runtime / "manifest.json", manifest)
        policy_sha = _put(self.runtime / "capacity-policy.json", policy)
        install_sha = _put(self.runtime / "installation.json", {"supervisor_policy_sha256": "c" * 64})
        provider_sha = _put(self.runtime / "provider-proof.json", provider)
        self.current_path = self.runtime / "current-admission.json"
        admission_sha = _put(self.current_path, dict(
            version=1, owner=self.ledger.owner, issue=self.issue, issue_id=self.polled.issue_id,
            source=str(self.workspace), manifest_sha256=manifest_sha, request_sha256=request_sha,
            policy_sha256=policy_sha, installation_sha256=install_sha, inputs={},
            ledger={field: getattr(self.ledger, field) for field in supervisor.LEDGER_FIELDS}))
        self.lease = _lease_id(self.runtime, manifest, self.issue)
        binding = dict(branch=self.branch, head=head, owner=self.ledger.owner, write_roots=task["allowed_paths"],
                       resources=task["resources"], task_sha256=_digest(
                           dict(runtime=str(self.runtime), manifest=manifest, request=request)))
        self.database = self.root / "pool" / "state.sqlite"
        self.dispatch = self.directory / "dispatch-intent.json"
        sha = _put(self.dispatch, dict(
            schema=SCHEMA, issue=asdict(self.polled), admission=asdict(self.ledger),
            attempt=7, workspace=str(self.workspace), source=UIF.repository,
            source_head=head, source_tree=tree, source_branch=self.branch,
            supervisor_sha256=file_digest(supervisor.__file__), requested_model=MODEL,
            requested_effort="medium", pins=dict(admission_sha256=admission_sha, provider_sha256=provider_sha),
            manifest_sha256=manifest_sha, request_sha256=request_sha,
            lease_id=self.lease, pool_database=str(self.database), worker_binding=binding))
        self.report = DispatchReportBinding(self.polled, 7, self.workspace, self.ledger.original_operation_id,
                                            self.dispatch, sha, self.ledger)
        (self.root / "child.py").write_text(CHILD)

    def spawn(self, behavior):
        return subprocess.Popen([sys.executable, "-c", DRIVER, str(self.root), self.issue, behavior,
                                 os.environ.get("UIF_SUPERVISOR_TEST_CAPACITY", "")],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def finish(self, child):
        out, err = child.communicate(timeout=12)
        self.assertEqual(child.returncode, 0, (out, err))
        return json.loads(out)

    def pool_state(self):
        with sqlite3.connect(self.database) as db:
            return db.execute("SELECT id,status,outcome FROM leases").fetchall()

    def test_orphan_in_new_session_reaped_before_release(self):
        with self.spawn("orphan") as child:
            result = self.finish(child)
        orphan = int((self.root / "orphan").read_text())
        self.assertFalse(Path(f"/proc/{orphan}").exists())
        self.assertTrue(result["child_tree_gone"])
        self.assertEqual(self.pool_state(), [(self.lease, "finished", "succeeded")])
        outcome = read_outcome(self.report)
        self.assertEqual((outcome.status, outcome.session_id, outcome.resolved_model),
                         ("succeeded", "fixture-session", None))
        self.assertEqual(result["requested_model"], MODEL)
        self.assertEqual(result["origin"], "native-coding")
        self.assertEqual(result["output_sha256"], {"source": file_digest(self.workspace / "source")})
        self.assertEqual(result["resolution_status"], "unobserved")
        self.assertEqual(list(self.directory.glob("codex-home-*/auth.json")), [])
        with self.spawn("orphan") as replay:
            replay.communicate(timeout=4)
            self.assertEqual(replay.returncode, 2)
        self.assertEqual(self.pool_state(), [(self.lease, "finished", "succeeded")])

    def test_signal_cleans_only_its_own_attempt(self):
        sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
        try:
            with self.spawn("linger") as child:
                deadline = time.monotonic() + 5
                while not (self.root / "orphan").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((self.root / "orphan").exists())
                child.send_signal(signal.SIGTERM)
                result = self.finish(child)
            self.assertIsNone(sibling.poll())
            self.assertTrue(result["child_tree_gone"])
            self.assertEqual(read_outcome(self.report).status, "cancelled")
            self.assertEqual(self.pool_state(), [(self.lease, "failed", "cancelled")])
        finally:
            sibling.terminate()
            sibling.wait(timeout=4)

    def test_unverified_cleanup_keeps_lease_and_attempt(self):
        with self.spawn("uncertain") as child:
            result = self.finish(child)
        self.assertEqual((result["status"], result["child_tree_gone"]), ("uncertain", False))
        self.assertEqual(self.pool_state(), [(self.lease, "running", None)])
        self.assertEqual(read_outcome(self.report).status, "uncertain")
        original = (self.directory / "receipt.json").read_bytes()
        with self.spawn("orphan") as replay:
            replay.communicate(timeout=4)
            self.assertEqual(replay.returncode, 2)
        self.assertEqual((self.directory / "receipt.json").read_bytes(), original)

    def test_receipt_without_lease_finish_remains_uncertain(self):
        with self.spawn("finish-failure") as child:
            child.communicate(timeout=5)
            self.assertEqual(child.returncode, 2)
        receipt = json.loads((self.directory / "receipt.json").read_bytes())
        self.assertTrue(receipt["child_tree_gone"])
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(self.pool_state(), [(self.lease, "running", None)])
        self.assertEqual(read_outcome(self.report).status, "uncertain")

    def test_invalid_claim_and_missing_guard_refuse_before_admission(self):
        current = self.current_path.read_bytes()
        self.current_path.write_text("{}")
        with self.assertRaisesRegex(SourceHold, "record_drift"):
            supervisor.execute(self.runtime, self.issue)
        self.current_path.write_bytes(current)
        with patch.object(supervisor, "POLICY", self.root / "absent"), patch.object(
                supervisor, "_trusted", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                supervisor.execute(self.runtime, self.issue)
        self.assertFalse(self.database.exists())
        self.assertFalse((self.directory / "intent.json").exists())

    def test_exit_zero_with_scope_violation_is_failed(self):
        with self.spawn("dirty") as child:
            result = self.finish(child)
        self.assertEqual(result["scope_violations"], ["outside-scope"])
        self.assertEqual(read_outcome(self.report).status, "failed")
        self.assertTrue((self.workspace / "outside-scope").exists())

    def test_review_launch_requires_read_only_attempt_path(self):
        cfg = dict(runtime_root=self.root, coordinator_runtime=self.root / "coordinator")
        review = self.runtime / "attempts" / ("review-" + self.issue)
        review.mkdir(mode=0o700)
        args = dict(runtime=self.runtime, issue=self.issue, workspace=self.workspace,
                    request=dict(prompt="review fixture", timeout_seconds=4),
                    stop=lambda: False, resource_check=lambda: None)
        with self.assertRaisesRegex(SourceHold, "guarded_path_or_mode"):
            supervisor.run_guarded_turn(cfg, **args, directory=review, mode="workspace-write")
        with self.assertRaisesRegex(SourceHold, "guarded_path_or_mode"):
            supervisor.run_guarded_turn(cfg, **args, directory=self.directory, mode="read-only")
        with self.spawn("review") as child:
            result = self.finish(child)
        self.assertTrue(result["child_tree_gone"])
        self.assertFalse(self.database.exists())
