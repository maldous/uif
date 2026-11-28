"""Reproduce the native tracking boundary on the selected Foo preimage, without egress."""
import ast
import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from uif.handoff_store import Hold
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable
from uif.stokowski_cloudflare.native_composition import (
    NativeComposition, _COMMENT_SLOTS, _FreshAssessmentExecution, _LinearClient, install_sinks,
)
import test_native_remote_authority as fixture


class PinnedNativeSuccessorTrackingTest(IsolatedAsyncioTestCase):
    def setUp(self):
        source = os.environ.get('UIF_PINNED_STOKOWSKI_ASSESSMENT_SOURCE')
        if source is None:
            self.skipTest('select native source with UIF_PINNED_STOKOWSKI_ASSESSMENT_SOURCE')
        root = Path(source)
        core = (root / 'orchestrator.py').read_bytes()
        tracking = (root / 'tracking.py').read_bytes()
        self.assertEqual(sha256(core).hexdigest(), '610cbf97a5f36c9dd305cfe456245a9efc29625287b44211adbd5cea77c3511f')
        self.assertEqual(sha256(tracking).hexdigest(), '6bcc4948cc563fafdd806c108227cff9798e0946bb1f5c82fb8c1b0146d9f770')
        names = ('_resume_settlement', '_transition', '_announce_state')
        controller = next(node for node in ast.parse(core).body
                          if isinstance(node, ast.ClassDef) and node.name == 'Orchestrator')
        methods = [node for node in controller.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        self.assertEqual({node.name for node in methods}, set(names))
        namespace = {}
        exec(compile(tracking, 'tracking.py', 'exec'), namespace)
        module = ast.Module(body=[
            ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
            *methods,
        ], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), 'orchestrator.py', 'exec'), namespace)
        owner_type = type('SelectedNative', (), {key: namespace[key] for key in names})
        for key in (*_COMMENT_SLOTS, '_reconcile'):
            if not hasattr(owner_type, key):
                setattr(owner_type, key, AsyncMock())
        self.worker, remote = fixture.make()
        self.composition = NativeComposition(remote, '/unused')
        install_sinks(SimpleNamespace(Orchestrator=owner_type, LinearClient=object, logger=Mock()), self.composition)
        self.owner = owner_type()
        self.native_settle = namespace['_resume_settlement'].__get__(self.owner)
        self.owner.project_name = 'OffMenu'
        self.issue = SimpleNamespace(id=fixture.ISSUE, identifier='OFF-1')
        self.record = dict(issue_id=self.issue.id, session_id=fixture.RECORD.attempt_id,
                           run=1, kind='worker', state='assess', phase='transition',
                           event='complete', report_posted=True, target='investigate')
        self.attempt = SimpleNamespace(state_name='assess', session_id=fixture.RECORD.attempt_id,
                                       status='settling', settlement_record=self.record)
        self.owner.running = {self.issue.id: self.attempt}
        self.owner._issue_current_state = {self.issue.id: 'assess'}
        self.owner._issue_state_runs = {self.issue.id: 1}
        self.owner._states_for = lambda issue: {'assess': SimpleNamespace(transitions={'complete': 'investigate'}),
                                                 'investigate': SimpleNamespace(type='agent')}
        self.owner._settlement_is_terminal = AsyncMock(return_value=False)
        self.owner._workflow_name_for = lambda issue: 'offmenu'
        self.owner._announced_states = set()
        self.owner._ensure_linear_client = lambda: _LinearClient(None, self.composition)
        self.owner._schedule_retry = Mock()
        self.saved = []
        self.owner._save_settlement = lambda issue, record: self.saved.append(deepcopy(record))

    async def test_refused_assessment_tracking_consumes_native_attempt_before_marker_or_retry(self):
        # This is the pinned preimage, not a proposed completion or attestation.
        # Stop at the physical writer. The authority separately rejects every
        # marked-assess comment other than its canonical assessment report.
        writes = []
        def refuse_marker(kind, issue_id, payload):
            _, claim = self.composition._running.get()
            writes.append((claim.attempt_id, self.composition._slot.get(), payload['body']))
            raise Hold('fixture_tracking_write_refused')
        self.composition.linear = refuse_marker
        self.composition._running.set((fixture.GRANT, fixture.RECORD))
        self.composition._fresh_assessment.set(_FreshAssessmentExecution(
            fixture.RECORD, Path('/unused'), SimpleNamespace(winner=asyncio.current_task())))
        self.assertFalse(await self.native_settle(self.issue, self.attempt))
        self.assertEqual(len(writes), 1)
        attempt_id, slot, marker = writes[0]
        self.assertEqual((attempt_id, slot), (fixture.RECORD.attempt_id, '_announce_state'))
        self.assertIn('<!-- stokowski:state {"state": "investigate", "run": 1,', marker)
        self.assertIn('"workflow": "offmenu"', marker)
        self.assertIn('fixture_tracking_write_refused', self.attempt.error)
        self.assertIs(self.saved[0]['attempted'], True)
        self.assertTrue(self.record['attempted'])
        self.assertEqual(self.owner._issue_current_state[self.issue.id], 'investigate')
        self.assertFalse(await self.native_settle(self.issue, self.attempt))
        self.assertIn('Original non-gate effect outcome unresolved', self.attempt.error)
        self.assertEqual(len(writes), 1)
        self.owner._schedule_retry.assert_not_called()
        self.assertEqual(self.owner._announced_states, set())
        self.assertEqual(self.worker.calls, [])

    async def test_only_current_successor_records_and_recovers_its_original_tracking(self):
        issue = SimpleNamespace(id=self.issue.id, identifier='OFF-1', title='Fixture', description='')
        stages = intake_stages({}, 'OffMenu', issue, fixture.GRANT.scope_sha256, 1)
        previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
        grant = replace(fixture.GRANT, attempts=(previous, successor))
        claim = replace(fixture.RECORD, attempt_id=previous)
        self.worker.claim = claim
        self.composition.assignment = lambda *args: grant
        self.composition._running.set((grant, claim))
        with TemporaryDirectory() as workspace:
            (Path(workspace) / '.git').mkdir()
            item = {'workspace_binding': {'path': workspace},
                    'fresh_assessment': {'attempt_id': previous, 'first_intent_sha256': 'e' * 64}}
            self.composition.installation = lambda: {'assignments': {issue.id: item}}
            with self.assertRaisesRegex(Hold, 'assessment_tracking_not_admitted'):
                await self.owner._announce_state(issue, 'investigate', 1)
            directory = Path(workspace) / '.git/uif-original-effects'
            self.assertFalse(directory.exists())

            # Supply the authority's post-CAS state. This test only exercises
            # tracking; control attestation and CAS are tested on the actual Worker.
            claim = replace(claim, attempt_id=successor)
            self.worker.claim = claim
            self.composition._running.set((grant, claim))
            inherited = self.composition._fresh_assessment.set(_FreshAssessmentExecution(
                replace(claim, attempt_id=previous), Path(workspace)))
            try:
                with self.assertRaisesRegex(Hold, 'successor_tracking_in_assessment'):
                    await self.owner._announce_state(issue, 'investigate', 1)
            finally:
                self.composition._fresh_assessment.reset(inherited)
            self.assertFalse(directory.exists())

            transport = self.worker.request
            def lost_reply(path, body=None, **options):
                result = transport(path, body, **options)
                if path == '/v1/linear/comment':
                    raise AuthorityUnavailable('fixture_lost_reply')
                return result
            self.worker.request = lost_reply
            with self.assertRaises(AuthorityUnavailable):
                await self.owner._announce_state(issue, 'investigate', 1)
            self.assertEqual(self.owner._announced_states, set())
            # A new native timestamp after loss still recovers the exact marker.
            await self.owner._announce_state(issue, 'investigate', 1)
            self.assertIn((issue.id, 'investigate', 1), self.owner._announced_states)
            sends = [data for path, data, _ in self.worker.calls if path == '/v1/linear/comment']
            self.assertEqual(len(sends), 1)
            self.assertEqual(sends[0]['attempt_id'], successor)
            self.assertIn('<!-- stokowski:state {"state": "investigate", "run": 1,', sends[0]['body'])
            self.assertIn('"workflow": "offmenu"', sends[0]['body'])
            self.owner._announced_states.clear()
            self.worker.effects.clear()
            with self.assertRaisesRegex(Hold, 'successor_tracking_outcome_unknown'):
                await self.owner._announce_state(issue, 'investigate', 1)
            self.assertEqual(self.owner._announced_states, set())
            self.owner._workflow_name_for = lambda issue: 'changed'
            with self.assertRaisesRegex(Hold, 'semantics_changed'):
                await self.owner._announce_state(issue, 'investigate', 1)
            with self.assertRaisesRegex(Hold, 'successor_tracking_attempt'):
                await self.owner._announce_state(issue, 'investigate', 2)
            self.assertEqual(sum(path == '/v1/linear/comment' for path, _, _ in self.worker.calls), 1)
