from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import offmenu_verifier_v2_loader as loader


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.pin = "1208a7d41aa5b754cd4ced82ff18f5aadbb0de758202a13bb73adcd76bfccae4"

        def p(obj, key, result):
            return self.enterContext(patch.object(obj, key, return_value=result))

        p(loader, "_fixed_private_process", True)
        p(loader, "protected_directory", 19)
        p(loader, "_shelf_names", set())
        info = SimpleNamespace(st_dev=1, st_ino=2, st_uid=0, st_mode=0o40755)
        p(loader.os, "fstat", info)
        p(loader.os, "stat", info)
        p(loader.os, "close", None)
        self.verify = p(loader, "verify_tree", None)
        self.events = []
        self.verify.side_effect = lambda *args: self.events.append(("verify", args))

        def load(name):
            self.events.append(("import", name))
            return SimpleNamespace(
                __file__=str(loader.SHELF / "tools/delivery" / (name + ".py")), CODE=loader.SHELF)

        self.loader = self.enterContext(patch.object(loader, "import_module", side_effect=load))

    def invoke(self, pin):
        with loader.open_fixed_offmenu_v2(pin) as result:
            self.assertEqual(sys.path[:2], [str(loader.SHELF / "tools/delivery"),
                                           str(loader.SHELF / "deps")])
            return result.release_sha256

    def test_verifies_exact_shelf_before_import_then_restores_path(self):
        before = sys.path[:]
        self.assertEqual(self.invoke(self.pin), self.pin)
        self.assertEqual(self.events[0], ("verify", (loader.SHELF, self.pin, 0)))
        self.assertEqual([event[1] for event in self.events[1:]], list(loader.MODULE_NAMES))
        self.assertEqual(sys.path, before)

    def test_wrong_approval_pin_or_shelf_stops_before_import(self):
        with self.assertRaisesRegex(ValueError, "PIN_REQUIRED"):
            self.invoke("b" * 64)
        self.verify.assert_not_called()
        self.verify.side_effect = ValueError("mismatch")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.invoke(self.pin)
        self.loader.assert_not_called()

    def test_mixed_cache_and_wrong_origin(self):
        with patch.object(loader, "_shelf_names", return_value={"sys"}):
            with self.assertRaisesRegex(ImportError, "MIXED"):
                self.invoke(self.pin)
            self.loader.assert_not_called()
        self.loader.side_effect = lambda name: SimpleNamespace(
            __file__="/usr/local/lib/offmenu-native-upgrade/" + name + ".py")
        with self.assertRaisesRegex(ImportError, "WRONG_MODULE"):
            self.invoke(self.pin)

    def test_directory_identity_drift_stops_before_import(self):
        loader.os.stat.return_value = SimpleNamespace(st_dev=1, st_ino=9, st_uid=0, st_mode=0o40755)
        with self.assertRaisesRegex(RuntimeError, "SHELF_CHANGED"):
            self.invoke(self.pin)
        self.loader.assert_not_called()
