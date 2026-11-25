from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / "config/windmill/source"
with patch.object(sys, "path", [str(SOURCE), *sys.path]):
    import guarded_idle_webhook as webhook


class WebhookTest(unittest.TestCase):
    def test_fixed_entrypoint_cannot_take_arguments_and_uses_platform_audit_id(self):
        current = "12345678-1234-1234-1234-123456789abc"
        transport = Mock(return_value={"state": "fixture"})
        with patch.dict(webhook.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": current}):
            with self.assertRaisesRegex(PermissionError, "UNBOUND"):
                webhook.main()
            fixed = webhook.bind_webhook(transport)
            self.assertEqual({"state": "fixture"}, fixed())
            with self.assertRaises(TypeError):
                fixed({"operation_id": "a"*64})
        transport.assert_called_once_with(current)

    def test_wrong_workspace_or_missing_platform_id_refuses_before_transport(self):
        transport = Mock()
        fixed = webhook.bind_webhook(transport)
        for workspace, job in (("offmenu", "12345678-1234-1234-1234-123456789abc"),
                               ("foo", ""), ("foo", "00000000-0000-0000-0000-000000000000")):
            with patch.dict(webhook.os.environ, {"WM_WORKSPACE": workspace, "WM_JOB_ID": job}):
                with self.assertRaises(PermissionError):
                    fixed()
        transport.assert_not_called()

    def test_bootstrap_launcher_pins_exact_module_bytes_and_takes_no_arguments(self):
        import hashlib
        import subprocess

        launcher = SOURCE / "launch_guarded_idle_bootstrap.bash"
        script = launcher.read_text()
        for name in ("guarded_service_upgrade.py", "install_guarded_idle_bootstrap.py"):
            module = SOURCE.parents[2] / "tools/delivery" / name
            self.assertIn(hashlib.sha256(module.read_bytes()).hexdigest(), script)
        self.assertEqual(0, subprocess.run(["/usr/bin/bash", "-n", str(launcher)], check=False).returncode)
        self.assertEqual(64, subprocess.run(
            ["/usr/bin/bash", str(launcher), "caller-value"], check=False).returncode)
