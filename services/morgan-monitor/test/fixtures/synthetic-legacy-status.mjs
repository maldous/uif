// Test-only legacy projection. Every input below is an invented schema example.
// No captured status, configuration, identity, event, or network input is read.
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { handleRequest } from '../../worker.mjs';

export const SYNTHETIC_TIME = Date.UTC(2020, 0, 2, 3, 4, 5);
const iso = offset => new Date(SYNTHETIC_TIME + offset).toISOString();
const TOKEN = 'synthetic-legacy-fixture-test-token-000000000000';

class D1 {
  constructor() {
    this.db = new DatabaseSync(':memory:');
    for (const file of readdirSync(new URL('../../migrations/', import.meta.url)).sort())
      this.db.exec(readFileSync(new URL('../../migrations/' + file, import.meta.url), 'utf8'));
  }
  prepare(sql) {
    const statement = this.db.prepare(sql);
    const bound = values => ({ bind: (...args) => bound(args), first: async () => statement.get(...values) ?? null,
      all: async () => ({ results: statement.all(...values) }), run: async () => statement.run(...values) });
    return bound([]);
  }
}

export async function syntheticLegacySnapshot() {
  const sources = [
    { id: 'synthetic_legacy_roster', domain: 'cloud', aliases: ['C', 'E'], capabilities: ['coordinator_lifecycle'],
      rosterGeneration: 17, staleAfterSeconds: 120, outageAfterSeconds: 600, tokenEnv: 'SYNTHETIC_ROSTER_TOKEN' },
    { id: 'synthetic_allowance', domain: 'foo', aliases: [], capabilities: ['foo_allowance'],
      staleAfterSeconds: 120, outageAfterSeconds: 600, tokenEnv: 'SYNTHETIC_ALLOWANCE_TOKEN' },
    { id: 'synthetic_unobserved_curation', domain: 'cloud', aliases: [], capabilities: ['curation'],
      staleAfterSeconds: 3600, outageAfterSeconds: 86400, tokenEnv: 'SYNTHETIC_CURATION_TOKEN' },
  ];
  const env = { DB: new D1(), SOURCE_CONFIG_JSON: JSON.stringify({ sources }),
    SYNTHETIC_ROSTER_TOKEN: TOKEN, SYNTHETIC_ALLOWANCE_TOKEN: TOKEN + '-allowance', SYNTHETIC_CURATION_TOKEN: TOKEN + '-curation' };
  async function ingest(payload, offset, token) {
    const response = await handleRequest(new Request('https://monitor.example/v1/ingest', { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload) }), env, SYNTHETIC_TIME + offset);
    assert.equal(response.status, 202, await response.clone().text());
  }
  try {
    await ingest({ schemaVersion: 1, sourceId: 'synthetic_legacy_roster', eventId: 'invented-roster-1', sequence: 1,
      rosterGeneration: 17, observedAt: iso(0), health: 'ok', coverage: { complete: false, observed: 2, total: null },
      sessions: [['C', 'engineering'], ['E', 'research']].map(([alias, topic]) => ({ alias, assignment: 'assigned',
        observedAt: iso(0), state: 'unknown', proof: 'coordinator_lifecycle', topicKeys: [topic], blockerKeys: [] })) }, 0, TOKEN);
    await ingest({ schemaVersion: 1, sourceId: 'synthetic_allowance', eventId: 'invented-allowance-1', sequence: 1,
      observedAt: iso(299000), health: 'ok', coverage: { complete: false, observed: 0, total: null }, sessions: [],
      fooAllowance: { observedAt: iso(299000), state: 'valid', canRunAgents: true } }, 299000, TOKEN + '-allowance');
    const response = await handleRequest(new Request('https://monitor.example/api/status'), env, SYNTHETIC_TIME + 300000);
    assert.equal(response.status, 200, await response.clone().text());
    return response.json();
  } finally {
    env.DB.db.close();
  }
}
