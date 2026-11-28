import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { canonical, digest, digestBytes, need } from '../protocol.mjs';
import { sourceRoute, sourceTables } from '../source-overlay.mjs';

const attempt = 'test-author',
  path = 'src/test.txt',
  rawPermit = 'a'.repeat(64),
  permitHash = 'b'.repeat(64);
const bytes = (s) => new TextEncoder().encode(s);
const base64 = (s) => Buffer.from(s).toString('base64');

async function fixture(t, { role = 'native', marked = true } = {}) {
  // The verifier fixture tests the sink contract and SQL ordering. The
  // authority owns cryptographic verification and admission of recovered peers.
  const db = new DatabaseSync(':memory:');
  t.after(() => db.close());
  db.exec([
    ...sourceTables,
    'CREATE TABLE effects (id TEXT PRIMARY KEY, issue TEXT NOT NULL, op TEXT NOT NULL, attempt TEXT NOT NULL, account TEXT, amount INTEGER NOT NULL, state TEXT NOT NULL, receipt TEXT, settled_at INTEGER)',
    'CREATE TABLE destination_results (id TEXT PRIMARY KEY, result TEXT NOT NULL)',
    'CREATE TABLE provider_results (id TEXT PRIMARY KEY, response BLOB NOT NULL, policy TEXT NOT NULL)',
  ].join(';'));
  const peer = { role, session_id: role + '-session' };
  const fence = {
    scope: { key: { workspace_id: 'workspace', issue_id: 'issue' } },
    source: { repository: 'maldous/offmenu' },
    owner_session: peer.session_id,
    coordinator_epoch: 2,
  };
  const lease = { fence, expires_at: 100 };
  const account = 'fixture-account';
  const original = 'a'.repeat(64);
  const preimage = await digestBytes(bytes('before'));
  const postimage = await digestBytes(bytes('after'));
  const base = { revision: 0, sealed: null };
  db.prepare('INSERT INTO source_attempts VALUES (?,?,?,0,?,0,NULL)')
    .run(attempt, canonical(fence), '4'.repeat(64), 'before'.length);
  db.prepare('INSERT INTO source_paths VALUES (?,?,?,?,?,?,?)')
    .run(attempt, path, 1, '100644', preimage, '5'.repeat(40), base64('before'));
  db.prepare('INSERT INTO effects VALUES (?,?,?,?,?,0,\'verified\',?,0)')
    .run(original, 'workspace:issue', canonical({ fence }), attempt, account, '6'.repeat(64));
  db.prepare('INSERT INTO provider_results VALUES (?,?,?)').run(original, bytes(canonical({
    choices: [{ message: { tool_calls: [{
      id: 'tool', type: 'function',
      function: { name: 'replace_text', arguments: canonical({ path, old: 'before', replacement: 'after' }) },
    }] } }],
  })), '{}');

  const ref = { attempt_id: attempt, provider_request_id: original, tool_call_index: 0, tool_call_id: 'tool' };
  const edit = { ...ref, path, expected_revision: 0, preimage_sha256: preimage, postimage_sha256: postimage };
  const operation = {
    fence,
    original_id: await digest({ kind: 'uif.source-tool-original/1', ...ref }),
    payload_sha256: await digest({ kind: 'uif.native-source-write/1', ...edit }),
  };
  const write = { ...edit, operation, body_base64: base64('after') };
  const read = { current: lease, attempt_id: attempt };
  const calls = [];

  let inTransaction = false, currentAttempt = attempt, epoch = fence.coordinator_epoch;
  const a = {
    c: { route: { account_id: account } },
    store: {
      transactionSync(fn) {
        db.exec('BEGIN');
        inTransaction = true;
        try {
          const result = fn();
          db.exec('COMMIT');
          return result;
        } catch (error) {
          db.exec('ROLLBACK');
          throw error;
        } finally {
          inTransaction = false;
        }
      },
    },
    sql: { exec: (sql, ...args) => db.prepare(sql).all(...args) },
    row: (sql, ...args) => db.prepare(sql).get(...args),
    put: (sql, ...args) => {
      assert.equal(inTransaction, true);
      db.prepare(sql).run(...args);
    },
    owner(requester, suppliedFence) {
      assert.equal(requester, peer);
      assert.deepEqual(suppliedFence, fence);
      return { row: { attempt: currentAttempt }, lease };
    },
    grant: () => ({ review_of: { reviewer: attempt } }),
    bodyReader() {},
    clock: () => 1,
    afterCheck: async () => {},
    async recoveryPermitHash(suppliedFence, suppliedAttempt, requester, permit) {
      assert.deepEqual(suppliedFence, fence);
      assert.equal(suppliedAttempt, attempt);
      assert.equal(requester, peer);
      assert.equal(inTransaction, false);
      calls.push('preflight');
      const hash = marked ? permitHash : undefined;
      if (marked) need(permit === rawPermit, 'recovery_winner_required', 403);
      await a.afterCheck();
      return hash;
    },
    requireRecoveryPermit(suppliedFence, suppliedAttempt, hash) {
      assert.deepEqual(suppliedFence, fence);
      assert.equal(suppliedAttempt, attempt);
      assert.equal(inTransaction, true);
      calls.push('commit');
      if (marked) {
        need(hash === permitHash, 'recovery_winner_required', 403);
        need(suppliedFence.coordinator_epoch === epoch, 'recovery_epoch_changed');
      }
    },
  };
  return {
    a, calls, ref, base, write, read, operation, peer,
    review: () => { currentAttempt = 'reviewer'; },
    revoke: () => { epoch++; },
    call: (route, body, permit) => sourceRoute(a, '/v1/source/' + route, body, peer,
      (config, op) => {
        assert.equal(config, a.c);
        assert.deepEqual(op, operation);
      }, permit),
    progress: () => ({ ...a.row('SELECT revision,sealed FROM source_attempts WHERE attempt=?', attempt) }),
    effect: () => a.row('SELECT id FROM effects WHERE id=?', operation.original_id),
  };
}

