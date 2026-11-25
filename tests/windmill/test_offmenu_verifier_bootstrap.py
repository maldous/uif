import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "config/windmill/source/offmenu_verifier_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("offmenu_verifier_bootstrap", PATH)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.data = {"source": b"reviewed fixture"}
        self.events = []
        self.op = "a" * 64
        self.digest = bootstrap.pin(self.data["source"])

    def create(self, key, value):
        self.assertNotIn(key, self.data)
        self.events.append(key)
        self.data[key] = value

    def run_original(self, create=None):
        return bootstrap.transition(self.data.get, create or self.create, self.op, self.digest)

    def test_intent_precedes_effect_and_replay_only_reads(self):
        self.assertEqual("verified", self.run_original())
        self.assertEqual(["intent", "target", "receipt"], self.events)
        self.assertEqual("verified", self.run_original())
        self.assertEqual(["intent", "target", "receipt"], self.events)

    def test_interruption_recovers_only_original(self):
        def interrupted(key, value):
            if key == "target":
                raise OSError("fixture interruption")
            self.create(key, value)
        with self.assertRaises(OSError):
            self.run_original(interrupted)
        with self.assertRaisesRegex(RuntimeError, "ORIGINAL_MISMATCH"):
            bootstrap.transition(self.data.get, self.create, "b" * 64, self.digest)
        self.assertEqual(["intent"], self.events)
        self.assertEqual("verified", self.run_original())

    def test_unexplained_effect_and_corrupt_source_fail_before_writes(self):
        self.data["target"] = self.data["source"]
        with self.assertRaisesRegex(RuntimeError, "UNEXPLAINED"):
            self.run_original()
        self.data["source"] = b"changed"
        with self.assertRaisesRegex(RuntimeError, "MISMATCH"):
            self.run_original()
        self.assertFalse(self.events)

    def test_real_entrypoint_is_not_a_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "RELEASE_UNQUALIFIED"):
            bootstrap.main()

    def test_atomic_noreplace_preserves_existing_final(self):
        import os
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            anchor = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                bootstrap.publish_noreplace(anchor, "original.intent", b"exact")
                with self.assertRaises(FileExistsError):
                    bootstrap.publish_noreplace(anchor, "original.intent", b"other")
                self.assertEqual(b"exact", Path(temp, "original.intent").read_bytes())
                self.assertEqual(["original.intent"], sorted(os.listdir(temp)))
            finally:
                os.close(anchor)

    def test_failed_file_sync_exposes_no_final_at_intent_or_receipt(self):
        import os
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        for role in ("original.intent", "original.receipt"):
            with self.subTest(role=role), TemporaryDirectory() as temp:
                anchor = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with patch.object(bootstrap.os, "fsync", side_effect=OSError("interrupted")):
                        with self.assertRaisesRegex(OSError, "interrupted"):
                            bootstrap.publish_noreplace(anchor, role, b"exact")
                    self.assertEqual([], os.listdir(temp))
                    bootstrap.publish_noreplace(anchor, role, b"exact")
                    self.assertEqual(b"exact", Path(temp, role).read_bytes())
                finally:
                    os.close(anchor)

    def test_failed_directory_sync_leaves_only_a_complete_final(self):
        import os
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        with TemporaryDirectory() as temp:
            anchor = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            fsync = os.fsync
            def interrupted(fd):
                if fd == anchor:
                    raise OSError("directory sync interrupted")
                fsync(fd)
            try:
                with patch.object(bootstrap.os, "fsync", side_effect=interrupted):
                    with self.assertRaisesRegex(OSError, "directory sync interrupted"):
                        bootstrap.publish_noreplace(anchor, "original.receipt", b"complete")
                fsync(anchor)
                self.assertEqual(b"complete", Path(temp, "original.receipt").read_bytes())
                self.assertEqual(["original.receipt"], os.listdir(temp))
            finally:
                os.close(anchor)
