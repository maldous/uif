"""Exercise the controller boundary with disposable role-return records."""

import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from uif.controller_launch import _replace_file, prepare_role_launch
from uif.dispatch import _encode, _seal_supervisor_start, _supervisor_identity
from uif.role_broker import LaunchKey, LaunchObservation, projection_digest
from uif.source import file_digest
from uif.supervisor import finish_attempt, run_registered_role_turn


class ControllerLaunchTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.directory = self.root / "attempt"
        self.workspace = self.root / "workspace"
        for path in (self.directory, self.workspace, self.workspace / ".git"):
            path.mkdir(mode=0o700)
        self.put(self.workspace / "source", b"original\n")
        self.put(self.workspace / ".git" / "identity", b"controller\n")
        self.put(self.workspace / ".git" / "HEAD", b"ref: refs/heads/feature/fixture\n")
        self.put(self.workspace / ".git" / "packed-refs", b"fixture refs\n")
        self.admission = self.directory / "admission.json"
        self.intent = self.directory / "intent.json"
        self.request = self.directory / "request.json"
        for path, value in ((self.admission, {}), (self.intent, {}),
                            (self.request, {"prompt": "offline fixture", "timeout_seconds": 2})):
            self.put(path, _encode(value))
        self.key = LaunchKey("coding", "store", "row", "a" * 64,
                             "1765ee92-55d6-4012-a889-95e65555711d", "UIF-900001",
                             "owner", "epoch", "original-op", "initial", file_digest(self.intent), "lease")
        self.calls = []
        self.cfg = dict(controller_root=self.root, role_limits={"coding": {"max_projection_bytes": 100_000}})

    @staticmethod
    def put(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def prepare(self):
        return prepare_role_launch(
            self.cfg, key=self.key, admission_path=self.admission, intent_path=self.intent,
            request_path=self.request, workspace=self.workspace, policy_sha256="b" * 64,
            pool_binding={"owner": "owner"},
        )

    def terminal(self, launch, *, outside=False, changed_head=False, replace_directory=False, replace_file=False, change_directories=False):
        self.calls.append("start")
        slot = self.root / launch.slot
        returned = slot / "returned"
        returned.mkdir(mode=0o700)
        target = returned / "workspace"
        shutil.copytree(slot / "projection", target)
        self.put(target / "source", b"approved fixture output\n")
        self.put(target / ".git" / "identity", b"untrusted Git metadata\n")
        if replace_directory:
            shutil.rmtree(target / "z-directory")
            self.put(target / "z-directory", b"replaces a directory")
        if replace_file:
            (target / "z-file").unlink()
            (target / "z-file").mkdir(mode=0o700)
        if change_directories:
            shutil.rmtree(target / "old-dir")
            (target / "new-dir" / "empty").mkdir(mode=0o700, parents=True)
        if outside:
            self.put(target / "outside", b"held output\n")
        if changed_head:
            self.put(target / ".git" / "HEAD", b"ref: refs/heads/feature/other\n")
        self.put(returned / "stdout.jsonl", b'{"type":"thread.started","thread_id":"fixture"}\n'
                 b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}\n')
        self.put(returned / "stderr.log", b"")
        artifacts = {name: file_digest(returned / name) for name in ("stdout.jsonl", "stderr.log")}
        manifest = returned / "manifest.json"
        self.put(manifest, _encode(dict(workspace_sha256=projection_digest(target, custody_root=self.root), artifacts=artifacts)))
        return LaunchObservation("exited-clean", launch.digest, "f" * 64, 0, file_digest(manifest))

    def run_role(self, launch, *, write_roots=("source",), stop=lambda: False):
        return run_registered_role_turn(self.cfg, launch, directory=self.directory, workspace=self.workspace,
                                        write_roots=write_roots, output_bytes=100, stop=stop)

    def test_original_start_imports_checked_output_and_preserves_git_metadata(self):
        launch = self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(start=self.terminal)
        result = self.run_role(launch)
        self.assertEqual((result["status"], result["session_id"]), ("succeeded", "fixture"))
        self.assertEqual(result["launch_receipt_sha256"], "f" * 64)
        self.assertEqual((self.workspace / "source").read_bytes(), b"approved fixture output\n")
        self.assertEqual((self.workspace / ".git" / "identity").read_bytes(), b"controller\n")
        self.assertEqual(self.calls, ["start"])

    def test_verified_cleanup_with_scope_violation_imports_nothing(self):
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(start=lambda request: self.terminal(request, outside=True))
        result = self.run_role(launch)
        self.assertEqual((result["status"], result["child_tree_gone"]), ("failed", True))
        self.assertEqual((self.workspace / "source").read_bytes(), b"original\n")
        self.assertFalse((self.workspace / "outside").exists())

    def test_changed_returned_head_is_held_without_import(self):
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(start=lambda request: self.terminal(request, changed_head=True))
        result = self.run_role(launch)
        self.assertEqual((result["status"], result["child_tree_gone"]), ("failed", True))
        self.assertEqual((self.workspace / "source").read_bytes(), b"original\n")

    def test_ambiguous_start_only_observes_and_keeps_capacity(self):
        launch = self.prepare()

        def call(action, request):
            self.calls.append(action)
            return LaunchObservation("uncertain", request.digest)

        self.cfg["role_broker"] = SimpleNamespace(start=lambda r: call("start", r),
                                                   observe=lambda r: call("observe", r))
        result = self.run_role(launch)
        pool = SimpleNamespace(finish=lambda *_: self.fail("uncertain role released Pool lease"))
        finish_attempt(pool, launch.key.lease_id, self.directory / "receipt.json", result)
        self.assertEqual(self.calls, ["start", "observe"])
        self.assertFalse(result["child_tree_gone"])

    def test_unsupported_return_shape_leaves_earlier_file_and_artifacts_untouched(self):
        (self.workspace / "z-directory").mkdir(mode=0o700)
        self.put(self.workspace / "z-directory" / "original", b"original")
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(
            start=lambda request: self.terminal(request, replace_directory=True))
        result = self.run_role(launch, write_roots=("source", "z-directory"))
        self.assertEqual((result["status"], result["child_tree_gone"]), ("failed", True))
        self.assertEqual((self.workspace / "source").read_bytes(), b"original\n")
        self.assertEqual((self.workspace / "z-directory" / "original").read_bytes(), b"original")
        self.assertFalse((self.directory / "stdout.jsonl").exists())
        self.assertFalse((self.root / launch.slot / "import-intent.json").exists())

    def test_file_replaced_by_empty_directory_holds_before_any_write(self):
        self.put(self.workspace / "z-file", b"original regular file")
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(
            start=lambda request: self.terminal(request, replace_file=True))
        result = self.run_role(launch, write_roots=("source", "z-file"))
        self.assertEqual((result["status"], result["child_tree_gone"]), ("failed", True))
        self.assertEqual((self.workspace / "source").read_bytes(), b"original\n")
        self.assertEqual((self.workspace / "z-file").read_bytes(), b"original regular file")
        self.assertFalse((self.root / launch.slot / "import-intent.json").exists())

    def test_ordinary_directory_addition_and_removal_match_verified_return(self):
        (self.workspace / "old-dir").mkdir(mode=0o700)
        self.put(self.workspace / "old-dir" / "old-file", b"old regular file")
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(
            start=lambda request: self.terminal(request, change_directories=True))
        result = self.run_role(launch, write_roots=("source", "old-dir", "new-dir"))
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue((self.workspace / "new-dir" / "empty").is_dir())
        self.assertFalse((self.workspace / "old-dir").exists())

    def test_partial_import_keeps_original_evidence_and_lease(self):
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(start=self.terminal)

        def fail_after_replace(target, source, expected):
            _replace_file(target, source, expected)
            raise OSError("injected error after replace")

        with patch("uif.controller_launch._replace_file", side_effect=fail_after_replace):
            result = self.run_role(launch)
        self.assertEqual((result["status"], result["child_tree_gone"]), ("uncertain", True))
        pool = SimpleNamespace(finish=lambda *_: self.fail("partial import released Pool lease"))
        finish_attempt(pool, launch.key.lease_id, self.directory / "receipt.json", result)
        slot = self.root / launch.slot
        self.assertTrue((slot / "import-intent.json").is_file())
        self.assertEqual((slot / "projection" / "source").read_bytes(), b"original\n")
        self.assertEqual((self.workspace / "source").read_bytes(), b"approved fixture output\n")

    def test_prestart_cancel_releases_only_with_exact_helper_no_fork_receipt(self):
        launch = self.prepare()
        calls = []

        def cancel(request):
            calls.append(request)
            return LaunchObservation("refused-before-fork", request.digest, "f" * 64)

        self.cfg["role_broker"] = SimpleNamespace(cancel=cancel)
        result = self.run_role(launch, stop=lambda: True)
        self.assertEqual((result["status"], result["child_started"]), ("cancelled", False))
        self.assertEqual(result["launch_receipt_sha256"], "f" * 64)
        self.assertEqual(calls, [launch])
        finished = []
        pool = SimpleNamespace(finish=lambda *args: finished.append(args))
        finish_attempt(pool, launch.key.lease_id, self.directory / "receipt.json", result)
        self.assertEqual(finished, [(launch.key.lease_id, True, "cancelled")])

    def test_stop_with_absent_or_unverified_original_holds_even_on_fresh_launch(self):
        launch = self.prepare()
        for state, digest in (("absent", launch.digest), ("uncertain", launch.digest),
                              ("refused-before-fork", "a" * 64)):
            with self.subTest(state=state):
                self.cfg["_fresh_role_launch"] = launch
                self.cfg["role_broker"] = SimpleNamespace(
                    cancel=lambda request: LaunchObservation(state, digest, "f" * 64),
                    observe=lambda request: LaunchObservation("uncertain", request.digest),
                )
                result = self.run_role(launch, stop=lambda: True)
                self.assertEqual((result["status"], result["child_tree_gone"]), ("uncertain", False))

    def test_retained_launch_only_observes_original(self):
        launch = self.prepare()
        self.cfg["role_broker"] = SimpleNamespace(
            start=lambda _: self.fail("retained launch started"),
            observe=lambda request: LaunchObservation("uncertain", request.digest),
        )
        self.cfg.pop("_fresh_role_launch")
        result = self.run_role(launch)
        self.assertEqual((result["status"], result["child_tree_gone"]), ("uncertain", False))

    def test_supervisor_handoff_is_create_only_and_binds_process_identity(self):
        identity = _supervisor_identity(os.getpid())
        _seal_supervisor_start(self.directory, os.getpid(), self.key.operation_id, self.key.intent_sha256)
        marker = self.directory / "controller-supervisor.json"
        self.assertEqual(marker.read_bytes(), _encode(dict(**identity,
                         operation_id=self.key.operation_id, dispatch_sha256=self.key.intent_sha256)))
        with self.assertRaises(FileExistsError):
            _seal_supervisor_start(self.directory, os.getpid(), self.key.operation_id, self.key.intent_sha256)
