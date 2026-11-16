"""Credential-free selected-hook contracts, not native execution or authority."""
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('future_source_profiles', ROOT / 'tools/white_room/migrate_cix_source.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture(name):
    profile = M.PROFILES[name]
    old_clone = 'clone --single-branch -- ' + profile.origin + ' .'
    hook = 'set -eu\n# synthetic, no authentication\ngit ' + old_clone + '\n'
    selected = replace(profile, hook_sha256=M.digest(hook.encode()))
    value = {'sealed_original': {'operation': 'preserved', 'base': 'main'}, 'projects': [
        {'name': 'Other', 'paused': False, 'delivery': {'base': 'main'}},
        {'name': selected.project, 'paused': True,
         'delivery': {'repo': selected.repository, 'origin': selected.origin,
                      'base': selected.expected_base},
         'hooks': {'after_create': hook, 'timeout_ms': 120000}}]}
    raw = (json.dumps(value, ensure_ascii=False, indent=3) + '\n').encode()
    return selected, old_clone, raw


def state(project):
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'projects': [
        {'name': project, 'paused': True, 'counts': dict(running=0, settling=0, tracked_tasks=0, retrying=0)},
        {'name': 'Other', 'paused': False, 'counts': dict(running=1)}]}


class ExplicitFutureProfiles(unittest.TestCase):
    def test_each_profile_changes_only_its_pinned_hook_and_preserves_publication_base(self):
        for name in M.PROFILES:
            profile, old_clone, raw = fixture(name)
            with self.subTest(profile=name), patch.dict(M.PROFILES, {name: profile}):
                after, receipt = M.render(raw, name)
                new_clone = ('clone --branch ' + profile.source_branch
                             + ' --single-branch -- ' + profile.origin + ' .')
                self.assertEqual(after, raw.replace(old_clone.encode(), new_clone.encode()))
                self.assertEqual(receipt['old_base'], receipt['new_base'])
                self.assertIs(receipt['publication_base_changed'], False)
                self.assertEqual(receipt['changed_paths'], ['projects/' + profile.project + '/hooks/after_create'])
                self.assertEqual(json.loads(after)['sealed_original'], json.loads(raw)['sealed_original'])

    def test_explicit_allowlist_and_production_pins_are_not_caller_parameters(self):
        for name in (None, False, [], 'ANQR', 'arbitrary', 'refs/heads/develop'):
            with self.subTest(profile=name), self.assertRaises(M.MigrationError):
                M.selected_profile(name)
        for name in M.PROFILES:
            _, _, raw = fixture(name)
            with self.subTest(profile=name), self.assertRaises(M.MigrationError):
                M.render(raw, name)

    def test_each_profile_refuses_other_project_repo_base_or_hook(self):
        for name in M.PROFILES:
            profile, _, raw = fixture(name)
            with self.subTest(profile=name), patch.dict(M.PROFILES, {name: profile}):
                for section, key, value in [('delivery', 'repo', 'other/repo'),
                                             ('delivery', 'base', 'wrong'),
                                             ('hooks', 'after_create', 'git clone somewhere')]:
                    data = json.loads(raw)
                    data['projects'][1][section][key] = value
                    with self.subTest(field=key), self.assertRaises(M.MigrationError):
                        M.render(json.dumps(data).encode(), name)

    def test_selected_project_state_is_checked_not_cix_or_global_idle(self):
        for name, profile in M.PROFILES.items():
            with self.subTest(profile=name):
                self.assertTrue(M.check_state(state(profile.project), project_name=profile.project))
                for key in ('running', 'settling', 'tracked_tasks', 'retrying'):
                    data = state(profile.project)
                    data['projects'][0]['counts'][key] = 1
                    with self.subTest(count=key), self.assertRaises(M.MigrationError):
                        M.check_state(data, project_name=profile.project)

    def test_hook_only_apply_preserves_base_other_bytes_and_original_backup(self):
        for name in M.PROFILES:
            profile, _, raw = fixture(name)
            with self.subTest(profile=name), patch.dict(M.PROFILES, {name: profile}), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'workflow.json'
                path.write_bytes(raw)
                path.chmod(0o600)
                receipt = M.migrate(path, M.digest(raw), apply=True,
                                    state_reader=lambda: state(profile.project), profile=name)
                self.assertEqual(path.read_bytes(), M.render(raw, name)[0])
                self.assertEqual(receipt['status'], 'applied')
                self.assertIs(receipt['publication_base_changed'], False)
                self.assertEqual((path.parent / '.uif-source-migrations' / (M.digest(raw) + '.json')).read_bytes(), raw)
                self.assertIs(receipt['claims_transferred'], False)
                self.assertIs(receipt['existing_workspaces_changed'], False)


if __name__ == '__main__':
    unittest.main()
