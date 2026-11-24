"""Generated-only binding tests; no inference, network or real credentials."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from uif.effect_authority import (
    AuthorityBinding, AuthorityIdentity, ClaimFence, Lease, PendingEffect, Scope,
    SharedAtomicStore, SourcePin, TerminalEffect, bind_effect_authority,
)
from uif.handoff_store import Hold
from uif.quota_handoff import LinearKey
from uif.stokowski_cloudflare.account_admission import AccountUsageEvidence, FreeRoute
from uif.stokowski_cloudflare.budget import BudgetRefused, PROJECTS, ProjectAccountRouter
from uif.stokowski_cloudflare.client import (
    FencedFreeCall, MAX_OUTPUT_TOKENS, MODEL, MODEL_RESERVATIONS, WorkersAIClient,
)
from uif.stokowski_cloudflare.shared_admission import (
    AccountUsageReceipt, NativeClaim, SharedFreePorts, receive_original,
)


NOW = datetime(2030, 1, 11, 12, 0, tzinfo=timezone.utc)
WINDOW = timedelta(seconds=20)
ISSUE = '00000000-0000-4000-8000-000000000003'
WORKSPACE = '00000000-0000-4000-8000-000000000001'
PROJECT = '00000000-0000-4000-8000-000000000002'
ROUTE = FreeRoute('a' * 32, MODEL, 'reviewed-free', 'e' * 64)
PROJECT_SCOPE = Scope('fixture-repository', LinearKey(WORKSPACE, PROJECT, ISSUE))
OWNER = AuthorityIdentity('fixture-claim', 'b' * 64, 'c' * 64, 'fixture-owner')
ISSUER = AuthorityIdentity('fixture-usage', 'd' * 64, 'f' * 64, 'fixture-meter')
SOURCE = SourcePin('test/ordinary-cookbook', 'develop', '1' * 40, '2' * 40, '3' * 40)
FENCE = ClaimFence(PROJECT_SCOPE, SOURCE, OWNER.session_id, 1, 1, 'fixture-lease',
                   OWNER.authority_id, OWNER.deployment_sha256, OWNER.policy_sha256)
CLAIM = NativeClaim('OffMenu', 'fixture-attempt', Lease(FENCE, int((NOW + WINDOW * 2).timestamp())))
METER = AccountUsageReceipt(ISSUER, '9' * 64, AccountUsageEvidence(
    ROUTE, NOW.date().isoformat(), NOW, NOW + WINDOW * 2, 500, 600, True, True))
BODY = json.dumps({'model': MODEL, 'max_tokens': MAX_OUTPUT_TOKENS,
                   'messages': [], 'stream': False}, sort_keys=True).encode()
CALL = FencedFreeCall('OffMenu', CLAIM.attempt_id, '7' * 64, sha256(BODY).hexdigest(),
                      ROUTE, NOW + WINDOW)
PATH = '/client/v4/accounts/' + ROUTE.account_id + '/ai/v1/chat/completions'
HEADERS = {'Content-Type': 'application/json', 'cf-aig-gateway-id': 'reviewed-free',
           'cf-aig-max-attempts': '1', 'cf-aig-skip-cache': 'true'}


class Response:
    status = 200

    def __init__(self):
        self.closed = False

    def getheader(self, key):
        return 'generated_receipt' if key == 'cf-aig-log-id' else None

    def read(self, _):
        return json.dumps({'model': MODEL, 'choices': [{'message': 'fixture'}],
                           'usage': {'neurons': 2}}).encode()

    def close(self):
        self.closed = True


class OriginalStore(SharedAtomicStore):
    """Ordinary one-operation test double, not an installation."""
    def __init__(self):
        self.begun = []
        self.rechecked = []
        self.terminal = None
        self.lose_ack = False
        self.stale = False

    async def claim(self, *_): raise AssertionError('never acquire live custody')
    async def observe_claim(self, *_): raise AssertionError('not a claim reader')
    async def renew(self, *_): raise AssertionError('read does not renew')
    async def handoff(self, *_): raise AssertionError('read does not transfer')
    async def observe_original(self, *_): raise AssertionError('never implicit replay')
    async def reconcile(self, *_): raise AssertionError('positive send is not settlement')
    async def release(self, *_): raise AssertionError('send is not completion')

    async def begin(self, identity, operation):
        assert identity == OWNER
        if self.terminal is not None:
            return self.terminal
        if self.begun:
            raise Hold('original still uncertain')
        self.begun.append(operation)
        if self.lose_ack:
            raise OSError('simulated original ack uncertainty')
        return PendingEffect(operation, 'fixture-dispatch-fence')

    async def recheck(self, identity, pending):
        assert identity == OWNER
        self.rechecked.append(pending)
        if self.stale:
            raise Hold('owner generation changed')
        return pending


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.time = NOW
        self.meter, self.claim = METER, CLAIM
        self.valid_usage = self.valid_claim = True
        self.effects = []
        self.response = Response()

    def make(self, **changes):
        opts = dict(routes={'OffMenu': ROUTE}, repository_id='fixture-repository',
                    workspace_id=WORKSPACE, project_id=PROJECT, claim_authority=OWNER,
                    quota_issuer=ISSUER, read_usage=lambda _: self.meter,
                    verify_usage=lambda issuer, _: issuer == ISSUER and self.valid_usage,
                    read_claim=lambda *_: self.claim,
                    verify_claim=lambda owner, _: owner == OWNER and self.valid_claim,
                    send_once=self.send, clock=lambda: self.time)
        return SharedFreePorts(**(opts | changes))

    def send(self, intent, body):
        self.effects.append((intent, body))
        return self.response

    def request(self, ports, call=CALL, *, method='POST', path=PATH, body=BODY, headers=HEADERS):
        conn = ports.open_fenced_connection(call)
        conn.request(method, path, body=body, headers=headers)
        return conn

    def test_real_client_reserve_send_settle_via_launcher_ports_no_legacy_credentials(self):
        ports = self.make()
        with tempfile.TemporaryDirectory() as tmp:
            opts = ports.launcher_options()
            accounts = {name: format(i, '032x') for i, name in enumerate(sorted(PROJECTS), 1)}
            accounts['OffMenu'] = ROUTE.account_id
            router = ProjectAccountRouter(Path(tmp) / 'fixture.sqlite', accounts,
                                          eligible_projects={'OffMenu'}, strict_routes={'OffMenu': ROUTE})
            def legacy_token(_): self.fail('strict path must not access legacy credential')
            client = WorkersAIClient(router, legacy_token, gateways={ROUTE.account_id: ROUTE.gateway_id},
                                     read_account_usage=opts['read_account_usage'],
                                     open_fenced_connection=opts['open_fenced_connection'])
            self.assertTrue(opts['allows_native_dispatch']('OffMenu', ISSUE))
            with patch('uif.stokowski_cloudflare.client._utc_now', return_value=NOW), patch(
                    'uif.stokowski_cloudflare.budget._utc_time', side_effect=lambda n=None: n or NOW):
                result = client.complete(project='OffMenu', attempt_id=CLAIM.attempt_id,
                                         request_id=CALL.request_id, active_projects={'OffMenu'},
                                         messages=[], tools=[], timeout=20)
            self.assertEqual(result['usage']['neurons'], 2)
            self.assertEqual(len(self.effects), 1)
            intent, sent = self.effects[0]
            self.assertEqual((intent.operation.fence, intent.operation.original_id,
                              intent.request_sha256, intent.quota_receipt_sha256,
                              intent.request_not_after, intent.maximum_millineurons),
                             (FENCE, CALL.request_id, sha256(sent).hexdigest(), METER.receipt_sha256,
                              int((NOW + WINDOW).timestamp()), MODEL_RESERVATIONS[MODEL]))
            self.assertTrue(self.response.closed)

    def test_missing_unverified_sampled_incomplete_or_out_of_window_usage_holds(self):
        for item in (None, replace(METER, issuer=OWNER), replace(METER, receipt_sha256='uncertain'),
                     replace(METER, projection=replace(METER.projection, completeness_verified=False)),
                     replace(METER, projection=replace(METER.projection, gap_upper=7_500_000)),
                     replace(METER, projection=replace(METER.projection,
                                                        covered_through=NOW - timedelta(seconds=91))),
                     replace(METER, projection=replace(METER.projection,
                                                       free_standard_no_fallback=False))):
            self.meter = item
            with self.subTest(item=item), self.assertRaises(BudgetRefused):
                self.make().read_account_usage(20)
        self.meter, self.valid_usage = METER, False
        with self.assertRaises(BudgetRefused):
            self.make().read_account_usage(20)

    def test_usage_is_read_again_after_reservation(self):
        ports = self.make()
        self.assertIn('OffMenu', ports.read_account_usage(20).usage_snapshots)
        self.meter = None
        with self.assertRaises(BudgetRefused):
            self.request(ports)
        self.assertEqual(self.effects, [])

    def test_native_issue_scope_and_claim_are_independently_checked(self):
        ports = self.make()
        self.assertFalse(ports.allows_native_dispatch('CIX', ISSUE))
        self.assertFalse(ports.allows_native_dispatch('OffMenu', WORKSPACE))
        for fence in (replace(FENCE, scope=replace(PROJECT_SCOPE, repository_id='other')),
                      replace(FENCE, owner_session='other'),
                      replace(FENCE, policy_sha256='a'*64),
                      replace(FENCE, scope=replace(PROJECT_SCOPE,
                                                   key=LinearKey(WORKSPACE, ISSUE, ISSUE)))):
            self.claim = replace(CLAIM, lease=replace(CLAIM.lease, fence=fence))
            self.assertFalse(ports.allows_native_dispatch('OffMenu', ISSUE))
            with self.assertRaises(BudgetRefused):
                self.request(ports)
        self.claim, self.valid_claim = CLAIM, False
        self.assertFalse(ports.allows_native_dispatch('OffMenu', ISSUE))
        self.assertEqual(self.effects, [])

    def test_lease_must_cover_whole_request_and_exact_attempt(self):
        ports = self.make()
        for claim in (replace(CLAIM, attempt_id='different-attempt'),
                      replace(CLAIM, lease=replace(CLAIM.lease, expires_at=int(NOW.timestamp()))),
                      replace(CLAIM, lease=replace(CLAIM.lease, expires_at=int(NOW.timestamp()) + 12))):
            self.claim = claim
            with self.assertRaises(BudgetRefused):
                self.request(ports)
        self.assertEqual(self.effects, [])

    def test_paid_names_disjoint_authorities_and_writes_cannot_fallback(self):
        for options in (
            {'routes': {'Paid': ROUTE}},
            {'quota_issuer': replace(ISSUER, authority_id=OWNER.authority_id)},
            {'send_once': None},
            {'verify_claim': None},
        ):
            with self.subTest(options=options), self.assertRaises(BudgetRefused):
                self.make(**options)
        self.assertEqual(self.effects, [])

    def test_offmenu_only_accepts_its_one_exact_account_route(self):
        another = replace(ROUTE, account_id='5' * 32)
        for roles in ({'UIF': ROUTE}, {'OffMenu': ROUTE, 'UIF': another}):
            with self.assertRaises(BudgetRefused):
                self.make(routes=roles)
        with self.assertRaises(BudgetRefused):
            self.make().open_fenced_connection(replace(CALL, route=another))
        self.assertEqual(self.effects, [])

    def test_one_exact_wire_send_and_do_not_replay_a_connection(self):
        ports = self.make()
        for change in ({'method': 'GET'}, {'path': '/fixture'},
                       {'headers': HEADERS | {'Authorization': 'fixture-never-sent'}},
                       {'body': BODY + b' '},
                       {'call': replace(CALL, route=replace(ROUTE, gateway_id=None))}):
            with self.subTest(change=change), self.assertRaises(BudgetRefused):
                self.request(ports, **change)
        self.assertEqual(self.effects, [])
        conn = self.request(ports)
        with self.assertRaises(BudgetRefused):
            conn.request('POST', PATH, body=BODY, headers=HEADERS)
        self.assertEqual(len(self.effects), 1)

    def test_receiver_hold_is_a_native_budget_refusal_without_implicit_replay(self):
        calls = []

        def hold_original(*args):
            calls.append(args[0].operation)
            raise Hold('uncertain original; read-only reconciliation required')

        with self.assertRaises(BudgetRefused):
            self.request(self.make(send_once=hold_original))
        self.assertEqual(len(calls), 1)

    def test_stale_deadline_and_changed_body_shape_fail_closed(self):
        self.time = NOW + WINDOW
        with self.assertRaises(BudgetRefused):
            self.request(self.make())
        self.time = NOW
        for body in (b'{}', BODY.replace(b'false', b'true'),
                     BODY.replace(b'"model":', b'"model": "different", "model":')):
            with self.assertRaises(BudgetRefused):
                self.request(self.make(), call=replace(CALL, request_sha256=sha256(body).hexdigest()),
                             body=body)
        self.assertEqual(self.effects, [])

    def test_receiver_obeys_existing_v3_begin_recheck_and_one_consume(self):
        self.request(self.make())
        intent, body = self.effects[0]
        store = OriginalStore()
        authority = bind_effect_authority(mode='shared', shared_store=store, identity=OWNER,
                                          verify_binding=lambda _, owner: AuthorityBinding(owner, 'f'*64))
        consumed = []

        async def consume(pending, got, raw):
            self.assertEqual((got, raw), (intent, body))
            consumed.append(pending)
            store.terminal = TerminalEffect(intent.operation, 'verified', 'c' * 64)
            return 'fixture-complete'

        async def receive(candidate=intent, **changes):
            options = dict(scope=PROJECT_SCOPE, allowed_routes=(ROUTE,), clock=lambda: self.time,
                           consume_current_once=consume)
            return await receive_original(authority, candidate, body, **(options | changes))

        for changes in ({'scope': replace(PROJECT_SCOPE, repository_id='other')},
                        {'allowed_routes': ()},
                        {'clock': lambda: NOW + WINDOW}):
            with self.assertRaises(BudgetRefused):
                asyncio.run(receive(**changes))
        self.assertEqual((store.begun, consumed), ([], []))
        self.assertEqual(asyncio.run(receive()), 'fixture-complete')
        with self.assertRaises(BudgetRefused):
            asyncio.run(receive())
        self.assertEqual(store.begun, [intent.operation])
        self.assertEqual(store.rechecked, consumed)
        self.assertEqual(len(consumed), 1)

    def test_receiver_uncertain_original_and_stale_recheck_never_consume(self):
        self.request(self.make())
        intent, body = self.effects[0]

        async def consume(*_):
            self.fail('unsettled original or stale fence cannot cause send')

        for lose_ack in (True, False):
            store = OriginalStore()
            store.lose_ack = lose_ack
            store.stale = not lose_ack
            authority = bind_effect_authority(mode='shared', shared_store=store, identity=OWNER,
                                              verify_binding=lambda _, owner: AuthorityBinding(owner, 'f'*64))
            for _ in range(2):
                with self.assertRaises(Hold):
                    asyncio.run(receive_original(authority, intent, body, scope=PROJECT_SCOPE,
                                                 allowed_routes=(ROUTE,), clock=lambda: self.time,
                                                 consume_current_once=consume))
            self.assertEqual(store.begun, [intent.operation])


if __name__ == '__main__':
    unittest.main()