test('recovered source writes and checkpoint seals require the exact winner in the SQL transaction', async (t) => {
  const f = await fixture(t);
  for (const permit of [undefined, 'c'.repeat(64)]) {
    await assert.rejects(f.call('write', f.write, permit), /recovery_winner_required/);
    assert.deepEqual(f.progress(), f.base);
    assert.equal(f.effect(), undefined);
  }
  f.calls.length = 0;
  const result = await f.call('write', f.write, rawPermit);
  assert.deepEqual(f.calls, ['preflight', 'commit']);
  assert.equal(result.revision, 1);
  assert.deepEqual(f.progress(), { revision: 1, sealed: null });

  const request = { ...f.read, revision: 1 };
  await assert.rejects(f.call('checkpoint', request), /recovery_winner_required/);
  assert.deepEqual(f.progress(), { revision: 1, sealed: null });
  f.calls.length = 0;
  const checkpoint = await f.call('checkpoint', request, rawPermit);
  assert.deepEqual(f.calls, ['preflight', 'commit']);
  assert.deepEqual(f.progress(), { revision: 1, sealed: checkpoint.candidate_sha256 });
});

for (const stage of ['write', 'checkpoint']) {
  test('recovered ' + stage + ' refuses a winner revoked after async preflight', async (t) => {
    const f = await fixture(t);
    if (stage === 'checkpoint') await f.call('write', f.write, rawPermit);
    const prior = f.progress();
    const before = f.effect();
    const waiting = Promise.withResolvers(), release = Promise.withResolvers();
    f.a.afterCheck = () => {
      waiting.resolve();
      return release.promise;
    };
    f.calls.length = 0;
    const request = stage === 'write' ? f.write : { ...f.read, revision: 1 };
    const result = f.call(stage, request, rawPermit);
    await waiting.promise;
    f.revoke();
    release.resolve();
    await assert.rejects(result, /recovery_epoch_changed/);
    assert.deepEqual(f.calls, ['preflight', 'commit']);
    assert.deepEqual(f.progress(), prior);
    assert.deepEqual(f.effect(), before);
  });
}

test('original write, receipt and sealed checkpoint reads retain their existing access', async (t) => {
  const f = await fixture(t);
  const result = await f.call('write', f.write, rawPermit);
  const request = { ...f.read, revision: 1 };
  const checkpoint = await f.call('checkpoint', request, rawPermit);
  f.calls.length = 0;
  assert.deepEqual(await f.call('write', f.write), result);
  assert.deepEqual(await f.call('receipt', { ...f.read, ...f.ref }), result);
  f.review();
  assert.deepEqual(await f.call('checkpoint', request), checkpoint);
  assert.deepEqual(f.calls, []);
  assert.deepEqual(f.progress(), { revision: 1, sealed: checkpoint.candidate_sha256 });
});

test('ordinary native and Morgan source work preserves its existing permit-free path', async (t) => {
  for (const role of ['native', 'morgan']) {
    const f = await fixture(t, { role, marked: false });
    assert.equal((await f.call('write', f.write)).revision, 1);
    const checkpoint = await f.call('checkpoint', { ...f.read, revision: 1 });
    assert.deepEqual(f.progress(), { revision: 1, sealed: checkpoint.candidate_sha256 });
    assert.deepEqual(f.calls, ['preflight', 'commit', 'preflight', 'commit']);
  }
});
