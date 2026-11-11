import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('policy_check', ROOT / 'tools/agents/check_policy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in (module.POLICY_PATH, module.MANIFEST_PATH, 'AGENTS.md'):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        self.expected = (ROOT / module.POLICY_PATH).read_bytes()

    def check(self):
        return module.check(self.root, self.expected, 'maldous/uif')

    def edit_manifest(self, key, value):
        path = self.root / module.MANIFEST_PATH
        data = json.loads(path.read_text())
        data[key] = value
        path.write_text(json.dumps(data))

    def test_valid(self):
        self.assertEqual(self.check(), [])

    def test_stale_version(self):
        self.edit_manifest('version', '1.1.0')
        self.assertIn('Invalid version', self.check())

    def test_previous_release_version(self):
        self.edit_manifest('version', '1.1.1')
        self.assertIn('Invalid version', self.check())

    def test_tamper(self):
        (self.root / module.POLICY_PATH).write_text('changed\n')
        self.assertIn('Policy hash mismatch', self.check())

    def test_wrong_identity(self):
        self.edit_manifest('repository', 'other/repo')
        self.assertIn('Repository identity mismatch', self.check())

    def test_escape_reference(self):
        self.edit_manifest('policy_path', '../missing.md')
        self.assertIn('Invalid policy_path', self.check())

    def test_missing_entrypoint(self):
        (self.root / 'AGENTS.md').write_text('hello\n')
        self.assertIn('Missing root startup references', self.check())

    def test_bad_json(self):
        (self.root / module.MANIFEST_PATH).write_text('bad')
        self.assertTrue(self.check())

    def test_non_object(self):
        (self.root / module.MANIFEST_PATH).write_text('[]')
        self.assertEqual(self.check(), ['Manifest must be an object'])

    def test_symlink(self):
        target = self.root / module.POLICY_PATH
        target.unlink()
        target.symlink_to(ROOT / module.POLICY_PATH)
        self.assertTrue(any('Nonportable' in error for error in self.check()))

    def test_different_maintenance_copy(self):
        self.assertIn('Policy differs from selected UIF source', module.check(self.root, b'other'))


if __name__ == '__main__':
    unittest.main()
