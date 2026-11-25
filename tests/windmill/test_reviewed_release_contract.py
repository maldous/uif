import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "config/windmill/source/reviewed_release_contract.py"
SPEC = importlib.util.spec_from_file_location("reviewed_release_contract", PATH)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class ReviewedReleaseContractTest(unittest.TestCase):
    def request(self):
        return {
            "action": "install-reviewed-release", "profile": "offmenu-native-upgrade-v1",
            "operation_id": "a"*64, "release_sha256": "b"*64,
            "package_sha256": "c"*64, "protected_request_sha256": "d"*64,
        }

    def test_valid_shape_is_not_admission(self):
        self.assertEqual(contract.main(self.request()), {
            "status": "denied", "reason": "TRUSTED_CURRENT_JOB_BINDING_UNAVAILABLE",
        })

    def test_caller_cannot_choose_path_command_or_authority(self):
        for field in ("principal", "job_id", "command", "cwd", "path", "authorization"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "FIELDS"):
                contract.main(dict(self.request(), **{field: "untrusted"}))

    def test_only_exact_profile_and_full_lowercase_pins(self):
        for changes in ({"profile": "uif"}, {"action": "run"},
                        {"package_sha256": True}, {"operation_id": "a"*64+"\n"},
                        {"release_sha256": "A"*64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                contract.main(dict(self.request(), **changes))
