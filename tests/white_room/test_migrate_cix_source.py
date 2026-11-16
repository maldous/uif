"""Synthetic source/configuration contracts; never touches native state."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('migrate_cix_source', ROOT / 'tools/white_room/migrate_cix_source.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
HOOK = 'set -eu\n# synthetic fixture, no credentials\ngit ' + M.OLD_CLONE + '\n'


def config():
    return {'preserved': 'π and whitespace', 'projects': [
        {'name': 'Other', 'paused': True, 'delivery': {'base': 'main'}},
        {'name': 'CIX', 'paused': True,
         'delivery': {'repo': M.REPOSITORY, 'origin': M.ORIGIN, 'base': 'main'},
         'hooks': {'after_create': HOOK, 'timeout_ms': 120000},
         'tracker': {'retained': 'synthetic-only'}}]}


def raw(value=None):
    return (json.dumps(value if value is not None else config(), ensure_ascii=False, indent=3) + '\n').encode()


def state():
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'projects': [
        {'name': 'CIX', 'paused': True, 'counts': dict(running=0, settling=0, tracked_tasks=0, retrying=0)},
        {'name': 'Other', 'paused': False, 'counts': {'running': 1}}]}


class SourceCorrection(unittest.TestCase):
    def setUp(self):
        self.pin = patch.object(M, 'EXPECTED_HOOK_SHA256', M.digest(HOOK.encode()))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_only_two_scalar_spans_change_including_unicode_and_whitespace(self):
        before = raw()
        after, receipt = M.render(before)
        expected = before.replace(b'"base": "main"', b'"base": "develop"', 2)
        # The unrelated first project's base must remain byte-for-byte unchanged.
        expected = expected.replace(b'"base": "develop"', b'"base": "main"', 1)
        expected = expected.replace(M.OLD_CLONE.encode(), M.NEW_CLONE.encode())
        self.assertEqual(after, expected)
        self.assertEqual(receipt['after_sha256'], M.digest(after))
        self.assertFalse(receipt['runtime_adoption_verified'])
        for key in ('admission_changed', 'claims_transferred', 'existing_workspaces_changed', 'runtime_restarted'):
            self.assertIs(receipt[key], False)

    def test_legacy_project_pin_must_match(self):
        for field, value in [('paused', False), ('paused', 1), ('name', 'wrong')]:
            data = config()
            data['projects'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(M.MigrationError):
                M.render(raw(data))
        for field, value in [('repo', 'other/repo'), ('origin', 'https://example.invalid/'), ('base', 'develop')]:
            data = config()
            data['projects'][1]['delivery'][field] = value
            with self.subTest(field=field), self.assertRaises(M.MigrationError):
                M.render(raw(data))

    def test_hook_digest_and_exact_single_clone_are_required(self):
        for hook in (HOOK + '# changed\n', HOOK + HOOK, HOOK.replace('clone', 'fetch')):
            data = config()
            data['projects'][1]['hooks']['after_create'] = hook
            with self.subTest(hook=hook), self.assertRaises(M.MigrationError):
                M.render(raw(data))

    def test_duplicate_ambiguous_trailing_nonfinite_and_deep_json_refused(self):
        duplicate = raw().replace(b'"paused": true', b'"paused": true, "paused": true', 1)
        data = config()
        data['projects'].append(copy.deepcopy(data['projects'][1]))
        for payload in (duplicate, raw(data), raw() + b'false', raw().replace(b'120000', b'NaN'), b'[' * 66 + b'0' + b']' * 66):
            with self.subTest(payload=payload[:40]), self.assertRaises((M.MigrationError, ValueError)):
                M.render(payload)

    def test_fresh_project_specific_quiescence_not_global_idle(self):
        self.assertEqual(M.check_state(state())[:10], datetime.now(timezone.utc).isoformat()[:10])
        for key in ('running', 'settling', 'tracked_tasks', 'retrying'):
            for count in (1, False, 0.0, None):
                data = state()
                data['projects'][0]['counts'][key] = count
                with self.subTest(key=key, count=count), self.assertRaises(M.MigrationError):
                    M.check_state(data)

    def test_state_pause_freshness_and_unique_project_required(self):
        now = datetime.now(timezone.utc)
        for stamp in ((now - timedelta(seconds=6)).isoformat(), (now + timedelta(seconds=1)).isoformat(), 'invalid', now.replace(tzinfo=None).isoformat()):
            data = state()
            data['generated_at'] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(M.MigrationError):
                M.check_state(data, now)
        for mutate in (lambda d: d['projects'][0].update(paused=False), lambda d: d['projects'].append(copy.deepcopy(d['projects'][0])), lambda d: d.update(projects=[])):
            data = state()
            mutate(data)
            with self.assertRaises(M.MigrationError):
                M.check_state(data)

    def test_state_counts_schema_is_strict(self):
        for counts in ([], ['running'], None, 'zero'):
            data = state()
            data['projects'][0]['counts'] = counts
            with self.subTest(counts=counts), self.assertRaises(M.MigrationError):
                M.check_state(data)

    def fixture(self, directory):
        path = Path(directory) / 'workflow.json'
        path.write_bytes(raw())
        path.chmod(0o640)
        return path

    def test_dry_run_has_no_file_or_backup_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            result = M.migrate(path, M.digest(raw()), state_reader=state)
            self.assertEqual(result['status'], 'dry-run')
            self.assertEqual(path.read_bytes(), raw())
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_apply_preserves_original_and_mode_and_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            expected, _ = M.render(raw())
            result = M.migrate(path, M.digest(raw()), apply=True, state_reader=state)
            self.assertEqual(result['status'], 'applied')
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            backup = path.parent / '.uif-source-migrations' / (M.digest(raw()) + '.json')
            self.assertEqual(backup.read_bytes(), raw())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.parent.stat().st_mode), 0o700)

    def test_preimage_symlink_hardlink_and_wrong_pin_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            for pin in ('0' * 64, 'short'):
                with self.assertRaises(M.MigrationError):
                    M.migrate(path, pin, state_reader=state)
            alias = path.with_name('alias.json')
            alias.symlink_to(path)
            with self.assertRaises(M.MigrationError):
                M.migrate(alias, M.digest(raw()), state_reader=state)
            alias.unlink()
            os.link(path, alias)
            with self.assertRaises(M.MigrationError):
                M.migrate(path, M.digest(raw()), state_reader=state)

    def test_broad_write_permission_or_incompatible_backup_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            path.chmod(0o666)
            with self.assertRaises(M.MigrationError):
                M.migrate(path, M.digest(raw()), apply=True, state_reader=state)
            path.chmod(0o640)
            backup_dir = path.parent / '.uif-source-migrations'
            backup_dir.mkdir(mode=0o700)
            backup = backup_dir / (M.digest(raw()) + '.json')
            backup.write_bytes(b'different')
            backup.chmod(0o600)
            with self.assertRaises(M.MigrationError):
                M.migrate(path, M.digest(raw()), apply=True, state_reader=state)
            self.assertEqual(path.read_bytes(), raw())
            self.assertEqual(backup.read_bytes(), b'different')

    def test_state_change_before_effect_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            calls = 0
            def observe():
                nonlocal calls
                calls += 1
                data = state()
                if calls == 2:
                    data['projects'][0]['counts']['running'] = 1
                return data
            with self.assertRaises(M.MigrationError):
                M.migrate(path, M.digest(raw()), apply=True, state_reader=observe)
            self.assertEqual(path.read_bytes(), raw())
            self.assertFalse(list(path.parent.glob('.workflow.json.source-*')))

    def test_configuration_change_during_preparation_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            changed = raw() + b' '
            def observe():
                path.write_bytes(changed)
                return state()
            with self.assertRaises(M.MigrationError):
                M.migrate(path, M.digest(raw()), apply=True, state_reader=observe)
            self.assertEqual(path.read_bytes(), changed)

    def test_configuration_change_during_final_state_read_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            calls = 0
            changed = raw() + b' '
            def observe():
                nonlocal calls
                calls += 1
                if calls == 2:
                    path.write_bytes(changed)
                return state()
            with self.assertRaises(M.MigrationError):
                M.migrate(path, M.digest(raw()), apply=True, state_reader=observe)
            self.assertEqual(path.read_bytes(), changed)

    def test_post_replace_failure_is_uncertain_not_automatic_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            original_fsync = M.fsync_directory
            def fail_after_replace(selected):
                if selected == path.parent:
                    raise OSError('synthetic post-effect failure')
                original_fsync(selected)
            with patch.object(M, 'fsync_directory', fail_after_replace), self.assertRaises(M.UncertainMigrationError):
                M.migrate(path, M.digest(raw()), apply=True, state_reader=state)
            self.assertEqual(path.read_bytes(), M.render(raw())[0])
            self.assertEqual((path.parent / '.uif-source-migrations' / (M.digest(raw()) + '.json')).read_bytes(), raw())


if __name__ == '__main__':
    unittest.main()
