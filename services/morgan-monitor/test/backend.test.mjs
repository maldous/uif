import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { handleRequest, evaluateScheduled, TOPICS } from '../worker.mjs';

// Actual SQLite constraints/triggers through a minimal D1 API adapter. This is
// offline SQL/handler coverage, NOT proof of deployed Cloudflare D1 behavior.
class D1 {
  constructor(maxMigration = Infinity) {
    this.sqlite = new DatabaseSync(':memory:');
    const dir = new URL('../migrations/', import.meta.url);
    for (const file of readdirSync(dir).sort().filter(file => parseInt(file, 10) <= maxMigration)) this.sqlite.exec(readFileSync(new URL(file, dir), 'utf8'));
  }
  prepare(sql) {
    const database = this.sqlite;
    const make = params => ({
      bind: (...values) => make(values),
      first: async () => database.prepare(sql).get(...params) ?? null,
      all: async () => ({ results: database.prepare(sql).all(...params), success: true }),
      run: async () => ({ meta: database.prepare(sql).run(...params), success: true }),
    });
    return make([]);
  }
}
const NOW = Date.UTC(2026, 10, 9, 6);
const TOKEN = 'test-only-token-not-a-real-credential-00000000';
const source = (extra = {}) => ({ id: 'foo_registry', domain: 'foo', aliases: ['1', '2'],
  capabilities: ['registry', 'turn_history'], staleAfterSeconds: 120, outageAfterSeconds: 600,
  tokenEnv: 'FOO_TOKEN', ...extra });
function setup(sources = [source()]) {
  return { DB: new D1(), SOURCE_CONFIG_JSON: JSON.stringify({ sources }), FOO_TOKEN: TOKEN };
}
const session = (alias = '1', extra = {}) => ({ alias, assignment: 'assigned', observedAt: new Date(NOW).toISOString(),
  state: 'running', proof: 'registry', topicKeys: ['monitoring'], blockerKeys: [], ...extra });
const event = (extra = {}) => ({ schemaVersion: 1, sourceId: 'foo_registry', eventId: '1', sequence: 1,
  observedAt: new Date(NOW).toISOString(), health: 'ok', coverage: { complete: false, observed: 1, total: 3 }, sessions: [session()], ...extra });
async function ingest(env, payload, now = NOW, token = TOKEN) {
  return handleRequest(new Request('https://monitor.example/v1/ingest', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload) }), env, now);
}
async function get(env, path = '/api/status', now = NOW) {
  return handleRequest(new Request(`https://monitor.example${path}`), env, now);
}
const foo = data => data.slots.find(slot => slot.id === 'foo-1');

test('no configured feed yields 6+64 unconfigured slots, never unassigned', async () => {
  const response = await get(setup([])); const data = await response.json();
  assert.equal(response.status, 200); assert.equal(data.slots.length, 70);
  assert.deepEqual(data.slots.slice(0, 6).map(s => s.alias), [...'ABCDEF']);
  assert(data.slots.every(s => s.assignment === 'unavailable' && s.displayState === null && s.availability === 'unconfigured'));
  assert.equal(data.summary.executionCoverageComplete, false);
});

test('authorization precedes payload parsing and isolates registered sources', async () => {
  const env = setup();
  assert.equal((await ingest(env, { private: 'not parsed' }, NOW, 'incorrect-token-but-long-enough-000000000')).status, 401);
  assert.equal((await ingest(env, event({ sourceId: 'cloud_registry' }))).status, 422);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM observations').get().n, 0);
  const response = await handleRequest(new Request('https://monitor.example/v1/ingest', { method: 'POST', body: '{' }), env, NOW);
  assert.equal(response.status, 401);
});

test('unknown/private fields and unreviewed content fail closed at every level', async () => {
  for (const change of [
    { title: 'secret' }, { sessions: [session('1', { url: 'https://private.invalid' })] },
    { sessions: [session('1', { topicKeys: ['<script>secret</script>'] })] },
    { sessions: [session('1', { blockerKeys: ['private-error'] })] },
    { coverage: { complete: false, observed: 1, total: 3, raw: 'secret' } },
    { conversationTopics: { voice: ['monitoring'], chat: [] } },
    { serviceState: 'stopped' },
  ]) assert.equal((await ingest(setup(), event(change))).status, 422);
});

