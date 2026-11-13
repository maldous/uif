"""Exercise the rendered launcher without entering the privileged helper path."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from uif import role_broker, supervisor
from uif.ledger import SNAPSHOT_SHA256, read_snapshot
from uif.role_broker import LEDGER_SCHEMA, NEUTRAL_BINDING, NEUTRAL_NAMESPACE, NEUTRAL_SCHEMA, _digest, slot_for_lease
from uif.task_identity import LedgerTaskKey


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy/stokowski/uif-role-helper.sh"
MAIN = 'if __name__ == "__main__":\n    raise SystemExit(main())\n'
PROBE = """def main(*, typed_only):
    assert typed_only is True
    directory = Path(__file__).parents[2] / sys.argv[2]
    cfg = {"controller_root": str(directory.parent), "controller_uid": os.getuid(),
           "roles": {"neutral": {}}}
    def probe_original(cfg, action, path, request):
        assert isinstance(request, LaunchRequest)
        return request
    globals()["_handle_original"] = probe_original
    try:
        request = _handle(cfg, sys.argv[1], sys.argv[2], typed_only=typed_only)
    except LaunchHold:
        return 2
    if isinstance(request.key, _LedgerRoleKey):
        from uif.stokowski import LEDGER_DISPATCH_INTENT
        assert LEDGER_DISPATCH_INTENT == "uif-dispatch-intent/2"
    print(json.dumps({
        "role": request.key.role, "workspace": request.key.workspace_key, "action": sys.argv[1],
        "isolated": sys.flags.isolated, "no_site": sys.flags.no_site,
        "no_bytecode": sys.flags.dont_write_bytecode,
    }))
    return 0
