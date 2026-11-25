import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import verifier_shelf_provision as provisioner


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ShelfProvisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        stage = root / "stage"
        self.parent = root / "parent"
        self.parent.mkdir()
        self.path = stage / "release" / "tools" / "ingress.py"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"reviewed fixture")
        pins = {"tools/ingress.py": digest(self.path.read_bytes())}
        raw = provisioner.encode({"schema": "offmenu-detached-verifier-candidate-v1",
                                  "files": {key: {"sha256": value} for key, value in pins.items()}})
        (stage / "release-manifest.json").write_bytes(raw)
        self.profile = provisioner.Profile(stage, self.parent, "test-shelf",
            digest(provisioner.encode(pins)), digest(raw), "tools/ingress.py")

    def run_main(self, op="a"*64):
        with (patch.object(provisioner, "PROFILES", {"offmenu": self.profile}),
              patch.object(provisioner.os, "getresuid", return_value=(0, 0, 0)),
              patch.object(provisioner, "protected_directory", side_effect=lambda p, _:
                           provisioner.os.open(p, provisioner.os.O_RDONLY | provisioner.os.O_DIRECTORY)),
              patch.object(provisioner, "verify_installed") as verify):
            result = provisioner.main(["offmenu", op])
            verify.assert_called_once_with(self.profile)
            return result

    def test_entrypoint_publishes_exact_tree_then_same_original_reads_back(self):
        self.assertEqual(self.run_main(), self.run_main())
        self.assertEqual(b"reviewed fixture", (self.parent / "test-shelf/tools/ingress.py").read_bytes())
        with self.assertRaisesRegex(RuntimeError, "RECONCILIATION"):
            self.run_main("b"*64)

    def test_bad_member_publishes_no_shelf_and_recovers_same_intent(self):
        self.path.write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeError, "DIGEST"):
            self.run_main()
        self.assertFalse((self.parent / "test-shelf").exists())
        self.assertTrue((self.parent / ".test-shelf.intent").is_file())
        self.path.write_bytes(b"reviewed fixture")
        self.run_main()

    def test_unreviewed_existing_target_does_not_get_adopted(self):
        (self.parent / "test-shelf").mkdir()
        with self.assertRaisesRegex(RuntimeError, "RECONCILIATION"):
            self.run_main()
        self.assertFalse((self.parent / ".test-shelf.receipt").exists())

    def test_same_original_can_read_back_after_detached_stage_removed(self):
        import shutil
        self.run_main()
        shutil.rmtree(self.profile.stage)
        self.run_main()

    def test_fixed_launcher_embeds_exact_python_digest_and_refuses_bad_id(self):
        import subprocess
        launcher = SOURCE / "launch_verifier_shelf.bash"
        text = launcher.read_text()
        self.assertIn(digest((SOURCE / "verifier_shelf_provision.py").read_bytes()), text)
        self.assertNotIn("offmenu_verifier_bootstrap", (SOURCE / "verifier_shelf_provision.py").read_text())
        self.assertEqual(64, subprocess.run(
            ["/usr/bin/bash", str(launcher), "not-an-original"], check=False).returncode)