test('public projection is allowlisted and metadata running is not live execution', async () => {
  const env = setup(); assert.equal((await ingest(env, event())).status, 202);
  const response = await get(env); const data = await response.json();
  assert.equal(foo(data).displayState, null); assert.equal(foo(data).recordedState, 'running');
  assert.equal(foo(data).availability, 'fresh'); assert.equal(data.sources[0].evidenceScope, 'metadata_only');
  assert.equal(data.sources[0].executionCoverageComplete, false);
  assert.equal(data.slots.find(s => s.id === 'foo-2').assignment, 'unavailable');
  const text = JSON.stringify(data);
  for (const privateValue of ['foo_registry', 'FOO_TOKEN', TOKEN, 'source_id', 'payload_sha256', 'eventId', 'sequence']) assert(!text.includes(privateValue));
  assert.equal(response.headers.get('cache-control'), 'no-store, max-age=0');
});

test('idempotent retries and conflicts cannot freshen or duplicate observations', async () => {
  const env = setup(); await ingest(env, event());
  const retry = await ingest(env, event(), NOW + 1000000);
  assert.deepEqual(await retry.json(), { accepted: true, duplicate: true, eventId: '1', sequence: 1 });
  assert.equal(env.DB.sqlite.prepare('SELECT received_at FROM source_heads').get().received_at, NOW);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM observations').get().n, 1);
  assert.equal((await ingest(env, event({ coverage: { complete: false, observed: 1, total: 4 } }), NOW + 2000)).status, 409);
  assert.equal((await ingest(env, event({ eventId: 'other' }), NOW + 2000)).status, 409);
  assert.equal(foo(await (await get(env, '/api/status', NOW + 1000000)).json()).availability, 'outage');
});

test('SQL atomic guards enforce sequence, source-clock monotonicity and rate', async () => {
  const env = setup(); await ingest(env, event());
  const second = event({ eventId: '2', sequence: 2 });
  assert.equal((await ingest(env, second, NOW + 999)).status, 429);
  assert.equal((await ingest(env, second, NOW + 1000)).status, 202);
  assert.equal((await ingest(env, event({ eventId: '3', sequence: 3, observedAt: new Date(NOW - 1000).toISOString() }), NOW + 2000)).status, 409);
  assert.equal(env.DB.sqlite.prepare('SELECT sequence FROM source_heads').get().sequence, 2);
});

test('concurrent exact retry commits once, and partial sources remain independent', async () => {
  const env = setup([source(), source({ id: 'cloud_feed', domain: 'cloud', aliases: ['A'], capabilities: [], tokenEnv: 'CLOUD_TOKEN' })]);
  const results = await Promise.all([ingest(env, event()), ingest(env, event())]);
  assert.deepEqual(results.map(result => result.status).sort(), [200, 202]);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM observations').get().n, 1);
  const data = await (await get(env)).json();
  assert.equal(data.sources[0].status, 'fresh'); assert.equal(data.sources[1].status, 'unavailable');
  assert.equal(data.slots.find(slot => slot.id === 'cloud-A').assignment, 'unavailable');
  assert.equal(data.summary.state, 'partial');
});

test('old durable outbox events are accepted but stale; future/invalid clocks rejected', async () => {
  const env = setup();
  assert.equal((await ingest(env, event(), NOW + 3600000)).status, 202);
  assert.equal(foo(await (await get(env, '/api/status', NOW + 3600000)).json()).availability, 'outage');
  for (const time of [NOW + 30001, Date.UTC(1999, 0, 1)]) {
    assert.equal((await ingest(setup(), event({ observedAt: new Date(time).toISOString() }))).status, 422);
  }
  assert.equal((await ingest(setup(), event({ observedAt: '2026-02-30T00:00:00Z' }))).status, 422);
  assert.equal((await ingest(setup(), event({ sessions: [session('1', { observedAt: new Date(NOW + 30001).toISOString() })] }))).status, 422);
});

test('external clock alone expires healthy sources and old session observations', async () => {
  const env = setup([source({ capabilities: ['process'] })]);
  await ingest(env, event({ sessions: [session('1', { proof: 'process' })] }));
  assert.equal(foo(await (await get(env)).json()).displayState, 'running');
  assert.equal(foo(await (await get(env, '/api/status', NOW + 120000)).json()).availability, 'stale');
  assert.equal(foo(await (await get(env, '/api/status', NOW + 600000)).json()).availability, 'outage');
  assert.equal(foo(await (await get(env, '/api/status', NOW + 600000)).json()).displayState, null);
  const old = setup();
  await ingest(old, event({ sessions: [session('1', { observedAt: new Date(NOW - 86400000).toISOString() })] }));
  assert.equal(foo(await (await get(old)).json()).availability, 'outage');
});

