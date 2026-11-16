"""Synthetic future-hook safety only; never consumes a real retry or claim."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('paused_retry_source', ROOT / 'tools/white_room/migrate_cix_source.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def state(retrying=1):
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'projects': [
        {'name': 'ANQR', 'paused': True,
         'counts': dict(running=0, settling=0, tracked_tasks=0, retrying=retrying)}]}


class PausedRetrySource(unittest.TestCase):
    def fixture(self, directory):
        program = Path(directory) / 'orchestrator.py'
        program.write_bytes(b'# synthetic qualified program, no execution\n')
        self.enterContext(patch.object(M, 'PAUSE_DISPATCH_SOURCE', program))
        self.enterContext(patch.object(M, 'PAUSE_DISPATCH_SHA256', M.digest(program.read_bytes())))
        profile = M.PROFILES['anqr-develop']
        hook = 'set -eu\ngit clone --single-branch -- ' + profile.origin + ' .\n'
        selected = replace(profile, hook_sha256=M.digest(hook.encode()))
        self.enterContext(patch.dict(M.PROFILES, {'anqr-develop': selected}))
        value = {'projects': [{'name': 'ANQR', 'paused': True,
                  'delivery': {'repo': selected.repository, 'origin': selected.origin, 'base': 'develop'},
                  'hooks': {'after_create': hook}}]}
        raw = (json.dumps(value, indent=2) + '\n').encode()
        config = Path(directory) / 'workflow.json'
        config.write_bytes(raw)
        config.chmod(0o600)
        return program, config, raw

    def test_paused_retry_preserving_apply_keeps_state_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            _, config, raw = self.fixture(directory)
            observation = state()
            before = copy.deepcopy(observation)
            result = M.migrate(config, M.digest(raw), profile='anqr-develop',
                               state_reader=lambda: observation, apply=True)
            self.assertEqual(result['status'], 'applied')
            self.assertIs(result['paused_retries_preserved'], True)
            self.assertEqual(observation, before)
            self.assertEqual(json.loads(config.read_bytes())['projects'][0]['delivery']['base'], 'develop')
            self.assertEqual((config.parent / '.uif-source-migrations' / (M.digest(raw) + '.json')).read_bytes(), raw)

    def test_source_drift_or_absence_refuses_before_config_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            program, config, raw = self.fixture(directory)
            program.write_bytes(b'# wrong source\n')
            with self.assertRaises(M.MigrationError):
                M.migrate(config, M.digest(raw), profile='anqr-develop', state_reader=state, apply=True)
            self.assertEqual(config.read_bytes(), raw)
            program.unlink()
            with self.assertRaises(OSError):
                M.migrate(config, M.digest(raw), profile='anqr-develop', state_reader=state, apply=True)
            self.assertEqual(config.read_bytes(), raw)

    def test_fifo_or_symlink_source_is_not_followed_or_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            program, _, _ = self.fixture(directory)
            program.unlink()
            os.mkfifo(program)
            with self.assertRaises(M.MigrationError):
                M.verify_pause_dispatch_source()
            program.unlink()
            program.symlink_to(Path(directory) / 'absent')
            with self.assertRaises(M.MigrationError):
                M.verify_pause_dispatch_source()

    def test_no_active_worker_or_unpaused_exception_is_introduced(self):
        for key in ('running', 'settling', 'tracked_tasks'):
            value = state()
            value['projects'][0]['counts'][key] = 1
            with self.subTest(key=key), self.assertRaises(M.MigrationError):
                M.check_state(value, project_name='ANQR', allow_paused_retries=True)
        value = state()
        value['projects'][0]['paused'] = False
        with self.assertRaises(M.MigrationError):
            M.check_state(value, project_name='ANQR', allow_paused_retries=True)
        for count in (-1, True, 1.0, None):
            with self.subTest(retry=count), self.assertRaises(M.MigrationError):
                M.check_state(state(count), project_name='ANQR', allow_paused_retries=True)

    def test_strict_default_and_other_profiles_cannot_use_this_relaxation(self):
        with self.assertRaises(M.MigrationError):
            M.check_state(state(), project_name='ANQR')
        with tempfile.TemporaryDirectory() as directory:
            self.fixture(directory)
            for change in ({'project': 'CQ'}, {'corrected_base': 'other'}):
                profile = replace(M.PROFILES['anqr-develop'], **change)
                value = state()
                value['projects'][0]['name'] = profile.project
                with self.subTest(change=change), self.assertRaises(M.MigrationError):
                    M.check_profile_state(value, profile)

    def test_source_pin_is_rechecked_at_final_effect_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            program, config, raw = self.fixture(directory)
            calls = 0
            def observe():
                nonlocal calls
                calls += 1
                if calls == 2:
                    program.write_bytes(b'# changed during preparation\n')
                return state()
            with self.assertRaises(M.MigrationError):
                M.migrate(config, M.digest(raw), profile='anqr-develop', state_reader=observe, apply=True)
            self.assertEqual(config.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
