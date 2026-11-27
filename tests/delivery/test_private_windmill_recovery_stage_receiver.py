import builtins
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "delivery"
SPEC = importlib.util.spec_from_file_location(
    "private_recovery_stage_receiver", TOOLS / "private_windmill_recovery_stage_receiver.py")
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)
NAMES = ("fresh_idle_generation.py", "fresh_idle_generation_controller.py",
         "fresh_idle_recovery_host.py", "fresh_idle_recovery_receiver.py",
         "90-uif-fresh-idle-recovery.conf")


class PrivateRecoveryStageReceiver(unittest.TestCase):
    def setUp(self):
        self.start = patch.object(receiver.os, "getresuid", return_value=(0, 0, 0))
        self.start.start()
        self.addCleanup(self.start.stop)
        control = SimpleNamespace(modules=dict(sys.modules), dont_write_bytecode=True,
                                  flags=SimpleNamespace(isolated=True, no_site=True))
        self.flags = patch.object(receiver, "sys", control)
        self.flags.start()
        self.addCleanup(self.flags.stop)
        self.pins = dict(operation_id="a" * 64, prior_dropin_sha256="b" * 64,
                         retained_selector_sha256="c" * 64,
                         members={n: str(i) * 64 for i, n in reversed(list(enumerate(NAMES)))})
        self.approved = []

    def read_input(self, name, maximum):
        if name == receiver.PINS:
            return json.dumps(self.pins).encode()
        return (TOOLS / name).read_bytes()

    def execute(self, code, namespace):
        # Use the exact reviewed module bytes but substitute the single
        # host effect. Preserve real dataclass/import behavior.
        name = namespace["__name__"]
        with patch.dict(sys.modules, receiver.sys.modules):
            builtins.exec(code, namespace)
        if name == "install_fresh_idle_recovery_stage":
            def stage(approval):
                self.approved.append(approval)
                return {"status": "staged_not_activated"}
            namespace["stage"] = stage

    def invoke(self, args=()):
        with patch.object(receiver, "read", side_effect=self.read_input), \
                patch.object(receiver, "exec", side_effect=self.execute, create=True):
            return receiver.main(args)

    def test_exact_pins_use_fixed_member_order(self):
        self.assertEqual(self.invoke(), {"status": "staged_not_activated"})
        self.assertEqual(len(self.approved), 1)
        self.assertEqual(self.approved[0].operation_id, self.pins["operation_id"])
        self.assertEqual(self.approved[0].member_sha256,
                         tuple(self.pins["members"][n] for n in NAMES))

    def test_a_caller_cannot_supply_a_release_or_action(self):
        with self.assertRaises(PermissionError):
            self.invoke(["start"])
        self.assertEqual(self.approved, [])

    def test_all_modules_checked_before_first_exec(self):
        bad = receiver.SOURCES[-1][0] + ".py"
        with patch.object(receiver, "read",
                          side_effect=lambda n, m: b"x" if n == bad else self.read_input(n, m)), \
                patch.object(receiver, "exec", create=True) as run:
            with self.assertRaises(PermissionError):
                receiver.main(())
            run.assert_not_called()

    def test_unrecognized_member_rejected_before_any_host_effect(self):
        self.pins["members"]["extra"] = "d" * 64
        with self.assertRaisesRegex(Exception, "expected exact recovery stage member pins"):
            self.invoke()
        self.assertEqual(self.approved, [])


if __name__ == "__main__":
    unittest.main()