test('source error preserves last success and cannot prevent outage', async () => {
  const env = setup(); await ingest(env, event());
  const error = event({ sequence: 2, eventId: '2', observedAt: new Date(NOW + 30000).toISOString(), health: 'error', sessions: [], coverage: { complete: false, observed: 0, total: null } });
  assert.equal((await ingest(env, error, NOW + 30000)).status, 202);
  const freshError = await (await get(env, '/api/status', NOW + 30000)).json();
  assert.equal(freshError.sources[0].status, 'error'); assert.equal(freshError.sources[0].lastSuccessAt, new Date(NOW).toISOString());
  assert.equal((await (await get(env, '/api/status', NOW + 600000)).json()).sources[0].status, 'outage');
});

test('complete metadata coverage never establishes complete execution', async () => {
  const complete = event({ coverage: { complete: true, observed: 2, total: 2 }, sessions: [session('1'), session('2')] });
  const env = setup(); assert.equal((await ingest(env, complete)).status, 202);
  const data = await (await get(env)).json();
  assert.equal(data.sources[0].coverage.complete, true); assert.equal(data.sources[0].executionCoverageComplete, false);
  assert.equal((await ingest(setup(), event({ coverage: { complete: true, observed: 1, total: 1 } }))).status, 422);
  assert.equal((await ingest(setup(), event({ sessions: [session('1', { proof: 'process' })] }))).status, 422);
  assert.equal((await ingest(setup(), event({ sessions: [session('1', { assignment: 'unassigned' })] }))).status, 422);
});

test('explicit unassigned requires a real observation, and service stopped is separate', async () => {
  const env = setup([source({ capabilities: ['service_process', 'process'] })]);
  await ingest(env, event({ serviceState: 'stopped', sessions: [session('1', { assignment: 'unassigned', state: 'unknown', proof: 'process', topicKeys: [] })] }));
  const data = await (await get(env)).json();
  assert.equal(data.sources[0].serviceState, 'stopped'); assert.equal(data.sources[0].status, 'fresh');
  assert.equal(foo(data).assignment, 'unassigned'); assert.equal(foo(data).displayState, null);
  assert.equal(data.slots.find(s => s.id === 'foo-2').assignment, 'unavailable');
});

test('configuration changes invalidate last-good claims without resetting replay protection', async () => {
  const env = setup(); await ingest(env, event());
  env.SOURCE_CONFIG_JSON = JSON.stringify({ sources: [source({ capabilities: ['process'] })] });
  const data = await (await get(env)).json();
  assert.equal(foo(data).availability, 'unavailable'); assert.equal(foo(data).recordedState, null);
  assert.equal(env.DB.sqlite.prepare('SELECT sequence FROM source_heads').get().sequence, 1);
  const invalid = setup([source(), source({ id: 'other', tokenEnv: 'OTHER_TOKEN' })]);
  assert.equal((await get(invalid)).status, 503);
});

test('curated topic ring is newest-first, capped10, idempotent, heartbeat-preserving', async () => {
  const env = setup([source({ aliases: [], capabilities: ['curation'] })]);
  const base = { sessions: [], coverage: { complete: false, observed: 0, total: null } };
  let sequence = 1;
  for (const key of Object.keys(TOPICS)) {
    const now = NOW + sequence * 1000;
    assert.equal((await ingest(env, event({ ...base, sequence, eventId: String(sequence), observedAt: new Date(now).toISOString(), conversationTopics: { voice: [key], chat: [] } }), now)).status, 202);
    sequence++;
  }
  let data = await (await get(env, '/api/status', NOW + sequence * 1000)).json();
  assert.equal(data.conversationTopics.voice.length, 10);
  assert.equal(data.conversationTopics.voice[0].key, Object.keys(TOPICS).at(-1));
  const before = structuredClone(data.conversationTopics.voice);
  const now = NOW + sequence * 1000;
  await ingest(env, event({ ...base, sequence, eventId: String(sequence), observedAt: new Date(now).toISOString() }), now);
  data = await (await get(env, '/api/status', now)).json();
  assert.deepEqual(data.conversationTopics.voice, before);
  sequence++;
  const nextTime = NOW + sequence * 1000;
  const curated = event({ ...base, sequence, eventId: String(sequence), observedAt: new Date(nextTime).toISOString(), conversationTopics: { voice: ['monitoring_coordination', 'status_display'], chat: ['status_updates'] } });
  await ingest(env, curated, nextTime); await ingest(env, curated, nextTime + 5000);
  data = await (await get(env, '/api/status', nextTime + 5000)).json();
  assert.deepEqual(data.conversationTopics.voice.slice(0, 2).map(t => t.key), ['monitoring_coordination', 'status_display']);
  assert.equal(data.conversationTopics.chat[0].observedAt, new Date(nextTime).toISOString());
  assert.equal(new Set(data.conversationTopics.voice.map(t => t.key)).size, 10);
});