"""


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


@skipUnless(os.getuid() != 0 and os.geteuid() != 0, "launcher probes must be unprivileged")
class InstalledRoleHelperTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.package = self.root / "src" / "uif"
        self.package.mkdir(parents=True)
        for source in (ROOT / "src/uif").glob("*.py"):
            shutil.copyfile(source, self.package / source.name)
        self.helper = self.package / "role_broker.py"
        self.manifest = self.root / "package.json"
        self.launcher = self.root / "launcher.sh"
        self.python = Path(sys.executable).resolve()

    def render(self, *, substitute=True):
        if substitute:
            source = self.helper.read_text()
            self.assertTrue(source.endswith(MAIN))
            self.helper.write_text(source.removesuffix(MAIN) + PROBE)
        self.manifest.write_text(json.dumps({
            "schema": "uif-role-package/1",
            "files": {path.name: digest(path) for path in self.package.glob("*.py")},
        }))
        template = TEMPLATE.read_text()
        for token, value in {
            "UIF_ISOLATED_PYTHON": self.python,
            "UIF_ISOLATED_PYTHON_SHA256": digest(self.python),
            "UIF_ROLE_HELPER_SOURCE": self.helper,
            "UIF_ROLE_HELPER_SHA256": digest(self.helper),
            "UIF_ROLE_PACKAGE_MANIFEST": self.manifest,
            "UIF_ROLE_PACKAGE_MANIFEST_SHA256": digest(self.manifest),
        }.items():
            template = template.replace(f"@{token}@", str(value))
        self.launcher.write_text(template)

    def run_launcher(self, *args):
        return subprocess.run(
            ("/bin/sh", str(self.launcher), *args), cwd=self.root,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(self.root), "PYTHONHOME": str(self.root)},
            capture_output=True, text=True, check=False,
        )

    def request(self, role, *, ledger, change=None):
        lease = f"fixture-{role}-{'ledger' if ledger else 'issue'}"
        slot = slot_for_lease(lease)
        path = self.root / slot
        path.mkdir(mode=0o700)
        key = dict(intent_sha256="a" * 64, lease_id=lease)
        row = read_snapshot(ROOT / "README.md")["src-059-qualification"]
        if ledger:
            stable = LedgerTaskKey("fixture-store", row.row_id)
            workspace = stable.workspace_key
            key.update(task=dict(schema="uif-ledger-task/1", store_id=stable.store_id, row_id=stable.row_id,
                                 snapshot_sha256=SNAPSHOT_SHA256, source_sha256=row.source_sha256,
                                 owner="owner", epoch="epoch", original_operation_id="opaque-original"),
                       workspace_key=workspace, invocation_id="00000000-0000-0000-0000-000000000001")
            if role == "coding":
                key.update(attempt=1, handoff_sha256="b" * 64)
        else:
            workspace = "UIF-1"
            key.update(role=role, store_id="fixture-store", row_id=row.row_id, row_source_sha256=row.source_sha256,
                       issue_id="00000000-0000-0000-0000-000000000001", issue_identifier=workspace,
                       owner="owner", epoch="epoch", operation_id="original", attempt_id="attempt")
        record = dict(key={**key, **(change or {})}, slot=slot, admission_sha256="c" * 64,
                      request_sha256="d" * 64, projection_sha256="e" * 64, policy_sha256="f" * 64)
        if ledger:
            record.update(schema=LEDGER_SCHEMA, role=role)
        request = path / "launch.json"
        request.write_text(json.dumps(record))
        request.chmod(0o600)
        return slot, workspace

    def test_decodes_legacy_and_both_ledger_keys_through_isolated_launcher(self):
        spoof = self.root / "uif"
        spoof.mkdir()
        (spoof / "__init__.py").write_text("raise AssertionError('untrusted Python path used')\n")
        self.render()
        for role in ("coding", "review"):
            for ledger in (False, True):
                with self.subTest(role=role, ledger=ledger):
                    slot, workspace = self.request(role, ledger=ledger)
                    result = self.run_launcher("observe", slot)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout),
                                     dict(role=role, workspace=workspace, action="observe",
                                          isolated=1, no_site=1, no_bytecode=1))

    def test_refuses_forged_ledger_key_and_invalid_cli(self):
        self.render()
        slot, _ = self.request("coding", ledger=True, change={"workspace_key": "ledger-" + "0" * 64})
        for args in (("observe", slot), ("run", slot), ("observe", slot.upper()), ("observe",)):
            with self.subTest(args=args):
                self.assertEqual(self.run_launcher(*args).returncode, 2)

    def test_typed_launcher_refuses_neutral_before_original_handler(self):
        self.render()
        job = "ordinary-fixture"
        lease = "neutral-" + _digest([NEUTRAL_NAMESPACE, "foo", job])
        slot = slot_for_lease(lease)
        directory = self.root / slot
        directory.mkdir(mode=0o700)
        binding = dict.fromkeys(NEUTRAL_BINDING, "a" * 64)
        binding.update(namespace=NEUTRAL_NAMESPACE, workspace="foo", job_id=job)
        path = directory / "launch.json"
        path.write_text(json.dumps(dict(schema=NEUTRAL_SCHEMA, binding=binding, slot=slot,
                                        intent_sha256="b" * 64, request_file_sha256="c" * 64,
                                        pool_binding_sha256="d" * 64, cpu_ids=[0])))
        path.chmod(0o600)
        for action in ("start", "observe", "cancel"):
            with self.subTest(action=action):
                result = self.run_launcher(action, slot)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (2, "", ""))

    def test_privileged_policy_reader_pins_the_fixed_typed_role_config(self):
        registration = self.root / "typed-supervisor.json"
        roles = self.root / "typed-role-launch.json"
        roles.write_text('{}')
        roles.chmod(0o600)
        registration.write_text(json.dumps({"role_policy": {"path": str(roles), "sha256": digest(roles)}}))
        registration.chmod(0o600)
        checked = role_broker._regular
        with (patch.object(supervisor, "TYPED_POLICY", registration),
              patch.object(role_broker, "TYPED_POLICY", roles), patch.object(role_broker, "_dir"),
              patch.object(role_broker, "_regular", wraps=lambda fd, uid, mode: checked(fd, os.getuid(), mode))):
            with self.assertRaises(KeyError) as incomplete:
                role_broker._policy(typed_only=True)
            self.assertEqual(incomplete.exception.args, ("schema",))
            roles.write_text('{"changed":true}')
            with self.assertRaisesRegex(role_broker.LaunchHold, "role_launch_digest"):
                role_broker._policy(typed_only=True)

    def test_refuses_changed_module_unlisted_file_and_symlink(self):
        self.render()
        slot, _ = self.request("coding", ledger=True)
        path = self.package / "ledger.py"
        original = path.read_bytes()
        path.write_bytes(original + b"\n# drift\n")
        self.assertEqual(self.run_launcher("observe", slot).returncode, 2)
        path.write_bytes(original)
        path.chmod(0o660)
        self.assertEqual(self.run_launcher("observe", slot).returncode, 2)
        path.chmod(0o600)
        extra = self.package / "unlisted.py"
        extra.touch()
        self.assertEqual(self.run_launcher("observe", slot).returncode, 2)
        extra.unlink()
        elsewhere = self.root / "same-bytes.py"
        path.rename(elsewhere)
        path.symlink_to(elsewhere)
        self.assertEqual(self.run_launcher("observe", slot).returncode, 2)

    def test_refuses_changed_manifest(self):
        self.render()
        slot, _ = self.request("coding", ledger=True)
        self.assertEqual(self.run_launcher("observe", slot).returncode, 0)
        self.manifest.write_bytes(self.manifest.read_bytes() + b"\n")
        result = self.run_launcher("observe", slot)
        self.assertEqual((result.returncode, result.stdout), (2, ""))

    def test_preserves_nonroot_helper_refusal(self):
        self.render(substitute=False)
        result = self.run_launcher("observe", "0" * 64)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (2, "", ""))
