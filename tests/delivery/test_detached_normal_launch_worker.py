"""Pinned Stokowski normal launch against disposable SQLite Worker authority."""

import asyncio
from contextvars import copy_context
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from inspect import signature
from io import BytesIO
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
from types import SimpleNamespace
from threading import Lock, Thread
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_native_remote_authority as fixture
from uif.handoff_store import Hold, decode, wire
from uif.stokowski_cloudflare import launcher
from uif.stokowski_cloudflare.intake import intake_identity
from uif.stokowski_cloudflare.native_assignment_projection import project_unstarted_assignment
from uif.stokowski_cloudflare.native_composition import NativeComposition, install_sinks, observe_clean_assignment_workspace
from uif.stokowski_cloudflare.remote_authority import AccessTransport, RemoteAuthority


ROOT = Path(__file__).resolve().parents[2]
RECIPIENT_NATIVE_POSTIMAGE = 'afdc9c589570edfb24d62ecb0e8caf8a3e5fe045230dc93e6225b33a2ccfe431'
PINNED_SOURCE = {
    'orchestrator.py': {'610cbf97a5f36c9dd305cfe456245a9efc29625287b44211adbd5cea77c3511f',
                        '228523e5a9e78ed5ad3ea78c8fe8f37093e5ce2cf5460068215ffb655028fa96',
                        '3b8a3bfd6291bb96560b5ff2b3f9b2af5ef7aa61eb3bcb81f75b1e89fef3ba38',
                        RECIPIENT_NATIVE_POSTIMAGE},
    'runner.py': {'2492210a8d43cae9b2e0ebae256f8d37298fc75c0f2c65c3fdbd66a925c6b351'},
}
WORKER = r'''
import { createInterface } from 'node:readline';
import { DatabaseSync } from 'node:sqlite';
import { Authority } from './authority.mjs';
import { day } from './protocol.mjs';

globalThis.fetch = () => { throw Error('network prohibited in detached fixture'); };
const lines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
const seed = JSON.parse((await lines.next()).value);
const { scope, source, route, attempt, now, recovered } = seed;
const roles = ['native', 'morgan', 'control', 'quota'];
const peers = Object.fromEntries(roles.map(role => [role, { role, session_id: role + '-session', expires_at: now + 600 }]));
const DB = new DatabaseSync(':memory:');
const storage = {
  sql: { exec: (sql, ...args) => DB.prepare(sql).all(...args.map(x => x instanceof ArrayBuffer ? new Uint8Array(x) : x)) },
  transactionSync(fn) {
    DB.exec('BEGIN');
    try { const result = fn(); DB.exec('COMMIT'); return result; }
    catch (e) { DB.exec('ROLLBACK'); throw e; }
  },
  sync: async () => {},
};
const h = digit => digit.repeat(64);
const projection = { route, utc_day: day(now), covered_through: now, valid_until: now + 250,
  upper: 0, gap_upper: 0, free_standard_no_fallback: true, completeness_verified: true };
const out = [];
const report = recovered ? JSON.stringify({ summary: 'Synthetic bounded report.', verdict: 'approve',
  data_sources: [{name: 'Detached issue assignment', how_verified: 'Pinned fixture original and task scope.'}],
  routing: { issue: seed.identifier, state: 'assess', run: 1, actionable: true, ownership_clear: true,
    deferred: false, owner_pause: false, uncertain_effects: false, missing_inputs: [] } }) : 'Synthetic bounded report.';
let authority;
const startAuthority = () => new Authority(storage, { ...scope.key, repository_id: scope.repository_id,
  peers, route, authority_id: 'realm', quota_authority_id: 'quota', quota_verification_sha256: h('d'),
  deployment_sha256: h('a'), policy_sha256: h('b'), lease_seconds: 300, upload_hosts: [],
}, {
  clock: () => now, providerToken: 'fixture-only', linearAuthorization: 'fixture-only',
  quotaVerifier: async () => ({ projection, binding_sha256: h('d'), source_receipt_sha256: h('5'),
    coverage: 'entire-account-all-routes-from-utc-midnight', gap_evidence_sha256: h('f'),
    checkpoint: { sha256: h('3'), opening_sha256: h('4'), unresolved_upper: 0, opening_upper: 0 } }),
  provider: async (req) => {
    if (req.url === `https://api.cloudflare.com/client/v4/accounts/${route.account_id}/ai/v1/chat/completions`) {
      const body = await req.json();
      if (body.model !== route.model || body.stream !== false) throw Error('unbound fixture request');
      out.push('provider');
      return Response.json({ model: route.model, choices: [{ message: { content: report } }],
        usage: { neurons: 0.001 } }, { headers: { 'cf-aig-log-id': 'fixture-only' } });
    }
    if (req.url === 'https://api.linear.app/graphql') {
      const { variables: { input } } = await req.json();
      if (input.issueId !== scope.key.issue_id) throw Error('unbound fixture issue');
      out.push('linear');
      return Response.json({ data: { commentCreate: { success: true,
        comment: { id: input.id, body: input.body, issue: { id: input.issueId } } } } });
    }
    throw Error('unexpected outbound fixture URL');
  },
});
authority = startAuthority();
const call = (role, path, body, permit) => authority.handle('/v1/' + path, body, peers[role], permit);
await call('control', 'control', { request_id: 'resume', expected_coordinator_epoch: 1, stopped: false });
const grant = { scope, source, sessions: ['native-session', 'morgan-session'], attempt_ids: [attempt],
  reconstruction_sha256: seed.reconstruction, valid_until: now + 600,
  allow_provider: true, allow_comments: true, allow_uploads: false, review_of: {},
  result_retention: { max_response_bytes: 65536, max_retained_results: 16,
    readers: ['native-session', 'morgan-session', 'control-session'], evidence_sha256: h('e') } };
await call('control', 'grant', { request_id: 'grant', previous_sha256: null, grant });
const base = { scope, source, attempt_id: attempt, request_id: seed.claim_id,
  expected_coordinator_epoch: 2, reconstruction_sha256: seed.reconstruction };
let submitted = null, original;
original = await call(recovered ? 'morgan' : 'native', 'claim', base);
if (recovered) {
  const revocation_id = 'freeze';
  await call('control', 'revoke', { request_id: revocation_id, fence: original.lease.fence,
    unstarted_attempt_id: attempt });
  const handoff = { current: original.lease, successor_session: 'native-session', expected_coordinator_epoch: 2,
    quiesced_sha256: h('e'), reconstruction_sha256: seed.reconstruction,
    recovery: { attempt_id: attempt, revocation_id } };
  await call('control', 'attest', { request_id: 'attest', fact: { kind: 'handoff', ...handoff } });
  submitted = { ...handoff, request_id: 'take-original' };
  original = await call('native', 'handoff', submitted);
}
await call('quota', 'usage/write', { request_id: 'meter', projection, source_receipt_sha256: h('5') });
console.log(JSON.stringify({ submitted, original, realm: authority.realm }));
for (;;) {
  const next = await lines.next();
  if (next.done) break;
  const input = JSON.parse(next.value);
  if (input.path === '@inspect') {
    console.log(JSON.stringify({ out, effects: authority.row('SELECT COUNT(*) AS n FROM effects').n }));
    continue;
  }
  if (input.path === '@restart') {
    authority = startAuthority();
    console.log('{}');
    continue;
  }
  if (input.path === '@stop') {
    await call('control', 'control', { request_id: 'stop', expected_coordinator_epoch: 2, stopped: true });
    console.log('{}');
    continue;
  }
  if (input.path === '@drop-quota') {
    authority.put('DELETE FROM usage_checkpoints WHERE account=?', route.account_id);
    console.log('{}');
    continue;
  }
  if (input.path === '@restore-quota') {
    await call('quota', 'usage/write', { request_id: 'meter-restored', projection, source_receipt_sha256: h('5') });
    console.log('{}');
    continue;
  }
  try {
    const result = await call('native', input.path.slice(4), input.body ?? {}, input.permit);
    const headers = { 'content-type': 'application/json' };
    if (!(result instanceof Response)) console.log(JSON.stringify({ status: 200, headers, body: result }));
    else console.log(JSON.stringify({ status: result.status, headers: { ...headers,
      ...Object.fromEntries(result.headers) }, body: JSON.parse(await result.text()) }));
  } catch (error) {
    console.log(JSON.stringify({ status: error.status ?? 500, headers: { 'content-type': 'application/json' },
      body: { error: error.message } }));
  }
}
'''


class NormalLaunchAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = (os.environ.get('UIF_PINNED_RECIPIENT_NATIVE')
                or os.environ.get('UIF_PINNED_STOKOWSKI_ASSESSMENT_SOURCE'))
        if path is None:
            self.skipTest('set UIF_PINNED_RECIPIENT_NATIVE to the reviewed native stage')
        self.shelf = Path(path).resolve(strict=True)
        for name, expected in PINNED_SOURCE.items():
            self.assertIn(sha256((self.shelf / name).read_bytes()).hexdigest(), expected)
        from stokowski import config, models, orchestrator, publication, runner
        for module in (orchestrator, runner):
            self.assertEqual(Path(module.__file__).resolve(), self.shelf / f'{module.__name__.split(".")[-1]}.py')
        self.orchestrator, self.publication, self.runner, self.config = orchestrator, publication, runner, config
        self.case = fixture.RealCheckoutTest()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.issue = models.Issue(fixture.ISSUE, self.case.path.name, 'Fixture', 'Bounded assessment', state='In Progress')
        self.owner = orchestrator.Orchestrator(self.case.path.parent / 'workflow.yaml', 'OffMenu',
            pool=SimpleNamespace(is_paused=lambda _: False, try_claim=lambda _: True, release=Mock()))
        self.owner.workflow = SimpleNamespace(config=config.ServiceConfig(
            workspace=config.WorkspaceConfig(root=str(self.case.path.parent)),
            tracker=config.TrackerConfig(project_slug='offmenu'),
            states={'assess': config.StateConfig(name='assess', runner='cloudflare', admission_review=True,
                permission_mode='plan', session='fresh', transitions={'complete': 'investigate'}),
                'investigate': config.StateConfig(name='investigate', runner='cloudflare')}))
        self.owner._issue_current_state[self.issue.id] = 'assess'
        self.owner._issue_state_runs[self.issue.id] = 1
        self.owner._announced_states.add((self.issue.id, 'assess', 1))
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.scope = publication.scope(self.issue)
        original = 'cf-intake-' + intake_identity('OffMenu', self.issue.id, self.scope, 1) + '-assess'
        self.grant = replace(self.case.grant, attempts=(original,), scope_sha256=self.scope, expected_coordinator_epoch=2)
        self.calls, self.effects, self.lost = [], {}, False
        self._bridge_lock = Lock()

    def bridge_read(self):
        if not select.select([self.bridge.stdout], [], [], 4)[0]:
            self.fail('detached authority did not return an original result')
        line = self.bridge.stdout.readline()
        self.assertTrue(line, 'detached authority closed before its reply')
        return json.loads(line)

    def exchange(self, path, body=None, permit=None):
        with self._bridge_lock:
            self.bridge.stdin.write(json.dumps({'path': path, 'body': body, 'permit': permit}) + '\n')
            self.bridge.stdin.flush()
            return self.bridge_read()

    def start_worker(self, *, recovered):
        self.recovered = recovered
        node = shutil.which('node')
        self.assertIsNotNone(node, 'fixture requires the locked Node runtime')
        self.bridge = subprocess.Popen([node, '--input-type=module', '-e', WORKER], cwd=ROOT / 'services/shared-authority',
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={'LANG': 'C', 'PATH': '/usr/bin:/bin'})

        def close():
            self.bridge.stdin.close()
            try:
                self.bridge.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.bridge.kill()
                self.bridge.wait(timeout=5)
            self.bridge.stdout.close()
        self.addCleanup(close)
        self.bridge.stdin.write(json.dumps({'scope': asdict(self.grant.scope), 'source': asdict(self.grant.source),
            'attempt': self.grant.attempts[0], 'reconstruction': self.grant.reconstruction_sha256,
            'claim_id': self.grant.claim(fixture.OWNER).request_id,
            'now': int(self.now.timestamp()), 'route': asdict(fixture.ROUTE), 'recovered': recovered,
            'identifier': self.issue.identifier}) + '\n')
        self.bridge.stdin.flush()
        original = self.bridge_read()
        self.expected_fence = original['original']['lease']['fence']

        class Reply(BytesIO):
            def __init__(self, value):
                super().__init__(wire(value['body']))
                self.status = value['status']
                self.headers = value['headers']

            def getheader(self, name, default=None):
                return self.headers.get(name.lower(), default)

        def connect(host, **_):
            self.assertEqual(host, 'fixture.example')
            connection = Mock()

            def request(method, path, *, body, headers):
                permit = headers.get('uif-dispatch-permit')
                self.calls.append((path, permit is not None))
                request = decode(body, limit=512_000) if body else None
                result = self.exchange(path, request, permit)
                if result['status'] == 200 and path in ('/v1/send', '/v1/linear/comment'):
                    self.effects[path] = request['intent' if path == '/v1/send' else 'operation']
                if self.lost and path == '/v1/recovery/enter':
                    raise OSError('fixture lost entry acknowledgement after SQLite commit')
                connection.getresponse.return_value = Reply(result)
            connection.request.side_effect = request
            return connection

        self.enterContext(patch('uif.stokowski_cloudflare.remote_authority.HTTPSConnection', side_effect=connect))
        self.enterContext(patch('uif.stokowski_cloudflare.remote_authority.protected_record',
                               return_value={'client_id': 'fixture', 'client_secret': 'fixture'}))
        transport = AccessTransport('https://fixture.example', '/unused-fixture')
        remote = RemoteAuthority(transport, native=fixture.OWNER, quota_issuer=fixture.QUOTA,
            route=fixture.ROUTE, repository_id=self.grant.scope.repository_id,
            workspace_id=self.grant.scope.key.workspace_id, project_id=self.grant.scope.key.project_id,
            clock=lambda: self.now)
        self.composition = NativeComposition(remote, '/unused-fixture')
        item = {**self.case.item, 'source': asdict(self.grant.source), 'scope_sha256': self.scope,
                'reconstruction_sha256': self.grant.reconstruction_sha256, 'attempts': list(self.grant.attempts)}
        item['workspace_binding']['original_claim_id'] = self.grant.claim(fixture.OWNER).request_id
        if recovered:
            checked = observe_clean_assignment_workspace(self.case.path, self.grant.source, item['git_config_sha256'])
            self.entries = decode(project_unstarted_assignment(self.grant, fixture.OWNER,
                installation_sha256=original['realm'], submitted_handoff=wire(original['submitted']),
                review_of={}, workspace_root=self.case.path.parent, issue_identifier=self.issue.identifier,
                observation=checked), limit=8192)
        else:
            self.entries = {self.issue.id: item}
        self.composition.installation = lambda: {'assignments': self.entries, 'workspace_root': str(self.case.path.parent),
                                                 'coordinator_epoch': 2}

    def wire_normal(self):
        auth = self.composition
        policy = {'provider_mode': 'cloudflare', 'projects': ['OffMenu'], 'credentials': '/unused-fixture',
            'accounts': {'OffMenu': fixture.ROUTE.account_id, 'UIF': 'a'*32, 'ANQR': 'b'*32, 'CIX': 'd'*32, 'CQ': 'e'*32},
            'gateways': {fixture.ROUTE.account_id: fixture.ROUTE.gateway_id}, 'model': fixture.ROUTE.model,
            'workers_ai_strict_free_routes': {'OffMenu': fixture.ROUTE.credential_epoch_sha256},
            'admissions': {self.issue.id: {'assess': {'project': 'OffMenu', 'scope_sha256': self.scope,
                'attempt': self.grant.attempts[0], 'read_paths': ['guide.md'], 'write_roots': [],
                'source_head': self.grant.source.head, 'branch': self.grant.source.branch,
                'prompt': 'Synthetic bounded assessment'}}}}
        if self.recovered:
            policy['admissions'][self.issue.id]['assess'].update(read_paths=[],
                project_intake={'origin': 'https://github.com/maldous/offmenu.git'})
        self.enterContext(patch.object(launcher, '_protected_json', return_value=policy))
        for module, names in ((self.orchestrator, ('LinearClient', 'run_turn', 'ensure_workspace')),
                              (self.runner, ('run_turn',)),
                              (self.orchestrator.MultiOrchestrator, ('start',)),
                              (self.orchestrator.Orchestrator, ('_dispatch', '_is_eligible', '_run_worker', '_reconcile',
                                '_resume_settlement', '_publication_preflight', *fixture._COMMENT_SLOTS))):
            for name in set(names):
                self.enterContext(patch.object(module, name, getattr(module, name)))
        install_sinks(self.orchestrator, auth)
        comments = {'issue': {'comments': {'nodes': [], 'pageInfo': {'hasNextPage': False}}}}
        self.owner._linear = fixture._LinearClient(SimpleNamespace(
            fetch_issue=AsyncMock(return_value=self.issue), _graphql=AsyncMock(return_value=comments)), auth)
        launcher.configure(self.runner, self.orchestrator, self.publication, policy=Path('/unused-fixture'),
                           database=self.case.path.parent / 'budget.sqlite', **auth.options())
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('fixture network prohibited')))
        self.enterContext(patch('asyncio.create_subprocess_exec', side_effect=AssertionError('fixture inference prohibited')))

    async def dispatch(self):
        self.assertTrue(self.owner._claim_slot(self.issue.id))
        self.owner._dispatch(self.issue)
        task = self.owner._tasks.get(self.issue.id)
        if task is not None:
            await asyncio.wait_for(task, 5)
        return task

    async def test_unregistered_issue_cannot_reach_any_native_worker_effect(self):
        self.start_worker(recovered=False)
        self.entries.clear()
        self.wire_normal()
        self.assertIsNone(await self.dispatch())
        self.assertFalse(self.owner.claimed or self.owner.running)
        self.assertFalse((self.case.path / '.stokowski').exists())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})

    async def test_ordinary_claim_uses_the_pinned_dispatch_and_worker(self):
        self.start_worker(recovered=False)
        self.wire_normal()
        self.assertIsNotNone(await self.dispatch(), self.calls)
        self.assertEqual(self.exchange('@inspect')['out'].count('provider'), 1)
        self.assertIn(('/v1/send', False), self.calls)
        self.assertFalse(any(path.startswith('/v1/recovery/') for path, _ in self.calls))

    def require_recipient_runtime(self):
        from uif.stokowski_cloudflare.native import NativeTurnBinding
        if ('prepare_turn_thread' not in NativeTurnBinding.__dataclass_fields__
                or not hasattr(launcher, '_recipient_native_prologue')
                or not hasattr(NativeComposition, 'recipient_assignment')):
            self.skipTest('recipient launcher, composition and owner-thread bridge must all be wired')
        if sha256((self.shelf / 'orchestrator.py').read_bytes()).hexdigest() != RECIPIENT_NATIVE_POSTIMAGE:
            self.skipTest('select the pinned native postimage with retained recipient restart cleanup')
        self.assertIn('verified_assessment_report', signature(
            self.orchestrator.Orchestrator._publish_run_report).parameters)

    async def test_exact_recovered_winner_reaches_provider_and_report_and_refuses_copied_thread(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.assertIsNotNone(self.composition._verified_assessment_report_sink)
        from uif.stokowski_cloudflare.client import WorkersAIClient

        complete = WorkersAIClient.complete
        failures = []

        def compete(client, **options):
            transport = self.composition.remote.transport

            def steal():
                try:
                    transport.request('/v1/send', {'intent': {}})
                except Hold as refused:
                    failures.append(str(refused))
            sibling = Thread(target=copy_context().run, args=(steal,))
            sibling.start()
            sibling.join(3)
            self.assertFalse(sibling.is_alive())
            return complete(client, **options)

        with patch.object(WorkersAIClient, 'complete', compete):
            self.assertIsNotNone(await self.dispatch(), self.calls)
        self.assertEqual(self.exchange('@inspect'), {'out': ['provider', 'linear'], 'effects': 2})
        self.assertRegex(failures.pop(), 'authority_recovery_.*thread|authority_recovery_selected_task')
        self.assertEqual(failures, [])
        self.assertEqual(self.calls.count(('/v1/recovery/enter', False)), 1)
        self.assertEqual(self.calls.count(('/v1/send', True)), 1)
        self.assertEqual(self.calls.count(('/v1/linear/comment', True)), 1)
        self.assertNotIn(('/v1/claim/attempt', False), self.calls)
        settlement = json.loads((self.case.path / '.stokowski/settlement.json').read_bytes())
        self.assertEqual(settlement['post_disposition']['target'], 'investigate')
        originals = (self.effects['/v1/send']['operation'], self.effects['/v1/linear/comment'])
        self.assertEqual([original['fence'] for original in originals], [self.expected_fence] * 2)
        self.exchange('@restart')
        for original in originals:
            observed = self.exchange('/v1/original', {'operation': original})
            self.assertEqual(observed['body']['terminal']['operation'], original)
            self.assertEqual(observed['body']['terminal']['status'], 'verified')
        self.assertEqual(self.exchange('@inspect'), {'out': ['provider', 'linear'], 'effects': 2})

    async def test_recovered_claim_cannot_reenter_after_uncertain_original(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.lost = True
        await self.dispatch()
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
        self.assertFalse((self.case.path / '.stokowski').exists())
        self.assertEqual(self.calls.count(('/v1/recovery/enter', False)), 1)
        self.exchange('@restart')
        self.owner._tasks.clear()
        self.owner.running.clear()
        self.owner.claimed.clear()
        self.lost = False
        await self.dispatch()
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
        self.assertEqual(self.calls.count(('/v1/recovery/enter', False)), 1)

    async def test_paused_recovered_attempt_never_enters(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.owner.pool.is_paused = lambda _: True
        self.assertIsNone(await self.dispatch())
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
        self.assertNotIn(('/v1/recovery/enter', False), self.calls)

    async def test_stopped_recovered_epoch_never_enters(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.exchange('@stop')
        self.assertIsNone(await self.dispatch())
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
        self.assertNotIn(('/v1/recovery/enter', False), self.calls)

    async def test_unavailable_quota_leaves_recovered_original_unentered(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.exchange('@drop-quota')
        self.assertIsNone(await self.dispatch())
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
        self.assertNotIn(('/v1/recovery/enter', False), self.calls)

    async def test_running_normal_discovery_recovers_admission_without_restart(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.exchange('@drop-quota')
        discovery = self.composition.options()['provider_discovery']
        discovery.interval = discovery.timeout = 0.15
        route_id = 'workers-ai-offmenu'

        async def wait_for(predicate):
            async with asyncio.timeout(4):
                while not predicate():
                    await asyncio.sleep(0.01)

        async def normal():
            self.assertTrue(discovery.active)
            await wait_for(lambda: discovery.status()[0][1] != 'not-observed')
            self.assertIsNone(discovery.observed(route_id))
            self.assertIsNone(await self.dispatch())
            self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
            self.assertNotIn(('/v1/recovery/enter', False), self.calls)

            self.exchange('@restore-quota')
            await wait_for(lambda: discovery.observed(route_id) is not None)
            self.assertTrue(discovery.active)
            self.assertIsNotNone(await self.dispatch(), self.calls)
            self.assertEqual(self.exchange('@inspect'), {'out': ['provider', 'linear'], 'effects': 2})
            self.assertEqual(self.calls.count(('/v1/recovery/enter', False)), 1)

        await discovery.run(normal)

    async def test_replaced_registered_task_cannot_enter_or_prepare_workspace(self):
        self.require_recipient_runtime()
        self.start_worker(recovered=True)
        self.wire_normal()
        self.assertTrue(self.owner._claim_slot(self.issue.id))
        self.owner._dispatch(self.issue)
        original = self.owner._tasks[self.issue.id]
        self.owner._tasks[self.issue.id] = asyncio.current_task()
        await asyncio.wait_for(original, 5)
        self.assertEqual(self.exchange('@inspect'), {'out': [], 'effects': 0})
        self.assertFalse((self.case.path / '.stokowski').exists())
        self.assertNotIn(('/v1/recovery/enter', False), self.calls)