test('projection revisions ignore clock drift but change on expiry, immutable lookup never falls forward', async () => {
  const env = setup(); await ingest(env, event());
  const first = await (await get(env)).json();
  const later = await (await get(env, '/api/status', NOW + 1000)).json();
  assert.equal(first.revision, later.revision); assert.notEqual(first.generatedAt, later.generatedAt);
  assert.deepEqual(await (await get(env, `/api/snapshots/${first.revision}`, NOW + 1000)).json(), first);
  const stale = await (await get(env, '/api/status', NOW + 120000)).json();
  assert.notEqual(stale.revision, first.revision);
  assert.equal((await get(env, `/api/snapshots/${'0'.repeat(64)}`)).status, 404);
  assert.equal((await get(env, '/api/snapshots/not-a-revision')).status, 404);
});

test('receipt and snapshot retention are bounded while permanent head fences replay', async () => {
  const env = setup();
  for (let sequence = 1; sequence <= 132; sequence++) {
    const now = NOW + sequence * 1000;
    assert.equal((await ingest(env, event({ sequence, eventId: String(sequence), observedAt: new Date(now).toISOString() }), now)).status, 202);
    await get(env, '/api/status', now);
  }
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM observations').get().n, 100);
  assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM public_snapshots').get().n, 128);
  assert.equal((await ingest(env, event(), NOW + 200000)).status, 409);
});

test('HTTP protocol limits body sizes, methods, unknown APIs and MCP writes', async () => {
  const env = setup();
  assert.equal((await get(env, '/v1/ingest')).status, 405);
  assert.equal((await get(env, '/api/private')).status, 404);
  assert.equal((await ingest(env, { padding: 'x'.repeat(65536) })).status, 413);
  const rpc = (message, origin) => handleRequest(new Request('https://monitor.example/mcp', { method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(origin ? { Origin: origin } : {}) }, body: JSON.stringify(message) }), env, NOW);
  const listed = await (await rpc({ jsonrpc: '2.0', id: 1, method: 'tools/list' })).json();
  assert.deepEqual(listed.result.tools.map(t => t.name), ['get_status', 'render_status']);
  assert.deepEqual(listed.result.tools[1].inputSchema.properties.layout.enum, ['o2', 'hexagon', 'focus', 'compact']);
  assert(listed.result.tools.every(t => t.annotations.readOnlyHint));
  assert.equal((await rpc({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, 'https://evil.invalid')).status, 403);
  const read = await (await rpc({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'get_status', arguments: {} } })).json();
  assert.equal(JSON.parse(read.result.content[0].text).slots.length, 70);
  assert.equal((await (await rpc({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'restart_agent', arguments: {} } })).json()).error.code, -32602);
});

test('storage failures expose generic health error without exception/source leakage', async () => {
  const env = setup(); env.DB.prepare = () => { throw new Error('secret /private/path token'); };
  const response = await get(env, '/health');
  assert.equal(response.status, 503); assert.deepEqual(await response.json(), { accepted: false, error: 'unavailable' });
});

test('outside scheduled evaluation persists an outage with no collector or browser', async () => {
  const env = setup(); await ingest(env, event());
  const snapshot = await evaluateScheduled(env, NOW + 600000);
  assert.equal(snapshot.summary.state, 'outage');
  const saved = await (await get(env, `/api/snapshots/${snapshot.revision}`, NOW + 700000)).json();
  assert.deepEqual(saved, snapshot);
});

