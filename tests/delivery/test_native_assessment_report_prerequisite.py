"""Offline report-boundary reproduction against explicitly selected Foo source."""
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from uif.handoff_store import Hold, wire
from uif.stokowski_cloudflare.assessment_readiness import project_assessment_readiness
from uif.stokowski_cloudflare.fresh_assessment_render import render_fresh_assessment
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.native import _native_report
from uif.stokowski_cloudflare.native_composition import _LinearClient


PINS = {
    'orchestrator.py': '610cbf97a5f36c9dd305cfe456245a9efc29625287b44211adbd5cea77c3511f',
    'report.py': '6f72f4cc2e39f92e4da6a44d5240ec7ba44f9f8fd94d611271ca8be905eb0fe6',
    'publication.py': '7d2007cc7993e496e11a0da30152278951b31f2c4f42c9dcacd0582619aca48b',
    'dependencies.py': '298694e9b728041716843a6f99a43671dbc0c6a75e3f67a25c9c4ca6aef36d06',
}
PATCH = Path(__file__).resolve().parents[2] / 'tools/delivery/stokowski-verified-assessment-report.patch'
PATCHED_ORCHESTRATOR_SHA256 = '228523e5a9e78ed5ad3ea78c8fe8f37093e5ce2cf5460068215ffb655028fa96'


