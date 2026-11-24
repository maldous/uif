import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import vm from 'node:vm';
import { handleRequest, loadConfig, prepareCoordinatorActivityObservation } from '../worker.mjs';
import { isRuntimeProjection } from '../public/lib/session-runtime.js';
import { graphLayout, renderApprovedGraph } from '../public/lib/approved-graph.js';
import { snapshotAtTime, isPublicProjection } from '../public/approved-app.js';

class D1 {
  constructor() {
    this.db = new DatabaseSync(':memory:');
    for (const file of readdirSync(new URL('../migrations/', import.meta.url)).sort())
      this.db.exec(readFileSync(new URL('../migrations/' + file, import.meta.url), 'utf8'));
  }
  prepare(sql) {
    const statement = this.db.prepare(sql);
    const bound = args => ({ bind: (...args) => bound(args), first: async () => statement.get(...args) ?? null,
      all: async () => ({ results: statement.all(...args) }), run: async () => statement.run(...args) });
    return bound([]);
  }
}
const NOW = Date.parse('2026-11-10T20:00:00.000Z');
const at = n => new Date(n).toISOString();
const TOKEN = 'synthetic-native-activity-test-token-000000000';
const binding = (project = 'CIX') => ({ project, kind: 'native_worker' });
const source = (more = {}) => ({ id: 'native_activity', domain: 'cloud', aliases: [], capabilities: ['coordinator_lifecycle'],
  rosterGeneration: 1, activityBindings: [binding()], staleAfterSeconds: 120, outageAfterSeconds: 600,
  tokenEnv: 'NATIVE_ACTIVITY_INGEST_DISABLED', ...more });
const activity = (more = {}) => ({ project: 'CIX', observedAt: at(NOW), state: 'completed', topicKeys: ['review'], blockerKeys: [], ...more });
const event = (more = {}) => ({ schemaVersion: 1, sourceId: 'native_activity', eventId: 'native-1', sequence: 1,
  rosterGeneration: 1, observedAt: at(NOW), health: 'ok', coverage: { complete: false, observed: 0, total: null },
  sessions: [], activities: [activity()], ...more });
const setup = (sources = [source()]) => ({ DB: new D1(), SOURCE_CONFIG_JSON: JSON.stringify({ sources }), NATIVE_ACTIVITY_INGEST_DISABLED: TOKEN });
const ingest = (env, payload = event(), now = NOW, token = TOKEN) => handleRequest(new Request('https://monitor.example/v1/ingest', {
  method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload) }), env, now);
const get = (env, now = NOW, path = '/api/status') => handleRequest(new Request('https://monitor.example' + path), env, now);
async function status(env, now = NOW) { const r = await get(env, now); assert.equal(r.status, 200, await r.clone().text()); return r.json(); }
const canonical = v => Array.isArray(v) ? '[' + v.map(canonical).join(',') + ']' : v && typeof v === 'object' ?
  '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}' : JSON.stringify(v);
function digest(snapshot) {
  const copy = structuredClone(snapshot); delete copy.revision; delete copy.generatedAt;
  for (const s of [...copy.sources, ...copy.slots]) delete s.ageSeconds;
  if (copy.fooAllowance) delete copy.fooAllowance.ageSeconds;
  return createHash('sha256').update(canonical(copy)).digest('hex');
}
function save(env, snapshot, createdAt = NOW) {
  snapshot.revision = digest(snapshot);
  env.DB.db.prepare('INSERT OR REPLACE INTO public_snapshots (revision,created_at,payload) VALUES (?,?,?)').run(snapshot.revision, createdAt, JSON.stringify(snapshot));
  return snapshot.revision;
}

test('native activity is typed, config-owned and never occupies or allocates a session slot', async () => {
  const env = setup([source({ activityBindings: ['CIX', 'UIF', 'CQ'].map(binding) })]);
  assert.equal((await ingest(env, event({ activities: ['CIX', 'UIF', 'CQ'].map(project => activity({ project })) }))).status, 202);
  const data = await status(env);
  assert.equal(isPublicProjection(data), true); assert.equal(data.projectActivities.length, 3);
  assert(!Object.hasOwn(data, 'projectAssociations')); assert(data.slots.every(s => s.assignment === 'unavailable' && s.proof === null));
  assert.deepEqual(data.projectActivities[0], { project: 'CIX', kind: 'native_worker', verified: true, observedAt: at(NOW), rosterGeneration: 1,
    state: 'completed', topicKeys: ['review'], topics: ['Review'], blockerKeys: [], blockers: [] });
  assert.equal(data.summary.executionCoverageComplete, false);
  const graph = graphLayout(data); assert.equal(graph.overall.percentage, null);
  assert(graph.cells.filter(c => c.kind === 'railway').every(c => c.status === 'neutral'));
  assert(graph.cells.filter(c => c.kind === 'durable').every(c => c.status === 'neutral'));
  assert.equal((await get(env, NOW, '/api/snapshots/' + data.revision)).status, 200);
});

