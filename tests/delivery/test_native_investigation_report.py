"""Original investigation reporting without a second turn or downstream effects."""
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from threading import current_thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

import test_marked_successor_entry as successor_fixture
from uif.handoff_store import Hold
from uif.effect_authority import Operation
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.investigation_report import render_investigation_report
from uif.stokowski_cloudflare.native_composition import _LinearClient
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable, ProviderReply
from uif.stokowski_cloudflare.shared_admission import SendIntent
from uif.handoff_store import wire


class InvestigationRendererTest(TestCase):
    def setUp(self):
        self.issue = SimpleNamespace(id='11111111-2222-4333-8444-555555555555', identifier='OFF-1', title='Fixture', description='Bounded investigation')
        self.scope = 'a' * 64
        self.attempt = intake_stages({}, 'OffMenu', self.issue, self.scope, 1)['investigate']['attempt']

    def render(self, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        return render_investigation_report(raw, issue=self.issue, run=1,
            scope_sha256=self.scope, attempt_id=self.attempt)

    def test_informative_unicode_summary_is_bounded_inside_json(self):
        summary = 'Évidence ` ``` <tag> @person'
        body = self.render({'summary': summary, 'verdict': 'approve'})
        self.assertIn('Évidence', body)
        for escaped in (r'\u0060', r'\u003c', r'\u003e', r'\u0040'):
            self.assertIn(escaped, body)
        witness = json.loads(body.split('```json\n', 1)[1].split('\n```', 1)[0])
        self.assertEqual(witness['summary'], summary)
        self.assertEqual(body.count('```'), 2)
        self.assertTrue(body.endswith('Independent review is required before a workflow transition.'))

    def test_summary_verdict_and_optional_routing_are_validated(self):
        for value in ({'summary': ' \t\r\n', 'verdict':'approve'},
                      {'summary':'é'*1025, 'verdict':'approve'},
                      {'summary':'x', 'verdict':'complete'},
                      {'summary':'x', 'verdict':'approve', 'routing':{'issue':'OFF-2','state':'investigate','run':1}}):
            with self.subTest(value=value), self.assertRaises(Hold):
                self.render(value)
        self.assertIn('blocked work.', self.render({'summary':'\u00a0','verdict':'blocked'}))
        with self.assertRaises(Hold):
            render_investigation_report(b'{"summary":"\\ud800","verdict":"approve"}', issue=self.issue,
                run=1, scope_sha256=self.scope, attempt_id=self.attempt)


class NativeInvestigationReportTest(successor_fixture.SuccessorEntryTest):
    async def finish_result(self, content):
        original = sha256(f'{self.successor}:1'.encode()).hexdigest()
        operation = Operation(self.claim.lease.fence, original, 'b'*64)
        intent = SendIntent(operation, self.remote.route, self.successor, 'b'*64, 'c'*64, 2, 1)
        self.raw = wire({'choices':[{'message':{'content':content}}]})
        self.remote.provider_result = Mock(side_effect=lambda _: ProviderReply(self.raw, None, 'c'*64))
        finish = self.native.successor_investigation_finisher(self.issue, {'attempt':self.successor}, 1)
        executor = None
        def validate():
            if current_thread() is not executor:
                raise Hold('foreign_thread')
        def fixed():
            self.native.retain_intent(intent)
            self.access.request('/v1/send', {'intent':asdict(intent)})
            finish(original, content)
        bound = self.native.prepare_successor_turn_thread(fixed, validate)
        def run():
            nonlocal executor
            executor = current_thread()
            try:
                return bound()
            finally:
                executor = None
        await asyncio.to_thread(run)

    async def test_fixed_report_after_tracking_and_read_only_recovery(self):
        self.advance_claim(); self.completed_workspace_fixture()
        selected = self.select()
        with self.enter(selected):
            client = _LinearClient(None, self.native)
            self.native._slot.set('_announce_state')
            tracking = ('<!-- stokowski:state {"state": "investigate", "run": 1, "timestamp": '
                        '"2030-01-11T12:00:00+00:00", "workflow": "offmenu"} -->\n\n'
                        '**[Stokowski]** Entering state: **investigate** (run 1) · workflow `offmenu`')
            await client.post_comment(self.issue.id, tracking)
            await self.finish_result('{"summary":"Evidence found", "verdict":"approve"}')
            permit = self.native._recipient_execution.get()
            body = permit.report.body
            self.native._slot.set('_publish_run_report')
            with self.assertRaisesRegex(Hold, 'report_scope'):
                await client.post_comment(self.issue.id, body + ' changed')
            self.assertTrue(await client.post_comment(self.issue.id, body))
            with self.assertRaisesRegex(Hold, 'already_prepared'):
                await client.post_comment(self.issue.id, body)
        self.native.observe_stage_report = Mock(return_value=('receipt', object()))
        result = self.native.observe_successor_report(self.claim.lease.fence, self.successor,
            self.issue, 1, self.scope, self.case.path)
        self.assertEqual(result[0], 'receipt')
        self.assertEqual(self.calls.count('/v1/send'), 1)
        self.assertEqual(self.calls.count('/v1/linear/comment'), 2)
        self.native.observe_stage_report.assert_called_once_with(self.claim.lease.fence, self.successor, body)
        self.raw += b' '
        with self.assertRaisesRegex(Hold, 'recovery_result'):
            self.native.observe_successor_report(self.claim.lease.fence, self.successor,
                self.issue, 1, self.scope, self.case.path)

    async def test_lost_response_recovers_original_without_reposting(self):
        self.advance_claim(); self.completed_workspace_fixture()
        with self.enter(self.select()):
            await self.finish_result('{"summary":"Evidence found", "verdict":"blocked"}')
            self.native._slot.set('_publish_run_report')
            body = self.native._recipient_execution.get().report.body
            original_request = self.access._request
            def request(path, *args, **kwargs):
                result = original_request(path, *args, **kwargs)
                if path == '/v1/linear/comment':
                    raise AuthorityUnavailable('lost response')
                return result
            self.access._request = request
            self.native.observe_stage_report = Mock(wraps=type(self.native).observe_stage_report.__get__(self.native))
            self.assertTrue(await _LinearClient(None, self.native).post_comment(self.issue.id, body))
            self.assertEqual(self.calls.count('/v1/linear/comment'), 1)
            self.native.observe_stage_report.assert_called_once()

    async def test_retained_unknown_original_does_not_send(self):
        self.advance_claim(); self.completed_workspace_fixture()
        with self.enter(self.select()):
            await self.finish_result('{"summary":"Evidence found", "verdict":"deferred"}')
            self.native._slot.set('_publish_run_report')
            permit = self.native._recipient_execution.get()
            operation = self.native._linear_operation(self.claim.lease.fence, self.successor,
                '_publish_run_report', 'comment', {'body':permit.report.body})
            self.native._retain_investigation_tracking_intent(operation)
            with self.assertRaisesRegex(Hold, 'report_outcome_unknown'):
                await _LinearClient(None, self.native).post_comment(self.issue.id, permit.report.body)
            self.assertNotIn('/v1/linear/comment', self.calls)

    async def test_foreign_slot_and_stale_owner_cannot_post_report(self):
        self.advance_claim(); self.completed_workspace_fixture()
        with self.enter(self.select()):
            await self.finish_result('{"summary":"Evidence found", "verdict":"approve"}')
            client = _LinearClient(None, self.native)
            body = self.native._recipient_execution.get().report.body
            self.native._slot.set('_enter_gate')
            with self.assertRaises(Hold):
                await client.post_comment(self.issue.id, body)
            self.native._slot.set('_publish_run_report')
            self.owner._issue_current_state[self.issue.id] = 'implement'
            with self.assertRaises(Hold):
                await client.post_comment(self.issue.id, body)
            self.assertNotIn('/v1/linear/comment', self.calls)
