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