test('native config and event validation rejects private fields, slot substitution and invented project/state', async () => {
  for (const more of [{ aliases: ['D'] }, { capabilities: ['registry'] }, { capabilities: ['coordinator_lifecycle', 'registry'] },
    { activityBindings: [] }, { activityBindings: [binding(), binding()] }, { activityBindings: [{ ...binding(), alias: 'D' }] },
    { activityBindings: [binding('PRIVATE')] }, { activityBindings: [{ project: 'CIX', kind: 'durable' }] },
    { staleAfterSeconds: 121 }, { outageAfterSeconds: 601 }, { rosterGeneration: 0 }]) await assert.rejects(loadConfig(setup([source(more)])));
  for (const more of [{ project: 'ANQR' }, { kind: 'native_worker' }, { state: 'idle' }, { state: 'interrupted' }, { state: 'stopped' },
    { state: 'blocked' }, { blockerKeys: ['awaiting_input'] }, { observedAt: at(NOW + 1) }, { task: 'PRIVATE' }, { agentId: '/root/PRIVATE' },
    { topicKeys: ['PRIVATE'] }, { projectAssociations: [] }]) assert.equal((await ingest(setup(), event({ activities: [activity(more)] }))).status, 422);
  for (const more of [{ activities: undefined }, { activities: [activity(), activity()] }, { observedAt: at(NOW + 1) },
    { rosterGeneration: 2 }, { projectActivities: [] }, { activityBindings: [] }, { coverage: { complete: false, observed: 1, total: null } },
    { health: 'error' }]) assert.equal((await ingest(setup(), event(more))).status, 422);
  assert.equal((await ingest(setup([source({ activityBindings: undefined })]))).status, 422);
  const legacy = source(); delete legacy.activityBindings;
  assert.equal((await ingest(setup([legacy]))).status, 422);
});

test('actual native lifecycle transitions preserve unknown, never imply idle and drop missing/failed evidence', async () => {
  const env = setup(); let i = 0;
  for (const state of ['running', 'completed', 'unknown', 'blocked']) {
    const time = NOW + i * 1000;
    assert.equal((await ingest(env, event({ eventId: `transition-${++i}`, sequence: i, observedAt: at(time),
      activities: [activity({ state, observedAt: at(time), blockerKeys: state === 'blocked' ? ['awaiting_input'] : [] })] }), time)).status, 202);
    assert.equal((await status(env, time)).projectActivities[0].state, state);
  }
  assert.equal((await ingest(env, event({ sequence: 5, eventId: 'missing', observedAt: at(NOW + 4000), activities: [] }), NOW + 4000)).status, 202);
  assert.deepEqual((await status(env, NOW + 4000)).projectActivities, []);
  await ingest(env, event({ sequence: 6, eventId: 'again', observedAt: at(NOW + 5000) }), NOW + 5000);
  await ingest(env, event({ sequence: 7, eventId: 'failed', observedAt: at(NOW + 6000), health: 'error', activities: [] }), NOW + 6000);
  const failed = await status(env, NOW + 6000); assert.equal(failed.sources[0].status, 'error'); assert.deepEqual(failed.projectActivities, []);
});

test('timestamps expire independently; replay, delayed delivery and heartbeat cannot renew cached work', async () => {
  const env = setup(); await ingest(env); const original = await status(env);
  assert.equal((await ingest(env, event(), NOW + 200000)).status, 200);
  assert.deepEqual((await status(env, NOW + 120000)).projectActivities, []);
  assert.equal((await status(env, NOW + 600000)).sources[0].status, 'outage');
  assert.equal((await ingest(env, event({ eventId: 'conflict', sequence: 1 }), NOW + 1000)).status, 409);
  assert.equal((await ingest(env, event({ eventId: 'native-1', activities: [] }), NOW + 1000)).status, 409);
  const cached = setup(); await ingest(cached);
  await ingest(cached, event({ sequence: 2, eventId: 'heartbeat', observedAt: at(NOW + 120000) }), NOW + 120000);
  const expired = await status(cached, NOW + 120000); assert.equal(expired.sources[0].status, 'fresh'); assert.deepEqual(expired.projectActivities, []);
  const delayed = setup(); await ingest(delayed, event(), NOW + 120000); assert.deepEqual((await status(delayed, NOW + 120000)).projectActivities, []);
  for (const [time, reachable] of [[NOW + 120000, true], [NOW, false]]) assert.deepEqual(snapshotAtTime(original, time, reachable).projectActivities, []);
  assert.deepEqual(await (await get(env, NOW + 900000, '/api/snapshots/' + original.revision)).json(), original);
});

