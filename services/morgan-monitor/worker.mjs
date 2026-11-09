/** Independent, read-only public projection. Ingest is authenticated per source.
 * No source discovery, OAuth inheritance, execution control, or deployment here.
 */
export const TOPICS = Object.freeze({
  infrastructure: 'Infrastructure', monitoring: 'Monitoring', research: 'Research',
  planning: 'Planning', engineering: 'Engineering', review: 'Review',
  testing: 'Testing', delivery: 'Delivery',
  monitoring_coordination: 'Monitoring coordination', status_display: 'Status display',
  status_updates: 'Status updates', offmenu_sync: 'OffMenu sync',
  monitor_service_implementation: 'Monitor service implementation', compact_display: 'Compact display',
  layout_design: 'Layout design',
  worker_identity_mapping: 'Worker identity mapping', expressive_animation: 'Expressive animation',
});
export const BLOCKERS = Object.freeze({
  source_unavailable: 'Source unavailable', coverage_partial: 'Partial coverage',
  awaiting_input: 'Awaiting input', dependency_unavailable: 'Dependency unavailable',
  quota_unavailable: 'Capacity unavailable', execution_failed: 'Recorded execution failure',
});
const CAPABILITIES = ['registry', 'turn_history', 'process', 'service_process', 'curation', 'foo_allowance'];
const STATES = ['running', 'idle', 'stopped', 'completed', 'failed', 'blocked', 'unknown'];
const ASSIGNMENTS = ['assigned', 'unassigned', 'unavailable'];
const MIN_DATE = Date.UTC(2000, 0, 1);
const MAX_BODY = 64 * 1024;
const EMPTY_COVERAGE = Object.freeze({ complete: false, observed: 0, total: null });
const SECURITY_HEADERS = {
  'Cache-Control': 'no-store, max-age=0',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  // Deterministic SVG contains an inline stylesheet. Scripts remain external-
  // only, and CSS cannot load arbitrary network images/fonts or connect out.
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
};

class HttpError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}
const reject = (status, code) => { throw new HttpError(status, code); };
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function exact(value, required, optional = []) {
  if (!object(value) || required.some(k => !Object.hasOwn(value, k)) ||
      Object.keys(value).some(k => !required.includes(k) && !optional.includes(k))) reject(422, 'invalid_event');
}
function integer(value, min, max) { return Number.isSafeInteger(value) && value >= min && value <= max; }
function uniqueList(value, allowed, max) {
  if (!Array.isArray(value) || value.length > max || new Set(value).size !== value.length ||
      value.some(x => typeof x !== 'string' || !allowed.includes(x))) reject(422, 'invalid_event');
  return [...value];
}
function timestamp(value, ceiling = Infinity) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/.test(value)) reject(422, 'invalid_event');
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || parsed < MIN_DATE || parsed > ceiling ||
      new Date(parsed).toISOString().replace('.000Z', 'Z') !== value.replace('.000Z', 'Z')) reject(422, 'invalid_event');
  return parsed;
}
const iso = value => value == null ? null : new Date(value).toISOString();
const age = (now, ...values) => values.some(x => x == null) ? null : Math.max(0, Math.floor((now - Math.min(...values)) / 1000));
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
async function hash(value) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, '0')).join('');
}
// Native HMAC verification compares the authenticator, not JS string prefixes.
// The comparison key is public and fixed; no credentials are created or persisted.
async function tokenEqual(provided, expected) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('morgan-token-comparison-v1'),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
  const signed = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(expected));
  return crypto.subtle.verify('HMAC', key, signed, new TextEncoder().encode(provided));
}

/** Operator-reviewed config only. Registration cannot come from an ingest body. */
export async function loadConfig(env) {
  try {
    if (typeof env.SOURCE_CONFIG_JSON !== 'string' || env.SOURCE_CONFIG_JSON.length > MAX_BODY) throw new Error();
    const config = JSON.parse(env.SOURCE_CONFIG_JSON);
    exact(config, ['sources']);
    if (!Array.isArray(config.sources) || config.sources.length > 16) throw new Error();
    const ids = new Set(), aliases = new Set(), tokens = new Set();
    let curationOwners = 0, allowanceOwners = 0;
    const sources = [];
    for (const source of config.sources) {
      exact(source, ['id', 'domain', 'aliases', 'capabilities', 'staleAfterSeconds', 'outageAfterSeconds', 'tokenEnv']);
      if (!/^[a-z][a-z0-9_-]{0,47}$/.test(source.id) || ids.has(source.id) ||
          !['foo', 'cloud'].includes(source.domain) || !/^[A-Z][A-Z0-9_]{2,63}$/.test(source.tokenEnv) ||
          tokens.has(source.tokenEnv) || !integer(source.staleAfterSeconds, 30, 3600) ||
          !integer(source.outageAfterSeconds, source.staleAfterSeconds + 1, 86400)) throw new Error();
      const allowed = source.domain === 'cloud' ? [...'ABCDEF'] : Array.from({ length: 64 }, (_, i) => String(i + 1));
      uniqueList(source.aliases, allowed, allowed.length);
      uniqueList(source.capabilities, CAPABILITIES, CAPABILITIES.length);
      for (const alias of source.aliases) {
        const key = `${source.domain}-${alias}`;
        if (aliases.has(key)) throw new Error();
        aliases.add(key);
      }
      if (source.capabilities.includes('curation')) curationOwners++;
      if (curationOwners > 1) throw new Error();
      if (source.capabilities.includes('foo_allowance')) {
        allowanceOwners++;
        if (source.domain !== 'foo' || allowanceOwners > 1) throw new Error();
      }
      ids.add(source.id); tokens.add(source.tokenEnv);
      sources.push({ ...source, configHash: await hash(canonical(source)), publicAlias: `source-${sources.length + 1}` });
    }
    return sources;
  } catch { reject(503, 'configuration_unavailable'); }
}