test('immutable snapshot clock fields cannot smuggle arbitrary private data', async () => {
  for (const mutate of [
    snapshot => { snapshot.generatedAt = { transcript: 'PRIVATE' }; },
    snapshot => { snapshot.slots[0].ageSeconds = { path: '/private' }; },
    snapshot => { snapshot.sources[0].ageSeconds = 'PRIVATE'; },
  ]) {
    const env = setup(); await ingest(env, event());
    const snapshot = await (await get(env)).json(); mutate(snapshot);
    env.DB.sqlite.prepare('UPDATE public_snapshots SET payload = ?').run(JSON.stringify(snapshot));
    const response = await get(env, `/api/snapshots/${snapshot.revision}`);
    assert.equal(response.status, 503); assert.equal((await response.json()).error, 'snapshot_unavailable');
  }
});

test('only fresh process-proven stopped is displayed as stopped', async () => {
  for (const proof of ['registry', 'turn_history', 'process']) {
    const env = setup([source({ capabilities: [proof] })]);
    await ingest(env, event({ sessions: [session('1', { state: 'stopped', proof })] }));
    assert.equal(foo(await (await get(env)).json()).displayState, proof === 'process' ? 'stopped' : null);
  }
});

test('deterministic SVG endpoint and read-only render MCP share immutable snapshots', async () => {
  const env = setup(); await ingest(env, event());
  const snapshot = await (await get(env)).json();
  const response = await get(env, `/render.svg?revision=${snapshot.revision}&width=1200&height=800`, NOW + 600000);
  assert.equal(response.status, 200); assert.equal(response.headers.get('content-type'), 'image/svg+xml; charset=utf-8');
  const text = await response.text(); assert(text.includes('<svg')); assert(text.includes(snapshot.revision));
  assert.equal(response.headers.get('x-morgan-snapshot-revision'), snapshot.revision);
  assert.equal(response.headers.get('x-morgan-render-sha256'), createHash('sha256').update(text).digest('hex'));
  const rpc = await handleRequest(new Request('https://monitor.example/mcp', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 'render', method: 'tools/call',
      params: { name: 'render_status', arguments: { revision: snapshot.revision, width: 1200, height: 800 } } }) }), env, NOW + 600000);
  const result = (await rpc.json()).result;
  assert.equal(result.content[0].text, text);
  assert.equal(result.structuredContent.renderSha256, response.headers.get('x-morgan-render-sha256'));
  const latest = await get(env, '/render.svg?width=1200&height=800', NOW + 1000);
  assert.equal(await latest.text(), text);
  assert.equal((await get(env, '/render.svg?width=999999')).status, 422);
  assert.equal((await get(env, '/render.svg?layout=o2')).status, 200);
  assert.equal((await get(env, '/render.svg?layout=compact')).status, 200);
  assert.equal((await (await get(env)).json()).fooAllowance.state, 'unavailable');
  assert.equal((await get(env, `/render.svg?revision=${'0'.repeat(64)}`)).status, 404);
});

const allowanceSource = (extra = {}) => source({ aliases: [], capabilities: ['foo_allowance'], ...extra });
const allowanceEvent = (fooAllowance, extra = {}) => event({ sessions: [], coverage: { complete: false, observed: 0, total: null },
  ...(fooAllowance === undefined ? {} : { fooAllowance }), ...extra });
const exhausted = (observedAt = NOW) => ({ state: 'exhausted', canRunAgents: false, observedAt: new Date(observedAt).toISOString() });

test('no allowance source or missing observation cannot assert exhaustion', async () => {
  for (const env of [setup([]), setup(), setup([allowanceSource()])]) {
    const data = await (await get(env)).json();
    assert.equal(data.fooAllowance.state, 'unavailable'); assert.equal(data.fooAllowance.canRunAgents, null);
    assert.equal(data.fooAllowance.verified, false);
    assert.equal(data.fooAllowance.availability, JSON.parse(env.SOURCE_CONFIG_JSON).sources.some(s => s.capabilities.includes('foo_allowance')) ? 'unavailable' : 'unconfigured');
  }
  const env = setup([allowanceSource()]);
  await ingest(env, allowanceEvent(undefined));
  assert.equal((await (await get(env)).json()).fooAllowance.availability, 'unavailable');
  assert.equal((await (await get(env)).json()).fooAllowance.verified, false);
});

test('allowance authority is unique, Foo-only, authenticated and not self-granted', async () => {
  assert.equal((await ingest(setup(), allowanceEvent(exhausted()))).status, 422);
  assert.equal((await ingest(setup([allowanceSource()]), allowanceEvent(exhausted()), NOW, 'incorrect-token-but-long-enough-000000000')).status, 401);
  assert.equal((await get(setup([allowanceSource({ domain: 'cloud' })]))).status, 503);
  assert.equal((await get(setup([allowanceSource(), allowanceSource({ id: 'second', tokenEnv: 'SECOND_TOKEN' })]))).status, 503);
});

