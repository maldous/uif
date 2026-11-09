// Offline test bridge only. No listener, real credentials, or source discovery.
// Exercises the actual Worker handlers and migrations with durable local SQLite.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { handleRequest, evaluateScheduled } from '../../services/morgan-monitor/worker.mjs';

class D1 {
  constructor(path) {
    this.sqlite = new DatabaseSync(path);
    if (!this.sqlite.prepare("SELECT 1 FROM sqlite_master WHERE name='source_heads'").get()) {
      const dir = new URL('../../services/morgan-monitor/migrations/', import.meta.url);
      for (const file of readdirSync(dir).sort()) this.sqlite.exec(readFileSync(new URL(file, dir), 'utf8'));
    }
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

const DB = new D1(process.argv[2]);
const env = {
  DB,
  SOURCE_CONFIG_JSON: JSON.stringify({ sources: [{
    id: 'codex_turns', domain: 'foo', aliases: ['1'], capabilities: ['turn_history'],
    staleAfterSeconds: 120, outageAfterSeconds: 600, tokenEnv: 'SYNTHETIC_TOKEN',
  }] }),
  SYNTHETIC_TOKEN: 'synthetic-test-token-not-real-32chars',
};

try {
  for await (const line of createInterface({ input: process.stdin })) {
    const input = JSON.parse(line);
    let result;
    if (input.action === 'scheduled') result = await evaluateScheduled(env, input.now);
    else if (input.action === 'stats') result = {
      count: DB.sqlite.prepare('SELECT COUNT(*) AS count FROM observations').get().count,
      head: DB.sqlite.prepare('SELECT sequence, observed_at, received_at FROM source_heads WHERE source_id=?').get('codex_turns') ?? null,
    };
    else {
      const response = await handleRequest(new Request(`https://receiver.example${input.path}`, {
        method: input.method ?? 'GET', headers: input.headers,
        ...(input.body === undefined ? {} : { body: input.body }),
      }), env, input.now);
      result = { status: response.status, body: await response.text() };
    }
    process.stdout.write(`${JSON.stringify(result)}\n`);
  }
} finally {
  DB.sqlite.close();
}