test('generation or binding changes fence old heads and shared public validators reject forged metadata', async () => {
  const env = setup(); await ingest(env); const original = await status(env);
  env.SOURCE_CONFIG_JSON = JSON.stringify({ sources: [source({ rosterGeneration: 2 })] });
  assert.deepEqual((await status(env)).projectActivities, []);
  assert.equal((await ingest(env, event({ sequence: 2, eventId: 'old' }), NOW + 1000)).status, 422);
  env.SOURCE_CONFIG_JSON = JSON.stringify({ sources: [source({ activityBindings: [binding('UIF')] })] });
  assert.deepEqual((await status(env)).projectActivities, []);
  for (const mutate of [s => s.projectActivities[0].project = 'UIF', s => s.projectActivities[0].state = 'idle',
    s => s.projectActivities[0].slotId = 'cloud-D', s => s.projectActivities[0].rosterGeneration = 2,
    s => s.projectActivities[0].observedAt = at(NOW + 1), s => s.projectActivities[0].observedAt = at(NOW - 120000),
    s => s.projectActivities[0].verified = false, s => s.projectActivities[0].topics = ['PRIVATE'], s => s.projectActivities[0].agentId = 'PRIVATE',
    s => s.projectActivities.push(structuredClone(s.projectActivities[0])), s => delete s.projectActivities,
    s => s.sources[0].activityBindings[0].project = 'UIF', s => s.sources[0].activityBindings[0].alias = 'D',
    s => s.sources[0].activityBindings.push(binding()), s => s.sources[0].status = 'error', s => s.sources[0].lastObservedAt = at(NOW - 1),
    s => s.sources[0].lastReceivedAt = at(NOW + 1), s => s.sources[0].ageSeconds = 120,
    s => s.sources.push(structuredClone(s.sources[0])), s => s.sources[0].aliases = ['D'], s => s.sources[0].capabilities = ['registry'],
    s => s.slots[0].agentId = 'PRIVATE', s => s.fooAllowance.state = { private: 'PRIVATE' }, s => s.projectAssociations = [],
    s => s.private = 'PRIVATE', s => s.generatedAt = at(NOW + 1)]) {
    const bad = structuredClone(original); mutate(bad);
    if (bad.generatedAt === original.generatedAt) assert.equal(isPublicProjection(bad), false);
    const revision = save(env, bad); assert.equal((await get(env, NOW, '/api/snapshots/' + revision)).status, 503);
    assert(!renderApprovedGraph(bad).includes('PRIVATE'));
  }
});

test('native activity coexists with unchanged runtime generation and legacy snapshots', async () => {
  const rt = { id: 'runtime', domain: 'cloud', aliases: ['A', 'B', 'E', 'F'], capabilities: ['session_runtime'], runtimeGeneration: 5,
    projectBindings: [{ alias: 'A', project: 'Cloud', kind: 'durable' }], staleAfterSeconds: 120, outageAfterSeconds: 600, tokenEnv: 'RUNTIME_TOKEN' };
  const env = setup([rt, source()]); env.RUNTIME_TOKEN = 'synthetic-runtime-test-token-000000000000'; await ingest(env);
  await ingest(env, { schemaVersion: 1, sourceId: 'runtime', eventId: 'runtime-1', sequence: 1, runtimeGeneration: 5, observedAt: at(NOW), health: 'ok',
    coverage: { complete: false, observed: 1, total: null }, sessions: [{ alias: 'A', assignment: 'assigned', observedAt: at(NOW), state: 'running',
      proof: 'session_runtime', runtime: { type: 'active', activeFlags: [] }, topicKeys: [], blockerKeys: [] }] }, NOW, env.RUNTIME_TOKEN);
  const data = await status(env); assert.equal(isPublicProjection(data), true); assert.equal(data.projectAssociations.length, 1);
  assert.equal(data.projectActivities.length, 1); assert.equal(data.slots.find(s => s.id === 'cloud-D').proof, null);
  const withoutNative = structuredClone(data); withoutNative.projectActivities = [];
  assert.deepEqual(graphLayout(data).overall, graphLayout(withoutNative).overall);
  const historical = JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json', import.meta.url), 'utf8'));
  assert.equal(isPublicProjection(historical), true); save(env, historical);
  assert.deepEqual(await (await get(env, NOW, '/api/snapshots/' + historical.revision)).json(), historical);
  for (const output of [JSON.stringify(data), renderApprovedGraph(data)]) for (const forbidden of ['native_activity', 'NATIVE_ACTIVITY_INGEST_DISABLED', TOKEN, 'eventId', 'sequence', 'agentId', '/root/']) assert(!output.includes(forbidden));
});

