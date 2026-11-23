from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from uif.stokowski_cloudflare.budget import BudgetRefused, ProjectAccountRouter
from uif.stokowski_cloudflare.client import (
    GLM_MODEL, MODEL, MODEL_RESERVATIONS, REQUEST_MAXIMUM, WorkersAIClient, WorkersAIRequestError,
)
from uif.stokowski_cloudflare.turn import Attempt, repository_tools, run_turn


class WorkersAIClientTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'budget.sqlite'
        self.accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                                 (digit * 32 for digit in 'abcde')))
        self.router = ProjectAccountRouter(self.database, self.accounts)
        self.client = WorkersAIClient(self.router, lambda account: 'test-' + account)
        self.request = dict(project='OffMenu', attempt_id='candidate', request_id='candidate/one',
                            active_projects={'OffMenu'}, messages=[{'role': 'user', 'content': 'test'}],
                            tools=[], timeout=1)

    def usage_record(self):
        with sqlite3.connect(self.database) as db:
            return db.execute('SELECT amount, settled FROM requests').fetchone()

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_glm_reserves_its_own_ceiling_and_rejects_different_reported_model(self, connection):
        client = WorkersAIClient(self.router, lambda _: 'test-token', model=GLM_MODEL)
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({
            'model': MODEL, 'choices': [{'message': {'content': 'Done'}}],
            'usage': {'neurons': 1},
        }).encode()
        with self.assertRaisesRegex(WorkersAIRequestError, 'verified usage'):
            client.complete(**self.request)
        self.assertEqual(self.usage_record(), (MODEL_RESERVATIONS[GLM_MODEL], 0))
        request = json.loads(conn.request.call_args.kwargs['body'])
        self.assertEqual(request['model'], GLM_MODEL)
        self.assertEqual(request['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(request['response_format'], {'type': 'json_object'})

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_selected_account_is_used_and_reported_usage_is_rounded_up(self, connection):
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({
            'model': MODEL, 'choices': [{'message': {'content': 'Done'}}],
            'usage': {'neurons': 1.0001},
        }).encode()
        conn.request.side_effect = lambda *args, **kwargs: self.assertEqual(
            self.usage_record(), (REQUEST_MAXIMUM, 0))
        self.client.complete(**self.request)
        self.assertEqual(self.usage_record(), (1001, 1))
        connection.assert_called_once_with('api.cloudflare.com', timeout=1)
        (method, path), kwargs = conn.request.call_args
        account = self.accounts['OffMenu']
        self.assertEqual((method, path), ('POST', f'/client/v4/accounts/{account}/ai/v1/chat/completions'))
        self.assertEqual(kwargs['headers']['Authorization'], f'Bearer test-{account}')
        self.assertEqual(json.loads(kwargs['body'])['max_tokens'], 2048)

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_timeout_retains_reservation_and_blocks_redispatch(self, connection):
        connection.return_value.getresponse.side_effect = TimeoutError
        with self.assertRaises(WorkersAIRequestError):
            self.client.complete(**self.request)
        self.assertEqual(self.usage_record(), (REQUEST_MAXIMUM, 0))
        with self.assertRaises(BudgetRefused):
            self.client.complete(**self.request)
        connection.assert_called_once()

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_no_credential_means_no_request(self, connection):
        client = WorkersAIClient(self.router, lambda account: None)
        with self.assertRaises(WorkersAIRequestError):
            client.complete(**self.request)
        connection.assert_not_called()
        self.assertEqual(self.usage_record(), (0, 1))

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_provider_refusal_hides_response_body_and_retains_reservation(self, connection):
        credential = 'test-private-token'
        client = WorkersAIClient(self.router, lambda account: credential)
        response = connection.return_value.getresponse.return_value
        response.status = 429
        response.read.return_value = credential.encode()
        with self.assertRaisesRegex(WorkersAIRequestError, r'^Workers AI refused request \(HTTP 429\)$'):
            client.complete(**self.request)
        with self.assertRaises(BudgetRefused):
            client.complete(**dict(self.request, request_id='candidate/two'))
        connection.assert_called_once()
        response.read.assert_not_called()
        self.assertEqual(self.usage_record(), (REQUEST_MAXIMUM, 0))

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_gateway_routes_to_the_selected_account_and_settles_usage(self, connection):
        account = self.accounts['OffMenu']
        client = WorkersAIClient(self.router, lambda _: 'test-token', gateways={account: 'stokowski'})
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.getheader.return_value = 'gateway-receipt'
        response.read.return_value = json.dumps({
            'model': MODEL, 'choices': [{'message': {'content': 'Done'}}],
            'usage': {'neurons': 3.0001},
        }).encode()
        result = client.complete(**self.request)
        (method, path), kwargs = conn.request.call_args
        self.assertEqual((method, path), ('POST', f'/client/v4/accounts/{account}/ai/v1/chat/completions'))
        self.assertEqual(kwargs['headers']['cf-aig-gateway-id'], 'stokowski')
        self.assertEqual(kwargs['headers']['cf-aig-max-attempts'], '1')
        self.assertEqual(json.loads(kwargs['body'])['model'], MODEL)
        self.assertEqual(result['choices'][0]['message']['content'], 'Done')
        self.assertEqual(self.usage_record(), (3001, 1))
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT * FROM gateway_receipts').fetchall(),
                             [(account, self.request['request_id'], 'gateway-receipt')])

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_gateway_limit_never_retries_the_direct_endpoint(self, connection):
        account = self.accounts['OffMenu']
        client = WorkersAIClient(self.router, lambda _: 'test-token', gateways={account: 'stokowski'})
        conn = connection.return_value
        conn.getresponse.return_value.status = 429
        with self.assertRaisesRegex(WorkersAIRequestError, 'HTTP 429'):
            client.complete(**self.request)
        with self.assertRaises(BudgetRefused):
            client.complete(**self.request)
        conn.request.assert_called_once()
        self.assertTrue(conn.request.call_args.args[1].endswith('/v1/chat/completions'))
        self.assertEqual(self.usage_record(), (REQUEST_MAXIMUM, 0))

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_gateway_success_without_receipt_preserves_reservation(self, connection):
        account = self.accounts['OffMenu']
        client = WorkersAIClient(self.router, lambda _: 'test-token', gateways={account: 'stokowski'})
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.getheader.return_value = None
        with self.assertRaisesRegex(WorkersAIRequestError, 'receipt missing'):
            client.complete(**self.request)
        self.assertEqual(self.usage_record(), (REQUEST_MAXIMUM, 0))

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    @patch('subprocess.Popen', side_effect=FileNotFoundError('No process launcher available'))
    def test_turn_works_without_codex_and_stays_on_one_account(self, popen, connection):
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        calls = [{'id': 'edit', 'type': 'function',
                  'function': {'name': 'replace_line', 'arguments': '{"line":"new"}'}}]
        response.read.side_effect = [
            json.dumps({'model': MODEL, 'choices': [{'message': message}],
                        'usage': {'neurons': usage}}).encode()
            for message, usage in [({'tool_calls': calls}, 1), ({'content': 'Patch prepared'}, 2)]
        ]
        target = self.database.parent / 'fixture.txt'
        target.write_text('old')

        def edit(name, arguments, remaining):
            self.assertEqual(name, 'replace_line')
            self.assertGreater(remaining, 0)
            target.write_text(arguments['line'])
            return 'Edited fixture'

        result = run_turn(self.client, Attempt('OffMenu', 'author'), 'Fix fixture',
                          tools=[{'type': 'function', 'function': {'name': 'replace_line'}}],
                          execute_tool=edit, active_projects=lambda: {'OffMenu'},
                          cancelled=lambda: False, timeout=5)
        self.assertEqual((result, target.read_text()), ('Patch prepared', 'new'))
        popen.assert_not_called()
        requests = conn.request.call_args_list
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].args[1], requests[1].args[1])
        self.assertEqual(json.loads(requests[1].kwargs['body'])['messages'][-1],
                         {'role': 'tool', 'tool_call_id': 'edit', 'content': 'Edited fixture'})

    def test_five_active_projects_can_have_requests_in_flight_together(self):
        barrier = Barrier(5, timeout=5)
        paths = []

        class Connection:
            def __init__(self, *args, **kwargs):
                pass

            def request(self, method, path, **kwargs):
                paths.append(path)
                barrier.wait()

            def getresponse(self):
                return SimpleNamespace(status=200, read=lambda _: json.dumps({
                    'model': MODEL, 'choices': [{'message': {'content': 'Done'}}],
                    'usage': {'neurons': 1},
                }).encode())

            def close(self):
                pass

        def complete(project):
            return self.client.complete(**dict(self.request, project=project,
                attempt_id=project, request_id=project+'/one', active_projects=set(self.accounts)))

        with patch('uif.stokowski_cloudflare.client.HTTPSConnection', Connection):
            with ThreadPoolExecutor(max_workers=5) as workers:
                self.assertEqual(len(list(workers.map(complete, self.accounts))), 5)
        self.assertCountEqual(paths, [
            f'/client/v4/accounts/{account}/ai/v1/chat/completions' for account in self.accounts.values()
        ])

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_malformed_tool_arguments_can_be_corrected_without_broadening_scope(self, connection):
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        fixture = self.database.parent / 'fixture.txt'
        fixture.write_text('old')
        shape = {'model': MODEL, 'usage': {'neurons': 1}}

        def calls(call_id, args):
            return {'tool_calls': [{'type': 'function', 'id': call_id,
                                    'function': {'name': 'replace_text', 'arguments': args}}]}

        messages = [
            calls('bad', '{"path":"fixture.txt"}'),
            calls('good', '{"path":"fixture.txt","old":"old","replacement":"new"}'),
            {'content': 'Patched fixture'},
        ]
        response.read.side_effect = [
            json.dumps(dict(shape, choices=[{'message': message}])).encode()
            for message in messages
        ]
        tools, execute = repository_tools(fixture.parent, (fixture.name,), (fixture.name,))
        output = run_turn(self.client, Attempt('OffMenu', 'fix-tool'), 'Fix fixture',
                          tools=tools, execute_tool=execute, active_projects=lambda: {'OffMenu'},
                          cancelled=lambda: False, timeout=5)
        self.assertEqual((output, fixture.read_text()), ('Patched fixture', 'new'))
        requests = conn.request.call_args_list
        first = json.loads(requests[0].kwargs['body'])
        second = json.loads(requests[1].kwargs['body'])
        self.assertTrue(all(t['function']['description'] for t in first['tools']))
        self.assertEqual(second['messages'][-1], {'role': 'tool', 'tool_call_id': 'bad',
                         'content': 'Tool did not run. Expected string fields path, old, replacement'})

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_settled_length_limit_requests_one_final_report_without_tools(self, connection):
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        responses = [
            {'choices': [{'finish_reason': 'length', 'message': {'content': None}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"summary":"Blocked"}'}}]},
        ]
        response.read.side_effect = [
            json.dumps(dict(value, model=GLM_MODEL, usage={'neurons': 1})).encode()
            for value in responses
        ]
        client = WorkersAIClient(self.router, lambda _: 'test-token', model=GLM_MODEL)
        fixture = self.database.parent / 'fixture.txt'
        fixture.write_text('old')
        tools, execute = repository_tools(fixture.parent, (fixture.name,), (fixture.name,))
        output = run_turn(client, Attempt('OffMenu', 'length'), 'Check fixture',
                          tools=tools, execute_tool=execute, active_projects=lambda: {'OffMenu'},
                          cancelled=lambda: False, timeout=5)
        self.assertEqual(output, '{"summary":"Blocked"}')
        first, second = [json.loads(call.kwargs['body']) for call in conn.request.call_args_list]
        self.assertIn('tools', first)
        self.assertNotIn('response_format', first)
        self.assertNotIn('tools', second)
        self.assertEqual(second['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(fixture.read_text(), 'old')

    @patch('uif.stokowski_cloudflare.client.HTTPSConnection')
    def test_unstructured_settled_result_gets_one_new_report_request(self, connection):
        conn = connection.return_value
        response = conn.getresponse.return_value
        response.status = 200
        final = '{"summary":"Scoped assessment","verdict":"blocked"}'
        response.read.side_effect = [
            json.dumps({'model': GLM_MODEL, 'usage': {'neurons': 1},
                        'choices': [{'message': {'content': content}}]}).encode()
            for content in ('Unstructured stage output', final)
        ]
        client = WorkersAIClient(self.router, lambda _: 'test-token', model=GLM_MODEL)
        tools, execute = repository_tools(self.database.parent, ('one.py',), ())
        self.assertEqual(execute('find_files', {'query': '.py'}, 5), 'one.py')
        self.assertEqual(run_turn(
            client, Attempt('OffMenu', 'structured'), 'Return a status',
            tools=tools, execute_tool=execute, active_projects=lambda: {'OffMenu'},
            cancelled=lambda: False, timeout=5, structured_report=True,
        ), final)
        first, second = [json.loads(call.kwargs['body']) for call in conn.request.call_args_list]
        self.assertIn('tools', first)
        self.assertEqual(second['response_format'], {'type': 'json_object'})
        self.assertNotIn('tools', second)
        self.assertEqual(second['messages'][-2]['content'], 'Unstructured stage output')

    def test_structured_turn_reserves_its_last_request_for_the_report(self):
        requested_tools = []
        final = '{"summary":"Budget reached","verdict":"blocked"}'

        def complete(**request):
            requested_tools.append(bool(request['tools']))
            if not request['tools']:
                return {'choices': [{'message': {'content': final}}]}
            return {'choices': [{'message': {'tool_calls': [{
                'id': str(len(requested_tools)), 'function': {'name': 'list_files', 'arguments': {}},
            }]}}]}

        tools, execute = repository_tools(self.database.parent, ('one.py',), ())
        with patch('uif.stokowski_cloudflare.turn.MAX_TURN_REQUESTS', 3):
            self.assertEqual(run_turn(
                SimpleNamespace(complete=complete), Attempt('OffMenu', 'limit'), 'Check fixture',
                tools=tools, execute_tool=execute, active_projects=lambda: {'OffMenu'},
                cancelled=lambda: False, timeout=5, structured_report=True,
            ), final)
        self.assertEqual(requested_tools, [True, True, False])

    def test_invalid_tool_batch_requests_a_report_without_executing_any_call(self):
        final = '{"summary":"Tool unavailable","verdict":"blocked"}'
        calls = [
            {'id': 'allowed', 'function': {'name': 'list_files', 'arguments': {}}},
            {'id': 'outside', 'function': {'name': 'unapproved', 'arguments': {}}},
        ]
        responses = iter([{'tool_calls': calls}, {'content': final}])
        requested_tools = []

        def complete(**request):
            requested_tools.append(bool(request['tools']))
            return {'choices': [{'message': next(responses)}]}

        def execute(*_):
            self.fail('A declined batch executed a tool')

        tools, _ = repository_tools(self.database.parent, ('one.py',), ())
        self.assertEqual(run_turn(
            SimpleNamespace(complete=complete), Attempt('OffMenu', 'bad-tool'), 'Check fixture',
            tools=tools, execute_tool=execute, active_projects=lambda: {'OffMenu'},
            cancelled=lambda: False, timeout=5, structured_report=True,
        ), final)
        self.assertEqual(requested_tools, [True, False])