/** Reject unknown fields rather than sanitizing arbitrary private payloads. */
export function validateEvent(input, source, now, enforceClock = true) {
  exact(input, ['schemaVersion', 'sourceId', 'eventId', 'sequence', 'observedAt', 'health', 'coverage', 'sessions'],
    ['serviceState', 'conversationTopics', 'fooAllowance']);
  if (input.schemaVersion !== 1 || input.sourceId !== source.id ||
      typeof input.eventId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(input.eventId) ||
      !integer(input.sequence, 1, Number.MAX_SAFE_INTEGER) || !['ok', 'error', 'unconfigured'].includes(input.health)) reject(422, 'invalid_event');
  const observedAt = timestamp(input.observedAt, enforceClock ? now + 30000 : Infinity);
  exact(input.coverage, ['complete', 'observed', 'total']);
  const coverage = input.coverage;
  if (typeof coverage.complete !== 'boolean' || !integer(coverage.observed, 0, 1000000) ||
      !(coverage.total === null || integer(coverage.total, coverage.observed, 1000000)) ||
      !Array.isArray(input.sessions) || input.sessions.length > source.aliases.length ||
      input.sessions.length > coverage.observed) reject(422, 'invalid_event');
  const aliases = new Set();
  for (const session of input.sessions) {
    exact(session, ['alias', 'assignment', 'observedAt', 'state', 'proof', 'topicKeys', 'blockerKeys']);
    if (!source.aliases.includes(session.alias) || aliases.has(session.alias) ||
        !ASSIGNMENTS.includes(session.assignment) || !STATES.includes(session.state) ||
        !['registry', 'turn_history', 'process'].includes(session.proof) ||
        !source.capabilities.includes(session.proof)) reject(422, 'invalid_event');
    timestamp(session.observedAt, Math.min(observedAt + 30000, enforceClock ? now + 30000 : Infinity));
    uniqueList(session.topicKeys, Object.keys(TOPICS), 3);
    uniqueList(session.blockerKeys, Object.keys(BLOCKERS), 3);
    if (session.assignment !== 'assigned' && (session.state !== 'unknown' || session.topicKeys.length || session.blockerKeys.length)) reject(422, 'invalid_event');
    aliases.add(session.alias);
  }
  if (coverage.complete && (coverage.total !== coverage.observed || coverage.observed !== input.sessions.length ||
      input.sessions.length !== source.aliases.length || source.aliases.length === 0)) reject(422, 'invalid_event');
  if (input.health !== 'ok' && (input.sessions.length || coverage.complete || coverage.observed !== 0 || coverage.total !== null)) reject(422, 'invalid_event');
  if (Object.hasOwn(input, 'serviceState') && (!source.capabilities.includes('service_process') ||
      ![null, 'running', 'stopped', 'unknown'].includes(input.serviceState) ||
      (input.health !== 'ok' && input.serviceState !== null))) reject(422, 'invalid_event');
  if (Object.hasOwn(input, 'conversationTopics')) {
    if (!source.capabilities.includes('curation') || input.health !== 'ok') reject(422, 'invalid_event');
    exact(input.conversationTopics, [], ['voice', 'chat', 'status']);
    if (Object.keys(input.conversationTopics).length === 0) reject(422, 'invalid_event');
    for (const [channel, keys] of Object.entries(input.conversationTopics)) uniqueList(keys, Object.keys(TOPICS), channel === 'status' ? 12 : 10);
  }
  if (Object.hasOwn(input, 'fooAllowance')) {
    if (source.domain !== 'foo' || !source.capabilities.includes('foo_allowance') || input.health !== 'ok') reject(422, 'invalid_event');
    exact(input.fooAllowance, ['state', 'canRunAgents', 'observedAt']);
    if (!validAllowancePair(input.fooAllowance.state, input.fooAllowance.canRunAgents)) reject(422, 'invalid_event');
    // This is a dated allowance observation, not a renewed collector heartbeat.
    // Future allowance evidence must not produce a compact-layout assertion.
    timestamp(input.fooAllowance.observedAt, Math.min(observedAt, enforceClock ? now : Infinity));
  }
  return input;
}