test('offline preparation uses exact validator, config hash and ten D1 columns with 60-second deadline', async () => {
  const config = { sources: [source()] }; const e = event();
  const prepared = await prepareCoordinatorActivityObservation(config, e, NOW + 5000);
  const [loaded] = await loadConfig({ SOURCE_CONFIG_JSON: JSON.stringify(config) });
  assert.equal(prepared.configHash, loaded.configHash); assert.equal(prepared.publishBefore, at(NOW + 60000));
  assert.equal(prepared.preparedAt, at(NOW + 5000)); assert.equal(prepared.params.length, 10);
  assert.deepEqual(prepared.columns, ['source_id', 'sequence', 'event_id', 'payload_sha256', 'observed_at', 'received_at', 'health', 'config_hash', 'payload', 'allowance_observed_at']);
  assert.equal(prepared.params[8], canonical(e)); assert.equal(prepared.payloadSha256, createHash('sha256').update(canonical(e)).digest('hex'));
  const env = setup(); await env.DB.prepare(`INSERT INTO observations (${prepared.columns.join(',')}) VALUES (${prepared.params.map(() => '?').join(',')})`).bind(...prepared.params).run();
  assert.equal((await status(env, NOW + 5000)).projectActivities[0].state, 'completed');
  for (const [eventInput, time] of [[e, NOW + 60001], [e, NOW - 1], [event({ rosterGeneration: 2 }), NOW],
    [event({ observedAt: at(NOW + 60000) }), NOW + 60001], [event({ agentId: 'PRIVATE' }), NOW]])
    await assert.rejects(prepareCoordinatorActivityObservation(config, eventInput, time));
  const future = event({ observedAt: at(NOW + 1), activities: [activity({ observedAt: at(NOW + 1) })] });
  env.DB.db.prepare('UPDATE source_heads SET last_good_payload = ?').run(JSON.stringify(future)); assert.equal((await get(env)).status, 503);
});

// Async lifecycle test uses the real browser admission/freshness logic with an
// inert renderer, to isolate response ordering from presentation changes.
test('browser keeps latest native state, ignores obsolete responses and redraws independently expiring activity', async () => {
  const env = setup(); await ingest(env, event({ activities: [activity({ state: 'running' })] })); const running = await status(env);
  await ingest(env, event({ sequence: 2, eventId: 'done', observedAt: at(NOW + 1000) }), NOW + 1000); const done = await status(env, NOW + 1000);
  let now = NOW + 1000, id = 0, last; const timers = new Map(), pending = [], events = {}, elements = new Map();
  for (const key of ['board', 'theme-toggle']) elements.set(key, { dataset: {}, innerHTML: '', setAttribute() {} });
  const context = { isRuntimeProjection, renderApprovedGraph: snapshot => { last = structuredClone(snapshot); return '<svg></svg>'; },
    URL, URLSearchParams, AbortController, structuredClone, Date: class extends Date { static now() { return now; } },
    setTimeout: (fn, ms) => { timers.set(++id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id),
    location: { search: '', href: 'https://monitor.example/' }, history: { replaceState() {} },
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener: (key, fn) => { events[key] = fn; } },
    document: { hidden: false, documentElement: { dataset: {} }, getElementById: id => elements.get(id), addEventListener: (key, fn) => { events[key] = fn; } },
    fetch: () => new Promise(resolve => pending.push(data => resolve({ ok: true, json: async () => data }))) };
  vm.runInNewContext(readFileSync(new URL('../public/approved-app.js', import.meta.url), 'utf8').replace(/^import .*;\n/, '').replace(/^export /gm, ''), context);
  const tick = () => new Promise(resolve => setImmediate(resolve));
  events.visibilitychange(); pending[1](done); await tick(); assert.equal(last.projectActivities[0].state, 'completed');
  pending[0](running); await tick(); assert.equal(last.projectActivities[0].state, 'completed');
  // Activity sampled one second before its source: it must expire first.
  now = NOW + 120000; [...timers.values()].find(t => t.ms === 1000).fn();
  assert.equal(last.sources[0].status, 'fresh'); assert.deepEqual(last.projectActivities, []);
  events.pagehide(); assert.deepEqual(last.projectActivities, []); assert.equal(elements.get('board').dataset.connection, 'unavailable');
});