class NativeAssessmentReportPrerequisiteTest(IsolatedAsyncioTestCase):
    def source(self, name, bindings=(), *, owner=None, patched=False, **namespace):
        raw = (self.root / name).read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), PINS[name])
        if patched:
            self.assertEqual(name, 'orchestrator.py')
            with TemporaryDirectory() as scratch:
                target = Path(scratch) / 'stokowski' / name
                target.parent.mkdir()
                target.write_bytes(raw)
                result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                                        cwd=scratch, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('offset', result.stdout)
                raw = target.read_bytes()
                self.assertEqual(sha256(raw).hexdigest(), PATCHED_ORCHESTRATOR_SHA256)
        parsed = ast.parse(raw)
        if bindings:
            nodes = parsed.body if owner is None else next(
                node.body for node in parsed.body
                if isinstance(node, ast.ClassDef) and node.name == owner
            )
            selected = [node for node in nodes
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name in bindings]
            self.assertEqual(len(selected), len(bindings))
            parsed.body = [ast.ImportFrom(module='__future__',
                           names=[ast.alias(name='annotations')], level=0), *selected]
            ast.fix_missing_locations(parsed)
        exec(compile(parsed, name, 'exec'), namespace)
        return SimpleNamespace(**namespace)

    def setUp(self):
        root = os.environ.get('UIF_PINNED_STOKOWSKI_ASSESSMENT_SOURCE')
        if root is None:
            self.skipTest('set UIF_PINNED_STOKOWSKI_ASSESSMENT_SOURCE to the selected Foo native source')
        self.root = Path(root)
        self.report = self.source('report.py', __name__='native_report_fixture')
        self.publication = self.source('publication.py', ('scope', 'human_context'),
            hashlib=hashlib, json=json,
            INTENT_MARKER='<!-- stokowski:publication-intent -->\n')
        self.publication.retain_stage_evidence = Mock(return_value='.stokowski/private-retention')
        dependencies = self.source('dependencies.py')
        methods = ('_publication_current_issue', '_publication_comments',
                   '_publish_run_report', '_completion_event', '_resume_settlement')
        self.native_bindings = dict(
            report_mod=self.report, publication_mod=self.publication, dependencies_mod=dependencies,
            artifacts_mod=SimpleNamespace(collect=Mock(return_value=[]), MAX_ARTIFACT_BYTES=1),
            state_key=str.casefold, datetime=datetime, timezone=timezone,
            asyncio=asyncio, logger=Mock(), Path=Path, RunAttempt=SimpleNamespace)
        methods = self.source('orchestrator.py', methods, owner='Orchestrator', **self.native_bindings)
        self.owner = type('NativeReportFixture', (), {
            name: method for name, method in vars(methods).items() if name.startswith('_')
            and callable(method)
        })()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.issue = SimpleNamespace(id='11111111-2222-4333-8444-555555555555',
            identifier='OFF-1', title='Fixture issue', description=None, state='Active', blocked_by=[])
        self.raw = wire(dict(summary='Private provider text', verdict='approve', routing=dict(
            issue=self.issue.identifier, state='assess', run=1, actionable=True, ownership_clear=True,
            deferred=False, owner_pause=False, uncertain_effects=False, missing_inputs=[])))
        self.reader = Mock(fetch_issue=AsyncMock(return_value=self.issue), _graphql=AsyncMock(
            return_value={'issue': {'comments': {'nodes': [], 'pageInfo': {'hasNextPage': False}}}}))
        self.owner.cfg = SimpleNamespace(active_linear_states=lambda: ['Active'],
            terminal_linear_states=lambda: ['Done'], tracker=SimpleNamespace(project_slug='offmenu'))
        self.owner._ensure_linear_client = lambda: self.reader
        self.owner._states_for = lambda issue: {'assess': SimpleNamespace(
            transitions={'complete': 'investigate', 'report-blocked': 'review'},
            publication=None, admission_review=True)}
        self.owner._issue_state_runs = {self.issue.id: 1}
        self.owner._issue_current_state = {self.issue.id: 'assess'}
        self.owner._publication_receipts = {}
        self.owner._workflow_name_for = lambda issue: 'fixture'
        self.owner.ledger = Mock()
        self.owner.project_name = 'OffMenu'
        self.owner._apply_classification_label = AsyncMock()
        self.owner._transition = AsyncMock(return_value=True)
        self.owner._finish_worker_bookkeeping = Mock()
        self.owner._settlement_path = lambda issue: self.workspace
        self.owner._save_settlement = Mock(side_effect=lambda issue, data: self.saved.append(deepcopy(data)))

    def prepared_attempt(self, raw):
        record = dict(phase='report-unknown', state='assess', run=1, kind='worker',
                      issue_id=self.issue.id, session_id='fixture', scope_sha256=self.publication.scope(self.issue))
        attempt = SimpleNamespace(status='succeeded', state_name='assess', attempt=0, session_id='fixture',
            result_text=_native_report(raw.decode()), started_at=None, model='fixture',
            total_tokens=0, cost_usd=0, tool_call_count=0, tool_error_count=0, artifacts=[],
            publication_event=None, error=None, report_posted=False, settlement_record=record,
            admission_context=self.publication.human_context([]), admission_state='active')
        self.owner.running = {self.issue.id: attempt}
        self.owner._tasks = {self.issue.id: asyncio.current_task()}
        self.saved = []
        return attempt

    def deterministic_body(self, raw):
        scope = self.publication.scope(self.issue)
        attempt = intake_stages({}, 'OffMenu', self.issue, scope, 1)['assess']['attempt']
        return render_fresh_assessment(raw, issue=self.issue, run=1, scope_sha256=scope, attempt_id=attempt)

    async def test_composition_preserves_native_pagination_and_restricts_transport(self):
        client = _LinearClient(self.reader, None)
        self.owner._ensure_linear_client = lambda: client
        first = dict(id='first', body='first page', createdAt='a', updatedAt='a')
        second = dict(id='second', body='second page', createdAt='b', updatedAt='b')

        def page(node, cursor=None):
            return {'issue': {'comments': {
                'nodes': [node], 'pageInfo': dict(hasNextPage=cursor is not None, endCursor=cursor)}}}

        self.reader._graphql.side_effect = [page(second, 'next'), page(first)]
        self.assertEqual(await self.owner._publication_comments(self.issue.id), [first, second])
        first_read, second_read = self.reader._graphql.await_args_list
        query, variables = first_read.args
        self.assertEqual(variables, dict(issueId=self.issue.id, after=None))
        self.assertEqual(second_read.args, (query, dict(issueId=self.issue.id, after='next')))

        with self.assertRaisesRegex(Hold, 'publication_comments_query'):
            await client._graphql(query + '\nmutation { commentCreate(input:{}) { success } }', variables)
        with self.assertRaisesRegex(Hold, 'publication_comments_variables'):
            await client._graphql(query, variables | {'extra': 'unapproved'})
        with self.assertRaisesRegex(Hold, 'publication_comments_variables'):
            await client._graphql(query, variables | {'after': []})
        self.assertEqual(self.reader._graphql.await_count, 2)

        self.reader._graphql.side_effect = [page(first, 'next'), page(first)]
        with self.assertRaisesRegex(RuntimeError, 'changed or is incomplete'):
            await self.owner._publication_comments(self.issue.id)

    async def test_provider_ready_projection_requires_native_substantive_evidence(self):
        thin = self.prepared_attempt(self.raw)
        self.assertEqual(project_assessment_readiness(self.raw, issue='OFF-1', run=1).readiness,
                         'reported-ready')
        self.reader.post_comment = AsyncMock(return_value=False)
        await self.owner._publish_run_report(self.issue, thin, self.workspace, 'assess')
        self.assertEqual(self.saved[0]['post_disposition']['event'], 'report-blocked')
        self.assertNotEqual(self.saved[0]['expected_report_body'], self.deterministic_body(self.raw))

    async def test_native_lost_ack_reconciles_exact_frozen_body_and_disposition(self):
        raw = wire(json.loads(self.raw) | {'data_sources': [{'name': 'fixture', 'how_verified': 'read'}]})
        attempt = self.prepared_attempt(raw)
        sent = []

        async def lose_ack(issue_id, body):
            self.assertEqual(issue_id, self.issue.id)
            self.assertEqual(self.saved[-1]['expected_report_body'], body)
            self.assertEqual(self.saved[-1]['post_disposition']['event'], 'complete')
            sent.append(body)
            raise OSError('fixture lost acknowledgement')

        self.reader.post_comment = AsyncMock(side_effect=lose_ack)
        with self.assertRaisesRegex(OSError, 'lost acknowledgement'):
            await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess')
        self.reader.fetch_issue.assert_awaited_once()
        self.reader._graphql.assert_awaited_once()
        self.assertIn('### Retained original stage evidence', sent[0])
        self.assertIn('Private provider text', sent[0])
        self.assertNotEqual(sent[0], self.deterministic_body(raw))
        attempt.status = 'settling'

        def visible(body):
            self.reader._graphql.return_value['issue']['comments']['nodes'] = [
                dict(id='fixture-report', body=body, createdAt='a', updatedAt='a')]

        visible(self.deterministic_body(raw))
        self.assertFalse(await self.owner._resume_settlement(self.issue, attempt))
        self.owner._transition.assert_not_awaited()
        visible(sent[0])
        self.assertTrue(await self.owner._resume_settlement(self.issue, attempt))
        self.assertEqual(attempt.settlement_record['phase'], 'complete')
        self.owner._transition.assert_awaited_once()
        self.reader.post_comment.assert_awaited_once()

    async def test_native_disposition_holds_changed_qualified_inputs(self):
        raw = wire(json.loads(self.raw) | {'data_sources': [{'name': 'fixture', 'how_verified': 'read'}]})
        human = dict(id='owner', body='Scope changed', createdAt='a', updatedAt='a')
        for changed, error in (
                ('scope', 'Original issue scope/state changed'),
                ('human', 'Intake ownership/input/dependency context changed'),
                ('blocker', 'Intake ownership/input/dependency context changed'),
                ('dependency', 'Explicit dependency input unavailable')):
            with self.subTest(changed=changed):
                attempt = self.prepared_attempt(raw)
                current = deepcopy(self.issue)
                if changed == 'scope':
                    current.title = 'Changed issue'
                if changed == 'blocker':
                    current.blocked_by = [SimpleNamespace(state='Active')]
                self.reader.fetch_issue.return_value = current
                nodes = self.reader._graphql.return_value['issue']['comments']
                nodes['nodes'] = [human] if changed == 'human' else []
                attempt.assessed_input = {'dependency_sources': [], 'dependency_observations': []}
                self.owner._dependency_input_snapshot = AsyncMock(
                    return_value=([{'known': False}] if changed == 'dependency' else [], []))
                self.reader.post_comment = AsyncMock(return_value=False)
                await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess')
                disposition = self.saved[0]['post_disposition']
                self.assertEqual(disposition['event'], 'report-blocked')
                self.assertIn(error, disposition['error'])

    def use_patched_report(self):
        method = self.source('orchestrator.py', ('_publish_run_report',), owner='Orchestrator',
                             patched=True, **self.native_bindings)._publish_run_report
        self.owner._publish_run_report = method.__get__(self.owner)

    def prepared_verified_attempt(self, raw):
        attempt = self.prepared_attempt(raw)
        attempt.assessed_input = {'dependency_sources': [], 'dependency_observations': []}
        self.owner._dependency_input_snapshot = AsyncMock(return_value=([], []))
        callback = Mock(return_value=(_native_report(raw.decode()), json.loads(raw),
                                      self.deterministic_body(raw)))
        return attempt, callback

    async def test_patched_report_preserves_the_default_native_body(self):
        attempt = self.prepared_attempt(self.raw)
        self.reader.post_comment = AsyncMock(return_value=False)
        await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess')
        original = self.saved[0]

        self.use_patched_report()
        attempt = self.prepared_attempt(self.raw)
        await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess')
        self.assertEqual(self.saved[0], original)
        self.assertEqual(self.reader.post_comment.await_args_list[0],
                         self.reader.post_comment.await_args_list[1])
        self.assertEqual(self.owner._apply_classification_label.await_count, 2)

    async def test_patched_report_freezes_approved_body_and_recovers_once(self):
        self.use_patched_report()
        raw = wire(json.loads(self.raw) | {'data_sources': [{'name': 'fixture', 'how_verified': 'read'}]})
        attempt, callback = self.prepared_verified_attempt(raw)
        body = self.deterministic_body(raw)

        def lose_ack(kind, issue_id, payload):
            self.assertEqual((kind, issue_id, payload), ('comment', self.issue.id, {'body': body}))
            self.assertEqual(self.saved[-1]['expected_report_body'], body)
            self.assertEqual(self.saved[-1]['post_disposition']['event'], 'complete')
            raise OSError('fixture lost acknowledgement')

        composition = Mock(linear=Mock(side_effect=lose_ack))
        self.owner._ensure_linear_client = lambda: _LinearClient(self.reader, composition)
        with self.assertRaisesRegex(OSError, 'lost acknowledgement'):
            await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess',
                                                 verified_assessment_report=callback)
        callback.assert_called_once_with(self.issue.id, attempt.session_id, 1)
        self.owner._apply_classification_label.assert_not_awaited()
        with self.assertRaisesRegex(RuntimeError, 'unfrozen native'):
            await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess',
                                                 verified_assessment_report=callback)
        attempt.status = 'settling'
        posted = dict(id='receipt', body=body, createdAt='a', updatedAt='a')
        self.reader._graphql.return_value['issue']['comments']['nodes'] = [posted]
        self.assertTrue(await self.owner._resume_settlement(self.issue, attempt))
        self.assertEqual(attempt.settlement_record['phase'], 'complete')
        self.owner._transition.assert_awaited_once()
        composition.linear.assert_called_once()
        callback.assert_called_once()

    async def test_patched_report_refuses_native_holds_and_provider_mismatch(self):
        self.use_patched_report()
        self.reader.post_comment = AsyncMock(return_value=False)
        raw = wire(json.loads(self.raw) | {'data_sources': [{'name': 'fixture', 'how_verified': 'read'}]})
        for changed in ('thin', 'blocker', 'result-envelope', 'workspace-report', 'settlement-custody'):
            with self.subTest(changed=changed):
                attempt, callback = self.prepared_verified_attempt(self.raw if changed == 'thin' else raw)
                current = deepcopy(self.issue)
                self.reader.fetch_issue.return_value = current
                if changed == 'blocker':
                    current.blocked_by = [SimpleNamespace(state='Active')]
                if changed == 'result-envelope':
                    attempt.result_text += '\n'
                if changed == 'workspace-report':
                    shadow = self.workspace / '.stokowski' / 'report.json'
                    shadow.parent.mkdir()
                    shadow.write_bytes(wire(json.loads(raw) | {'summary': 'Unverified local shadow'}))
                if changed == 'settlement-custody':
                    async def replaced_settlement(*args):
                        attempt.settlement_record = None
                        return current
                    self.reader.fetch_issue.side_effect = replaced_settlement
                with self.assertRaisesRegex(RuntimeError, 'Verified assessment'):
                    await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess',
                                                         verified_assessment_report=callback)
                self.assertEqual(self.saved, [])
                if changed == 'workspace-report':
                    shadow.unlink()
        self.reader.post_comment.assert_not_awaited()

    async def test_patched_report_keeps_the_existing_report_only_effect_scope(self):
        self.use_patched_report()
        self.reader.post_comment = AsyncMock(return_value=True)
        self.reader.upload_file = AsyncMock()
        self.publication.retain_working_source = Mock()
        self.publication.verified_stage_evidence = Mock(return_value={'artifacts': []})
        artifacts = self.native_bindings['artifacts_mod']
        artifacts.clear = Mock()

        blocked = wire(json.loads(self.raw) | {'verdict': 'blocked', 'classification': 'chore'})
        attempt, callback = self.prepared_verified_attempt(blocked)
        await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess',
                                             verified_assessment_report=callback)
        self.assertTrue(attempt.report_posted)
        self.assertEqual(self.saved[0]['post_disposition']['event'], 'report-blocked')
        self.reader.post_comment.assert_awaited_once_with(self.issue.id, self.deterministic_body(blocked))
        self.reader.post_comment.reset_mock()
        self.owner._apply_classification_label.assert_not_awaited()

        raw = json.loads(self.raw) | {'data_sources': [{'name': 'fixture', 'how_verified': 'read'}]}
        raw['routing']['working_source'] = {'ref': 'unapproved'}
        attempt, callback = self.prepared_verified_attempt(wire(raw))
        with self.assertRaisesRegex(RuntimeError, 'lacks current native admission evidence'):
            await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess',
                                                 verified_assessment_report=callback)
        self.publication.retain_working_source.assert_not_called()
        callback.assert_not_called()

        artifacts.collect.return_value = [self.workspace / 'artifact.txt']
        attempt, callback = self.prepared_verified_attempt(blocked)
        with self.assertRaisesRegex(RuntimeError, 'cannot upload artifacts'):
            await self.owner._publish_run_report(self.issue, attempt, self.workspace, 'assess',
                                                 verified_assessment_report=callback)
        self.reader.upload_file.assert_not_awaited()
        self.reader.post_comment.assert_not_awaited()
