import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { handleRequest, loadConfig, TOPICS, prepareRuntimeObservation } from '../worker.mjs';
import { runtimeEvidence } from '../public/lib/session-runtime.js';
import { graphLayout, renderApprovedGraph } from '../public/lib/approved-graph.js';
import { snapshotAtTime, isPublicProjection } from '../public/approved-app.js';

class D1 {
  constructor() {
    this.db = new DatabaseSync(':memory:');
    for (const f of readdirSync(new URL('../migrations/', import.meta.url)).sort())
      this.db.exec(readFileSync(new URL('../migrations/' + f, import.meta.url), 'utf8'));
  }
  prepare(sql) {
    const statement = this.db.prepare(sql);
    const bound = values => ({ bind: (...args) => bound(args), first: async () => statement.get(...values) ?? null,
      all: async () => ({ results: statement.all(...values) }), run: async () => statement.run(...values) });
    return bound([]);
  }
}
const NOW = Date.parse('2026-11-10T20:00:00.000Z');
const at = n => new Date(n).toISOString();
const TOKEN = 'synthetic-test-only-runtime-token-000000000000';
const source = (more = {}) => ({ id: 'runtime_test', domain: 'cloud', aliases: ['A'], capabilities: ['session_runtime'],
  runtimeGeneration: 4, projectBindings: [{ alias: 'A', project: 'Cloud', kind: 'durable' }],
  staleAfterSeconds: 120, outageAfterSeconds: 600, tokenEnv: 'RUNTIME_TEST_TOKEN', ...more });
const setup = (sources = [source()]) => ({ DB: new D1(), SOURCE_CONFIG_JSON: JSON.stringify({ sources }), RUNTIME_TEST_TOKEN: TOKEN });
const session = (raw = { type: 'active', activeFlags: [] }, more = {}) => {
  const evidence = runtimeEvidence(raw);
  return { alias: 'A', assignment: 'assigned', proof: 'session_runtime', observedAt: at(NOW),
    state: evidence.state, runtime: evidence.runtime, topicKeys: [], blockerKeys: evidence.blockerKeys, ...more };
};
const event = (more = {}) => ({ schemaVersion: 1, sourceId: 'runtime_test', eventId: '1', sequence: 1,
  runtimeGeneration: 4, observedAt: at(NOW), health: 'ok', coverage: { complete: false, observed: 1, total: null },
  sessions: [session()], ...more });
