"""Offline repository mapping regressions, not CQ runtime or admission tests."""
import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'white_room'))
from check import digest, evaluate

NOW = datetime(2026, 11, 9, 18, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


class CircularQuayRepositoryBinding(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / 'config/white-room.json').read_text())
        self.evidence = {
            'version': 1,
            'contract_sha256': digest(self.contract),
            'project': 'circularquay',
            'profile': 'cloud',
            'observations': [],
        }

    def run_check(self, profile='cloud'):
        self.evidence['profile'] = profile
        return evaluate(self.contract, self.evidence, profile, 'circularquay', NOW)

    def test_repository_uses_verified_case_preserving_identity(self):
        self.assertEqual(
            self.contract['projects']['circularquay']['repository'],
            'maldous/CircularQuay',
        )

    def test_mapping_does_not_declare_discovery_or_runtime_bindings(self):
        selected = self.contract['projects']['circularquay']
        self.assertEqual(set(selected), {'repository', 'discovery_complete'})
        self.assertEqual(selected['discovery_complete'], {'foo': False, 'cloud': False})

    def test_cloud_still_reports_incomplete_discovery_without_authority(self):
        result = self.run_check()
        self.assertFalse(result['preflight_passed'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['runtime_readiness_verified'])
        self.assertIn(
            {'id': 'discovery', 'state': 'unknown',
             'reason': 'applicable dependency discovery is incomplete'},
            result['checks'],
        )

    def test_foo_still_requires_all_applicable_observations(self):
        result = self.run_check('foo')
        ids = {row['id'] for row in result['checks']}
        expected = {
            req['id'] for req in self.contract['requirements']
            if 'foo' in req['profiles'] and 'circularquay' in req['projects']
        }
        self.assertTrue(expected.issubset(ids))
        self.assertFalse(result['preflight_passed'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['runtime_readiness_verified'])

    def test_another_project_cannot_reuse_cq_evidence_target(self):
        self.evidence['project'] = 'offmenu'
        result = self.run_check()
        self.assertTrue(any(row['id'] == 'target-binding' and row['state'] == 'drifted'
                            for row in result['checks']))
        self.assertFalse(result['preflight_passed'])

    def test_wrong_recovery_repository_is_blocked(self):
        # Deliberately synthetic metadata. It is not a current owner or source receipt.
        self.evidence['recovery'] = {
            'repository': 'maldous/offmenu',
            'accepted_commit': 'a' * 40,
            'selected_commit': 'a' * 40,
            'issue': 'synthetic-cq-issue',
            'owner': 'synthetic-cq-owner',
            'authoritative_owner': 'synthetic-cq-owner',
            'ownership_epoch': 1,
            'authoritative_epoch': 1,
            'remote_read_at': STAMP,
            'ownership_expires_at': '2026-11-09T18:05:00+00:00',
            'workspace_isolated': True,
            'recoverable_work_preserved': True,
            'previous_owner_fenced': True,
            'superseded': False,
            'pending_operations': [],
            'checkpoint': {'disposition': 'none', 'absence_verified': True},
            'published_checkpoint': {
                'repository': 'maldous/offmenu',
                'commit': 'a' * 40,
                'issue': 'synthetic-cq-issue',
                'owner': 'synthetic-cq-owner',
                'ownership_epoch': 1,
                'observed_at': STAMP,
                'source': 'synthetic:remote',
                'remote_verified': True,
            },
        }
        result = self.run_check()
        self.assertTrue(any(row['id'] == 'repository' and row['state'] == 'blocked'
                            for row in result['checks']))
        self.assertFalse(result['preflight_passed'])

    def test_stale_contract_digest_is_not_accepted_after_mapping(self):
        old = copy.deepcopy(self.contract)
        old['projects']['circularquay']['repository'] = None
        self.evidence['contract_sha256'] = digest(old)
        result = self.run_check()
        self.assertTrue(any(row['id'] == 'contract-binding' and row['state'] == 'drifted'
                            for row in result['checks']))
        self.assertFalse(result['preflight_passed'])


if __name__ == '__main__':
    unittest.main()
