from dataclasses import replace
from io import BytesIO
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

DELIVERY = Path(__file__).resolve().parents[2] / "tools/delivery"
with patch.object(sys, "path", [str(DELIVERY), *sys.path]):
    import install_guarded_idle_bootstrap as bootstrap

guard = bootstrap.guard


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.inputs = root / "prepared"
        self.inputs.mkdir()
        self.stages = root / "stages"
        self.stages.mkdir()
        self.root = root / "controller"
        self.dropin = root / "60-idle.conf"
        self.unit = guard.PROFILES[guard.PROFILE_NAME].unit
        self.enterContext(patch.multiple(guard, ROOT=self.root, PACKETS=self.root,
            STAGES=self.stages, LOCK=self.root / "selector.lock", TOOL_PATH=self.root / "loader.py",
            DROPIN=self.dropin, SELECTOR=self.root / "selection.json", RECEIPTS=self.root / "receipts"))
        self.enterContext(patch.multiple(bootstrap, PREPARED=self.inputs,
            SOURCE=self.inputs / "guarded_service_upgrade.py", BUNDLE=self.inputs / "bundle.tar"))
        self.enterContext(patch.object(bootstrap.os, "getresuid", return_value=(0, 0, 0)))
        self.enterContext(patch.object(guard, "protected_parents", lambda _: None))
        self.enterContext(patch.object(guard, "protected_open", lambda path, _: path.open("rb")))
        self.enterContext(patch.object(bootstrap, "_directory", lambda path, mode: path.mkdir(
            mode=mode, exist_ok=True)))
        self.stopped = self.enterContext(patch.object(guard, "stopped_unit_sha",
                                                      return_value=guard.BOOTSTRAP_PRIOR))
        self.enterContext(patch.object(bootstrap.time, "time", return_value=1000))
        config = guard.encode(dict(schema="uif.stokowski-remote-idle/1", unit=self.unit,
            stage=guard.FIXED_STAGE, origin=guard.HOST, access_credential=guard.ACCESS_FILE,
            native_identity=dict(authority_id="a", session_id="b",
                deployment_sha256="c"*64, policy_sha256="d"*64),
            coordinator_epoch=1, hold_reason="free_quota_unavailable"))
        controller, launch = b"reviewed controller bytes", b"synthetic idle launcher"
        bootstrap.SOURCE.write_bytes(controller)
        with tarfile.open(bootstrap.BUNDLE, "w") as archive:
            for name, raw, mode in (("launch.py", launch, 0o644), (guard.IDLE_CONFIG, config, 0o644),
                                    (guard.INTERPRETER, b"synthetic elf", 0o755)):
                item = tarfile.TarInfo(name)
                item.size, item.mode = len(raw), mode
                archive.addfile(item, BytesIO(raw))
        (self.inputs / "quiescence.json").write_bytes(guard.encode(dict(
            schema="uif.guarded-upgrade-quiescence/1", unit=self.unit,
            parked_unit_sha256=guard.BOOTSTRAP_PRIOR, observed_at=999, inflight=0,
            children_gone=True, unresolved_operation_ids=[])))
        self.approved = bootstrap.BootstrapApproval("a"*64, guard.digest(controller),
            guard.digest(bootstrap.BUNDLE.read_bytes()), guard.digest(launch), guard.digest(config))

    def test_installer_entrypoint_publishes_only_fixed_protected_files(self):
        receipt = bootstrap.install(self.approved)
        self.assertFalse(receipt["service_started"])
        self.assertFalse(receipt["daemon_reload_performed"])
        self.assertTrue(self.dropin.is_file())
        self.assertEqual(0o755, (self.stages / guard.FIXED_STAGE /
                                guard.INTERPRETER).stat().st_mode & 0o777)
        self.assertFalse((self.stages / guard.FIXED_STAGE / guard.MARKER).exists())
        self.assertEqual({"mode": "parked", "stage": guard.PARKED_STAGE},
                         __import__("json").loads(guard.SELECTOR.read_bytes()))
        self.assertEqual(sorted(p.name for p in guard.RECEIPTS.iterdir()),
                         ["private-webhook-bootstrap.intent.json"])
        self.stopped.side_effect = AssertionError("no original parked cat after drop-in stage")
        self.assertEqual(receipt, bootstrap.install(self.approved))

    def test_wrong_source_or_original_refuses_without_new_effect(self):
        with self.assertRaisesRegex(ValueError, "source changed"):
            bootstrap.install(replace(self.approved, controller_sha256="f"*64))
        self.assertFalse(self.dropin.exists())
        bootstrap.install(self.approved)
        with self.assertRaisesRegex(ValueError, "intent differs"):
            bootstrap.install(replace(self.approved, operation_id="f"*64))

    def test_publish_interruption_before_rename_leaves_no_final_and_can_retry(self):
        target = self.inputs / "receipt"
        with patch.object(bootstrap.os, "fsync", side_effect=OSError("file sync failed")):
            with self.assertRaisesRegex(OSError, "file sync"):
                bootstrap._same_file(target, b"complete")
        self.assertFalse(target.exists())
        bootstrap._same_file(target, b"complete")
        self.assertEqual(1, target.stat().st_nlink)
        self.assertEqual(b"complete", target.read_bytes())

    def test_publish_interruption_after_rename_retains_only_original_inode(self):
        import stat
        target = self.inputs / "receipt"
        fsync = bootstrap.os.fsync

        def interrupted(fd):
            if stat.S_ISDIR(bootstrap.os.fstat(fd).st_mode):
                raise OSError("directory sync failed")
            fsync(fd)
        with patch.object(bootstrap.os, "fsync", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "directory sync"):
                bootstrap._same_file(target, b"complete")
        self.assertEqual(1, target.stat().st_nlink)
        self.assertEqual(b"complete", target.read_bytes())
        bootstrap._same_file(target, b"complete")
        with self.assertRaisesRegex(ValueError, "differs"):
            bootstrap._same_file(target, b"other")