test('allowance rejects inconsistent values, private fields and future observation clocks', async () => {
  const invalid = [
    { ...exhausted(), canRunAgents: true }, { ...exhausted(), state: 'valid' },
    { ...exhausted(), state: 'unavailable' }, { ...exhausted(), state: 'unknown' },
    { ...exhausted(), canRunAgents: 0 }, { ...exhausted(), title: 'PRIVATE' },
    { ...exhausted(), observedAt: new Date(NOW + 1).toISOString() },
    { ...exhausted(), observedAt: '1999-12-31T23:59:59Z' },
    { ...exhausted(), observedAt: '2026-02-30T00:00:00Z' },
  ];
  for (const value of invalid) assert.equal((await ingest(setup([allowanceSource()]), allowanceEvent(value))).status, 422);
  assert.equal((await ingest(setup([allowanceSource()]), allowanceEvent(exhausted(), { health: 'error' }))).status, 422);
  assert.equal((await ingest(setup([allowanceSource()]), allowanceEvent(exhausted(), { observedAt: new Date(NOW - 1).toISOString() }))).status, 422);
});

test('fresh exhausted observation enables explicit claim; valid observation restores ability', async () => {
  const env = setup([allowanceSource()]);
  assert.equal((await ingest(env, allowanceEvent(exhausted()))).status, 202);
  const first = await (await get(env)).json();
  assert.deepEqual(first.fooAllowance, { state: 'exhausted', canRunAgents: false, verified: true, availability: 'fresh',
    observedAt: new Date(NOW).toISOString(), ageSeconds: 0, staleAfterSeconds: 120, outageAfterSeconds: 600 });
  const next = NOW + 1000;
  assert.equal((await ingest(env, allowanceEvent({ state: 'valid', canRunAgents: true, observedAt: new Date(next).toISOString() },
    { sequence: 2, eventId: '2', observedAt: new Date(next).toISOString() }), next)).status, 202);
  const restored = await (await get(env, '/api/status', next)).json();
  assert.equal(restored.fooAllowance.state, 'valid'); assert.equal(restored.fooAllowance.canRunAgents, true);
  assert.equal(restored.fooAllowance.verified, true); assert.notEqual(restored.revision, first.revision);
});

test('heartbeat, exact retry and repeated old allowance do not renew allowance freshness', async () => {
  const env = setup([allowanceSource()]);
  await ingest(env, allowanceEvent(exhausted()));
  assert.equal((await ingest(env, allowanceEvent(exhausted()), NOW + 1000)).status, 200);
  await ingest(env, allowanceEvent(undefined, { sequence: 2, eventId: '2', observedAt: new Date(NOW + 60000).toISOString() }), NOW + 60000);
  await ingest(env, allowanceEvent(exhausted(), { sequence: 3, eventId: '3', observedAt: new Date(NOW + 90000).toISOString() }), NOW + 90000);
  const record = env.DB.sqlite.prepare('SELECT observed_at, received_at FROM foo_allowances').get();
  assert.equal(record.observed_at, NOW); assert.equal(record.received_at, NOW);
  const data = await (await get(env, '/api/status', NOW + 120000)).json();
  assert.equal(data.sources[0].status, 'fresh'); assert.equal(data.fooAllowance.availability, 'stale');
  assert.equal(data.fooAllowance.state, 'unavailable'); assert.equal(data.fooAllowance.canRunAgents, null);
  assert.equal(data.fooAllowance.verified, false);
});

test('source errors, unconfigured feeds, expiry and old outbox delivery cannot assert compact eligibility', async () => {
  for (const health of ['error', 'unconfigured']) {
    const env = setup([allowanceSource()]); await ingest(env, allowanceEvent(exhausted()));
    await ingest(env, allowanceEvent(undefined, { sequence: 2, eventId: '2', health, observedAt: new Date(NOW + 1000).toISOString() }), NOW + 1000);
    const data = await (await get(env, '/api/status', NOW + 1000)).json();
    assert.equal(data.fooAllowance.availability, health === 'error' ? 'error' : 'unconfigured');
    assert.equal(data.fooAllowance.state, 'unavailable'); assert.equal(data.fooAllowance.verified, false);
  }
  const env = setup([allowanceSource()]); await ingest(env, allowanceEvent(exhausted()), NOW + 600000);
  const expired = await evaluateScheduled(env, NOW + 600000);
  assert.equal(expired.fooAllowance.availability, 'outage'); assert.equal(expired.fooAllowance.verified, false);
  assert.equal(expired.fooAllowance.canRunAgents, null);
});

