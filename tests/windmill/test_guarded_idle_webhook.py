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

    def test_real_sdk_composition_uses_only_fixed_runner_and_internal_audit_value(self):
        client = Mock()
        client.run_script_by_path_async.return_value = "child job"
        current = "12345678-1234-1234-1234-123456789abc"
        fixed = webhook.bind_webhook(webhook.bind_foo_transport(client))
        with patch.dict(webhook.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": current}):
            result = fixed()
        client.run_script_by_path_async.assert_called_once_with("u/admin/foo_root_jobs", args={
            "action": "run", "job_id": "uif-idle-webhook-" + current,
            "command": "/usr/bin/python3.12 -I -S -B /var/lib/uif-stokowski/idle-upgrade/private_windmill_idle_receiver.py " + current,
            "cwd": "/", "timeout_seconds": 60,
        })
        self.assertEqual("submitted", result["state"])
        self.assertEqual("child job", result["runner_run_id"])
        with self.assertRaises((ValueError, PermissionError)):
            webhook.bind_foo_transport(client)("; arbitrary fixture")
        client.run_script_by_path_async.assert_called_once()

    def test_deployed_mount_resolves_sdk_at_module_scope_and_keeps_fixed_main(self):
        import ast
        from types import SimpleNamespace

        path = SOURCE / "guarded_idle_webhook.py"
        mounted = path.read_text() + "\n\n" + (SOURCE / "guarded_idle_webhook_mount.py").read_text()
        tree = ast.parse(mounted)
        self.assertTrue(any(isinstance(node, ast.Import)
                            and any(alias.name == "wmill" for alias in node.names)
                            for node in tree.body))
        client = Mock()
        client.run_script_by_path_async.return_value = "original-bridge-fixture"
        current = "12345678-1234-1234-1234-123456789abc"
        module = SimpleNamespace(Windmill=Mock(return_value=client))
        namespace = {}
        with (patch.dict(sys.modules, {"wmill": module}),
              patch.dict(webhook.os.environ, {"WM_WORKSPACE": "foo", "WM_JOB_ID": current})):
            exec(compile(tree, str(path), "exec"), namespace)
            self.assertEqual("original-bridge-fixture", namespace["main"]()["runner_run_id"])
            with self.assertRaises(TypeError):
                namespace["main"]("caller value")
        client.run_script_by_path_async.assert_called_once()
