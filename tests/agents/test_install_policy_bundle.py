import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/agents'))
spec = importlib.util.spec_from_file_location('install_policy', ROOT / 'tools/agents/install_policy_bundle.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InstallPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bundle = self.base / 'bundle-2.0.0'
        self.bundle.mkdir()
        files = ['AGENTS.md', 'tools/agents/check_policy.py', 'tools/agents/install_policy_bundle.py',
                 'docs/agents/uif-worker-policy.md', 'docs/agents/uif-worker-policy.json',
                 'docs/agents/environment-profiles.json', 'docs/agents/skills-and-plugins.md',
                 'docs/agents/memory-routing.md', 'docs/agents/startup-template.md']
        for name in files:
            target = self.bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('commit', '-qm', 'Reviewed bundle fixture')
        self.commit = self.git('rev-parse', 'HEAD')
        self.startup = self.base / 'AGENTS.md'
        self.original = b'# Original\nOffMenu historical pointer\nCIX execution paused.\nUSF uses its separate router.\n'
        self.startup.write_bytes(self.original)
        self.startup.chmod(0o640)
        self.expected = module.digest(self.original)
        self.backup = self.startup.with_name(self.startup.name + '.before-' + self.expected + '.bak')
        self.patch_root = patch.object(module, 'BUNDLE_ROOT', self.bundle)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def git(self, *args):
        result = subprocess.run(
            ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=UIF Test',
             '-c', 'user.email=uif-test@example.invalid', '-C', str(self.bundle), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode().strip()

    def install(self, **kwargs):
        return module.install(self.bundle, self.commit, self.startup, self.expected, **kwargs)

    def test_default_dry_run_changes_no_files_or_git_index(self):
        index = (self.bundle / '.git/index').read_bytes()
        result = self.install()
        self.assertEqual(result['status'], 'dry-run')
        self.assertEqual(self.startup.read_bytes(), self.original)
        self.assertFalse(self.backup.exists())
        self.assertEqual((self.bundle / '.git/index').read_bytes(), index)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertNotIn(self.original.decode(), json.dumps(result))

    def test_direct_python_invocation_needs_no_install_or_source_writes(self):
        result = subprocess.run(
            [sys.executable, str(self.bundle / 'tools/agents/install_policy_bundle.py'),
             '--bundle', str(self.bundle), '--source-commit', self.commit,
             '--startup', str(self.startup), '--expected-preimage-sha256', self.expected],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'dry-run')
        self.assertEqual(self.startup.read_bytes(), self.original)
        self.assertFalse(list(self.bundle.rglob('__pycache__')))
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_atomic_apply_preserves_bytes_mode_and_readback(self):
        result = self.install(apply=True)
        new = self.startup.read_bytes()
        self.assertEqual(result['status'], 'installed')
        self.assertEqual(result['readback_sha256'], module.digest(new))
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.startup.stat().st_mode), 0o640)
        self.assertIn(self.original, new)
        self.assertIn(b'CIX execution and production remain paused', new)
        self.assertIn(b'Native Linear is the sole live intake', new)
        self.assertIn(self.commit.encode(), new)
        self.assertIn(str(self.bundle).encode(), new)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse(list(self.base.glob('.AGENTS.md.policy-*')))

    def test_wrong_preimage_and_wrong_commit_refused(self):
        self.startup.write_bytes(b'new caller instructions\n')
        with self.assertRaisesRegex(module.AdoptionError, 'preimage'):
            self.install(apply=True)
        self.assertFalse(self.backup.exists())
        with self.assertRaisesRegex(module.AdoptionError, 'actual HEAD'):
            module.install(self.bundle, '0' * 40, self.startup, self.expected, apply=True)

    def test_dirty_or_untracked_source_refused_before_target_mutation(self):
        (self.bundle / 'AGENTS.md').write_text('changed\n')
        with self.assertRaisesRegex(module.AdoptionError, 'must be clean'):
            self.install(apply=True)
        self.assertEqual(self.startup.read_bytes(), self.original)
        self.git('restore', 'AGENTS.md')
        (self.bundle / 'unexpected').write_text('unreviewed\n')
        with self.assertRaisesRegex(module.AdoptionError, 'must be clean'):
            self.install()
        self.assertFalse(self.backup.exists())

    def test_symlink_and_wrong_owner_refused(self):
        target = self.base / 'real-startup'
        self.startup.rename(target)
        self.startup.symlink_to(target)
        with self.assertRaisesRegex(module.AdoptionError, 'Symlink'):
            self.install(apply=True)
        self.startup.unlink()
        target.rename(self.startup)
        with patch.object(module.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(module.AdoptionError, 'owned by the caller'):
                self.install(apply=True)
        self.assertFalse(self.backup.exists())

    def test_backup_collision_never_overwrites(self):
        self.backup.write_bytes(b'different retained custody\n')
        with self.assertRaisesRegex(module.AdoptionError, 'not overwritten'):
            self.install(apply=True)
        self.assertEqual(self.backup.read_bytes(), b'different retained custody\n')
        self.assertEqual(self.startup.read_bytes(), self.original)

    def test_matching_backup_is_reused_without_rewrite(self):
        self.backup.write_bytes(self.original)
        before = self.backup.stat().st_mtime_ns
        self.install(apply=True)
        self.assertEqual(self.backup.stat().st_mtime_ns, before)

    def test_mutation_after_backup_is_detected_and_preserved(self):
        real_backup = module._backup

        def concurrent_change(path, original):
            real_backup(path, original)
            self.startup.write_bytes(b'concurrent owner update\n')

        with patch.object(module, '_backup', side_effect=concurrent_change):
            with self.assertRaisesRegex(module.AdoptionError, 'changed during adoption'):
                self.install(apply=True)
        self.assertEqual(self.startup.read_bytes(), b'concurrent owner update\n')
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertFalse(list(self.base.glob('.AGENTS.md.policy-*')))

    def test_replace_failure_retains_original_and_backup(self):
        with patch.object(module.os, 'replace', side_effect=OSError('replacement refused')):
            with self.assertRaises(OSError):
                self.install(apply=True)
        self.assertEqual(self.startup.read_bytes(), self.original)
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertFalse(list(self.base.glob('.AGENTS.md.policy-*')))

    def test_source_change_after_backup_refuses_startup_replacement(self):
        real_backup = module._backup

        def source_change(path, original):
            real_backup(path, original)
            (self.bundle / 'AGENTS.md').write_text('concurrent source edit\n')

        with patch.object(module, '_backup', side_effect=source_change):
            with self.assertRaisesRegex(module.AdoptionError, 'must be clean'):
                self.install(apply=True)
        self.assertEqual(self.startup.read_bytes(), self.original)
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertFalse(list(self.base.glob('.AGENTS.md.policy-*')))

    def test_source_checkout_cannot_be_target(self):
        with self.assertRaisesRegex(module.AdoptionError, 'must not modify the source checkout'):
            module.install(self.bundle, self.commit, self.bundle / 'AGENTS.md',
                           module.digest((self.bundle / 'AGENTS.md').read_bytes()), apply=True)

    def test_singly_linked_regular_file_required(self):
        os.link(self.startup, self.base / 'also-startup')
        with self.assertRaisesRegex(module.AdoptionError, 'singly linked'):
            self.install(apply=True)


if __name__ == '__main__':
    unittest.main()
