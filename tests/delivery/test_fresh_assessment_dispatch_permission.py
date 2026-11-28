import asyncio
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from uif.handoff_store import Hold, wire
from uif.stokowski_cloudflare.remote_authority import AccessTransport, AuthorityUnavailable
import test_native_remote_authority as fixture


class PermissionTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.access = AccessTransport('https://authority.example', '/unused')
        self.claim = fixture.RECORD
        self.admission = {'attempt_id': self.claim.attempt_id, 'first_intent_sha256': 'a' * 64}
        self.secret = 'e' * 64
        self.body = {'current': asdict(self.claim.lease), **self.admission}

    async def test_exact_effect_paths_across_inherited_task_with_no_ambient_leak(self):
        responses = iter([{'entered': True, 'permit': self.secret}, {}, {}, {}, {}, {}])
        http = Mock()
        def reply():
            response = Mock(status=200)
            response.getheader.return_value = 'application/json'
            response.read.return_value = wire(next(responses))
            return response
        http.getresponse.side_effect = reply
        edge = {'attempt_id': self.claim.attempt_id, 'operation': {'fence': asdict(self.claim.lease.fence)}}
        from uif.stokowski_cloudflare.native_composition import (
            _COMMENT_SLOTS, _FreshAssessmentExecution, install_sinks,
        )
        _, remote = fixture.make()
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        composition = NativeComposition(remote, '/unused')
        composition.publish_fresh_assessment_report = Mock(side_effect=lambda *args:
            self.access.request('/v1/linear/comment', edge))
        class Owner:
            project_name = 'OffMenu'
        async def no_legacy(*args):
            self.fail('legacy report or side-effect path ran')
        for slot in (*_COMMENT_SLOTS, '_reconcile', '_resume_settlement'):
            setattr(Owner, slot, no_legacy)
        install_sinks(SimpleNamespace(LinearClient=type('FixtureLinear', (), {}),
                                      Orchestrator=Owner), composition)
        owner = Owner()
        issue = SimpleNamespace(id=self.claim.lease.fence.scope.key.issue_id)
        attempt = SimpleNamespace(state_name='assess')
        owner.running, owner._tasks = {issue.id: attempt}, {}
        delayed, release = [], asyncio.Event()

        async def delayed_child():
            await release.wait()
            with self.assertRaisesRegex(Hold, 'dispatch_permit_revoked'):
                await asyncio.to_thread(self.access.request, '/v1/linear/comment', edge)

        async def child():
            with composition.fresh_assessment_worker(owner, issue, attempt):
                delayed.append(asyncio.create_task(delayed_child()))
                await asyncio.to_thread(self.access.request, '/v1/send', {'intent': edge})
                await owner._publish_run_report(issue, attempt, Path('/isolated-fixture'), 'assess')
            self.access.request('/v1/linear/upload', edge)
            self.access.request('/v1/provider/receipt', {})
            with self.assertRaisesRegex(Hold, 'dispatch_egress_scope'):
                self.access.request('/v1/send', {'intent': edge | {'attempt_id': 'wrong'}})
        with patch('uif.stokowski_cloudflare.remote_authority.HTTPSConnection', return_value=http), \
             patch('uif.stokowski_cloudflare.remote_authority.protected_record', return_value={
                 'client_id': 'fixture', 'client_secret': 'fixture-secret'}):
            with self.access.fresh_assessment_permission(self.claim, self.admission) as holder:
                binding = composition._fresh_assessment.set(
                    _FreshAssessmentExecution(self.claim, Path('/isolated-fixture'), holder))
                try:
                    task = owner._tasks[issue.id] = asyncio.create_task(child())
                    composition.attach_fresh_assessment_winner(owner, issue, None)
                finally:
                    composition._fresh_assessment.reset(binding)
            await task
            release.set()
            await delayed[0]
            self.access.request('/v1/send', {'intent': edge})
        composition.publish_fresh_assessment_report.assert_called_once()
        calls = http.request.call_args_list
        self.assertEqual([call.args[1] for call in calls], ['/v1/dispatch/enter', '/v1/send',
            '/v1/linear/comment', '/v1/linear/upload', '/v1/provider/receipt', '/v1/send'])
        self.assertEqual(calls[0].kwargs['body'], wire(self.body))
        self.assertEqual([call.kwargs['headers'].get('uif-dispatch-permit') for call in calls],
                         [None, self.secret, self.secret, None, None, None])
        self.assertTrue(all(self.secret.encode() not in call.kwargs['body'] for call in calls))

    async def test_replay_malformed_and_uncertainty_never_create_capability(self):
        for reply in ({'entered': False}, {'entered': True}, {'entered': True, 'permit': 'bad'},
                      {'entered': True, 'permit': self.secret, 'extra': 1}):
            with patch.object(self.access, 'request', return_value=reply) as request:
                with self.assertRaisesRegex(Hold, 'not_first_confirmed'):
                    with self.access.fresh_assessment_permission(self.claim, self.admission):
                        self.fail('permit acquired')
                request.assert_called_once_with('/v1/dispatch/enter', self.body)
        with patch.object(self.access, 'request', side_effect=[
                AuthorityUnavailable('lost'), {'entered': True}]) as request:
            with self.assertRaisesRegex(AuthorityUnavailable, 'outcome_unknown'):
                with self.access.fresh_assessment_permission(self.claim, self.admission):
                    self.fail('observation promoted')
            self.assertEqual([call.args for call in request.call_args_list], [
                ('/v1/dispatch/enter', self.body), ('/v1/dispatch/observe', self.body)])
        self.assertIsNone(self.access._dispatch_permit.get())

    async def test_one_shot_provider_scope_has_no_tools_or_fallback(self):
        from uif.stokowski_cloudflare.turn import Attempt, run_turn
        from uif.stokowski_cloudflare.client import WorkersAIRequestError
        client, retained, tools = Mock(), Mock(), Mock()
        fresh = Attempt('OffMenu', 'test-assessment')
        exec_run = lambda: run_turn(client, fresh, 'Bounded fixture', tools=[{
            'function': {'name': 'unexpected'}}], execute_tool=tools,
            active_projects=lambda: {'OffMenu'}, cancelled=lambda: False, timeout=4,
            structured_report=True, finish_original=retained, one_shot_report=True)
        client.complete.return_value = {'choices': [{'message': {'content': '', 'tool_calls': [{}]}}]}
        with self.assertRaisesRegex(WorkersAIRequestError, 'tool during a report-only'):
            exec_run()
        self.assertEqual(client.complete.call_count, 1)
        self.assertEqual(client.complete.call_args.kwargs['tools'], [])
        tools.assert_not_called()
        retained.assert_not_called()
        client.reset_mock()
        client.complete.return_value = {'choices': [{'message': {'content':
            '{"summary":"fixture","verdict":"blocked","routing":{}}'}}]}
        # The receipt callback owns semantic validation, not this syntactic turn.
        result = exec_run()
        self.assertEqual(client.complete.call_count, 1)
        retained.assert_called_once_with(client.complete.call_args.kwargs['request_id'], result)

    async def test_winner_cancelled_before_start_revokes_copies(self):
        ready = asyncio.Event()
        copied = []
        async def descendant():
            await ready.wait()
            copied.append(self.access._dispatch_permit.get())
            with self.assertRaisesRegex(Hold, 'dispatch_permit_revoked'):
                copied[-1].for_effect()
        with patch.object(self.access, 'request', return_value={'entered': True, 'permit': self.secret}):
            with self.access.fresh_assessment_permission(self.claim, self.admission) as holder:
                sleeper = asyncio.create_task(asyncio.sleep(20))
                holder.attach(sleeper)
                later = asyncio.create_task(descendant())
                sleeper.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sleeper
            ready.set()
            await later
        self.assertEqual(copied, [holder])