function validAllowancePair(state, canRunAgents) {
  return (state === 'valid' && canRunAgents === true) ||
    (state === 'exhausted' && canRunAgents === false) ||
    (state === 'unavailable' && canRunAgents === null);
}

async function readBody(request, max = MAX_BODY) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('Content-Type') || '')) reject(415, 'json_required');
  if (request.headers.has('Content-Encoding') && request.headers.get('Content-Encoding') !== 'identity') reject(415, 'encoding_unsupported');
  const length = request.headers.get('Content-Length');
  if (length && (!/^\d+$/.test(length) || Number(length) > max)) reject(413, 'body_too_large');
  if (!request.body) reject(400, 'invalid_json');
  const reader = request.body.getReader();
  const chunks = []; let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) { await reader.cancel(); reject(413, 'body_too_large'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { reject(400, 'invalid_json'); }
}

function response(value, status = 200, extra = {}) {
  return new Response(JSON.stringify(value), { status, headers: { ...SECURITY_HEADERS, 'Content-Type': 'application/json; charset=utf-8', ...extra } });
}
async function receipt(db, sourceId, eventId) {
  return db.prepare('SELECT sequence, event_id, payload_sha256 FROM observations WHERE source_id = ? AND event_id = ?').bind(sourceId, eventId).first();
}
async function head(db, sourceId) {
  return db.prepare('SELECT * FROM source_heads WHERE source_id = ?').bind(sourceId).first();
}
function ack(event, duplicate) {
  return response({ accepted: true, duplicate, eventId: event.eventId, sequence: event.sequence }, duplicate ? 200 : 202);
}
async function ingest(request, env, sources, now) {
  if (new URL(request.url).protocol !== 'https:') reject(400, 'https_required');
  // Authenticate before accepting or parsing any body. A credential identifies
  // exactly one registered source; a sourceId in the body cannot select authority.
  const auth = request.headers.get('Authorization') || '';
  if (!/^Bearer [\x21-\x7e]{32,512}$/.test(auth)) reject(401, 'unauthorized');
  const token = auth.slice(7);
  let source = null;
  for (const candidate of sources) {
    const expected = env[candidate.tokenEnv];
    if (typeof expected === 'string' && /^[\x21-\x7e]{32,512}$/.test(expected) && await tokenEqual(token, expected)) {
      if (source) reject(503, 'configuration_unavailable');
      source = candidate;
    }
  }
  if (!source) reject(401, 'unauthorized');
  const input = await readBody(request);
  const event = validateEvent(input, source, now);
  const payload = canonical(event), digest = await hash(payload);
  const existing = await receipt(env.DB, source.id, event.eventId);
  if (existing) {
    if (existing.payload_sha256 !== digest) reject(409, 'event_conflict');
    return ack(event, true);
  }
  try {
    await env.DB.prepare(`INSERT INTO observations
      (source_id, sequence, event_id, payload_sha256, observed_at, received_at, health, config_hash, payload, allowance_observed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(source.id, event.sequence, event.eventId, digest,
      timestamp(event.observedAt), now, event.health, source.configHash, payload,
      event.fooAllowance ? timestamp(event.fooAllowance.observedAt) : null).run();
  } catch {
    // Concurrent exact retries may race the pre-read. Inspect committed outcome,
    // never retry the write blindly or leak SQLite errors / binding details.
    const raced = await receipt(env.DB, source.id, event.eventId);
    if (raced) {
      if (raced.payload_sha256 !== digest) reject(409, 'event_conflict');
      return ack(event, true);
    }
    const current = await head(env.DB, source.id);
    if (current?.sequence >= event.sequence) reject(409, 'sequence_replay');
    if (current?.observed_at > timestamp(event.observedAt)) reject(409, 'clock_regression');
    if (current && current.received_at > now - 1000) reject(429, 'rate_limited');
    if (event.fooAllowance) {
      const previous = await env.DB.prepare('SELECT state, can_run_agents, observed_at FROM foo_allowances WHERE source_id = ?').bind(source.id).first();
      if (previous?.observed_at > timestamp(event.fooAllowance.observedAt)) reject(409, 'allowance_clock_regression');
      if (previous?.observed_at === timestamp(event.fooAllowance.observedAt) &&
          (previous.state !== event.fooAllowance.state || previous.can_run_agents !== (event.fooAllowance.canRunAgents === null ? null : Number(event.fooAllowance.canRunAgents)))) reject(409, 'allowance_observation_conflict');
    }
    reject(503, 'storage_unavailable');
  }
  return ack(event, false);
}

function freshness(source, seconds) {
  if (seconds === null) return 'unavailable';
  if (seconds >= source.outageAfterSeconds) return 'outage';
  if (seconds >= source.staleAfterSeconds) return 'stale';
  return 'fresh';
}
function blankSlot(domain, alias) {
  return { id: `${domain}-${alias}`, domain, alias, assignment: 'unavailable', displayState: null,
    availability: 'unconfigured', recordedState: null, proof: null, observedAt: null,
    ageSeconds: null, topicKeys: [], blockerKeys: [], topics: [], blockers: [] };
}

/** Explicit positive allowlist. Never return DB rows, config objects or spread
 * externally supplied values into the public response. */
export async function projectStatus(env, sources, now) {
  const filter = sources.length ? `source_id IN (${sources.map(() => '?').join(',')})` : '0';
  const sourceIds = sources.map(source => source.id);
  const records = await env.DB.prepare(`SELECT * FROM source_heads WHERE ${filter}`).bind(...sourceIds).all();
  const topicRecords = await env.DB.prepare(`SELECT * FROM curated_topics WHERE ${filter} ORDER BY observed_at DESC, sequence DESC, position ASC`).bind(...sourceIds).all();
  const allowanceRecords = await env.DB.prepare(`SELECT * FROM foo_allowances WHERE ${filter}`).bind(...sourceIds).all();
  const byId = new Map(records.results.map(row => [row.source_id, row]));
  const slots = [...'ABCDEF'].map(alias => blankSlot('cloud', alias)).concat(
    Array.from({ length: 64 }, (_, i) => blankSlot('foo', String(i + 1))));
  const bySlot = new Map(slots.map(slot => [slot.id, slot]));
  const publicSources = [];
  let conversationTopics = { voice: [], chat: [], status: [], availability: 'unconfigured', lastObservedAt: null };
  let fooAllowance = { state: 'unavailable', canRunAgents: null, verified: false, availability: 'unconfigured',
    observedAt: null, ageSeconds: null, staleAfterSeconds: null, outageAfterSeconds: null };
  for (const source of sources) {
    const row = byId.get(source.id);
    const matching = row?.config_hash === source.configHash;
    let good = null;
    if (row?.last_good_config_hash === source.configHash && row.last_good_payload) {
      // Validate durable payload again after migrations/manual restoration. A
      // malformed row degrades the whole API to a safe error, never raw output.
      good = validateEvent(JSON.parse(row.last_good_payload), source, now, false);
    }
    const seconds = good ? age(now, row.last_good_observed_at, row.last_good_received_at) :
      matching && row.health !== 'unconfigured' ? age(now, row.observed_at, row.received_at) : null;
    let status = freshness(source, seconds);
    if (!matching) status = 'unavailable';
    else if (status !== 'outage' && status !== 'stale' && row.health !== 'ok') status = row.health === 'unconfigured' ? 'unconfigured' : 'error';
    const coverage = good ? { complete: good.coverage.complete, observed: good.coverage.observed, total: good.coverage.total } : { ...EMPTY_COVERAGE };
    const sessionMap = new Map((good?.sessions || []).map(session => [session.alias, session]));
    let executionCoverageComplete = status === 'fresh' && coverage.complete && source.aliases.length > 0;
    for (const alias of source.aliases) {
      const slot = bySlot.get(`${source.domain}-${alias}`), session = sessionMap.get(alias);
      slot.availability = status === 'fresh' ? 'unavailable' : status;
      if (!session) { executionCoverageComplete = false; continue; }
      const sessionAge = age(now, timestamp(session.observedAt), row.last_good_received_at);
      slot.assignment = session.assignment;
      slot.observedAt = iso(timestamp(session.observedAt));
      slot.ageSeconds = sessionAge;
      slot.proof = session.proof;
      slot.recordedState = session.assignment === 'assigned' ? session.state : null;
      slot.availability = status === 'fresh' ? freshness(source, sessionAge) : status;
      if (session.assignment === 'unavailable') slot.availability = status === 'fresh' ? 'unavailable' : status;
      const live = session.proof === 'process' && session.assignment !== 'unavailable' && slot.availability === 'fresh';
      if (!live) executionCoverageComplete = false;
      slot.displayState = live && session.assignment === 'assigned' && session.state !== 'unknown' ? session.state : null;
      slot.topicKeys = [...session.topicKeys]; slot.blockerKeys = [...session.blockerKeys];
      slot.topics = session.topicKeys.map(key => TOPICS[key]);
      slot.blockers = session.blockerKeys.map(key => BLOCKERS[key]);
    }
    publicSources.push({ alias: source.publicAlias, domain: source.domain, aliases: [...source.aliases],
      capabilities: [...source.capabilities], status, lastReceivedAt: matching ? iso(row.received_at) : null,
      lastSuccessAt: good ? iso(row.last_good_received_at) : null, lastObservedAt: good ? iso(row.last_good_observed_at) : null,
      ageSeconds: seconds, staleAfterSeconds: source.staleAfterSeconds, outageAfterSeconds: source.outageAfterSeconds,
      coverage, executionCoverageComplete, evidenceScope: source.capabilities.includes('process') ? 'process_liveness' : 'metadata_only',
      serviceState: good?.serviceState ?? null });
    if (source.capabilities.includes('curation')) {
      const topics = topicRecords.results.filter(topic => topic.source_id === source.id && topic.config_hash === source.configHash);
      if (topics.some(topic => !Object.hasOwn(TOPICS, topic.topic_key) || !['voice', 'chat', 'status'].includes(topic.channel))) reject(503, 'storage_unavailable');
      const projectTopics = channel => topics.filter(topic => topic.channel === channel).slice(0, channel === 'status' ? 12 : 10)
        .map(topic => ({ key: topic.topic_key, text: TOPICS[topic.topic_key], observedAt: iso(topic.observed_at) }));
      const topicAge = topics.length ? age(now, topics[0].observed_at, topics[0].received_at) : null;
      conversationTopics = { voice: projectTopics('voice'), chat: projectTopics('chat'), status: projectTopics('status'),
        availability: status === 'fresh' ? freshness(source, topicAge) : status,
        lastObservedAt: topics.length ? iso(topics[0].observed_at) : null };
    }
    if (source.capabilities.includes('foo_allowance')) {
      const observation = allowanceRecords.results.find(item => item.source_id === source.id && item.config_hash === source.configHash);
      if (observation && (!validAllowancePair(observation.state, observation.can_run_agents === null ? null :
          observation.can_run_agents === 1 ? true : observation.can_run_agents === 0 ? false : undefined) ||
          !integer(observation.observed_at, MIN_DATE, Number.MAX_SAFE_INTEGER) ||
          !integer(observation.received_at, MIN_DATE, Number.MAX_SAFE_INTEGER))) reject(503, 'storage_unavailable');
      const allowanceAge = observation ? age(now, observation.observed_at, observation.received_at) : null;
      let allowanceAvailability = status === 'fresh' ? freshness(source, allowanceAge) : status;
      if (allowanceAvailability === 'fresh' && (observation?.state === 'unavailable' || observation.observed_at > now)) allowanceAvailability = 'unavailable';
      const verified = allowanceAvailability === 'fresh' && ['valid', 'exhausted'].includes(observation?.state);
      fooAllowance = { state: verified ? observation.state : 'unavailable',
        canRunAgents: verified ? observation.can_run_agents === 1 : null, verified,
        availability: allowanceAvailability, observedAt: observation ? iso(observation.observed_at) : null,
        ageSeconds: allowanceAge, staleAfterSeconds: source.staleAfterSeconds, outageAfterSeconds: source.outageAfterSeconds };
    }
  }
  const statuses = publicSources.map(source => source.status);
  const state = !sources.length ? 'unconfigured' : statuses.includes('outage') ? 'outage' :
    statuses.includes('stale') ? 'stale' : statuses.includes('error') ? 'error' :
    statuses.every(s => s === 'fresh') && publicSources.every(s => s.coverage.complete) ? 'current' : 'partial';
  return { schemaVersion: 1, generatedAt: iso(now), receiver: { state: 'ok', storage: 'durable', outsideFoo: true },
    summary: { state, configuredSources: sources.length, reportingSources: statuses.filter(s => s === 'fresh').length,
      executionCoverageComplete: publicSources.some(s => s.aliases.length > 0) &&
        publicSources.filter(s => s.aliases.length > 0).every(s => s.executionCoverageComplete) &&
        slots.every(slot => slot.availability !== 'unconfigured') },
    sources: publicSources, slots, conversationTopics, fooAllowance };
}

function revisionContent(snapshot) {
  // Exclude only known clock fields, not arbitrary nested names supplied by data.
  const content = structuredClone(snapshot);
  delete content.revision; delete content.generatedAt;
  for (const source of content.sources) delete source.ageSeconds;
  for (const slot of content.slots) delete slot.ageSeconds;
  if (content.fooAllowance) delete content.fooAllowance.ageSeconds;
  return content;
}
async function saveSnapshot(env, sources, now) {
  const snapshot = await projectStatus(env, sources, now);
  snapshot.revision = await hash(canonical(revisionContent(snapshot)));
  await env.DB.prepare('INSERT OR IGNORE INTO public_snapshots (revision, created_at, payload) VALUES (?, ?, ?)')
    .bind(snapshot.revision, now, canonical(snapshot)).run();
  return snapshot;
}
async function savedSnapshot(env, revision) {
  if (!/^[0-9a-f]{64}$/.test(revision)) reject(404, 'snapshot_not_found');
  const row = await env.DB.prepare('SELECT payload FROM public_snapshots WHERE revision = ?').bind(revision).first();
  if (!row) reject(404, 'snapshot_not_found');
  const snapshot = JSON.parse(row.payload);
  try { validatePublicSnapshot(snapshot); } catch { reject(503, 'snapshot_unavailable'); }
  // Check content integrity before returning a stored artifact. Only this Worker
  // writes snapshots, after the positive public projection above.
  if (snapshot.revision !== revision || await hash(canonical(revisionContent(snapshot))) !== revision) reject(503, 'snapshot_unavailable');
  return snapshot;
}

/** Revalidate persisted artifacts too: excluded clock fields must never become
 * an unchecked object/string escape hatch in an otherwise verified revision. */
function validatePublicSnapshot(snapshot) {
  // Old immutable revisions remain valid historical artifacts. Do not inject
  // allowance evidence into them or reinterpret their existing content hash.
  exact(snapshot, ['schemaVersion', 'generatedAt', 'receiver', 'summary', 'sources', 'slots', 'conversationTopics', 'revision'], ['fooAllowance']);
  const validAge = value => value === null || integer(value, 0, Number.MAX_SAFE_INTEGER);
  const validTime = value => value === null || Number.isFinite(timestamp(value));
  const availability = ['fresh', 'stale', 'outage', 'unconfigured', 'unavailable', 'error'];
  if (snapshot.schemaVersion !== 1 || !/^[0-9a-f]{64}$/.test(snapshot.revision)) reject(503, 'snapshot_unavailable');
  timestamp(snapshot.generatedAt);
  exact(snapshot.receiver, ['state', 'storage', 'outsideFoo']);
  if (snapshot.receiver.state !== 'ok' || snapshot.receiver.storage !== 'durable' || snapshot.receiver.outsideFoo !== true) reject(503, 'snapshot_unavailable');
  exact(snapshot.summary, ['state', 'configuredSources', 'reportingSources', 'executionCoverageComplete']);
  if (!['unconfigured', 'outage', 'stale', 'error', 'current', 'partial'].includes(snapshot.summary.state) ||
      !integer(snapshot.summary.configuredSources, 0, 16) || !integer(snapshot.summary.reportingSources, 0, 16) ||
      typeof snapshot.summary.executionCoverageComplete !== 'boolean' || !Array.isArray(snapshot.sources) ||
      snapshot.sources.length > 16 || !Array.isArray(snapshot.slots) || snapshot.slots.length !== 70) reject(503, 'snapshot_unavailable');
  for (const source of snapshot.sources) {
    exact(source, ['alias', 'domain', 'aliases', 'capabilities', 'status', 'lastReceivedAt', 'lastSuccessAt', 'lastObservedAt',
      'ageSeconds', 'staleAfterSeconds', 'outageAfterSeconds', 'coverage', 'executionCoverageComplete', 'evidenceScope', 'serviceState']);
    if (!/^source-(?:[1-9]|1[0-6])$/.test(source.alias) || !['foo', 'cloud'].includes(source.domain) ||
        !availability.includes(source.status) || !validAge(source.ageSeconds) ||
        !integer(source.staleAfterSeconds, 30, 3600) || !integer(source.outageAfterSeconds, source.staleAfterSeconds + 1, 86400) ||
        typeof source.executionCoverageComplete !== 'boolean' || !['process_liveness', 'metadata_only'].includes(source.evidenceScope) ||
        ![null, 'running', 'stopped', 'unknown'].includes(source.serviceState)) reject(503, 'snapshot_unavailable');
    [source.lastReceivedAt, source.lastSuccessAt, source.lastObservedAt].forEach(validTime);
    uniqueList(source.capabilities, CAPABILITIES, CAPABILITIES.length);
    uniqueList(source.aliases, source.domain === 'cloud' ? [...'ABCDEF'] : Array.from({ length: 64 }, (_, i) => String(i + 1)), 64);
    exact(source.coverage, ['complete', 'observed', 'total']);
    if (typeof source.coverage.complete !== 'boolean' || !integer(source.coverage.observed, 0, 1000000) ||
        !(source.coverage.total === null || integer(source.coverage.total, source.coverage.observed, 1000000))) reject(503, 'snapshot_unavailable');
  }
  const ids = new Set();
  for (const slot of snapshot.slots) {
    exact(slot, ['id', 'domain', 'alias', 'assignment', 'displayState', 'availability', 'recordedState', 'proof',
      'observedAt', 'ageSeconds', 'topicKeys', 'blockerKeys', 'topics', 'blockers']);
    if (!/^(cloud-[A-F]|foo-(?:[1-9]|[1-5][0-9]|6[0-4]))$/.test(slot.id) || ids.has(slot.id) ||
        slot.id !== `${slot.domain}-${slot.alias}` || !ASSIGNMENTS.includes(slot.assignment) ||
        ![null, ...STATES.filter(state => state !== 'unknown')].includes(slot.displayState) || !availability.includes(slot.availability) ||
        ![null, ...STATES].includes(slot.recordedState) || ![null, 'registry', 'turn_history', 'process'].includes(slot.proof) || !validAge(slot.ageSeconds)) reject(503, 'snapshot_unavailable');
    ids.add(slot.id); validTime(slot.observedAt);
    uniqueList(slot.topicKeys, Object.keys(TOPICS), 3); uniqueList(slot.blockerKeys, Object.keys(BLOCKERS), 3);
    if (canonical(slot.topics) !== canonical(slot.topicKeys.map(key => TOPICS[key])) ||
        canonical(slot.blockers) !== canonical(slot.blockerKeys.map(key => BLOCKERS[key]))) reject(503, 'snapshot_unavailable');
  }
  exact(snapshot.conversationTopics, ['voice', 'chat', 'availability', 'lastObservedAt'], ['status']);
  if (!availability.includes(snapshot.conversationTopics.availability)) reject(503, 'snapshot_unavailable');
  validTime(snapshot.conversationTopics.lastObservedAt);
  for (const channel of ['voice', 'chat', ...(Object.hasOwn(snapshot.conversationTopics, 'status') ? ['status'] : [])]) {
    const topics = snapshot.conversationTopics[channel];
    if (!Array.isArray(topics) || topics.length > (channel === 'status' ? 12 : 10)) reject(503, 'snapshot_unavailable');
    for (const topic of topics) {
      exact(topic, ['key', 'text', 'observedAt']);
      if (!Object.hasOwn(TOPICS, topic.key) || topic.text !== TOPICS[topic.key]) reject(503, 'snapshot_unavailable');
      timestamp(topic.observedAt);
    }
  }
  if (Object.hasOwn(snapshot, 'fooAllowance')) {
    const allowance = snapshot.fooAllowance;
    exact(allowance, ['state', 'canRunAgents', 'verified', 'availability', 'observedAt', 'ageSeconds', 'staleAfterSeconds', 'outageAfterSeconds']);
    const configured = allowance.staleAfterSeconds !== null;
    if (!validAllowancePair(allowance.state, allowance.canRunAgents) || typeof allowance.verified !== 'boolean' ||
        !availability.includes(allowance.availability) || !validAge(allowance.ageSeconds) ||
        (configured ? !integer(allowance.staleAfterSeconds, 30, 3600) ||
          !integer(allowance.outageAfterSeconds, allowance.staleAfterSeconds + 1, 86400) : allowance.outageAfterSeconds !== null) ||
        (allowance.verified ? allowance.availability !== 'fresh' || allowance.state === 'unavailable' ||
          !configured || allowance.observedAt === null || timestamp(allowance.observedAt) > timestamp(snapshot.generatedAt) ||
          allowance.ageSeconds === null || allowance.ageSeconds >= allowance.staleAfterSeconds :
          allowance.state !== 'unavailable' || allowance.canRunAgents !== null)) reject(503, 'snapshot_unavailable');
    validTime(allowance.observedAt);
  }
}

function renderOptions(value = {}) {
  exact(value, [], ['width', 'height', 'selected', 'revision', 'layout']);
  if ((value.width !== undefined && !integer(value.width, 320, 4096)) ||
      (value.height !== undefined && !integer(value.height, 240, 2160)) ||
      (value.selected !== undefined && !/^(cloud-[A-F]|foo-(?:[1-9]|[1-5][0-9]|6[0-4]))$/.test(value.selected)) ||
      (value.revision !== undefined && !/^[0-9a-f]{64}$/.test(value.revision)) ||
      (value.layout !== undefined && !['o2', 'hexagon', 'focus', 'compact'].includes(value.layout))) reject(422, 'invalid_options');
  return value;
}
async function svg(snapshot, options) {
  const { renderSvg } = await import('./public/lib/render.js');
  return renderSvg(snapshot, options);
}
async function rendered(snapshot, options) {
  const text = await svg(snapshot, options);
  const renderedLayout = text.match(/^<svg\b[^>]*\bdata-layout="(o2|hexagon|focus|compact)"/)?.[1];
  if (!renderedLayout) reject(503, 'renderer_unavailable');
  return { text, metadata: { revision: snapshot.revision, renderSha256: await hash(text),
    requestedLayout: options.layout ?? 'o2', renderedLayout } };
}
async function mcp(request, env, sources, now) {
  if (request.headers.has('Origin') && request.headers.get('Origin') !== new URL(request.url).origin) reject(403, 'origin_forbidden');
  const message = await readBody(request, 8192);
  if (!object(message) || message.jsonrpc !== '2.0' || typeof message.method !== 'string' ||
      (message.id !== undefined && !(typeof message.id === 'string' && message.id.length <= 128) && !Number.isSafeInteger(message.id))) reject(400, 'invalid_request');
  if (message.method === 'notifications/initialized' && message.id === undefined) return new Response(null, { status: 202, headers: SECURITY_HEADERS });
  if (message.id === undefined) reject(400, 'invalid_request');
  const done = result => response({ jsonrpc: '2.0', id: message.id, result });
  const fail = (code, text) => response({ jsonrpc: '2.0', id: message.id, error: { code, message: text } });
  if (message.method === 'initialize') return done({ protocolVersion: '2025-03-26',
    capabilities: { tools: { listChanged: false } }, serverInfo: { name: 'morgan-public-monitor', version: '1.0.0' } });
  if (message.method === 'ping') return done({});
  if (message.method === 'tools/list') return done({ tools: [
    { name: 'get_status', description: 'Read public, allowlisted monitoring metadata and freshness. No private session details.',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false } },
    { name: 'render_status', description: 'Render the same public monitoring projection as deterministic SVG. Explicit compact selects the MCP presentation only; it does not assert allowance exhaustion.',
      inputSchema: { type: 'object', properties: { width: { type: 'integer', minimum: 320, maximum: 4096 },
        height: { type: 'integer', minimum: 240, maximum: 2160 }, selected: { type: 'string', pattern: '^(cloud-[A-F]|foo-([1-9]|[1-5][0-9]|6[0-4]))$' },
        revision: { type: 'string', pattern: '^[0-9a-f]{64}$' }, layout: { type: 'string', enum: ['o2', 'hexagon', 'focus', 'compact'] } }, additionalProperties: false },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false } },
  ] });
  if (message.method === 'tools/call') {
    if (!object(message.params) || !['get_status', 'render_status'].includes(message.params.name)) return fail(-32602, 'Unknown tool');
    try {
      const args = message.params.arguments ?? {};
      if (message.params.name === 'get_status') exact(args, []);
      else renderOptions(args);
      let status = args.revision ? await savedSnapshot(env, args.revision) : await saveSnapshot(env, sources, now);
      if (message.params.name === 'get_status') return done({ content: [{ type: 'text', text: JSON.stringify(status) }], isError: false });
      // Match the web's immutable first capture even when no revision was supplied.
      if (!args.revision) status = await savedSnapshot(env, status.revision);
      const output = await rendered(status, args);
      return done({ content: [{ type: 'text', text: output.text }], structuredContent: output.metadata, isError: false });
    } catch (error) {
      if (error instanceof HttpError && error.status === 422) return fail(-32602, 'Invalid tool arguments');
      return done({ content: [{ type: 'text', text: 'Monitoring storage or renderer unavailable.' }], isError: true });
    }
  }
  return fail(-32601, 'Method not found');
}

/** now is injectable solely for deterministic tests; deployed fetch uses Date.now. */
export async function handleRequest(request, env, now = Date.now()) {
  try {
    const url = new URL(request.url), path = url.pathname;
    if (path === '/v1/ingest' && request.method !== 'POST') reject(405, 'method_not_allowed');
    if (path === '/mcp' && request.method !== 'POST') reject(405, 'method_not_allowed');
    if ((['/api/status', '/health', '/render.svg'].includes(path) || path.startsWith('/api/snapshots/')) && request.method !== 'GET') reject(405, 'method_not_allowed');
    if (path === '/v1/ingest' || path === '/mcp' || ['/api/status', '/health', '/render.svg'].includes(path) || path.startsWith('/api/snapshots/')) {
      const sources = await loadConfig(env);
      if (!env.DB) reject(503, 'storage_unavailable');
      if (path === '/v1/ingest') return await ingest(request, env, sources, now);
      if (path === '/mcp') return await mcp(request, env, sources, now);
      if (path.startsWith('/api/snapshots/')) return response(await savedSnapshot(env, path.slice('/api/snapshots/'.length)));
      const status = path === '/render.svg' && url.searchParams.has('revision') ? await savedSnapshot(env, url.searchParams.get('revision')) :
        path === '/health' ? await projectStatus(env, sources, now) : await saveSnapshot(env, sources, now);
      if (path === '/health') return response({ schemaVersion: 1, generatedAt: status.generatedAt,
        receiver: status.receiver, monitoring: status.summary });
      if (path === '/render.svg') {
        const options = {};
        for (const [key, value] of url.searchParams) {
          if (Object.hasOwn(options, key) || !['width', 'height', 'selected', 'revision', 'layout'].includes(key)) reject(422, 'invalid_options');
          options[key] = ['selected', 'revision', 'layout'].includes(key) ? value : Number(value);
        }
        const immutable = url.searchParams.has('revision') ? status : await savedSnapshot(env, status.revision);
        const output = await rendered(immutable, renderOptions(options));
        return new Response(output.text, { headers: { ...SECURITY_HEADERS, 'Content-Type': 'image/svg+xml; charset=utf-8',
          'X-Morgan-Snapshot-Revision': output.metadata.revision,
          'X-Morgan-Render-SHA256': output.metadata.renderSha256,
          'X-Morgan-Requested-Layout': output.metadata.requestedLayout,
          'X-Morgan-Rendered-Layout': output.metadata.renderedLayout } });
      }
      return response(status);
    }
    if (path.startsWith('/api/') || path.startsWith('/v1/')) reject(404, 'not_found');
    if (!['GET', 'HEAD'].includes(request.method)) reject(405, 'method_not_allowed');
    if (!env.ASSETS) reject(503, 'assets_unavailable');
    const asset = await env.ASSETS.fetch(request);
    const headers = new Headers(asset.headers);
    for (const [key, value] of Object.entries(SECURITY_HEADERS)) headers.set(key, value);
    return new Response(asset.body, { status: asset.status, headers });
  } catch (error) {
    const status = error instanceof HttpError ? error.status : 503;
    const code = error instanceof HttpError ? error.code : 'unavailable';
    return response({ accepted: false, error: code }, status, status === 429 ? { 'Retry-After': '1' } : {});
  }
}

/** An outside-Foo scheduled evaluation records threshold crossings even when
 * no collector, agent or browser is running. It does not mutate those systems. */
export async function evaluateScheduled(env, now = Date.now()) {
  try {
    return await saveSnapshot(env, await loadConfig(env), now);
  } catch {
    // Fixed message only: provider logs must not receive binding or DB details.
    throw new Error('Monitoring receiver evaluation unavailable');
  }
}

export default {
  fetch: (request, env) => handleRequest(request, env),
  scheduled: (_controller, env) => evaluateScheduled(env),
};
