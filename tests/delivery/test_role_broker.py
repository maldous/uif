"""Controller and lifecycle tests with no root execution or kernel mutation."""

from contextlib import contextmanager
from dataclasses import asdict, replace
import fcntl
import ctypes
import errno
import importlib.util
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from uif import role_broker as broker


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.uid = os.getuid()
        self.key = broker.LaunchKey(
            "coding", "store", "row", "a" * 64, "61452378-274d-4aa7-a998-0b91c3cf8462",
            "UIF-88", "owner", "epoch", "operation", "initial", "b" * 64, "rt-original")
        self.slot = broker.slot_for_lease(self.key.lease_id)
        self.directory = self.root / self.slot
        self.directory.mkdir(mode=0o700)
        self.lock_root = self.root / "locks"
        self.lock_root.mkdir(mode=0o700)
        self.pool = self.root / "pool.sqlite"
        self.cfg = {"controller_uid": self.uid, "controller_root": str(self.root),
                    "pool_database": str(self.pool), "operation_locks": str(self.lock_root)}
        self.request = broker.LaunchRequest(self.key, self.slot, "c" * 64, "d" * 64, "e" * 64, "f" * 64)

    def _put(self, name, value):
        p = self.directory / name
        broker._create(p, value)
        return sha256(p.read_bytes()).hexdigest()

    def originals(self):
        policy = "f" * 64
        admission_sha = self._put("admission.json", {
            "owner": self.key.owner, "issue_id": self.key.issue_id, "issue": self.key.issue_identifier,
            "policy_sha256": policy,
            "ledger": {"store_id": self.key.store_id, "row_id": self.key.row_id,
                       "source_sha256": self.key.row_source_sha256, "epoch": self.key.epoch,
                       "original_operation_id": self.key.operation_id}})
        limits = {"cpu_millis": 100, "memory_bytes": 1048576}
        binding = {"owner": self.key.owner, "write_roots": ["src"],
                   "branch": "feature/role-fixture", "head": "1" * 40, "task_sha256": "2" * 64,
                   "resources": {**limits, "io_slots": 1, "output_bytes": 1024}}
        work = {"prompt": "disposable test request", "timeout_seconds": 5}
        work_sha = self._put("request.json", work)
        intent_sha = self._put("intent.json", {
            "schema": "uif-dispatch-intent/1", "attempt": None, "issue": {"issue_id": self.key.issue_id},
            "admission": {"original_operation_id": self.key.operation_id},
            "lease_id": self.key.lease_id, "worker_binding": binding, "request_sha256": work_sha})
        key = replace(self.key, intent_sha256=intent_sha)
        request = broker.LaunchRequest(key, broker.slot_for_lease(key.lease_id), admission_sha, work_sha, "e" * 64, policy)
        self._put("fence.json", {"schema": broker.SCHEMA, "request_sha256": request.digest,
                                "lease": key.lease_id, "binding_sha256": broker._digest(binding)})
        self.pool_api = None
        if capacity_path := os.environ.get("UIF_ROLE_BROKER_TEST_CAPACITY"):
            spec = importlib.util.spec_from_file_location("fixture_symphony_capacity", capacity_path)
            capacity = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(capacity)
            policy = {
                "host": {"cpu_reserve": 0, "memory_reserve_bytes": 0, "disk_reserve_bytes": 0},
                "symphony": {"provider_proof_ttl_seconds": 60, "max_concurrent_agents": 2,
                             "provider_session_ceiling": 2, "cpu_budget_millis": 1000,
                             "memory_budget_bytes": 2097152, "io_slots": 2},
            }
            self.pool_api = capacity.Pool(self.root / "capacity", policy)
            self.pool = self.root / "capacity" / "state.sqlite"
            self.cfg["pool_database"] = str(self.pool)
            self.pool_api.admit(key.lease_id, binding,
                                {"route": "codex-chatgpt", "available": True,
                                 "checked_at": time.time(), "session_ceiling": 2},
                                {"cpu_millis": 1000, "available_memory_bytes": 2097152,
                                 "available_disk_bytes": 2048})
        else:
            self.pool.touch(mode=0o600)
            with sqlite3.connect(self.pool) as db:
                db.execute("CREATE TABLE leases (id TEXT PRIMARY KEY, fingerprint TEXT, status TEXT)")
                db.execute("INSERT INTO leases VALUES (?, ?, 'running')", (key.lease_id, broker._digest(binding)))
        lock = self.lock_root / (broker._digest([key.store_id, key.operation_id]) + ".lock")
        lock.touch(mode=0o600)
        return request, (work, limits), lock

    def test_original_fence_and_running_lease_required(self):
        request, work, lock = self.originals()
        with self.assertRaisesRegex(broker.LaunchHold, "fence_not_held"):
            broker._check_original(self.cfg, self.directory, request)
        with lock.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            self.assertEqual(broker._check_original(self.cfg, self.directory, request), work)
            with sqlite3.connect(self.pool) as db:
                db.execute("UPDATE leases SET status='finished'")
            with self.assertRaisesRegex(broker.LaunchHold, "role_launch_lease"):
                broker._check_original(self.cfg, self.directory, request)

    def test_controller_calls_fixed_helper_and_holds_unknown(self):
        helper = Path("/qualified/helper")
        with patch.object(broker, "_installed", return_value=helper), patch.object(broker.subprocess, "run") as run:
            backend = broker.RoleBroker(helper, "0" * 64)
            for action, state in (("start", "running"), ("observe", "exited-clean"), ("cancel", "uncertain")):
                run.return_value.stdout = broker._wire({
                    "schema": broker.SCHEMA, "state": state, "request_sha256": self.request.digest,
                    "receipt_sha256": "1" * 64 if state == "exited-clean" else None,
                    "exit_code": 0 if state == "exited-clean" else None})
                result = getattr(backend, action)(self.request)
                self.assertEqual(result.state, state)
                self.assertEqual(result.release_safe, state == "exited-clean")
                self.assertEqual(run.call_args.args[0], [broker.SUDO, "-n", "--", str(helper), action, self.slot])
            run.return_value.stdout = broker._wire({
                "schema": broker.SCHEMA, "state": "exited-clean", "request_sha256": "2" * 64,
                "receipt_sha256": "1" * 64, "exit_code": 0})
            self.assertFalse(backend.observe(self.request).release_safe)
            run.side_effect = subprocess.TimeoutExpired("fixture", 12)
            self.assertEqual(backend.start(self.request).state, "uncertain")
            self.assertEqual(run.call_count, 5)

    def test_kernel_readback_is_required_for_terminal_receipt(self):
        witness = self.directory / ".role-helper"
        witness.mkdir(mode=0o700)
        boot, group = "fixture-boot", [1, 42]
        started = {"pid": 500, "start_ticks": 123, "boot": boot, "cgroup": group,
                   "namespaces": {"mnt": [1, 2], "pid": [1, 3]}, "request": self.request.digest,
                   "isolation": broker.ATTEMPT_ISOLATION}
        records = {
            "reservation.json": {"schema": broker.SCHEMA, "request": self.request.digest,
                                 "boot": boot, "isolation": broker.ATTEMPT_ISOLATION},
            "cgroup.json": {"boot": boot, "cgroup": group},
            "started.json": started,
            "released.json": {"request": self.request.digest, "started_sha256": broker._digest(started)},
            "terminal.json": {"state": "exited-clean", **started, "exit_code": 0},
        }
        def read(path, uid, forbidden=0o077):
            self.assertEqual(uid, 0)
            return broker._wire(records[path.name])
        for name in records:
            (witness / name).touch()
        with patch.object(broker, "_dir", side_effect=lambda path, *_: path), patch.object(broker, "_read", side_effect=read),                 patch.object(broker, "_boot", return_value=boot), patch.object(broker, "_cgroup", return_value=self.root),                 patch.object(broker, "_cgroup_id", return_value=group), patch.object(broker, "_populated") as live:
            live.return_value = True
            self.assertFalse(broker._observe(self.cfg, witness, self.request).release_safe)
            live.return_value = False
            self.assertTrue(broker._observe(self.cfg, witness, self.request).release_safe)
            for field in ("namespaces", "start_ticks", "isolation", "cgroup"):
                original = records["terminal.json"].pop(field)
                # Missing evidence never projects a safe release.
                self.assertFalse(broker._observe(self.cfg, witness, self.request).release_safe)
                records["terminal.json"][field] = original
            records["terminal.json"]["namespaces"] = {"mnt": [1, 20], "pid": [1, 30]}
            self.assertFalse(broker._observe(self.cfg, witness, self.request).release_safe)

    def test_existing_reservation_is_observation_only(self):
        witness = self.directory / ".role-helper"
        witness.mkdir(mode=0o700)
        with patch.object(broker, "_request", return_value=self.request),                 patch.object(broker, "_observe", return_value=broker.LaunchObservation("uncertain", self.request.digest)),                 patch.object(broker, "_check_original") as check, patch.object(broker.os, "fork") as fork:
            self.assertEqual(broker._handle(self.cfg, "start", self.slot).state, "uncertain")
            check.assert_not_called()
            fork.assert_not_called()

    def test_watcher_pins_limits_and_waits_for_reap(self):
        from contextlib import nullcontext
        from types import SimpleNamespace

        witness = self.directory / ".role-helper"
        witness.mkdir(mode=0o700)
        cg = self.root / "cg"
        workspace = self.root / "work"
        workspace.mkdir(mode=0o700)
        work = {"prompt": "fixture", "timeout_seconds": 5}
        limits = {"cpu_millis": 100, "memory_bytes": 512}
        role = {"dropper": {"path": "/fixture/dropper", "sha256": "a" * 64},
                "guard": {"path": "/fixture/guard", "sha256": "b" * 64}, "max_processes": 3,
                "root": str(self.root)}
        child = SimpleNamespace(pid=500, returncode=0)
        def prepare_cgroup():
            mkdir(cg)
            for name in ("cpu.max", "memory.max", "pids.max", "cgroup.procs"):
                (cg / name).touch()
            return [1, 42]

        def launch(group, group_id, root, entries, command, logs, directory, request, deadline, fence, *, startup):
            self.assertEqual((cg / "cpu.max").read_text(), "10000 100000")
            self.assertEqual((cg / "memory.max").read_text(), "512")
            self.assertEqual((cg / "pids.max").read_text(), "4")
            self.assertEqual((group, group_id, command[1], request), (cg, [1, 42], "workspace-write", self.request))
            fence()
            return child, {"pid": 500, "start_ticks": 123, "boot": "fixture-boot", "cgroup": [1, 42],
                           "namespaces": {"mnt": [1, 2], "pid": [1, 3]}, "request": request.digest,
                           "isolation": broker.ATTEMPT_ISOLATION}

        def monitor(process, group, logs, request):
            self.assertEqual((process, group, request), (child, cg, work))
            self.assertFalse((witness / "terminal.json").exists())

        slot = self.root / "role-slot"
        slot.mkdir(mode=0o700)
        mkdir = Path.mkdir
        def create(path, *args, **kwargs):
            if path == cg:
                prepare_cgroup()
            else:
                mkdir(path, *args, **kwargs)
        fake_lib = SimpleNamespace(prctl=lambda *args: 0)
        with patch.object(broker, "_cgroup", return_value=cg), patch.object(Path, "mkdir", create), \
                patch.object(broker, "_cgroup_id", return_value=[1, 42]), patch.object(broker.ctypes, "CDLL", return_value=fake_lib), \
                patch.object(broker, "_prepare", return_value=(slot, workspace, slot, slot)), \
                patch.object(broker, "_check_original", return_value=(work, limits)), \
                patch.object(broker, "_installed", side_effect=lambda path, *_a, **_k: Path(path)), \
                patch.object(broker, "_log", side_effect=lambda *args: nullcontext(None)), \
                patch.object(broker, "_spawn_isolated", side_effect=launch), patch.object(broker, "_namespace_files", return_value=[]), \
                patch.object(broker, "_open_projection_root", side_effect=lambda path, **_: os.open(path, os.O_RDONLY | os.O_DIRECTORY)), \
                patch.object(broker, "_boot", return_value="fixture-boot"), patch.object(broker, "_populated", return_value=False), \
                patch.object(broker, "_monitor", side_effect=monitor), patch.object(broker, "_return_outputs", return_value="9" * 64), \
                patch.object(broker, "_wait_empty", return_value=True) as reaped:
            broker._watch(self.cfg, self.directory, witness, self.request, role, work, limits, time.monotonic() + 5)
            reaped.assert_called_once_with(cg)
            receipt = broker._record(witness / "terminal.json", self.uid)
            self.assertEqual((receipt["state"], receipt["request"]), ("exited-clean", self.request.digest))
            self.assertEqual(receipt["output_sha256"], "9" * 64)

    def test_same_role_namespace_views_are_disjoint(self):
        role_root = Path("/var/lib/uif-coding-fixture")
        review_root = Path("/var/lib/uif-review-fixture")
        cfg = {"controller_root": "/var/lib/uif-control-fixture", "cgroup_root": "/sys/fs/cgroup/uif-fixture",
               "roles": {"coding": {"root": str(role_root)}, "review": {"root": str(review_root)}}}
        role = {"runtime_files": [{"path": "/usr/lib/fixture", "target": "/lib/fixture", "sha256": "a"*64}],
                "auth_reference": "/var/lib/uif-coding-auth/fixture"}
        dropper, guard = Path("/opt/uif/dropper"), Path("/opt/uif/guard")
        with patch.object(broker, "_installed", side_effect=lambda path, *_a, **_k: Path(path)):
            views = [broker._namespace_files(cfg, role, dropper, guard,
                     role_root / slot / "workspaces/UIF-88", role_root / slot / "attempts/UIF-88", "coding")
                     for slot in ("first", "second")]
            review = broker._namespace_files(cfg, role, dropper, guard, review_root / "third/workspaces/UIF-88",
                                            review_root / "third/attempts/review-UIF-88", "review")
        self.assertTrue(review[-2][-1])
        self.assertFalse(review[-1][-1])
        for i, view in enumerate(views):
            view_root = self.root / f"view-{i}"
            view_root.mkdir()
            with patch.object(broker, "_mount") as mount, patch.object(broker.os, "chroot"), \
                    patch.object(broker.os, "chdir"), patch.object(broker.os, "getpid", return_value=1):
                broker._private_root(view_root, view)
                calls = [item.args for item in mount.call_args_list]
            self.assertEqual(calls[0], (None, "/", None, 16384 | 262144))
            self.assertIn(("proc", view_root / "proc", "proc", 2 | 4 | 8), calls)
            sources = {source for source, _, _, flags, *_ in calls if flags == 4096}
            self.assertEqual(sources, {item[0] for item in view})
            self.assertFalse(any(path in sources for path, _, folder, _ in views[1-i] if folder))
            self.assertFalse(any(path in sources for path in (Path("/"), Path("/proc"), role_root)))

    def test_gate_refuses_same_namespace_or_wrong_cgroup(self):
        cg = self.root / "cgroup"
        cg.mkdir()
        (cg / "cgroup.procs").write_text("500")
        ready = lambda fds, *_: (fds, [], [])
        host, child = {"mnt": [1, 2], "pid": [1, 3]}, {"mnt": [1, 20], "pid": [1, 30]}
        for identities, group_id, allowed in (([host, host], [1, 42], False),
                                               ([child, host], [1, 43], False),
                                               ([child, host], [1, 42], True)):
            witness = self.directory / f"witness-{group_id[1]}-{identities[0]['mnt'][1]}"
            witness.mkdir(mode=0o700)
            with patch.object(broker, "_clone_into", return_value=500), \
                    patch.object(broker.select, "select", side_effect=ready), \
                    patch.object(broker.os, "read", return_value=b"R"), \
                    patch.object(broker.os, "write") as gate, \
                    patch.object(broker, "_namespace_ids", side_effect=identities), \
                    patch.object(broker, "_cgroup_id", return_value=group_id), \
                    patch.object(broker, "_start_ticks", return_value=456), \
                    patch.object(broker, "_boot", return_value="test-boot"):
                args = (cg, [1, 42], self.root, [], ["/guard"], [], witness,
                        self.request, time.monotonic() + 5, lambda: None)
                if not allowed:
                    with self.assertRaisesRegex(broker.LaunchHold, "namespace_or_cgroup_identity"):
                        broker._spawn_isolated(*args)
                    gate.assert_not_called()
                    self.assertFalse((witness / "started.json").exists())
                else:
                    process, started = broker._spawn_isolated(*args)
                    process.stdin.close()
                    self.assertEqual(started["namespaces"], child)
                    self.assertEqual(broker._record(witness / "released.json", self.uid)["started_sha256"],
                                     broker._digest(started))
                    gate.assert_called_once()

    def test_namespace_failure_is_recorded_without_releasing_command(self):
        witness = self.directory / "startup-witness"
        witness.mkdir(mode=0o700)
        cg = self.root / "cgroup"
        cg.mkdir()
        startup = {}
        error = broker._startup_error(broker.LaunchHold("neutral_launch_mount_syscall:429:18"),
                                      "namespace-init")
        self.assertEqual((error["syscall"], error["errno"]), (429, 18))
        private = broker._startup_error(PermissionError(13, "private", "/private/path"), "prepare")
        self.assertEqual(private, {"phase": "prepare", "kind": "PermissionError", "errno": 13})
        with (patch.object(broker, "_clone_into", return_value=500),
              patch.object(broker, "_start_ticks", return_value=456),
              patch.object(broker.select, "select", return_value=([1], [], [])),
              patch.object(broker.os, "read", side_effect=[b"E", broker._wire(error)]),
              patch.object(broker.os, "write") as gate):
            with self.assertRaisesRegex(broker.LaunchHold, "namespace_readiness"):
                broker._spawn_isolated(cg, [1, 42], self.root, [], ["/guard"], [], witness,
                                       self.request, time.monotonic() + 5, lambda: None,
                                       startup=startup)
        self.assertEqual(startup["namespace_error"], error)
        self.assertEqual(startup["pid"], 500)
        self.assertFalse((witness / "released.json").exists())
        self.assertFalse((witness / "terminal.json").exists())
        gate.assert_not_called()

    def test_openat2_never_falls_back_on_mount_or_kernel_refusal(self):
        from types import SimpleNamespace
        tree = self.root / "tree"
        tree.mkdir()
        file = tree / "payload"
        file.touch(mode=0o600)
        fd = os.open(tree, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        for error in (errno.EXDEV, errno.ENOSYS):
            def syscall(number, directory, name, how, size):
                self.assertEqual((number, directory, name), (437, fd, b"payload"))
                self.assertTrue(how._obj.resolve & 0x01)
                ctypes.set_errno(error)
                return -1
            with patch.object(broker, "_linux", return_value=SimpleNamespace(syscall=syscall)):
                with self.assertRaisesRegex(broker.LaunchHold, f"openat2:{error}"):
                    broker._open_beneath(fd, "payload", os.O_RDONLY)

    def test_simulated_same_device_bind_blocks_projection_and_return(self):
        source = self.root / "tree"
        source.mkdir(mode=0o700)
        leaf = source / "bound"
        leaf.mkdir(mode=0o700)
        self.assertEqual(source.stat().st_dev, leaf.stat().st_dev)
        directory = self.root / "return"
        directory.mkdir(mode=0o700)
        fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        open_component = broker._open_beneath
        def boundary(parent, name, flags):
            if name == "bound":
                raise broker.LaunchHold(f"role_launch_projection_openat2:{errno.EXDEV}")
            return open_component(parent, name, flags)
        with patch.object(broker, "_open_beneath", side_effect=boundary):
            with self.assertRaisesRegex(broker.LaunchHold, "projection_openat2"):
                broker.projection_digest(source, custody_root=self.root)
            with patch.object(broker, "_new_role_dir", side_effect=lambda path, *_: path.mkdir(mode=0o700)):
                with self.assertRaisesRegex(broker.LaunchHold, "projection_openat2"):
                    broker._return_outputs({"controller_uid": self.uid, "controller_gid": os.getgid()},
                                           {"uid": self.uid, "max_projection_bytes": 100}, directory, fd, fd)
            self.assertFalse((directory / "returned/manifest.json").exists())

    def test_root_policy_requires_exact_isolation_contract(self):
        for value in (None, "uif-role-attempt-namespaces/0"):
            cfg = {"schema": broker.SCHEMA, "attempt_isolation": value}
            with patch.object(broker, "_dir"), patch.object(broker, "_record", return_value=cfg):
                with self.assertRaisesRegex(broker.LaunchHold, "role_launch_policy"):
                    broker._policy()

    def test_projection_anchor_checks_parent_and_all_ancestors(self):
        root = self.root / "qualified"
        source = root / "ancestor" / "parent" / "projection"
        source.mkdir(mode=0o700, parents=True)
        root.chmod(0o700)
        leaf = source / "file"
        leaf.touch(mode=0o600)
        real_beneath = broker._open_beneath
        for crossing in ("ancestor", "parent", "projection"):
            seen = []
            def refuse(fd, name, flags):
                seen.append(name)
                if name == crossing:
                    raise broker.LaunchHold(f"simulated_mount_crossing:{errno.EXDEV}")
                return real_beneath(fd, name, flags)
            with patch.object(broker, "_open_beneath", side_effect=refuse):
                with self.assertRaisesRegex(broker.LaunchHold, "simulated_mount_crossing"):
                    broker.projection_digest(source, custody_root=root)
            self.assertEqual(seen[-1], crossing)
            self.assertNotIn(root.name, seen)
        with patch.object(broker, "_open_beneath", wraps=real_beneath) as checked, \
                patch.object(broker.os, "open", wraps=os.open) as ordinary:
            self.assertTrue(broker._sha(broker.projection_digest(source, custody_root=root)))
            self.assertEqual([call.args[1] for call in checked.call_args_list],
                             ["ancestor", "parent", "projection", "file"])
            # The explicitly qualified root is the only plain path open.
            self.assertEqual([call.args[0] for call in ordinary.call_args_list], [root])

    def test_return_descriptors_check_containers_below_role_anchor(self):
        root = self.root / "role"
        workspace = root / "slot" / "workspaces" / "UIF-88"
        attempt = root / "slot" / "attempts" / "UIF-88"
        workspace.mkdir(mode=0o700, parents=True)
        attempt.mkdir(mode=0o700, parents=True)
        root.chmod(0o700)
        real_beneath = broker._open_beneath
        for path, container in ((workspace, "workspaces"), (attempt, "attempts")):
            def refuse(fd, name, flags):
                if name == container:
                    raise broker.LaunchHold(f"simulated_mount_crossing:{errno.EXDEV}")
                return real_beneath(fd, name, flags)
            with patch.object(broker, "_open_beneath", side_effect=refuse):
                with self.assertRaisesRegex(broker.LaunchHold, "simulated_mount_crossing"):
                    broker._open_projection_root(path, custody_root=root, custody_uid=self.uid)
        with self.assertRaisesRegex(broker.LaunchHold, "outside_custody"):
            broker.projection_digest(self.root, custody_root=root)

    def test_clone3_failure_has_no_fallback(self):
        from types import SimpleNamespace
        class Syscall:
            def __call__(inner, number, pointer, size):
                args = pointer._obj
                self.assertEqual(number, 435)
                self.assertEqual(args.flags, 0x00020000 | 0x20000000 | 0x200000000)
                self.assertEqual(size, ctypes.sizeof(broker._CloneArgs))
                ctypes.set_errno(errno.ENOSYS)
                return -1
        with patch.object(broker, "_linux", return_value=SimpleNamespace(syscall=Syscall())), \
                patch.object(broker.os, "fork") as fork:
            with self.assertRaisesRegex(broker.LaunchHold, f"clone3:{errno.ENOSYS}"):
                broker._clone_into(self.root)
            fork.assert_not_called()

    def test_projection_rejects_hardlinks(self):
        source, target = self.root / "source", self.root / "target"
        source.mkdir(mode=0o700)
        target.mkdir(mode=0o700)
        one = source / "one"
        one.touch(mode=0o600)
        os.link(one, source / "two")
        fd = broker._open_projection_root(source, custody_root=self.root, custody_uid=self.uid)
        self.addCleanup(os.close, fd)
        with self.assertRaisesRegex(broker.LaunchHold, "file_custody"):
            broker._project(fd, target, self.uid, self.uid, os.getgid())

    def test_projection_digest_refuses_links_and_budget(self):
        tree = self.root / "tree"
        tree.mkdir(mode=0o700)
        file = tree / "payload"
        file.write_bytes(b"bounded")
        file.chmod(0o600)
        self.assertEqual(broker.projection_digest(tree, custody_root=self.root), broker._digest(
            [["payload", "file", sha256(b"bounded").hexdigest()]]))
        with self.assertRaisesRegex(broker.LaunchHold, "byte_budget"):
            broker.projection_digest(tree, max_bytes=1, custody_root=self.root)
        (tree / "alias").symlink_to(file)
        with self.assertRaisesRegex(broker.LaunchHold, "projection_type"):
            broker.projection_digest(tree, custody_root=self.root)

    def test_reserialized_intent_cannot_reserve_same_lease_again(self):
        original, _, lock = self.originals()
        intent_path = self.directory / "intent.json"
        intent = json.loads(intent_path.read_bytes())
        alternate_bytes = json.dumps(intent, indent=2).encode()
        alternate_key = replace(original.key, intent_sha256=sha256(alternate_bytes).hexdigest())
        alternate = replace(original, key=alternate_key, slot=broker.slot_for_lease(alternate_key.lease_id))
        self.assertEqual(original.slot, alternate.slot)
        self.assertNotEqual(original.digest, alternate.digest)
        self._put("launch.json", asdict(original))

        # Use real slot, operation lock, lease and immutable reservation checks.
        # Treat only the disposable root witness directory as root custody.
        record, directory = broker._record, broker._dir
        with lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch.object(broker, "_boot", return_value="fixture-boot"), \
                    patch.object(broker, "_cgroup", return_value=self.root / "fixture-cgroup"), \
                    patch.object(broker.os, "fork", return_value=1234) as fork, \
                    patch.object(broker.time, "monotonic", side_effect=[10, 19]), \
                    patch.object(broker, "_record", side_effect=lambda path, uid, *rest:
                                 record(path, self.uid if uid == 0 else uid, *rest)), \
                    patch.object(broker, "_dir", side_effect=lambda path, uid, forbidden:
                                 directory(path, self.uid if uid == 0 else uid, forbidden)):
                self.assertEqual(broker._handle(self.cfg, "start", original.slot).state, "uncertain")
                self.assertEqual(broker._handle(self.cfg, "start", original.slot).state, "prepared")
                witness = self.directory / ".role-helper"
                reservation = (witness / "reservation.json").read_bytes()
                self.assertEqual(json.loads(reservation)["request"], original.digest)
                # Simulate an integrator accidentally resealing the same
                # logical attempt with changed bytes after the original start.
                intent_path.write_bytes(alternate_bytes)
                (self.directory / "launch.json").unlink()
                self._put("launch.json", asdict(alternate))
                (self.directory / "fence.json").unlink()
                self._put("fence.json", {"schema": broker.SCHEMA, "request_sha256": alternate.digest,
                                        "lease": alternate.key.lease_id,
                                        "binding_sha256": broker._digest(intent["worker_binding"])})
                for action in ("start", "observe", "cancel"):
                    outcome = broker._handle(self.cfg, action, alternate.slot)
                    self.assertEqual(outcome.state, "uncertain")
                    self.assertFalse(outcome.release_safe)
                fork.assert_called_once()
                self.assertEqual((witness / "reservation.json").read_bytes(), reservation)
                self.assertFalse((witness / "cancel.json").exists())

    @contextmanager
    def cancellation_fixture(self):
        request, _, lock = self.originals()
        self._put("launch.json", asdict(request))
        record, read, directory = broker._record, broker._read, broker._dir
        def owner(uid):
            return self.uid if uid == 0 else uid
        with lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch.object(broker, "_boot", return_value="fixture-boot"), \
                    patch.object(broker, "_cgroup", return_value=self.root / "fixture-cgroup"), \
                    patch.object(broker.os, "fork", return_value=1234) as fork, \
                    patch.object(broker, "_record", side_effect=lambda path, uid, *rest:
                                 record(path, owner(uid), *rest)), \
                    patch.object(broker, "_read", side_effect=lambda path, uid, *rest, **kwargs:
                                 read(path, owner(uid), *rest, **kwargs)), \
                    patch.object(broker, "_dir", side_effect=lambda path, uid, forbidden:
                                 directory(path, owner(uid), forbidden)):
                yield request, fork

    def test_cancel_before_start_commits_terminal_and_excludes_fork(self):
        with self.cancellation_fixture() as (request, fork):
            witness = self.directory / ".role-helper"
            self.assertEqual(broker._handle(self.cfg, "observe", request.slot).state, "absent")
            outcome = broker._handle(self.cfg, "cancel", request.slot)
            self.assertTrue(outcome.release_safe)
            self.assertEqual((outcome.state, outcome.exit_code), ("refused-before-fork", None))
            terminal = (witness / "terminal.json").read_bytes()
            self.assertEqual(outcome.receipt_sha256, sha256(terminal).hexdigest())
            self.assertEqual(json.loads(terminal)["decision"], broker.CANCEL_BEFORE_START)
            self.assertFalse((witness / "cgroup.json").exists())
            # The helper never releases Pool capacity. Exercise the controller's
            # subsequent release using the real disposable Pool when supplied.
            with sqlite3.connect(self.pool) as db:
                self.assertEqual(db.execute("SELECT status FROM leases").fetchone(), ("running",))
            if self.pool_api is not None:
                self.pool_api.finish(request.key.lease_id, outcome.release_safe, "cancelled")
            for action in ("observe", "start", "cancel"):
                self.assertEqual(broker._handle(self.cfg, action, request.slot), outcome)
            changed = replace(request, request_sha256="0" * 64)
            (self.directory / "launch.json").unlink()
            self._put("launch.json", asdict(changed))
            for action in ("observe", "start", "cancel"):
                self.assertEqual(broker._handle(self.cfg, action, request.slot).state, "uncertain")
            self.assertEqual((witness / "terminal.json").read_bytes(), terminal)
            fork.assert_not_called()

    def _assert_reservation_order(self, winner, loser):
        # Interleave the calls at the atomic publication boundary. No child
        # or cgroup is created.
        with self.cancellation_fixture() as (request, fork):
            witness = self.directory / ".role-helper"
            publish = broker._publish_reservation
            observed = []
            inside = False
            def collide(path, value):
                nonlocal inside
                if path == witness and not inside:
                    inside = True
                    observed.append(broker._handle(self.cfg, winner, request.slot))
                return publish(path, value)
            with patch.object(broker, "_publish_reservation", side_effect=collide), \
                    patch.object(broker.time, "monotonic", side_effect=[10, 19]):
                result = broker._handle(self.cfg, loser, request.slot)
            self.assertEqual(len(observed), 1)
            if winner == "start":
                self.assertFalse(result.release_safe)
                self.assertFalse((witness / "terminal.json").exists())
                self.assertTrue((witness / "cancel.json").is_file())
                fork.assert_called_once()
            else:
                self.assertTrue(result.release_safe)
                self.assertEqual(result, observed[0])
                fork.assert_not_called()

    def test_start_wins_race_and_cancel_observes(self):
        self._assert_reservation_order("start", "cancel")

    def test_single_cancel_after_reservation_becomes_visible_is_preserved(self):
        with self.cancellation_fixture() as (request, fork):
            witness = self.directory / ".role-helper"
            sync = broker._sync
            observed = []
            def cancel_when_visible(path):
                if path == self.directory and witness.exists() and not observed:
                    # Prevent recursion when cancel itself syncs the parent.
                    observed.append(None)
                    observed[0] = broker._handle(self.cfg, "cancel", request.slot)
                return sync(path)
            def watcher_fork():
                self.assertEqual(len(observed), 1)
                self.assertEqual(broker._record(witness / "cancel.json", self.uid),
                                 {"request": request.digest})
                return 1234
            with patch.object(broker, "_sync", side_effect=cancel_when_visible), \
                    patch.object(broker.time, "monotonic", side_effect=[10, 19]):
                fork.side_effect = watcher_fork
                outcome = broker._handle(self.cfg, "start", request.slot)
            self.assertFalse(outcome.release_safe)
            self.assertFalse(observed[0].release_safe)
            self.assertFalse((witness / "terminal.json").exists())
            fork.assert_called_once()
            with sqlite3.connect(self.pool) as db:
                self.assertEqual(db.execute("SELECT status FROM leases").fetchone(), ("running",))

    def test_cancel_wins_race_and_start_does_not_fork(self):
        self._assert_reservation_order("cancel", "start")

    def test_interrupted_cancel_and_unrecorded_cgroup_hold(self):
        with self.cancellation_fixture() as (request, fork):
            witness = self.directory / ".role-helper"
            cgroup = self.root / "fixture-cgroup"
            cgroup.mkdir()
            self.assertEqual(broker._handle(self.cfg, "cancel", request.slot).state, "uncertain")
            self.assertFalse(witness.exists())
            cgroup.rmdir()
            create = broker._create
            def interrupt(path, value):
                if path.name == "terminal.json":
                    raise OSError("fixture interrupted terminal write")
                return create(path, value)
            with patch.object(broker, "_create", side_effect=interrupt):
                with self.assertRaises(OSError):
                    broker._handle(self.cfg, "cancel", request.slot)
            self.assertTrue((witness / "reservation.json").is_file())
            for action in ("observe", "start", "cancel"):
                outcome = broker._handle(self.cfg, action, request.slot)
                self.assertEqual(outcome.state, "uncertain")
                self.assertFalse(outcome.release_safe)
            self.assertFalse((witness / "terminal.json").exists())
            fork.assert_not_called()

    def test_slot_is_derived_from_lease_and_request_keeps_full_identity(self):
        variant = replace(self.request, key=replace(self.key, epoch="other-epoch"))
        self.assertEqual(variant.slot, self.request.slot)
        self.assertNotEqual(variant.digest, self.request.digest)
        with self.assertRaisesRegex(broker.LaunchHold, "role_launch_request"):
            replace(self.request, key=replace(self.key, lease_id="different-lease"))
        with self.assertRaisesRegex(broker.LaunchHold, "role_launch_request"):
            replace(self.request, slot=broker._digest(asdict(self.key)))
        with self.assertRaisesRegex(broker.LaunchHold, "role_launch_request"):
            replace(self.request, slot="../control")


class NeutralBrokerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.uid = os.getuid()
        self.cpus = (min(os.sched_getaffinity(0)),)
        self.control, self.runner, self.execution, self.locks = (
            self.root / name for name in ("controller", "runner", "execution", "locks"))
        for item in (self.control, self.runner, self.execution, self.locks):
            item.mkdir(mode=0o700)
        self.source = self.execution / "approved"
        self.source.mkdir(mode=0o700)
        self.workdir = self.source / "write"
        self.workdir.mkdir(mode=0o700)
        self.capacity = None

    def fixture(self):
        resources = dict(workers=2, overhead_processes=1, cpu_millis=100,
                         memory_bytes=1048576, output_bytes=1024, io_slots=1,
                         read_roots=[str(self.source)], write_roots=[str(self.workdir)])
        body = dict(protocol="foo-user-exec-v1", workspace="foo", job_id="ordinary-test",
                    descriptor_sha256="a"*64, mode="write", timeout_seconds=1200,
                    resources=resources, command="printf '%s' 'ordinary command fixture'")
        binding = dict(namespace=broker.NEUTRAL_NAMESPACE, workspace="foo", job_id=body["job_id"],
                       descriptor_sha256=body["descriptor_sha256"], request_sha256=broker._digest(body),
                       command_sha256=sha256(body["command"].encode()).hexdigest(), mode=body["mode"],
                       timeout_seconds=body["timeout_seconds"], resources=resources,
                       capacity_policy_sha256="b"*64, runner_sha256="c"*64)
        work = {**body, "action": "run", "request_sha256": binding["request_sha256"]}
        lease = "neutral-" + broker._digest([broker.NEUTRAL_NAMESPACE, "foo", body["job_id"]])
        policy = {"host": {"cpu_reserve": 0, "memory_reserve_bytes": 0, "disk_reserve_bytes": 0},
                  "symphony": {"max_concurrent_agents": 2, "cpu_budget_millis": 1000,
                               "memory_budget_bytes": 2097152, "io_slots": 2}}
        pool_binding = dict(mode="foo-neutral-user", owner="controller", epoch="1",
                            write_roots=resources["write_roots"], request=binding,
                            resources={k: resources[k] for k in
                                       ("cpu_millis", "memory_bytes", "output_bytes", "io_slots")},
                            registration_sha256="d"*64, policy_fingerprint=broker._digest(policy))
        job = self.runner / binding["job_id"]
        job.mkdir(mode=0o700)
        broker._create(job / "intent.json", binding)
        broker._create(job / "request.json", work)
        slot = broker.slot_for_lease(lease)
        request = broker.NeutralLaunchRequest(broker.NEUTRAL_SCHEMA, binding, slot, broker._digest(binding),
                                              broker._digest(work), broker._digest(pool_binding), self.cpus)
        directory = self.control / slot
        directory.mkdir(mode=0o700)
        broker._create(directory / "launch.json", asdict(request))
        broker._create(directory / "request.json", work)
        broker._create(directory / "fence.json", dict(schema=broker.SCHEMA, request_sha256=request.digest,
                       lease=lease, binding_sha256=broker._digest(pool_binding)))
        pooldir = self.root / "pool"
        pooldir.mkdir(mode=0o700)
        db = pooldir / "state.sqlite"
        if filename := os.environ.get("UIF_NEUTRAL_HELPER_TEST_CAPACITY"):
            spec = importlib.util.spec_from_file_location("fixture_neutral_capacity", filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.capacity = module.Pool(pooldir, policy)
            self.capacity.admit_neutral(lease, pool_binding, dict(cpu_millis=1000,
                available_memory_bytes=2097152, available_disk_bytes=2048))
        else:
            db.touch(mode=0o600)
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE leases (id TEXT, binding TEXT, fingerprint TEXT, status TEXT)")
                connection.execute("INSERT INTO leases VALUES (?, ?, ?, 'running')",
                                   (lease, broker._wire(pool_binding).decode(), request.pool_binding_sha256))
        role = dict(uid=32145, gid=32146, store_id="registered-store", cpu_ids=list(self.cpus),
                    runner_uid=self.uid, runner_gid=os.getgid(), runner_control=str(self.runner),
                    execution_uid=self.uid, execution_root=str(self.execution),
                    descriptor_sha256=binding["descriptor_sha256"],
                    capacity_policy_sha256=binding["capacity_policy_sha256"],
                    runner_sha256=binding["runner_sha256"],
                    registration_sha256=pool_binding["registration_sha256"],
                    policy_fingerprint=pool_binding["policy_fingerprint"], protected_roots=[],
                    timeout_seconds=1800, limits={key: resources[key] for key in broker.NEUTRAL_LIMITS},
                    read_roots=[str(self.source)], write_roots=[str(self.source)],
                    bash={"path": "/usr/bin/bash", "target": "/bin/bash", "sha256": "1"*64},
                    setpriv={"path": "/usr/bin/setpriv", "target": "/usr/bin/setpriv", "sha256": "2"*64},
                    runtime_files=[{"path": "/usr/lib/fixture", "target": "/lib/fixture", "sha256": "3"*64}])
        cfg = dict(controller_uid=self.uid, controller_root=str(self.control), operation_locks=str(self.locks),
                   pool_database=str(db), cgroup_root=str(self.root / "cgroups"), roles={"neutral": role})
        lock = self.locks / (broker._digest([role["store_id"], lease]) + ".lock")
        lock.touch(mode=0o600)
        return request, directory, work, role, cfg, lock, job

    def test_admitted_command_requires_exact_pool_intent_cpu_and_fence(self):
        request, directory, work, role, cfg, lock, job = self.fixture()
        with self.assertRaisesRegex(broker.LaunchHold, "fence_not_held"):
            broker._check_original(cfg, directory, request)
        with lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(broker._check_original(cfg, directory, request)[0], work)
            changed = replace(request, cpu_ids=(self.cpus[0] + 1,))
            with self.assertRaisesRegex(broker.LaunchHold, "cpu_eligibility"):
                broker._check_original(cfg, directory, changed)
            with sqlite3.connect(cfg["pool_database"]) as db:
                db.execute("UPDATE leases SET status='cancel_requested'")
            with self.assertRaisesRegex(broker.LaunchHold, "neutral_launch_lease"):
                broker._check_original(cfg, directory, request)
            self.assertEqual(broker._check_neutral(cfg, directory, request, cancelling=True)[0], work)
            with sqlite3.connect(cfg["pool_database"]) as db:
                db.execute("UPDATE leases SET status='finished'")
            with self.assertRaisesRegex(broker.LaunchHold, "neutral_launch_lease"):
                broker._check_original(cfg, directory, request)

    def test_start_requires_byte_exact_original_runner_request(self):
        request, directory, work, _, cfg, lock, job = self.fixture()
        original = job / "request.json"
        original.unlink()
        with lock.open("rb") as held, patch.object(broker, "_publish_reservation") as reserve, \
                patch.object(broker, "_cgroup", return_value=self.root / "absent-cgroup"), \
                patch.object(broker.os, "fork", side_effect=AssertionError("unexpected fork")) as fork:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.subTest("missing original"), self.assertRaisesRegex(
                    broker.LaunchHold, "neutral_launch_original_request"):
                broker._handle(cfg, "start", request.slot)
            broker._create(original, {**work, "command": "printf different"})
            with self.subTest("different command"), self.assertRaisesRegex(
                    broker.LaunchHold, "neutral_launch_original_request"):
                broker._handle(cfg, "start", request.slot)
            original.write_bytes(broker._wire(work) + b"\n")
            self.assertEqual(broker._json(original.read_bytes()), work)
            with self.subTest("same object with different bytes"), self.assertRaisesRegex(
                    broker.LaunchHold, "neutral_launch_original_request"):
                broker._handle(cfg, "start", request.slot)
            reserve.assert_not_called()
            fork.assert_not_called()
            original.write_bytes((directory / "request.json").read_bytes())
            self.assertEqual(broker._check_original(cfg, directory, request)[0], work)

    def test_plan_keeps_exact_shell_scope_and_read_write_overlap(self):
        request, directory, work, role, cfg, lock, job = self.fixture()
        from contextlib import ExitStack
        with (ExitStack() as stack,
              patch.object(broker, "_installed", side_effect=lambda path, *_a, **_k: Path(path)),
              patch.object(broker, "_mount_call", side_effect=lambda _, fd, *_args: os.dup(fd)) as detach):
            runtime, mounts, command, cwd, env = broker._neutral_plan(cfg, role, work, stack)
            self.assertEqual([call.args[0] for call in detach.call_args_list], [428, 428])
            self.assertNotEqual([call.args[1] for call in detach.call_args_list], [entry[0] for entry in mounts])
            self.assertEqual(command[-5:], ["/bin/bash", "--noprofile", "--norc", "-c", work["command"]])
            self.assertIn("--no-new-privs", command)
            self.assertEqual(cwd, str(self.execution))
            self.assertEqual(env["UIF_COMPUTATION_SUBPROCESS_CEILING"], "2")
            self.assertEqual([(path, ro) for _, path, _, ro in mounts],
                             [(self.source, True), (self.workdir, False)])
            self.assertEqual({item[1] for item in runtime}, {Path("/bin/bash"), Path("/usr/bin/setpriv"), Path("/lib/fixture")})
            role["protected_roots"] = [str(self.workdir)]
            with self.assertRaisesRegex(broker.LaunchHold, "mount_boundary"):
                broker._neutral_plan(cfg, role, work, stack)

    def test_no_fork_original_evidence_waits_for_runner_receipt(self):
        request, directory, work, role, cfg, lock, job = self.fixture()
        cgroup = self.root / "absent-cgroup"
        record, read, check_dir = broker._record, broker._read, broker._dir
        local = lambda uid: self.uid if uid == 0 else uid
        with lock.open("rb") as held, patch.object(broker, "_boot", return_value="fixture-boot"), \
                patch.object(broker, "_cgroup", return_value=cgroup), patch.object(broker.os, "fork") as fork, \
                patch.object(broker, "_record", side_effect=lambda path, uid, *args: record(path, local(uid), *args)), \
                patch.object(broker, "_read", side_effect=lambda path, uid, *args, **kw: read(path, local(uid), *args, **kw)), \
                patch.object(broker, "_dir", side_effect=lambda path, uid, mode: check_dir(path, local(uid), mode)):
            fcntl.flock(held, fcntl.LOCK_EX)
            if self.capacity is not None:
                self.capacity.cancel(request.key.lease_id)
            stopped = broker._handle(cfg, "cancel", request.slot)
            self.assertEqual((stopped.state, stopped.outcome), ("refused-before-fork", "cancelled"))
            self.assertTrue(stopped.containment_stopped)
            self.assertFalse(stopped.release_safe)
            self.assertIsNone(stopped.stopped_evidence)
            receipt = dict(request.binding, lease_id=request.key.lease_id, status="cancelled",
                           child_tree_gone=True, exit_code=None)
            receipt_path = job / "receipt.json"
            for reconcile in ({}, {"reconcile_required": True}, {"reconcile_required": 0}):
                with self.subTest(reconcile=reconcile):
                    broker._create(receipt_path, {**receipt, **reconcile})
                    unsettled = broker._handle(cfg, "observe", request.slot)
                    self.assertTrue(unsettled.containment_stopped)
                    self.assertFalse(unsettled.release_safe)
                    self.assertIsNone(unsettled.stopped_evidence)
                receipt_path.unlink()
            receipt["reconcile_required"] = False
            broker._create(receipt_path, receipt)
            observed = broker._handle(cfg, "observe", request.slot)
            self.assertTrue(observed.release_safe)
            self.assertEqual(observed.stopped_evidence["runner_receipt_sha256"], broker._digest(receipt))
            self.assertEqual(observed.stopped_evidence["trusted_terminal"]["decision"], broker.CANCEL_BEFORE_START)
            self.assertEqual(observed.stopped_evidence["reservation"]["request"], request.digest)
            self.assertEqual(broker._handle(cfg, "start", request.slot), observed)
            fork.assert_not_called()

    def test_reaped_observation_requires_kernel_and_exact_runner_result(self):
        request, directory, work, role, cfg, lock, job = self.fixture()
        witness = directory / ".role-helper"
        witness.mkdir(mode=0o700)
        group = [1, 42]
        started = dict(pid=500, start_ticks=123, boot="fixture-boot", cgroup=group,
                       namespaces={"mnt": [1, 2], "pid": [1, 3]}, request=request.digest,
                       isolation=broker.ATTEMPT_ISOLATION)
        record, read, check_dir = broker._record, broker._read, broker._dir
        local = lambda uid: self.uid if uid == 0 else uid
        with patch.object(broker, "_boot", return_value="fixture-boot"), \
                patch.object(broker, "_cgroup", return_value=self.root), \
                patch.object(broker, "_cgroup_id", return_value=group), patch.object(broker, "_populated") as live, \
                patch.object(broker, "_record", side_effect=lambda path, uid, *args: record(path, local(uid), *args)), \
                patch.object(broker, "_read", side_effect=lambda path, uid, *args, **kw: read(path, local(uid), *args, **kw)), \
                patch.object(broker, "_dir", side_effect=lambda path, uid, mode: check_dir(path, local(uid), mode)):
            broker._create(witness / "reservation.json", broker._reservation(request))
            broker._create(witness / "cgroup.json", {"boot": "fixture-boot", "cgroup": group})
            broker._create(witness / "started.json", started)
            broker._create(witness / "released.json", {"request": request.digest, "started_sha256": broker._digest(started)})
            terminal = dict(state="exited-clean", **started, exit_code=0, outcome="succeeded",
                            log_bytes={"stdout": 8, "stderr": 0}, logs_truncated={"stdout": True, "stderr": False})
            broker._create(witness / "terminal.json", terminal)
            receipt = dict(request.binding, lease_id=request.key.lease_id, status="succeeded",
                           child_tree_gone=True, reconcile_required=False, exit_code=0,
                           log_bytes=terminal["log_bytes"], logs_truncated=terminal["logs_truncated"])
            broker._create(job / "receipt.json", {**receipt, "status": "failed"})
            live.return_value = False
            self.assertFalse(broker._handle(cfg, "observe", request.slot).release_safe)
            (job / "receipt.json").unlink()
            broker._create(job / "receipt.json", receipt)
            live.return_value = True
            self.assertFalse(broker._handle(cfg, "observe", request.slot).containment_stopped)
            live.return_value = False
            observed = broker._handle(cfg, "observe", request.slot)
            self.assertTrue(observed.release_safe)
            self.assertEqual(observed.stopped_evidence["started"], started)
            self.assertEqual(observed.stopped_evidence["trusted_terminal_sha256"], broker._digest(terminal))

    def test_ordinary_logs_keep_original_combined_budget(self):
        from contextlib import ExitStack
        from io import BytesIO
        from types import SimpleNamespace
        with ExitStack() as stack:
            readers = {}
            for name, payload in (("stdout", b"abc"), ("stderr", b"def")):
                read, write = os.pipe()
                readers[name] = stack.enter_context(os.fdopen(read, "rb"))
                with os.fdopen(write, "wb") as stream:
                    stream.write(payload)
            child = SimpleNamespace(stdin=BytesIO(), poll=lambda: 0)
            work = {"timeout_seconds": 5, "resources": {"output_bytes": 4}}
            role = {"runner_control": str(self.runner), "memory_reserve_bytes": 0,
                    "disk_reserve_bytes": 0}
            with patch.object(broker.os, "statvfs", return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)), \
                    patch.object(Path, "read_text", return_value="MemAvailable: 1024 kB"), \
                    patch.object(broker, "_kill") as kill:
                outcome, logs, truncated = broker._neutral_monitor(child, self.root, readers, self.root, work, role)
            self.assertEqual(outcome, "succeeded")
            self.assertEqual(sum(map(len, logs.values())), 4)
            self.assertTrue(any(truncated.values()))
            kill.assert_not_called()

    def test_neutral_watcher_publishes_only_after_original_reap(self):
        from types import SimpleNamespace
        request, directory, work, role, cfg, lock, job = self.fixture()
        witness = directory / ".role-helper"
        witness.mkdir(mode=0o700)
        group = self.root / "simulated-cgroup"
        mkdir = Path.mkdir
        def create(path, *args, **kwargs):
            mkdir(path, *args, **kwargs)
            if path == group:
                for name in ("cpu.max", "memory.max", "pids.max"):
                    (path / name).touch()
        child = SimpleNamespace(returncode=0)
        started = dict(pid=500, start_ticks=123, boot="fixture", cgroup=[1, 42],
                       request=request.digest, isolation=broker.ATTEMPT_ISOLATION,
                       namespaces={"mnt": [1, 2], "pid": [1, 3]})
        def launch(cg, group_id, root, entries, argv, streams, launch_witness,
                   original, deadline, check_fence, **kwargs):
            self.assertEqual((cg, group_id, original), (group, [1, 42], request))
            self.assertEqual(kwargs["cpu_ids"], self.cpus)
            self.assertEqual(argv[-1], work["command"])
            self.assertEqual((cg / "pids.max").read_text(), "4")
            check_fence()
            return child, started
        with lock.open("rb") as held, patch.object(broker, "_cgroup", return_value=group),                 patch.object(Path, "mkdir", create), patch.object(broker, "_cgroup_id", return_value=[1, 42]),                 patch.object(broker, "_boot", return_value="fixture"),                 patch.object(broker.ctypes, "CDLL", return_value=SimpleNamespace(prctl=lambda *_: 0)),                 patch.object(broker, "_installed", side_effect=lambda path, *_a, **_k: Path(path)),                 patch.object(broker, "_mount_call", side_effect=lambda _, fd, *_args: os.dup(fd)),                 patch.object(broker, "_spawn_isolated", side_effect=launch),                 patch.object(broker, "_neutral_monitor", return_value=(
                    "succeeded", {"stdout": b"fixture", "stderr": b""}, {"stdout": False, "stderr": False})),                 patch.object(broker, "_populated", return_value=False),                 patch.object(broker, "_wait_empty", return_value=False) as reaped,                 patch.object(broker, "_neutral_logs") as logs:
            fcntl.flock(held, fcntl.LOCK_EX)
            _, limits = broker._check_original(cfg, directory, request)
            broker._watch(cfg, directory, witness, request, role, work, limits, time.monotonic() + 5)
            reaped.assert_called_once_with(group)
            logs.assert_not_called()
        self.assertFalse((witness / "terminal.json").exists())
        with sqlite3.connect(cfg["pool_database"]) as db:
            self.assertEqual(db.execute("SELECT status FROM leases").fetchone(), ("running",))
