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

    def test_canonical_flat_release_identity_is_not_descriptive_candidate_digest(self):
        actual = provisioner.PROFILES["offmenu"]
        # Independently read on Foo for all 176 exact protected release members.
        self.assertEqual("c292384551558da7766d6a107dfc2c00dfd589bfa01e622b5c326e2cf26a9f1a",
                         actual.release)
        self.assertEqual("170f0d65ddc957f3704b0ecfb1438b8d814e870ceca7734e4a6b0d74087596b4",
                         actual.manifest_sha256)
        descriptive = "8fbd507239d4c81ac1e2aa4223856ff521f28ebd93d820266a13c331ad55a08f"
        raw = provisioner.encode({"schema": "offmenu-detached-verifier-candidate-v1",
                                  "candidate_release_sha256": descriptive,
                                  "files": {self.profile.ingress: {
                                      "sha256": digest(self.path.read_bytes()), "stage_mode": "0444"}}})
        profile = self.profile._replace(manifest_sha256=digest(raw))
        self.assertEqual({profile.ingress: digest(self.path.read_bytes())},
                         provisioner.manifest_files(raw, profile))
        with self.assertRaisesRegex(RuntimeError, "REVIEWED_SHELF_MANIFEST_MISMATCH"):
            provisioner.manifest_files(raw, profile._replace(release=descriptive))

    def test_side_by_side_profiles_keep_distinct_shelves_and_original_receipts(self):
        import json
        v1_operation, v2_operation = "a" * 64, "b" * 64
        second_stage = self.profile.stage.parent / "second-stage"
        second_ingress = second_stage / "release" / self.profile.ingress
        second_ingress.parent.mkdir(parents=True)
        second_ingress.write_bytes(b"reviewed successor fixture")
        pins = {self.profile.ingress: digest(second_ingress.read_bytes())}
        raw = provisioner.encode({"schema": "offmenu-detached-verifier-candidate-v1",
                                 "files": {key: {"sha256": value} for key, value in pins.items()}})
        (second_stage / "release-manifest.json").write_bytes(raw)
        successor = self.profile._replace(stage=second_stage, shelf="test-shelf-v2",
            release=digest(provisioner.encode(pins)), manifest_sha256=digest(raw))
        with (patch.object(provisioner, "PROFILES", {"offmenu": self.profile, "offmenu-v2": successor}),
              patch.object(provisioner.os, "getresuid", return_value=(0, 0, 0)),
              patch.object(provisioner, "protected_directory", side_effect=lambda p, _:
                           provisioner.os.open(p, provisioner.os.O_RDONLY | provisioner.os.O_DIRECTORY)),
              patch.object(provisioner, "verify_installed")):
            v1 = provisioner.main(["offmenu", v1_operation])
            v2 = provisioner.main(["offmenu-v2", v2_operation])
            self.assertEqual(v1, provisioner.main(["offmenu", v1_operation]))
            self.assertEqual(v2, provisioner.main(["offmenu-v2", v2_operation]))
            with self.assertRaisesRegex(RuntimeError, "RECONCILIATION"):
                provisioner.main(["offmenu-v2", v1_operation])
        self.assertEqual(v1_operation, json.loads((self.parent / ".test-shelf.receipt").read_bytes())["operation_id"])
        self.assertEqual(v2_operation, json.loads((self.parent / ".test-shelf-v2.receipt").read_bytes())["operation_id"])
        self.assertEqual(b"reviewed fixture", (self.parent / "test-shelf" / self.profile.ingress).read_bytes())
        self.assertEqual(b"reviewed successor fixture", (self.parent / "test-shelf-v2" / successor.ingress).read_bytes())

    def test_v2_launcher_uses_independent_leaf_and_live_profile_has_verified_stage_pins(self):
        import subprocess
        v1 = provisioner.PROFILES["offmenu"]
        v2 = provisioner.PROFILES["offmenu-v2"]
        self.assertEqual(v1.parent, v2.parent)
        self.assertNotEqual(v1.shelf, v2.shelf)
        self.assertEqual("offmenu-native-upgrade-v2", v2.shelf)
        self.assertEqual("1208a7d41aa5b754cd4ced82ff18f5aadbb0de758202a13bb73adcd76bfccae4", v2.release)
        self.assertEqual("e9a593ab451b3f964d0596c0b2b7d0d4a53f08ad8447cac49bc76ac6b5d9e599", v2.manifest_sha256)
        launcher = SOURCE / "launch_verifier_shelf_v2.bash"
        text = launcher.read_text()
        self.assertIn(digest((SOURCE / "verifier_shelf_provision.py").read_bytes()), text)
        self.assertIn('/uif-maintenance/verifier_shelf_provision_v2.py', text)
        self.assertIn('"$descriptor" offmenu-v2 "$1"', text)
        self.assertEqual(64, subprocess.run(
            ["/usr/bin/bash", str(launcher), "not-an-original"], check=False).returncode)
