"""Synthetic full ownership/white-room lifecycle; no live effects or saver writes.

The only checkpoint changes below are explicit fixture producer events. Transfer
and verifier calls must leave the original checkpoint's metadata unchanged.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'delivery'))
sys.path.insert(0, str(ROOT / 'tests' / 'white_room'))
import test_quota_handoff as authority_fixture
import test_transition as white_room_fixture
from tools.white_room.check import digest
from tools.white_room.transition import CheckpointRead, RecoveryInputs, WhiteRoomVerifier
from uif.handoff_store import Hold


class RegistryWhiteRoomLifecycle(unittest.TestCase):
    # Reuse the existing actor/operation fixtures, not a fake RecoveryReceipt.
    reconstruct = authority_fixture.QuotaHandoffTests.reconstruct
    request = authority_fixture.QuotaHandoffTests.request
    status = authority_fixture.QuotaHandoffTests.status
    provider = authority_fixture.QuotaHandoffTests.provider
    heartbeat = authority_fixture.QuotaHandoffTests.heartbeat
    claim = authority_fixture.QuotaHandoffTests.claim
    capture = authority_fixture.QuotaHandoffTests.capture
    acknowledge = authority_fixture.QuotaHandoffTests.acknowledge
    prepare_transfer = authority_fixture.QuotaHandoffTests.prepare_transfer

    def setUp(self):
        authority_fixture.QuotaHandoffTests.setUp(self)
        self.addCleanup(self.temp.cleanup)
        self.white = white_room_fixture.Transition()
        self.white.setUp()
        self.now = white_room_fixture.NOW.timestamp()
        self.source = deepcopy(authority_fixture.SOURCE)
        self.white.reader.responses['linear.issue'].update(
            uuid=self.key.issue_id, workspaceId=self.key.workspace_id,
            projectId=self.key.project_id)
        self.white.contract['requirements'][-1]['expected']['project_id'] = self.key.project_id
        self.metadata = dict(
            linear_workspace_id=self.key.workspace_id,
            linear_project_id=self.key.project_id, linear_issue_id=self.key.issue_id,
            owner='native', generation=1, repository=self.source['repository'],
            source_base=self.source['base'], source_head=self.source['head'],
            source_tree=self.source['tree'], graph_sha256='d' * 64,
            schema_sha256='e' * 64, code_sha256='f' * 64, receipt_sha256='a' * 64,
            completed=['coding', 'focused-checks'], pending=['review', 'publication'],
            original_operations=['original-' + self.key.issue_id])
        self.record = SimpleNamespace(
            config={'configurable': {'thread_id': 'original-thread', 'checkpoint_ns': '',
                                      'checkpoint_id': 'local-gen1'}},
            checkpoint={'id': 'local-gen1', 'v': 1},
            metadata={'uif_recovery': deepcopy(self.metadata)}, pending_writes=[])
        self.white.saver.record = self.record
        self.checkpoint = CheckpointRead(self.white.saver, 'original-thread', '',
                                         'local-gen1', 1, deepcopy(self.metadata))
        self.contexts = []
        self.registry.recovery_verifier = WhiteRoomVerifier(
            self.inputs, clock=lambda: white_room_fixture.NOW)

    def inputs(self, expected):
        """Trusted fixture acquisition bound to the proposed current claim."""
        self.contexts.append(expected)
        profile = 'foo' if expected.lane == 'local' else 'cloud'
        scope = replace(self.white.target.scope, profile=profile,
                        workspace_id=self.key.workspace_id, project_id=self.key.project_id)
        target = replace(self.white.target, profile=profile, scope=scope,
                         linear_project_id=self.key.project_id, issue_uuid=self.key.issue_id,
                         contract_sha256=digest(self.white.contract))
        evidence = deepcopy(self.white.evidence)
        evidence.update(profile=profile, contract_sha256=digest(self.white.contract))
        evidence['observations'].append(dict(id='windmill', state='compliant',
            actual=deepcopy(self.white.contract['requirements'][1]['expected']),
            source='synthetic-current-foo-observation', observed_at=white_room_fixture.NOW.isoformat()))
        evidence['recovery'].update(
            owner=expected.owner, ownership_epoch=expected.generation,
            authoritative_owner=expected.owner, authoritative_epoch=expected.generation,
            linear_workspace_id=self.key.workspace_id,
            linear_project_id=self.key.project_id, linear_issue_id=self.key.issue_id,
            branch=expected.source['branch'], candidate_head=expected.source['head'],
            candidate_tree=expected.source['tree'], checkpoint={'disposition': 'resume'})
        evidence['recovery']['published_checkpoint'].update(owner=expected.owner,
            ownership_epoch=expected.generation)
        return RecoveryInputs(deepcopy(self.white.contract), evidence, target,
                              self.white.reader, self.checkpoint)

    def cloud_effect(self, operation_id):
        return self.registry.perform_effect(principal='cloud-session', actor='cloud',
            key=self.key, generation=2, operation_id=operation_id,
            payload_sha256=authority_fixture.PIN,
            effect=lambda: dict(status='verified', receipt_sha256=authority_fixture.PIN))

    def test_origin_checkpoint_survives_ack_first_effect_and_successor_save(self):
        original = deepcopy(self.record.metadata)
        self.prepare_transfer()
        self.capture()
        self.assertEqual(self.acknowledge()['generation'], 2)
        self.assertEqual(self.record.metadata, original)
        self.assertEqual(self.cloud_effect('cloud-first-effect')['status'], 'verified')
        self.assertEqual(self.record.metadata, original)
        self.assertEqual((self.contexts[-1].origin_owner, self.contexts[-1].origin_generation),
                         ('native', 1))
        # A later, real producer event saves a successor checkpoint. The
        # verifier accepts it without requiring or modifying predecessor bytes.
        successor = deepcopy(self.metadata)
        successor.update(owner='cloud', generation=2, completed=['coding', 'focused-checks', 'review'],
                         pending=['publication'])
        self.record = SimpleNamespace(
            config={'configurable': {'thread_id': 'original-thread', 'checkpoint_ns': '',
                                      'checkpoint_id': 'cloud-gen2'}},
            checkpoint={'id': 'cloud-gen2', 'v': 1},
            metadata={'uif_recovery': deepcopy(successor)}, pending_writes=[])
        self.white.saver.record = self.record
        self.checkpoint = CheckpointRead(self.white.saver, 'original-thread', '',
                                         'cloud-gen2', 1, deepcopy(successor))
        self.assertEqual(self.cloud_effect('cloud-second-effect')['status'], 'verified')
        self.assertEqual(self.record.metadata['uif_recovery'], successor)
        self.assertEqual(original['uif_recovery']['owner'], 'native')
        with self.assertRaisesRegex(Hold, 'obsolete_claim'):
            self.registry.perform_effect(principal='native-session', actor='native',
                key=self.key, generation=1, operation_id='late-local-effect',
                payload_sha256=authority_fixture.PIN,
                effect=lambda: self.fail('obsolete owner reached effect'))

    def test_real_white_room_refusal_is_bounded_and_leaves_no_claim(self):
        self.provider('available', 1)
        self.heartbeat()
        self.white.contract['projects']['uif']['discovery_complete']['foo'] = False
        with self.assertRaisesRegex(Hold, 'reconstruction_unavailable'):
            self.claim()
        self.assertNotIn(self.key.identity, self.status()['tasks'])

    def test_uncertain_latest_checkpoint_holds_ack_and_preserves_original_owner(self):
        self.prepare_transfer()
        self.capture()
        self.record.pending_writes = [('original-operation', 'pending')]
        with self.assertRaisesRegex(Hold, 'reconstruction_unavailable'):
            self.acknowledge()
        current = self.status()['tasks'][self.key.identity]
        self.assertEqual((current['owner'], current['generation'], current['state']),
                         ('native', 1, 'handoff-pending'))


if __name__ == '__main__':
    unittest.main()
