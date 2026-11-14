import copy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'white_room'))
import test_check as comparison_fixture
import test_remote_evidence as remote_fixture
from tools.white_room.check import digest
from tools.white_room.remote_evidence import ProjectScope, Target
from tools.white_room.transition import CheckpointRead, RecoveryHold, RecoveryInputs, WhiteRoomVerifier
from uif.quota_handoff import LinearKey, RecoveryContext

WORKSPACE = '11111111-1111-4111-8111-111111111111'
PROJECT = '22222222-2222-4222-8222-222222222222'
ISSUE = '33333333-3333-4333-8333-333333333333'
NOW = comparison_fixture.NOW


class Saver:
    def __init__(self, record=None):
        self.record = record
        self.calls = []

    def get_tuple(self, config):
        self.calls.append(copy.deepcopy(config))
        return self.record


class Transition(unittest.TestCase):
    def setUp(self):
        fixture = comparison_fixture.Recovery()
        fixture.setUp()
        self.contract, self.evidence = fixture.contract, fixture.evidence
        self.contract['requirements'][0].update(id='github-access', expected={'repository':'maldous/uif', 'read':True})
        self.contract['requirements'].append(dict(id='linear-access', profiles=['cloud','foo'], projects=['uif'],
            expected={'issue':'UIF-247', 'team_id':remote_fixture.TEAM, 'project_id':PROJECT, 'read':True}, max_age_seconds=300))
        self.evidence['observations'][0]['id'] = 'github-access'
        self.evidence['contract_sha256'] = digest(self.contract)
        self.source = dict(repository='maldous/uif', branch='feature/current',
                           base='a' * 40, head='b' * 40, tree='c' * 40)
        self.context = RecoveryContext(LinearKey(WORKSPACE, PROJECT, ISSUE), self.source,
                                       2, 'cloud', 'cloud-b', 'private-current-evidence')
        self.evidence['recovery'].update(branch=self.source['branch'], candidate_head=self.source['head'],
            candidate_tree=self.source['tree'], linear_workspace_id=WORKSPACE,
            linear_project_id=PROJECT, linear_issue_id=ISSUE)
        scope = ProjectScope('maldous/uif', 'develop', 'uif', 'cloud', WORKSPACE,
                             remote_fixture.TEAM, PROJECT, 'UIF')
        self.target = Target(digest(self.contract), 'a' * 40, 'UIF-247',
                             remote_fixture.TEAM, PROJECT, 8, scope=scope, issue_uuid=ISSUE)
        self.reader = remote_fixture.Fake()
        self.reader.responses['linear.issue'].update(projectId=PROJECT, uuid=ISSUE, workspaceId=WORKSPACE)
        self.saver = Saver()
        self.checkpoint = CheckpointRead(self.saver, 'registered-thread', '')

    def verifier(self, clock=lambda: NOW):
        return WhiteRoomVerifier(lambda expected: RecoveryInputs(self.contract, self.evidence,
                                 self.target, self.reader, self.checkpoint), clock)

    def test_reuses_comparator_reconstruction_and_latest_absence_read(self):
        result = self.verifier()(self.context)
        self.assertEqual(result.evidence_ref, self.context.evidence_ref)
        self.assertEqual(len(result.report_sha256), 64)
        self.assertEqual(self.saver.calls, [{'configurable': {'thread_id': 'registered-thread', 'checkpoint_ns': ''}}])
        self.assertEqual(len(self.reader.calls), 8)
        self.assertNotIn('checkpoint_id', self.saver.calls[0]['configurable'])

    def test_passing_saved_report_is_not_a_provider(self):
        with self.assertRaises(RecoveryHold):
            WhiteRoomVerifier(lambda expected: {'preflight_passed': True})(self.context)

    def test_fresh_model_quota_does_not_waive_white_room(self):
        self.contract['projects']['uif']['discovery_complete']['cloud'] = False
        self.target = Target(digest(self.contract), 'a' * 40, 'UIF-247', remote_fixture.TEAM,
                             PROJECT, 8, scope=self.target.scope, issue_uuid=ISSUE)
        self.evidence['contract_sha256'] = digest(self.contract)
        with self.assertRaisesRegex(RecoveryHold, 'white_room_prerequisite_failed'):
            self.verifier()(self.context)

    def test_unknown_operations_and_dirty_work_block_resume(self):
        for changes in ({'pending_operations': [{'id': 'original', 'state': 'unknown'}]},
                        {'recoverable_work_preserved': False}, {'ownership_epoch': 1}):
            evidence = copy.deepcopy(self.evidence)
            self.evidence['recovery'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(RecoveryHold):
                self.verifier()(self.context)
            self.evidence = evidence

    def test_wrong_issue_uuid_and_workspace_never_pass_by_human_identifier(self):
        for field in ('uuid', 'workspaceId'):
            self.reader.responses['linear.issue'][field] = PROJECT
            with self.subTest(field=field), self.assertRaisesRegex(RecoveryHold, 'remote_reconstruction_inconclusive'):
                self.verifier()(self.context)
            self.reader.responses['linear.issue'].update(uuid=ISSUE, workspaceId=WORKSPACE)

    def test_completed_or_canceled_issue_is_not_replayed(self):
        for state in ('completed', 'canceled'):
            self.reader.responses['linear.issue']['statusType'] = state
            with self.subTest(state=state), self.assertRaisesRegex(RecoveryHold, 'issue_not_eligible'):
                self.verifier()(self.context)

    def test_new_checkpoint_invalidates_old_absence_claim(self):
        self.saver.record = object()
        with self.assertRaisesRegex(RecoveryHold, 'checkpoint_absence_unverified'):
            self.verifier()(self.context)

    def test_expired_observations_block_before_claim(self):
        with self.assertRaises(RecoveryHold):
            self.verifier(lambda: NOW + timedelta(seconds=301))(self.context)

    def test_profile_does_not_make_foo_dependencies_cloud_requirements(self):
        self.assertIsNotNone(self.verifier()(self.context))
        self.context = RecoveryContext(self.context.key, self.source, 2, 'local', 'cloud-b', 'same')
        with self.assertRaisesRegex(RecoveryHold, 'reconstruction_target_mismatch'):
            self.verifier()(self.context)

    def test_latest_checkpoint_and_exact_graph_metadata_required(self):
        metadata = dict(linear_workspace_id=WORKSPACE, linear_project_id=PROJECT,
            linear_issue_id=ISSUE, owner='cloud-b', generation=2, repository='maldous/uif',
            source_base='a'*40, source_head='b'*40, source_tree='c'*40,
            graph_sha256='d'*64, schema_sha256='e'*64, code_sha256='f'*64,
            receipt_sha256='a'*64, completed=['coding'], pending=['review'], original_operations=['original'])
        self.evidence['recovery']['checkpoint'] = dict(disposition='resume', commit='a'*40,
            issue='UIF-247', owner='cloud-b', epoch=2, compatibility_verified=True,
            receipt_source='private-original-receipt')
        record = SimpleNamespace(config={'configurable': {'thread_id':'registered-thread', 'checkpoint_ns':'', 'checkpoint_id':'latest'}},
                                 checkpoint={'id':'latest', 'v':1}, metadata={'uif_recovery':metadata}, pending_writes=[])
        self.saver.record = record
        self.checkpoint = CheckpointRead(self.saver, 'registered-thread', '', 'latest', 1, metadata)
        self.assertIsNotNone(self.verifier()(self.context))
        record.pending_writes = [('original', 'pending')]
        with self.assertRaisesRegex(RecoveryHold, 'checkpoint_incompatible_or_pending'):
            self.verifier()(self.context)

    def test_other_project_requires_explicit_matching_scope(self):
        scope = ProjectScope('maldous/anqr-v3', 'develop', 'anqr', 'cloud', WORKSPACE,
                             remote_fixture.TEAM, PROJECT, 'ANQR')
        target = Target('d'*64, 'a'*40, 'ANQR-1', remote_fixture.TEAM, PROJECT, 8,
                        repository=scope.repository, project='anqr', scope=scope, issue_uuid=ISSUE)
        target.validate()
        with self.assertRaises(ValueError):
            Target('d'*64, 'a'*40, 'UIF-1', remote_fixture.TEAM, PROJECT, 8,
                   repository='maldous/uif', scope=scope, issue_uuid=ISSUE).validate()

    def test_predecessor_checkpoint_transfers_without_rewriting_original(self):
        original = dict(linear_workspace_id=WORKSPACE, linear_project_id=PROJECT,
            linear_issue_id=ISSUE, owner='foo-worker', generation=1, repository='maldous/uif',
            source_base='a'*40, source_head='b'*40, source_tree='c'*40,
            graph_sha256='d'*64, schema_sha256='e'*64, code_sha256='f'*64,
            receipt_sha256='a'*64, completed=['coding', 'validation'],
            pending=['review'], original_operations=['original'])
        record = SimpleNamespace(config={'configurable': {'thread_id':'registered-thread', 'checkpoint_ns':'', 'checkpoint_id':'origin'}},
            checkpoint={'id':'origin', 'v':1}, metadata={'uif_recovery':copy.deepcopy(original)}, pending_writes=[])
        self.saver.record = record
        self.checkpoint = CheckpointRead(self.saver, 'registered-thread', '', 'origin', 1, original)
        self.evidence['recovery']['checkpoint'] = {'disposition':'resume'}
        successor = SimpleNamespace(**{**self.context.__dict__, 'origin_owner':'foo-worker', 'origin_generation':1})
        self.assertIsNotNone(self.verifier()(successor))
        self.assertEqual(record.metadata['uif_recovery'], original)
        self.assertEqual(original['owner'], 'foo-worker')
        self.assertEqual(original['completed'], ['coding', 'validation'])
        stale = SimpleNamespace(**{**self.context.__dict__, 'origin_owner':'other-worker', 'origin_generation':1})
        with self.assertRaisesRegex(RecoveryHold, 'checkpoint_task_binding'):
            self.verifier()(stale)


if __name__ == '__main__':
    unittest.main()
