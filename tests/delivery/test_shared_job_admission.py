"""Exercise the neutral adapter through the retained, patched Pool and OwnerAuthority."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sqlite3
from threading import Barrier
import time
from unittest import TestCase, skipUnless
from unittest.mock import patch

from uif import shared_job_admission as shared
from uif.dispatch import _digest, _encode
from uif.neutral_launch import NeutralLaunch
from uif.owner_runtime import OwnerAuthority
from uif.role_broker import ATTEMPT_ISOLATION, CANCEL_BEFORE_START, NeutralObservation, SCHEMA, slot_for_lease
from uif.source import SourceHold, file_digest
from uif.supervisor import load_capacity

CAPACITY = os.environ.get("UIF_NEUTRAL_POOL_SOURCE")
SNAPSHOT = Path(__file__).resolve().parents[2] / "README.md"


class FixtureBroker:
    """Substitute only the privileged transport with exact typed observations."""

    def __init__(self):
        self.actions = []
        self.terminal = None

    def start(self, request):
        self.actions.append(("start", request))
        return NeutralObservation("running", request.digest)

    def observe(self, request):
        self.actions.append(("observe", request))
        return self.terminal or NeutralObservation("running", request.digest)

    def cancel(self, request):
        self.actions.append(("cancel", request))
        return self.terminal or NeutralObservation("uncertain", request.digest)


@skipUnless(CAPACITY, "set UIF_NEUTRAL_POOL_SOURCE to the patched retained Pool")
class SharedJobTests(TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.root = root
        self.runtime, self.execution = root / "controller", root / "neutral"
        self.execution.mkdir(mode=0o700)
        self.runner_control = root / "runner"
        self.runner_control.mkdir(mode=0o700)
        self.receipts = self.runtime / "stopped"
        self.database = self.runtime / "pool/state.sqlite"
        for directory in (self.runtime, self.receipts, self.database.parent,
                          self.runtime / "coordinator", self.runtime / "operations"):
            directory.mkdir(mode=0o700)
        self.policy = {
            "resource_accounting": "observed-host-v1",
            "host": dict(cpu_reserve=0, memory_reserve_bytes=0, disk_reserve_bytes=0),
            "symphony": dict(pool_path=str(self.database.parent), provider_proof_ttl_seconds=60,
                             max_concurrent_agents=4, provider_session_ceiling=1,
                             cpu_budget_millis=1000, memory_budget_bytes=8 * 1024**2, io_slots=4),
        }
        capacity_path = Path(CAPACITY)
        self.assertEqual(file_digest(capacity_path), shared.CAPACITY_SHA256)
        self.capacity = load_capacity({"capacity": capacity_path})
        self.pool = self.capacity.Pool(self.database.parent, self.policy)
        lock = self.runtime / "catalogue.lock"
        lock.touch(mode=0o600)
        catalogue = self.runtime / "catalogue.json"
        self.put(catalogue, dict(schema="uif-owner-catalogue/1", store_id="fixture-store",
                                 owner="fixture-owner", epoch="fixture-epoch", rows={}))
        self.owner_registration = root / "owner.json"
        self.put(self.owner_registration, dict(
            schema="uif-owner-runtime/1", store_id="fixture-store", owner="fixture-owner", epoch="fixture-epoch",
            catalogue=str(catalogue), lock=str(lock), snapshot=str(SNAPSHOT),
            runtime_root=str(self.runtime), coordinator_runtime=str(self.runtime / "coordinator"),
            pool_database=str(self.database), operation_locks=str(self.runtime / "operations")))
        self.authority = OwnerAuthority.load(self.owner_registration, file_digest(self.owner_registration))
        self.config = {
            "descriptor_sha256": "a" * 64, "runner_sha256": "b" * 64,
            "capacity_policy": {"sha256": "c" * 64}, "execution_root": str(self.execution),
            "runner_control": str(self.runner_control),
            "read_roots": [str(self.execution)], "write_roots": [str(self.execution)],
            "protected_roots": [str(self.execution / "protected")],
            "stopped_receipts": str(self.receipts), "timeout_seconds": 90,
            "limits": dict(cpu_millis=1000, memory_bytes=8 * 1024**2, io_slots=4, output_bytes=32768,
                           workers=4, overhead_processes=2),
        }
        self.broker = FixtureBroker()
        self.launch = NeutralLaunch(self.runtime, self.broker, sorted(os.sched_getaffinity(0))[:1])
        self.adapter = self.open()
        self.binding = {
            "namespace": shared.NAMESPACE, "workspace": "foo", "job_id": "original-job",
            "descriptor_sha256": "a" * 64, "runner_sha256": "b" * 64, "capacity_policy_sha256": "c" * 64,
            "request_sha256": "d" * 64, "command_sha256": "e" * 64, "mode": "write", "timeout_seconds": 15,
            "resources": dict(cpu_millis=600, memory_bytes=2 * 1024**2, io_slots=1, output_bytes=4096,
                              workers=1, overhead_processes=1, read_roots=[],
                              write_roots=[str(self.execution / "ordinary")]),
        }
        body = {key: self.binding[key] for key in ("workspace", "job_id", "descriptor_sha256",
                                                  "mode", "timeout_seconds", "resources")}
        body.update(protocol="foo-user-exec-v1", command=":")
        self.binding.update(command_sha256=sha256(b":").hexdigest(), request_sha256=_digest(body))
        self.work = {**body, "action": "run", "request_sha256": self.binding["request_sha256"]}

    @staticmethod
    def put(path, value):
        path.write_bytes(_encode(value))
        path.chmod(0o600)

    def open(self):
        return shared.SharedJobAdmission(self.authority, self.capacity, self.policy, self.config, "f" * 64, self.launch)

    def call(self, action, binding=None, **extra):
        return self.adapter.handle(dict(schema=shared.SCHEMA, action=action,
                                        binding=self.binding if binding is None else binding, **extra))

    def witness(self, lease, outcome="cancelled", *, no_fork=False, **extra):
        original, = [row for row in self.pool.status() if row["id"] == lease]
        request, _ = self.launch.request(lease, original["binding"])
        exit_code = None if no_fork else 0 if outcome == "succeeded" else -9
        logs, truncated = {"stdout": 0, "stderr": 0}, {"stdout": False, "stderr": False}
        receipt = dict(self.binding, lease_id=lease, status=outcome, child_tree_gone=True,
                       reconcile_required=False, exit_code=exit_code, log_bytes=logs, logs_truncated=truncated,
                       **extra)
        reservation = dict(schema=SCHEMA, request=request.digest, boot="fixture-boot", isolation=ATTEMPT_ISOLATION)
        terminal = dict(request=request.digest, boot=reservation["boot"], isolation=ATTEMPT_ISOLATION,
                        exit_code=exit_code)
        if no_fork:
            state = "refused-before-fork"
            terminal.update(state=state, decision=CANCEL_BEFORE_START, cgroup=None, pid=None)
            reservation["decision"] = CANCEL_BEFORE_START
            shape = {}
        else:
            state = "exited-clean"
            started = dict(boot=terminal["boot"], cgroup=[1, 2], pid=42, start_ticks=1, request=request.digest,
                           isolation=ATTEMPT_ISOLATION, namespaces={"mnt": [1, 3], "pid": [1, 4]})
            terminal.update(**{**started, "state": state, "outcome": outcome,
                               "log_bytes": logs, "logs_truncated": truncated})
            shape = dict(cgroup={"boot": terminal["boot"], "cgroup": started["cgroup"]}, started=started,
                         released={"request": request.digest, "started_sha256": _digest(started)})
        evidence = dict(namespace=self.binding["namespace"], workspace="foo", job_id=self.binding["job_id"],
                        lease_id=lease, binding_sha256=_digest(self.binding), runner_intent=self.binding,
                        runner_intent_sha256=request.intent_sha256, runner_receipt=receipt,
                        runner_receipt_sha256=_digest(receipt), trusted_terminal=terminal,
                        trusted_terminal_sha256=_digest(terminal), reservation=reservation, **shape)
        self.broker.terminal = NeutralObservation(state, request.digest, _digest(terminal), exit_code,
                                                  outcome=outcome, stopped_evidence=evidence,
                                                  log_bytes=logs, logs_truncated=truncated)
        return dict(lease_id=lease, outcome=outcome, child_tree_gone=True, receipt_sha256=_digest(receipt))

    def native(self, name, cpu, review=False, policy=None, backend=None):
        resources = dict(cpu_millis=cpu, memory_bytes=2 * 1024**2, io_slots=1, output_bytes=4096)
        binding = dict(branch="feature/fixture", head="1" * 40, owner="native",
                       write_roots=["native"], resources=resources, task_sha256="2" * 64)
        if review:
            binding.update(mode="native-review-read-only", read_roots=[str(self.runtime)],
                           write_roots=[], source_sha256="3" * 64)
        provider = dict(route="codex-chatgpt", checked_at=time.time(), available=True, session_ceiling=1)
        pool = (backend or self.capacity).Pool(self.database.parent, self.policy if policy is None else policy)
        return pool.admit(name, binding, provider, self.capacity.host_snapshot(self.execution))

    def test_host_disk_reserve_uses_runner_storage_not_project_quota(self):
        self.policy["host"]["disk_reserve_bytes"] = 70 * 1024**3
        self.adapter = self.open()
        available = {self.execution: 16 * 1024**2, self.runner_control: 80 * 1024**3}

        def snapshot(path):
            return dict(available_disk_bytes=available[path], available_memory_bytes=1024**3,
                        cpu_millis=1000)

        with patch.object(self.capacity, "host_snapshot", side_effect=snapshot) as measure:
            self.assertEqual(self.call("admit")["status"], "admitted")
            measure.assert_called_once_with(self.runner_control)

            available[self.runner_control] = 69 * 1024**3
            another = deepcopy(self.binding)
            another["job_id"] = "other-job"
            another["resources"]["write_roots"] = [str(self.execution / "other")]
            self.assertEqual(self.call("admit", another)["status"], "refused")
            self.assertEqual(measure.call_count, 2)
        self.assertEqual(len(self.pool.status()), 1)

    def test_original_lease_and_observation_only_retry_after_lost_reply(self):
        # Commit via the actual retained transaction, then lose the adapter response.
        admit = self.adapter.pool.admit_neutral

        def response_lost(*args):
            admit(*args)
            raise OSError("response lost after commit")

        with patch.object(self.adapter.pool, "admit_neutral", side_effect=response_lost):
            with self.assertRaises(OSError):
                self.call("admit")
        self.adapter = self.open()
        held = self.call("admit")
        self.assertEqual((held["status"], held["reused"]), ("held", True))
        self.assertNotIn("cpu_ids", held)
        self.assertEqual(self.call("status")["status"], "held")
        self.assertEqual(self.call("status")["lease_id"], held["lease_id"])
        with self.assertRaises(FileNotFoundError):
            self.call("start", request=self.work)
        self.assertEqual(self.broker.actions, [])
        changed = {**self.binding, "request_sha256": "9" * 64}
        self.assertEqual(self.call("admit", changed)["lease_id"], held["lease_id"])
        self.assertEqual(len(self.pool.status()), 1)
        stored = self.pool.status()[0]["binding"]
        self.assertEqual((stored["owner"], stored["epoch"], stored["request"]),
                         ("fixture-owner", "fixture-epoch", self.binding))

    def test_exact_cancel_and_terminal_receipt_release_and_replay(self):
        admitted = self.call("admit")
        self.assertEqual(admitted["status"], "admitted")
        self.assertTrue(set(admitted["cpu_ids"]) <= os.sched_getaffinity(0))
        lease = admitted["lease_id"]
        self.assertEqual(self.call("cancel")["status"], "cancel_requested")
        self.assertEqual(self.call("cancel")["status"], "cancel_requested")
        with self.assertRaisesRegex(SourceHold, "original_launch_missing"):
            self.call("finish", lease_id=lease, outcome="cancelled", child_tree_gone=True, receipt_sha256="1" * 64)
        stopped = self.call("cancel", request=self.work)
        self.assertEqual(stopped["observation"]["state"], "uncertain")
        self.assertEqual([action for action, _ in self.broker.actions], ["cancel"])
        finish = self.witness(lease, no_fork=True)
        with self.assertRaisesRegex(SourceHold, "stopped_receipt"):
            self.call("finish", **{**finish, "receipt_sha256": "1" * 64})
        self.assertEqual(self.pool.status()[0]["status"], "cancel_requested")
        released = self.call("finish", **finish)
        self.assertEqual((released["status"], released["receipt_sha256"]), ("finished", finish["receipt_sha256"]))
        self.assertEqual(self.call("finish", **finish), released)
        self.assertEqual(self.call("admit")["status"], "held")
        replacement = self.witness(lease, observation="changed")
        with self.assertRaisesRegex(SourceHold, "stopped_receipt"):
            self.call("finish", **replacement)
        with sqlite3.connect(self.database) as database:
            events = database.execute("SELECT event FROM events WHERE id=?", (lease,)).fetchall()
        self.assertEqual(events.count(("neutral_stopped_receipt:" + finish["receipt_sha256"],)), 1)

    def test_late_cancel_releases_original_authenticated_success(self):
        lease = self.call("admit")["lease_id"]
        self.assertEqual(self.call("start", request=self.work)["observation"]["state"], "running")
        finish = self.witness(lease, outcome="succeeded")
        stopped_path = self.receipts / (lease + ".json")
        cancel = self.broker.cancel

        def cancel_after_pool_transition(request):
            original, = self.pool.status()
            self.assertEqual((original["id"], original["status"]), (lease, "cancel_requested"))
            return cancel(request)

        with patch.object(self.broker, "cancel", side_effect=cancel_after_pool_transition):
            late = self.call("cancel", request=self.work)
        self.assertEqual(late["status"], "cancel_requested")
        self.assertEqual(late["observation"]["outcome"], "succeeded")
        self.assertTrue(late["observation"]["containment_stopped"])
        with self.assertRaisesRegex(SourceHold, "stopped_receipt"):
            self.call("finish", **{**finish, "receipt_sha256": "1" * 64})
        self.assertFalse(stopped_path.exists())
        self.assertEqual(self.pool.status()[0]["status"], "cancel_requested")

        finish_neutral = self.adapter.pool.finish_neutral

        def finish_after_stopped(*args):
            self.assertTrue(stopped_path.is_file())
            return finish_neutral(*args)

        with patch.object(self.adapter.pool, "finish_neutral", side_effect=finish_after_stopped):
            released = self.call("finish", **finish)
        self.assertEqual((released["status"], released["lease_id"], released["receipt_sha256"]),
                         ("finished", lease, finish["receipt_sha256"]))
        self.assertTrue(stopped_path.is_file())
        self.assertEqual(self.call("finish", **finish), released)
        status = self.call("status")
        self.assertEqual((status["outcome"], status["receipt_sha256"]),
                         ("succeeded", finish["receipt_sha256"]))
        original, = self.pool.status()
        self.assertEqual((original["id"], original["outcome"]), (lease, "succeeded"))
        self.assertEqual([action for action, _ in self.broker.actions], ["start", "cancel", "observe", "observe"])
        with sqlite3.connect(self.database) as database:
            events = database.execute("SELECT event FROM events WHERE id=? ORDER BY rowid", (lease,)).fetchall()
        self.assertEqual(events[-3:], [("cancel_requested",),
                         ("neutral_stopped_receipt:" + finish["receipt_sha256"],), ("reconciled_succeeded",)])

    def test_native_cancel_still_refuses_success_and_neutral_finish(self):
        lease = self.native("native-one", 500)["id"]
        self.pool.cancel(lease)
        with self.assertRaisesRegex(self.capacity.AdmissionError, "cancelled_task_cannot_succeed"):
            self.pool.finish(lease, True, "succeeded")
        with self.assertRaisesRegex(self.capacity.AdmissionError, "lease_lane_mismatch"):
            self.pool.finish_neutral(lease, True, "succeeded", "1" * 64)
        self.assertEqual(self.pool.status()[0]["status"], "cancel_requested")
        self.pool.finish(lease, True, "cancelled")
        self.assertEqual(self.pool.status()[0]["outcome"], "cancelled")

    def test_capacity_is_shared_in_both_directions_and_keeps_native_provider_limit(self):
        self.assertEqual(self.native("native-one", 500)["status"], "running")
        self.assertEqual(self.call("admit")["status"], "refused")
        self.pool.finish("native-one", True, "succeeded")
        lease = self.call("admit")["lease_id"]
        with self.assertRaisesRegex(self.capacity.AdmissionError, "capacity_cpu_millis"):
            self.native("native-two", 500)
        with self.assertRaisesRegex(self.capacity.AdmissionError, "shared_policy_conflict"):
            self.native("native-two", 200, policy={**self.policy, "symphony": {
                **self.policy["symphony"], "cpu_budget_millis": 2000}})
        self.assertEqual(self.native("native-two", 200, review=True)["status"], "running")
        with self.assertRaisesRegex(self.capacity.AdmissionError, "capacity_sessions"):
            self.native("native-three", 100)
        with self.assertRaisesRegex(self.capacity.AdmissionError, "lease_lane_mismatch"):
            self.pool.finish(lease, True, "failed")

    def test_original_start_seals_exact_run_cpu_and_pool_and_never_starts_again(self):
        lease = self.call("admit")["lease_id"]
        with self.assertRaisesRegex(SourceHold, "original_run"):
            self.call("start", request={**self.work, "command": "different"})
        self.assertEqual(self.broker.actions, [])
        launched = self.call("start", request=self.work)
        self.assertEqual(launched["observation"]["state"], "running")
        self.assertNotIn("stopped_evidence", launched["observation"])
        self.assertEqual(self.call("start", request=self.work), launched)
        self.assertEqual([action for action, _ in self.broker.actions], ["start", "observe"])
        request = self.broker.actions[0][1]
        self.assertEqual(request.cpu_ids, self.launch.cpu_ids)
        self.assertEqual(request.pool_binding_sha256, self.pool.status()[0]["fingerprint"])
        self.assertEqual((self.runtime / slot_for_lease(lease) / "request.json").read_bytes(), _encode(self.work))

    def test_reaped_release_rejects_changed_runner_receipt_and_publishes_create_only(self):
        lease = self.call("admit")["lease_id"]
        self.call("start", request=self.work)
        finish = self.witness(lease, "succeeded")
        witness = self.broker.terminal
        witness.stopped_evidence["runner_receipt"]["reconcile_required"] = True
        with self.assertRaisesRegex(SourceHold, "stopped_receipt"):
            self.call("finish", **finish)
        self.assertEqual(list(self.receipts.iterdir()), [])
        self.assertEqual(self.pool.status()[0]["status"], "running")
        witness.stopped_evidence["runner_receipt"]["reconcile_required"] = False
        result = self.call("finish", **finish)
        path = self.receipts / f"{lease}.json"
        exact = path.read_bytes()
        self.broker.terminal = None
        self.assertEqual(self.call("finish", **finish), result)
        self.assertEqual(path.read_bytes(), exact)
        self.assertEqual(self.call("status")["receipt_sha256"], finish["receipt_sha256"])

    def test_native_first_mismatch_holds_the_reviewed_500m_1000m_case(self):
        older = deepcopy(self.policy)
        older["symphony"]["cpu_budget_millis"] = 500
        self.assertEqual(self.native("native-first", 300, policy=older)["status"], "running")
        with self.assertRaisesRegex(self.capacity.AdmissionError, "shared_policy_conflict"):
            self.call("admit")
        rows = self.pool.status()
        self.assertEqual([(row["id"], row["status"]) for row in rows], [("native-first", "running")])
        self.assertEqual(sum(row["binding"]["resources"]["cpu_millis"] for row in rows), 300)

    def test_unknown_legacy_policy_blocks_mixing_but_native_finish_still_works(self):
        capacity_path = Path(CAPACITY).with_name("retained-capacity.py")
        self.assertEqual(file_digest(capacity_path),
                         "cddb868121b590af31d92ed87bb346f00c2fa917804753531322e12e7544957c")
        legacy = load_capacity({"capacity": capacity_path})
        self.assertEqual(self.native("legacy-native", 300, backend=legacy)["status"], "running")
        for status in ("running", "cancel_requested", "uncertain"):
            if status != "running":
                with sqlite3.connect(self.database) as database:
                    database.execute("UPDATE leases SET status=? WHERE id=?", (status, "legacy-native"))
            with self.subTest(status=status), self.assertRaisesRegex(
                    self.capacity.AdmissionError, "shared_policy_unverified"):
                self.call("admit")
        self.pool.finish("legacy-native", True, "failed")
        self.assertEqual(self.call("admit")["status"], "admitted")

    def test_status_recovers_exact_release_after_lost_finish_reply(self):
        lease = self.call("admit")["lease_id"]
        self.call("start", request=self.work)
        finish = self.witness(lease)
        complete = self.adapter.pool.finish_neutral

        def reply_lost(*args):
            complete(*args)
            raise OSError("response lost after release")

        with patch.object(self.adapter.pool, "finish_neutral", side_effect=reply_lost):
            with self.assertRaisesRegex(OSError, "response lost"):
                self.call("finish", **finish)
        self.adapter = self.open()
        released = self.call("status")
        self.assertEqual(released, dict(schema=shared.SCHEMA, binding_sha256=_digest(self.binding),
                                        lease_id=lease, status="finished", pool_status="failed",
                                        outcome="cancelled", receipt_sha256=finish["receipt_sha256"]))
        self.assertNotIn("receipt_sha256", self.call("status", {**self.binding, "request_sha256": "9" * 64}))
        self.assertEqual(self.call("admit")["status"], "held")
        with sqlite3.connect(self.database) as database:
            database.execute("DELETE FROM events WHERE id=? AND event GLOB 'neutral_stopped_receipt:*'", (lease,))
        with self.assertRaisesRegex(self.capacity.AdmissionError, "neutral_release_unverified"):
            self.call("status")

    def test_parallel_neutral_jobs_reserve_capacity_atomically(self):
        barrier = Barrier(2)

        def compete(suffix):
            binding = deepcopy(self.binding)
            binding["job_id"] += suffix
            binding["resources"]["write_roots"] = [str(self.execution / suffix)]
            barrier.wait(timeout=5)
            return self.call("admit", binding)["status"]

        with ThreadPoolExecutor(max_workers=2) as threads:
            states = list(threads.map(compete, ("first", "second")))
        self.assertCountEqual(states, ["admitted", "refused"])
        self.assertEqual(len(self.pool.status()), 1)

    def test_scope_refusal_and_observation_do_not_allocate(self):
        self.assertEqual(self.call("cancel")["status"], "absent")
        self.assertEqual(self.call("status")["status"], "absent")
        for path in (self.runtime, self.execution / "protected", self.execution.parent):
            binding = deepcopy(self.binding)
            binding["resources"]["write_roots"] = [str(path)]
            with self.subTest(path=path), self.assertRaisesRegex(SourceHold, "scope"):
                self.call("admit", binding)
        self.assertEqual(self.pool.status(), [])

    def test_loader_reconciles_native_supervisor_pins_and_caller(self):
        if os.getuid() == 0:
            self.skipTest("requires unprivileged controller fixture")
        policy_file, registration, supervisor = (self.root / name for name in ("policy", "registration", "supervisor"))
        self.put(policy_file, self.policy)
        def pin(path):
            return {"path": str(path), "sha256": file_digest(path)}
        config = dict(self.config, schema="uif-shared-job-registration/1", controller_uid=os.getuid(),
                      runner_uid=os.getuid() + 1, runner_control=str(self.root / "runner"),
                      owner_registration=pin(self.owner_registration),
                      capacity=pin(Path(CAPACITY)), capacity_policy=pin(policy_file))
        self.put(registration, config)
        role_policy, wrapper = self.root / "roles", self.root / "wrapper"
        wrapper.write_text("fixture wrapper")
        neutral = {name: config[name] for name in ("runner_uid", "runner_control", "descriptor_sha256", "runner_sha256",
                   "execution_root", "read_roots", "write_roots", "protected_roots", "limits", "timeout_seconds")}
        neutral.update(store_id=self.authority.store_id, registration_sha256=file_digest(registration),
                       capacity_policy_sha256=config["capacity_policy"]["sha256"], policy_fingerprint=_digest(self.policy),
                       memory_reserve_bytes=0, disk_reserve_bytes=0, cpu_ids=list(self.launch.cpu_ids))
        self.put(role_policy, dict(schema=SCHEMA, attempt_isolation=ATTEMPT_ISOLATION, controller_uid=os.getuid(),
                 controller_gid=os.getegid(), controller_root=str(self.runtime), pool_database=str(self.database),
                 operation_locks=str(self.authority.operation_locks_path), roles={"neutral": neutral}))
        installed = dict(schema="uif-coding-supervisor/2", uid=os.getuid(), capacity=config["capacity"],
                         owner_registration=config["owner_registration"], runtime_root=str(self.runtime),
                         pool_root=str(self.database.parent), role_policy=pin(role_policy), role_helper=pin(wrapper))
        self.put(supervisor, installed)
        # Substitute root-owned installation checks and privileged transport only.
        with (patch.object(shared, "_trusted", side_effect=Path),
              patch.object(shared, "SUPERVISOR_POLICY", supervisor), patch.object(shared, "ROLE_POLICY", role_policy),
              patch.object(shared, "RoleBroker", return_value=self.broker)):
            with patch.dict(os.environ, SUDO_UID=str(config["runner_uid"])):
                opened = shared.SharedJobAdmission.load(registration)
                self.assertEqual(opened.authority.pool_database_path, self.database)
                self.put(supervisor, {**installed, "pool_root": str(self.execution)})
                with self.assertRaisesRegex(SourceHold, "native_pool_binding"):
                    shared.SharedJobAdmission.load(registration)
            with patch.dict(os.environ, SUDO_UID=str(os.getuid())):
                with self.assertRaisesRegex(SourceHold, "invocation_binding"):
                    shared.SharedJobAdmission.load(registration)