const ingest = (env, payload = event(), now = NOW) => handleRequest(new Request('https://monitor.example/v1/ingest', {
  method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` }, body: JSON.stringify(payload) }), env, now);
const get = (env, now = NOW, path = '/api/status') => handleRequest(new Request('https://monitor.example' + path), env, now);
async function status(env, now = NOW) {
  const r = await get(env, now); assert.equal(r.status, 200, await r.clone().text()); return r.json();
}
const slot = data => data.slots.find(s => s.id === 'cloud-A');
const cell = data => graphLayout(data).cells.find(c => c.id === 'durable-Cloud');
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']' :
  value && typeof value === 'object' ? '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}' : JSON.stringify(value);
function digest(snapshot) {
  const copy = structuredClone(snapshot); delete copy.revision; delete copy.generatedAt;
  for (const s of [...copy.sources, ...copy.slots]) delete s.ageSeconds;
  if (copy.fooAllowance) delete copy.fooAllowance.ageSeconds;
  return createHash('sha256').update(canonical(copy)).digest('hex');
}
function save(env, value, rehash = false) {
  const snapshot = structuredClone(value);
  if (rehash) snapshot.revision = digest(snapshot);
  env.DB.db.prepare('INSERT OR REPLACE INTO public_snapshots (revision,created_at,payload) VALUES (?,?,?)')
    .run(snapshot.revision, NOW, JSON.stringify(snapshot));
  return snapshot.revision;
}

test('runtime union maps sampled active, waiting, idle and unknown exactly, never from turn results', async () => {
  for (const [raw, expected, blockers] of [
    [{ type: 'active', activeFlags: [] }, 'running', []],
    [{ type: 'active', activeFlags: ['waitingOnApproval'] }, 'blocked', ['awaiting_approval']],
    [{ type: 'active', activeFlags: ['waitingOnUserInput'] }, 'blocked', ['awaiting_input']],
    [{ type: 'active', activeFlags: ['waitingOnUserInput', 'waitingOnApproval'] }, 'blocked', ['awaiting_approval', 'awaiting_input']],
    [{ type: 'idle' }, 'idle', []], [{ type: 'systemError' }, 'unknown', []], [{ type: 'notLoaded' }, 'unknown', []],
  ]) {
    const env = setup(); assert.equal((await ingest(env, event({ sessions: [session(raw)] }))).status, 202);
    const data = await status(env);
    assert.equal(slot(data).recordedState, expected); assert.equal(slot(data).displayState, expected === 'unknown' ? null : expected);
    assert.deepEqual(slot(data).blockerKeys, blockers);
    assert.deepEqual(slot(data).runtime, runtimeEvidence(raw).runtime);
    assert.equal(isPublicProjection(data), true);
    assert.equal(data.sources[0].evidenceScope, 'session_runtime'); assert.equal(data.sources[0].observationMode, 'sampled');
    assert.equal(data.sources[0].executionCoverageComplete, false); assert.equal(data.summary.executionCoverageComplete, false);
    assert.equal(data.summary.state, 'partial'); assert.equal(data.projectAssociations.length, 1);
    assert.equal(cell(data).status, expected === 'blocked' ? 'blocked' : ['running', 'idle'].includes(expected) ? 'success' : 'neutral');
    assert.equal(graphLayout(data).overall.percentage, expected === 'unknown' ? null : expected === 'blocked' ? 0 : 100);
    assert.equal(graphLayout(data).overall.complete, false);
    assert.equal((await get(env, NOW, '/api/snapshots/' + data.revision)).status, 200);
  }
});

test('strict runtime and derived state reject private fields, unknown flags, duplicates, flags on other types and history', async () => {
  const bad = [null, [], {}, { type: 'running' }, { type: 'active' }, { type: 'active', activeFlags: null },
    { type: 'active', activeFlags: ['private text'] }, { type: 'active', activeFlags: ['waitingOnApproval', 'waitingOnApproval'] },
    { type: 'idle', activeFlags: [] }, { type: 'systemError', activeFlags: [] }, { type: 'notLoaded', activeFlags: [] },
    { type: 'active', activeFlags: [], threadId: 'PRIVATE_ID' }, { type: 'systemError', message: 'PRIVATE_ERROR' },
    { type: 'idle', latestTurn: { status: 'completed' } }];
  for (const raw of bad) assert.equal((await ingest(setup(), event({ sessions: [session(undefined, { runtime: raw })] }))).status, 422);
  for (const more of [{ state: 'completed' }, { state: 'failed' }, { state: 'idle' }, { blockerKeys: ['awaiting_input'] },
    { threadId: 'PRIVATE_ID' }, { title: 'PRIVATE_TITLE' }, { environmentKey: 'cloud_scratch' }, { assignment: 'unassigned' },
    { project: 'Cloud' }, { projectBindings: [] }, { latestTurn: 'completed' }, { runtimeGeneration: 4 }])
    assert.equal((await ingest(setup(), event({ sessions: [session(undefined, more)] }))).status, 422);
  for (const more of [{ runtimeGeneration: 3 }, { runtimeGeneration: undefined }, { rosterGeneration: 4 },
    { projectAssociations: [] }, { projectBindings: [] }, { raw: 'PRIVATE' },
    { coverage: { complete: true, observed: 1, total: 1 } }, { coverage: { complete: false, observed: 2, total: null } },
    { coverage: { complete: false, observed: 1, total: 64 } }])
    assert.equal((await ingest(setup(), event(more))).status, 422);
});

test('config requires separate capability, reviewed generation, owned aliases and globally unique cells', async () => {
  const variants = [source({ runtimeGeneration: undefined }), source({ runtimeGeneration: 0 }), source({ runtimeGeneration: '4' }),
    source({ capabilities: ['session_runtime', 'process'] }), source({ capabilities: ['session_runtime', 'coordinator_lifecycle'], rosterGeneration: 3 }),
    source({ capabilities: ['session_runtime', 'foo_allowance'] }), source({ staleAfterSeconds: 121 }), source({ outageAfterSeconds: 601 }),
    source({ projectBindings: undefined }), source({ projectBindings: [{ alias: 'B', project: 'Cloud', kind: 'durable' }] }),
    source({ projectBindings: [{ alias: 'A', project: 'private-name', kind: 'durable' }] }),
    source({ projectBindings: [{ alias: 'A', project: 'Cloud', kind: 'durable', index: 1 }] }),
    source({ projectBindings: [{ alias: 'A', project: 'Cloud', kind: 'primary' }] }),
    source({ projectBindings: [{ alias: 'A', project: 'Cloud', kind: 'primary', index: 7 }] }),
    source({ projectBindings: [{ alias: 'A', project: 'Cloud', kind: 'durable', threadId: 'PRIVATE_ID' }] }),
    source({ aliases: ['A', 'A'] }), source({ aliases: ['A', 'B'], projectBindings: [
      { alias: 'A', project: 'Cloud', kind: 'durable' }, { alias: 'A', project: 'UIF', kind: 'durable' }] }),
    source({ aliases: ['A', 'B'], projectBindings: [
      { alias: 'A', project: 'Cloud', kind: 'durable' }, { alias: 'B', project: 'Cloud', kind: 'durable' }] })];
  for (const s of variants) await assert.rejects(loadConfig(setup([s])));
  await assert.rejects(loadConfig(setup([source(), source({ id: 'second', tokenEnv: 'SECOND_TOKEN' })])));
  await assert.rejects(loadConfig(setup([source(), source({ id: 'second', tokenEnv: 'SECOND_TOKEN', aliases: ['B'],
    projectBindings: [{ alias: 'B', project: 'Cloud', kind: 'durable' }] })])));
  for (const kind of ['primary', 'overflow']) for (const index of [1, 6])
    assert.equal((await loadConfig(setup([source({ projectBindings: [{ alias: 'A', project: 'CQ', kind, index }] })]))).length, 1);
  assert.equal((await loadConfig(setup([source({ projectBindings: [] })]))).length, 1);
});

test('fresh assignment is bound by reviewed config only; B-F and unbound samples remain neutral', async () => {
  const env = setup([source({ aliases: ['A', 'B'] })]);
  await ingest(env, event({ sessions: [session(), session({ type: 'idle' }, { alias: 'B' })], coverage: { complete: false, observed: 2, total: null } }));
  const data = await status(env); assert.equal(data.projectAssociations.length, 1); assert.equal(cell(data).slotId, 'cloud-A');
  assert.equal(graphLayout(data).cells.filter(c => c.slotId).length, 1);
  assert.equal(graphLayout(data).cells.find(c => c.id === 'durable-UIF').slotId, null);
  const absent = setup([source({ projectBindings: [] })]); await ingest(absent);
  assert.equal((await status(absent)).projectAssociations.length, 0); assert.equal(cell(await status(absent)).status, 'neutral');
  const empty = setup(); assert.equal((await status(empty)).projectAssociations.length, 0);
});

test('timestamps are actual sample times; retries, heartbeats and delayed delivery cannot rejuvenate cached status', async () => {
  for (const more of [{ observedAt: at(NOW + 1) }, { sessions: [session(undefined, { observedAt: at(NOW + 1) })] },
    { observedAt: '2026-02-30T00:00:00Z' }, { sessions: [session(undefined, { observedAt: 'PRIVATE_TIME' })] }])
    assert.equal((await ingest(setup(), event(more))).status, 422);
  const env = setup(); await ingest(env); const initial = await status(env);
  assert.equal((await ingest(env, event(), NOW + 200000)).status, 200);
  assert.equal((await status(env, NOW + 120000)).projectAssociations.length, 0);
  assert.equal(slot(await status(env, NOW + 600000)).availability, 'outage');
  const delayed = setup(); await ingest(delayed, event(), NOW + 600000);
  assert.equal(slot(await status(delayed, NOW + 600000)).displayState, null);
  const cached = setup(); await ingest(cached);
  await ingest(cached, event({ eventId: '2', sequence: 2, observedAt: at(NOW + 120000) }), NOW + 120000);
  const stale = await status(cached, NOW + 120000);
  assert.equal(stale.sources[0].status, 'fresh'); assert.equal(slot(stale).availability, 'stale'); assert.deepEqual(stale.projectAssociations, []);
  assert.equal(slot(stale).observedAt, at(NOW));
  const repeated = await status(env, NOW + 1000); assert.equal(repeated.revision, initial.revision);
});

test('runtime transition changes revision and failed source immediately withholds associations and current state', async () => {
  const env = setup(); await ingest(env); const running = await status(env);
  await ingest(env, event({ eventId: '2', sequence: 2, observedAt: at(NOW + 1000), sessions: [session({ type: 'idle' }, { observedAt: at(NOW + 1000) })] }), NOW + 1000);
  const idle = await status(env, NOW + 1000); assert.equal(slot(idle).displayState, 'idle'); assert.notEqual(idle.revision, running.revision);
  await ingest(env, event({ eventId: '3', sequence: 3, observedAt: at(NOW + 2000), health: 'error', sessions: [],
    coverage: { complete: false, observed: 0, total: null } }), NOW + 2000);
  const failed = await status(env, NOW + 2000);
  assert.equal(failed.sources[0].status, 'error'); assert.deepEqual(failed.projectAssociations, []);
  assert.equal(slot(failed).displayState, null); assert.equal(slot(failed).recordedState, 'idle'); assert.equal(slot(failed).observedAt, at(NOW + 1000));
  assert.equal(cell(failed).status, 'neutral');
});

test('generation or binding changes fence old observations, but historical immutable snapshots remain exact', async () => {
  const env = setup(); await ingest(env); const before = await status(env);
  env.SOURCE_CONFIG_JSON = JSON.stringify({ sources: [source({ runtimeGeneration: 5 })] });
  let data = await status(env); assert.equal(slot(data).proof, null); assert.deepEqual(data.projectAssociations, []);
  assert.equal((await ingest(env, event({ eventId: '2', sequence: 2 }), NOW + 1000)).status, 422);
  const preserved = await (await get(env, NOW + 5000000, '/api/snapshots/' + before.revision)).json(); assert.deepEqual(preserved, before);
  env.SOURCE_CONFIG_JSON = JSON.stringify({ sources: [source({ projectBindings: [{ alias: 'A', project: 'UIF', kind: 'durable' }] })] });
  data = await status(env); assert.equal(slot(data).proof, null); assert.deepEqual(data.projectAssociations, []);
  const historical = JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json', import.meta.url), 'utf8'));
  assert.equal(digest(historical), historical.revision); save(env, historical);
  const oldResponse = await get(env, NOW, '/api/snapshots/' + historical.revision); assert.equal(oldResponse.status, 200);
  assert.deepEqual(await oldResponse.json(), historical); assert(!Object.hasOwn(historical, 'projectAssociations'));
});

test('immutable validation rejects injected fields, malformed ownership and state even with a recalculated revision', async () => {
  const env = setup(); await ingest(env); const original = await status(env);
  for (const mutate of [s => s.private = 'PRIVATE', s => slot(s).runtime.latestTurn = 'PRIVATE',
    s => slot(s).runtime.activeFlags = ['PRIVATE'], s => slot(s).recordedState = 'completed',
    s => slot(s).displayState = 'failed', s => slot(s).runtimeGeneration = 3, s => s.sources[0].runtimeGeneration = 3,
    s => s.sources[0].executionCoverageComplete = true, s => s.sources[0].coverage.complete = true,
    s => s.sources[0].capabilities.push('process'), s => s.sources.push(structuredClone(s.sources[0])),
    s => s.projectAssociations[0].runtimeGeneration = 3, s => s.projectAssociations[0].privateId = 'PRIVATE',
    s => s.projectAssociations[0].slotId = 'cloud-B', s => s.projectAssociations[0].index = 1,
    s => s.projectAssociations.push(structuredClone(s.projectAssociations[0])), s => s.projectAssociations[0].observedAt = at(NOW - 1000),
    s => slot(s).ageSeconds = { private: 'PRIVATE' }, s => s.sources[0].ageSeconds = 'PRIVATE',
    s => s.generatedAt = at(NOW + 1), s => s.generatedAt = at(NOW + 120000), s => s.generatedAt = 'PRIVATE',
    s => s.sources[0].lastSuccessAt = at(NOW + 1), s => s.sources[0].lastReceivedAt = at(NOW + 1),
    s => s.sources[0].coverage.observed = 1000000, s => s.sources[0].ageSeconds = 121, s => slot(s).ageSeconds = 121,
    s => s.summary.executionCoverageComplete = true,
    s => { slot(s).proof = 'process'; delete slot(s).runtime; delete slot(s).runtimeGeneration; s.projectAssociations = []; }]) {
    const bad = structuredClone(original); mutate(bad); const revision = save(env, bad, true);
    assert.equal((await get(env, NOW, '/api/snapshots/' + revision)).status, 503);
  }
  const changed = structuredClone(original); changed.projectAssociations[0].project = 'UIF'; save(env, changed);
  assert.equal((await get(env, NOW, '/api/snapshots/' + original.revision)).status, 503, 'binding is included in integrity hash');
});

test('public status, SVG and served source contain no ingest identifiers, credentials or private thread fields', async () => {
  const env = setup(); await ingest(env); const data = await status(env);
  const outputs = [JSON.stringify(data), await (await get(env, NOW, '/render.svg')).text()];
  for (const output of outputs) for (const value of ['runtime_test', TOKEN, 'RUNTIME_TEST_TOKEN', 'eventId', 'sequence', 'threadId', 'hostId', '"cursor"', 'latestTurn'])
    assert(!output.includes(value), value);
  assert.equal((await get(env, NOW, '/lib/session-runtime.js')).status, 200);
  assert(renderApprovedGraph(data).includes('>100%</text>'));
  assert(renderApprovedGraph(data).includes('>1/1 observed</text>'));
  assert(renderApprovedGraph(data).includes('>partial</text>'));
});

test('renderer validates runtime binding generation, evidence, expiry, duplicates and failed browser transport', async () => {
  const env = setup(); await ingest(env); const data = await status(env);
  for (const mutate of [s => delete s.projectAssociations, s => s.projectAssociations[0].verified = false,
    s => s.projectAssociations[0].runtimeGeneration = 3, s => s.projectAssociations[0].observedAt = at(NOW - 1000),
    s => s.projectAssociations[0].kind = 'arbitrary', s => s.projectAssociations[0].index = 1,
    s => s.projectAssociations[0].raw = 'PRIVATE', s => s.projectAssociations.push(structuredClone(s.projectAssociations[0])),
    s => s.sources[0].status = 'error', s => s.sources[0].runtimeGeneration = 3, s => s.sources.push(structuredClone(s.sources[0])),
    s => slot(s).runtime = { type: 'active', activeFlags: ['PRIVATE'] }, s => slot(s).displayState = 'completed',
    s => slot(s).assignment = 'unassigned', s => slot(s).observedAt = at(NOW + 1)]) {
    const bad = structuredClone(data); mutate(bad); assert.equal(cell(bad).status, 'neutral');
  }
  for (const [clock, reachable] of [[NOW + 120000, true], [NOW, false]]) {
    const expired = snapshotAtTime(data, clock, reachable);
    assert.deepEqual(expired.projectAssociations, []);
    assert.equal(graphLayout(expired, { now: clock }).cells.find(c => c.id === 'durable-Cloud').status, 'neutral');
  }
  for (const mode of ['full', 'compact']) for (const theme of ['light', 'dark']) {
    const layout = graphLayout(data, { mode, theme }); assert.equal(layout.cells.length, mode === 'full' ? 91 : 13);
    assert.equal(layout.overall.percentage, 100); assert.equal(layout.overall.complete,false); assert(layout.cells.filter(c => c.kind === 'railway').every(c => c.status === 'neutral'));
  }
});


test('browser admission rejects private content, false completeness, owner mismatches, unsupported clocks and limits', async () => {
  const env = setup([source({ aliases: ['A', 'B'] })]); await ingest(env); const original = await status(env);
  assert.equal(isPublicProjection(original), true);
  for (const mutate of [s => s.privateThreadId = 'PRIVATE', s => s.sources[0].privateThreadId = 'PRIVATE',
    s => slot(s).privateThreadId = 'PRIVATE', s => slot(s).topics = ['PRIVATE'], s => slot(s).topicKeys = ['PRIVATE'],
    s => s.sources[0].runtimeGeneration = 1000001, s => s.sources[0].staleAfterSeconds = 500 * 365 * 86400,
    s => s.sources[0].coverage.complete = true, s => s.sources[0].coverage.observed = 1000000,
    s => s.summary.executionCoverageComplete = true, s => s.sources[0].lastSuccessAt = at(NOW + 1),
    s => s.sources[0].lastReceivedAt = at(NOW + 1), s => slot(s).observedAt = at(NOW + 1),
    s => slot(s).ageSeconds = 121, s => s.sources[0].ageSeconds = 121,
    s => { const b = s.slots.find(slot => slot.id === 'cloud-B'); delete b.runtimeGeneration; },
    s => { slot(s).proof = 'process'; delete slot(s).runtime; delete slot(s).runtimeGeneration; s.projectAssociations = []; },
    s => s.projectAssociations[0].runtimeGeneration = 3, s => slot(s).runtime.activeFlags = ['PRIVATE'],
    s => s.conversationTopics.voice = [{ key: 'engineering', text: 'PRIVATE', observedAt: at(NOW) }]]) {
    const bad = structuredClone(original); mutate(bad); assert.equal(isPublicProjection(bad), false);
    assert.equal(cell(bad).status, 'neutral');
    assert(!renderApprovedGraph(bad).includes('PRIVATE'));
  }
});

test('persisted future runtime read fails closed before current API projection, not only immutable retrieval', async () => {
  const env = setup(); await ingest(env);
  const future = event({ observedAt: at(NOW + 10000), sessions: [session(undefined, { observedAt: at(NOW + 10000) })] });
  env.DB.db.prepare('UPDATE source_heads SET last_good_payload = ?').run(JSON.stringify(future));
  assert.equal((await get(env)).status, 503);
});

test('safe topic vocabulary remains identical between receiver, browser and runtime renderer', async () => {
  for (const key of Object.keys(TOPICS)) {
    const env = setup(); await ingest(env, event({ sessions: [session(undefined, { topicKeys: [key] })] }));
    const data = await status(env); assert.equal(isPublicProjection(data), true); assert.equal(cell(data).status, 'success');
    assert.equal(slot(data).topics[0], TOPICS[key]);
  }
});


test('mixed runtime and legacy projections reject nested private values with browser/server parity', async () => {
  const legacy = { id: 'legacy', domain: 'foo', aliases: ['1'], capabilities: ['registry'], staleAfterSeconds: 120, outageAfterSeconds: 600, tokenEnv: 'LEGACY_TOKEN' };
  const env = setup([source(), legacy]); await ingest(env); const original = await status(env);
  for (const mutate of [s => s.fooAllowance.state = { privateThreadId: 'PRIVATE' },
    s => s.sources[1].coverage = { privateThreadId: 'PRIVATE' }, s => s.slots.find(slot => slot.id === 'foo-1').proof = 'PRIVATE',
    s => s.sources[1].serviceState = { privateThreadId: 'PRIVATE' }, s => s.fooAllowance.observedAt = { privateThreadId: 'PRIVATE' }]) {
    const bad = structuredClone(original); mutate(bad); assert.equal(isPublicProjection(bad), false);
    assert.equal(cell(bad).status, 'neutral'); assert(!renderApprovedGraph(bad).includes('PRIVATE'));
    assert.equal((await get(env, NOW, '/api/snapshots/' + save(env, bad, true))).status, 503);
  }
});

test('runtime idle pulses green without edge travel; active alone travels, with mapped state and original time accessible without page chrome', async () => {
  for (const type of ['active', 'idle']) {
    const env = setup(); await ingest(env, event({ sessions: [session(type === 'active' ? { type, activeFlags: [] } : { type })] }));
    const data = await status(env), svg = renderApprovedGraph(data, { interactive: false, footer: false });
    assert.equal(cell(data).status, 'success');
    assert.equal(svg.includes('class="ag-travel"'), type === 'active');
    assert.match(svg, new RegExp('Cloud durable: sampled session ' + (type === 'active' ? 'running' : 'idle') + '; observed ' + at(NOW)));
    assert(!svg.includes('ag-snapshot-footer')); assert(!svg.includes('role="button"')); assert(!svg.includes('data-cell-id='));
    assert(svg.includes('>100%</text>'));
    assert(svg.includes('>1/1 observed</text>'));assert(svg.includes('>partial</text>'));
    assert(svg.includes('class="ag-pulse"'));
  }
});

test('offline preparation uses actual config/event validators and exact canonical D1 parameter order', async () => {
  const config = { sources: [source()] }, e = event();
  const result = await prepareRuntimeObservation(config, e, NOW);
  assert.equal(result.configHash, (await loadConfig(setup())).at(0).configHash);
  assert.equal(result.payloadSha256, createHash('sha256').update(canonical(e)).digest('hex'));
  assert.deepEqual(result.params, ['runtime_test', 1, '1', result.payloadSha256, NOW, NOW, 'ok', result.configHash, canonical(e), null]);
  assert.equal(result.publishBefore, at(NOW + 60000)); assert.equal(result.preparedAt, at(NOW));
  assert(!JSON.stringify(result).includes(TOKEN)); assert(!JSON.stringify(result).includes('RUNTIME_TEST_TOKEN'));
  for (const [cfg, payload, clock] of [[config, e, NOW + 60001], [config, e, NOW - 1],
    [config, event({ runtimeGeneration: 3 }), NOW], [config, event({ privateId: 'PRIVATE' }), NOW],
    [config, event({ observedAt: at(NOW + 60000) }), NOW + 60001],
    [{ sources: [source({ projectBindings: [{ alias: 'B', project: 'Cloud', kind: 'durable' }] })] }, e, NOW]])
    await assert.rejects(prepareRuntimeObservation(cfg, payload, clock));
  assert.equal((await prepareRuntimeObservation(config, e, NOW + 60000)).sequence, 1);
});

test('observability-corrected SVG output is deterministic in all approved modes', () => {
  const snapshot = JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json', import.meta.url), 'utf8'));
  const expected = JSON.parse(readFileSync(new URL('./fixtures/synthetic-render-sha256.json', import.meta.url), 'utf8'));
  for (const { options, sha256 } of expected)
    assert.equal(createHash('sha256').update(renderApprovedGraph(snapshot, options)).digest('hex'), sha256);
});