test('allowance observation regression/conflict is atomically rejected without renewing source success', async () => {
  const env = setup([allowanceSource()]); await ingest(env, allowanceEvent(exhausted()));
  for (const [value, code] of [
    [exhausted(NOW - 1), 'allowance_clock_regression'],
    [{ state: 'valid', canRunAgents: true, observedAt: new Date(NOW).toISOString() }, 'allowance_observation_conflict'],
  ]) {
    const response = await ingest(env, allowanceEvent(value, { sequence: 2, eventId: '2', observedAt: new Date(NOW + 1000).toISOString() }), NOW + 1000);
    assert.equal(response.status, 409); assert.equal((await response.json()).error, code);
    assert.equal(env.DB.sqlite.prepare('SELECT sequence FROM source_heads').get().sequence, 1);
    assert.equal(env.DB.sqlite.prepare('SELECT COUNT(*) AS n FROM observations').get().n, 1);
  }
});

test('explicit unavailable and registration changes fail closed without an allowance inference', async () => {
  const env = setup([allowanceSource()]);
  await ingest(env, allowanceEvent({ state: 'unavailable', canRunAgents: null, observedAt: new Date(NOW).toISOString() }));
  const unavailable = await (await get(env)).json();
  assert.equal(unavailable.fooAllowance.availability, 'unavailable'); assert.equal(unavailable.fooAllowance.verified, false);
  const second = setup([allowanceSource()]); await ingest(second, allowanceEvent(exhausted()));
  second.SOURCE_CONFIG_JSON = JSON.stringify({ sources: [allowanceSource({ staleAfterSeconds: 60 })] });
  const changed = await (await get(second)).json();
  assert.equal(changed.fooAllowance.state, 'unavailable'); assert.equal(changed.fooAllowance.observedAt, null);
});

