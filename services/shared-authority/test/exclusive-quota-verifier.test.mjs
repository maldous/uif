import assert from 'node:assert/strict';
import { createPrivateKey, createPublicKey, sign } from 'node:crypto';
import test from 'node:test';
import { resolveObservation } from '../exclusive-quota-verifier.mjs';
import { canonical, digest, digestBytes } from '../protocol.mjs';

const h = (digit) => digit.repeat(64);
const epoch = Date.parse('2026-11-25T00:00:00Z') / 1000;
const enc = new TextEncoder();
const url = 'https://quota.internal/v1/observation/read';
const route = {
  account_id: 'a'.repeat(32),
  model: '@cf/openai/gpt-oss-20b',
  gateway_id: 'fixture',
  credential_epoch_sha256: h('b'),
};

function vector(seed) {
  // Fixed, intentionally public test-only seeds, never deployment credentials.
  const key = createPrivateKey({ key: Buffer.from('302e020100300506032b657004220420' + h(seed), 'hex'),
    format: 'der', type: 'pkcs8' });
  const pub = createPublicKey(key).export({ format: 'jwk' }).x;
  return [Buffer.from(pub, 'base64url').toString('base64'),
    (body) => sign(null, enc.encode(canonical(body)), key).toString('base64')];
}
const [closureKey, signClosure] = vector('1');
const [ledgerKey, signLedger] = vector('2');
const binding = {
  route, topology_sha256: h('c'), closure_public_key: closureKey, ledger_public_key: ledgerKey,
};

async function fixture(modify = (proof) => proof, handler = (blob) => new Response(blob, {
  headers: { 'content-type': 'application/octet-stream' },
})) {
  const closure = {
    schema: 'uif.exclusive-quota.closure/1',
    binding_sha256: await digest(binding), route, utc_day: '2026-11-25', covered_from: epoch,
    valid_until: epoch + 200, inventory_sha256: h('d'), egress_enforcement_sha256: h('e'),
    carryover_sha256: h('f'), outside_gap_upper: 100, outside_carryover_upper: 150,
    billing_mode: 'workers-free-standard-no-fallback',
  };
  const ledger = {
    schema: 'uif.exclusive-quota.ledger/1', closure_sha256: await digest(closure),
    chain_sha256: h('3'), covered_through: epoch + 50, valid_until: epoch + 120,
    account_usage_upper: 1_200, outside_unresolved_upper: 300,
  };
  const original = enc.encode(canonical(modify({
    closure, closure_signature: signClosure(closure), ledger, ledger_signature: signLedger(ledger),
  })));
  const originalSha = await digestBytes(original);
  const lookup = { source_receipt_sha256: originalSha, route };
  const calls = [];
  const env = {
    EXCLUSIVE_QUOTA_BINDING: JSON.stringify(binding),
    QUOTA_EVIDENCE: {
      fetch: async (r) => {
        calls.push(r);
        return handler(original);
      },
    },
  };
  const submit = (body = lookup, endpoint = url, additional = {}) => resolveObservation(
    new Request(endpoint, { method: 'POST', headers: {
      'content-type': 'application/json', 'authorization': 'external-ignored',
    }, body: JSON.stringify(body), ...additional }), env, () => epoch + 60,
  );
  return { env, lookup, calls, submit };
}

test('exact shared-authority request uses a single internal digest GET and returns a bounded projection', async () => {
  const f = await fixture();
  const response = await f.submit();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(f.calls.length, 1);
  const [request] = f.calls;
  assert.equal(request.url, 'https://quota-evidence.internal/v1/original/' + f.lookup.source_receipt_sha256);
  assert.equal(request.method, 'GET');
  assert.equal(request.redirect, 'error');
  assert.equal(request.headers.has('authorization'), false);
  const result = await response.json();
  assert.equal(result.binding_sha256, await digest(binding));
  assert.equal(result.source_receipt_sha256, f.lookup.source_receipt_sha256);
  assert.equal(result.projection.upper, 1_650);
  assert.equal(result.projection.gap_upper, 100);
  assert.equal(result.projection.free_standard_no_fallback, true);
  assert.equal(result.projection.completeness_verified, true);
});

test('public, alternate, paid and inline input cannot resolve evidence or perform paid effects', async () => {
  const f = await fixture();
  for (const endpoint of ['https://public.example/v1/observation/read', url + '?x=1',
    'https://quota.internal/another']) {
    assert.equal((await f.submit(f.lookup, endpoint)).status, 404);
  }
  assert.equal((await f.submit({ ...f.lookup, projection: {} })).status, 400);
  assert.equal((await f.submit({ ...f.lookup, route: { ...route, paid: true } })).status, 400);
  assert.equal((await f.submit({ ...f.lookup, route: { ...route, gateway_id: null } })).status, 403);
  assert.equal((await f.submit({ ...f.lookup, source_receipt_sha256: '/redirect' })).status, 400);
  assert.equal((await f.submit({ ...f.lookup, source_receipt_sha256: '0'.repeat(3000) })).status, 400);
  assert.equal(f.calls.length, 0);
});

test('missing evidence, wrong media type, redirects, and unbound dependencies are held', async () => {
  for (const handler of [
    () => new Response(null, { status: 404 }),
    () => Response.redirect('https://public.example/', 302),
    () => new Response('not proof'),
    () => new Response(new Uint8Array(17000), { headers: { 'content-type': 'application/octet-stream' } }),
    () => { throw new Error('private store not reachable'); },
  ]) {
    const f = await fixture(undefined, handler);
    const r = await f.submit();
    assert.equal(r.status, 503);
    assert.deepEqual(await r.json(), { error: 'original_unavailable' });
    assert.equal(f.calls.length, 1);
  }
  const noStore = await fixture();
  delete noStore.env.QUOTA_EVIDENCE;
  assert.equal((await noStore.submit()).status, 503);
  assert.equal(noStore.calls.length, 0);
  const noTrust = await fixture();
  noTrust.env.EXCLUSIVE_QUOTA_BINDING = '{}';
  assert.equal((await noTrust.submit()).status, 503);
  assert.equal(noTrust.calls.length, 0);
});

test('tampered bytes and a digest-matched changed original without the independent signature are refused', async () => {
  const swapped = await fixture(undefined, (bytes) => {
    bytes[170] ^= 1;
    return new Response(bytes, { headers: { 'content-type': 'application/octet-stream' } });
  });
  assert.deepEqual(await (await swapped.submit()).json(), { error: 'original_unverified' });
  const forged = await fixture((p) => ({ ...p, ledger: { ...p.ledger, account_usage_upper: 0 } }));
  const result = await forged.submit();
  assert.equal(result.status, 403);
  assert.deepEqual(await result.json(), { error: 'original_unverified' });
});