test('allowance revision excludes request age and survives immutable lookup and public MCP', async () => {
  const env = setup([allowanceSource()]); await ingest(env, allowanceEvent(exhausted()));
  const first = await (await get(env)).json(), later = await (await get(env, '/api/status', NOW + 1000)).json();
  assert.equal(first.revision, later.revision); assert.equal(later.fooAllowance.ageSeconds, 1);
  assert.deepEqual(await (await get(env, `/api/snapshots/${first.revision}`, NOW + 600000)).json(), first);
  const expired = await (await get(env, '/api/status', NOW + 120000)).json(); assert.notEqual(expired.revision, first.revision);
  const rpc = await handleRequest(new Request('https://monitor.example/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 'allowance', method: 'tools/call', params: { name: 'get_status', arguments: {} } }) }), env, NOW);
  assert.deepEqual(JSON.parse((await rpc.json()).result.content[0].text).fooAllowance, first.fooAllowance);
});

test('old immutable snapshots remain unchanged; stored allowance clock fields remain allowlisted', async () => {
  const env = setup([allowanceSource()]); await ingest(env, allowanceEvent(exhausted()));
  const snapshot = await (await get(env)).json();
  const old = structuredClone(snapshot); delete old.fooAllowance;
  delete old.conversationTopics.status;
  const content = structuredClone(old); delete content.generatedAt; delete content.revision;
  for (const source of content.sources) delete source.ageSeconds;
  for (const slot of content.slots) delete slot.ageSeconds;
  const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` :
    value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
  old.revision = createHash('sha256').update(canonical(content)).digest('hex');
  env.DB.sqlite.prepare('INSERT INTO public_snapshots (revision, created_at, payload) VALUES (?, ?, ?)').run(old.revision, NOW, JSON.stringify(old));
  assert.deepEqual(await (await get(env, `/api/snapshots/${old.revision}`)).json(), old);
  snapshot.fooAllowance.ageSeconds = { private: 'PRIVATE' };
  env.DB.sqlite.prepare('UPDATE public_snapshots SET payload = ? WHERE revision = ?').run(JSON.stringify(snapshot), snapshot.revision);
  const response = await get(env, `/api/snapshots/${snapshot.revision}`);
  assert.equal(response.status, 503); assert.equal((await response.json()).error, 'snapshot_unavailable');
});

test('generic reviewed status has no invented voice/chat attribution and preserves order', async () => {
  const env = setup([source({ aliases: [], capabilities: ['curation'] })]);
  const subjects = ['monitor_service_implementation', 'compact_display', 'layout_design'];
  const curated = allowanceEvent(undefined, { conversationTopics: { status: subjects } });
  assert.equal((await ingest(env, curated)).status, 202);
  const data = await (await get(env)).json();
  assert.deepEqual(data.conversationTopics.voice, []); assert.deepEqual(data.conversationTopics.chat, []);
  assert.deepEqual(data.conversationTopics.status.map(topic => topic.key), subjects);
  assert(data.conversationTopics.status.every(topic => topic.observedAt === new Date(NOW).toISOString()));
  assert.deepEqual(data.conversationTopics.status[0], { key: 'monitor_service_implementation', text: 'Monitor service implementation', observedAt: new Date(NOW).toISOString() });
  const before = data.conversationTopics.status;
  await ingest(env, curated, NOW + 1000);
  await ingest(env, allowanceEvent(undefined, { sequence: 2, eventId: '2', observedAt: new Date(NOW + 2000).toISOString(), conversationTopics: { voice: ['review'] } }), NOW + 2000);
  const updated = await (await get(env, '/api/status', NOW + 2000)).json();
  assert.deepEqual(updated.conversationTopics.status, before);
  assert.equal(updated.conversationTopics.voice[0].key, 'review');
});

test('generic status is strictly allowlisted and retains only twelve newest reviewed keys', async () => {
  const config = [source({ aliases: [], capabilities: ['curation'] })];
  for (const topics of [{ status: ['PRIVATE'] }, { status: Object.keys(TOPICS).slice(0, 13) },
    { status: ['review', 'review'] }, { status: ['review'], privateTranscript: 'PRIVATE' }, {}]) {
    assert.equal((await ingest(setup(config), allowanceEvent(undefined, { conversationTopics: topics }))).status, 422);
  }
  assert.equal((await ingest(setup(), event({ conversationTopics: { status: ['review'] } }))).status, 422);
  const env = setup(config); let sequence = 0;
  for (const key of Object.keys(TOPICS)) {
    sequence++; const now = NOW + sequence * 1000;
    assert.equal((await ingest(env, allowanceEvent(undefined, { sequence, eventId: String(sequence), observedAt: new Date(now).toISOString(), conversationTopics: { status: [key] } }), now)).status, 202);
  }
  const data = await (await get(env, '/api/status', NOW + sequence * 1000)).json();
  assert.equal(data.conversationTopics.status.length, 12);
  assert.deepEqual(data.conversationTopics.status.slice(0, 3).map(topic => topic.key), Object.keys(TOPICS).slice(-3).reverse());
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS n FROM curated_topics WHERE channel = 'status'").get().n, 12);
});

test('generic-status migration preserves existing voice/chat rows and archive atomically', async () => {
  const env = setup([source({ aliases: [], capabilities: ['curation'] })]); env.DB = new D1(4);
  await ingest(env, allowanceEvent(undefined, { conversationTopics: { voice: ['review'], chat: ['planning'] } }));
  const before = env.DB.sqlite.prepare('SELECT * FROM curated_topics ORDER BY channel').all();
  const sql = readFileSync(new URL('../migrations/0005_generic_status_topics.sql', import.meta.url), 'utf8');
  env.DB.sqlite.exec(`BEGIN;\n${sql}\nCOMMIT;`);
  assert.deepEqual(env.DB.sqlite.prepare('SELECT * FROM curated_topics ORDER BY channel').all(), before);
  assert.deepEqual(env.DB.sqlite.prepare('SELECT * FROM curated_topics_before_status ORDER BY channel').all(), before);
  await ingest(env, allowanceEvent(undefined, { sequence: 2, eventId: '2', observedAt: new Date(NOW + 1000).toISOString(), conversationTopics: { status: ['monitor_service_implementation'] } }), NOW + 1000);
  const data = await (await get(env, '/api/status', NOW + 1000)).json();
  assert.equal(data.conversationTopics.status[0].key, 'monitor_service_implementation');
  assert.equal(data.conversationTopics.voice[0].key, 'review'); assert.equal(data.conversationTopics.chat[0].key, 'planning');
  assert.deepEqual(env.DB.sqlite.prepare('SELECT * FROM curated_topics_before_status ORDER BY channel').all(), before);
});
