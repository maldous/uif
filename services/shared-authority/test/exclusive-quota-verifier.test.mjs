import assert from 'node:assert/strict';
import { createPrivateKey, createPublicKey, sign } from 'node:crypto';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { Miniflare } from 'miniflare';
import { prepareExclusiveQuotaCheckpoint, verifyExclusiveQuotaCurrent } from '../exclusive-quota-proof.mjs';
import { resolveObservation } from '../exclusive-quota-verifier.mjs';
import { canonical, digest, digestBytes } from '../protocol.mjs';

const h = (digit) => digit.repeat(64);
const epoch = Date.parse('2026-11-25T00:00:00Z') / 1000;
const enc = new TextEncoder();
const url = 'https://quota.internal/v1/observation/read';
const route = {
  account_id: 'a'.repeat(32),
  model: '@cf/zai-org/glm-4.7-flash',
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
}), headOptions = {}, now = epoch + 60) {
  const midnight = Math.floor(now / 86400) * 86400;
  const watermark = now - 10;
  const validUntil = Math.min(now + 60, midnight + 86399);
  const opening = {
    schema: 'uif.exclusive-quota.opening/1', binding_sha256: await digest(binding),
    outside_carryover_upper: 150,
  };
  const closure = {
    schema: 'uif.exclusive-quota.closure/1',
    binding_sha256: await digest(binding), route,
    utc_day: new Date(midnight * 1000).toISOString().slice(0, 10), covered_from: midnight,
    valid_until: Math.min(now + 140, midnight + 86399),
    inventory_sha256: h('d'), egress_enforcement_sha256: h('e'),
    carryover_sha256: await digest(opening), outside_gap_upper: 100, outside_carryover_upper: 150,
    billing_mode: 'workers-free-standard-no-fallback',
  };
  const ledger = {
    schema: 'uif.exclusive-quota.ledger/1', closure_sha256: await digest(closure),
    chain_sha256: h('3'), covered_through: watermark, valid_until: validUntil,
    account_usage_upper: 1_200, outside_unresolved_upper: 300,
  };
  const original = enc.encode(canonical(modify({
    closure, closure_signature: signClosure(closure), ledger, ledger_signature: signLedger(ledger),
  })));
  const originalSha = await digestBytes(original);
  const checkpoint = {
    schema: 'uif.exclusive-quota.current-checkpoint/1',
    binding_sha256: closure.binding_sha256, opening_sha256: closure.carryover_sha256,
    source_receipt_sha256: originalSha, ledger_chain_sha256: ledger.chain_sha256,
    utc_day: closure.utc_day, valid_until: ledger.valid_until,
    windows: [{ from: midnight, through: midnight + 20, usage_upper: 400 },
      { from: midnight + 20, through: watermark, usage_upper: 800 }],
    outside_unresolved_upper: ledger.outside_unresolved_upper, ...headOptions.fields,
  };
  const head = enc.encode(canonical({
    checkpoint, signature: (headOptions.signer || signLedger)(checkpoint),
    opening: { ...opening, ...headOptions.opening },
  }));
  const lookup = { source_receipt_sha256: originalSha, route };
  const calls = [];
  const env = {
    EXCLUSIVE_QUOTA_BINDING: JSON.stringify(binding),
    EXCLUSIVE_QUOTA_OPENING_SHA256: closure.carryover_sha256,
    QUOTA_EVIDENCE: {
      fetch: async (r) => {
        calls.push(r);
        if (r.url === 'https://quota-evidence.internal/v1/checkpoint/current/' + route.account_id)
          return headOptions.missing ? new Response(null, { status: 404 }) : new Response(head, {
            headers: { 'content-type': 'application/octet-stream' },
          });
        return handler(original);
      },
    },
  };
  const submit = (body = lookup, endpoint = url, additional = {}, deadlineMs) => resolveObservation(
    new Request(endpoint, { method: 'POST', headers: {
      'content-type': 'application/json', 'authorization': 'external-ignored',
    }, body: JSON.stringify(body), ...additional }), env, () => now, deadlineMs,
  );
  return { env, lookup, calls, submit, original, opening, checkpoint, head };
}

test('exact request joins independent original bytes to the private account current head', async () => {
  const f = await fixture();
  const response = await f.submit();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(f.calls.length, 2);
  const [request, head] = f.calls;
  assert.equal(request.url, 'https://quota-evidence.internal/v1/original/' + f.lookup.source_receipt_sha256);
  assert.equal(request.method, 'GET');
  assert.equal(request.redirect, 'manual');
  assert.equal(request.headers.has('authorization'), false);
  assert.equal(head.url, 'https://quota-evidence.internal/v1/checkpoint/current/' + route.account_id);
  assert.equal(head.headers.has('authorization'), false);
  const result = await response.json();
  assert.equal(result.binding_sha256, await digest(binding));
  assert.equal(result.source_receipt_sha256, f.lookup.source_receipt_sha256);
  assert.equal(result.projection.upper, 1_650);
  assert.equal(result.projection.gap_upper, 100);
  assert.equal(result.projection.free_standard_no_fallback, true);
  assert.equal(result.projection.completeness_verified, true);
  assert.equal(result.checkpoint.opening_sha256, f.env.EXCLUSIVE_QUOTA_OPENING_SHA256);
  assert.equal(result.checkpoint.unresolved_upper, 300);
});

test('workerd verifies the signed current checkpoint through the private evidence binding', async (t) => {
  const now = Math.floor(Date.now() / 1000);
  if (now % 86400 < 31 || now % 86400 > 86339) {
    t.skip('requires an open UTC window for the real Worker clock');
    return;
  }
  const f = await fixture(undefined, undefined, undefined, now);
  const { QUOTA_EVIDENCE, ...bindings } = f.env;
  const root = new URL('../', import.meta.url);
  let outboundRequests = 0;
  const instance = new Miniflare({
    modules: true,
    scriptPath: fileURLToPath(new URL('exclusive-quota-verifier.mjs', root)),
    modulesRoot: fileURLToPath(root),
    compatibilityDate: '2026-07-30',
    bindings,
    serviceBindings: { QUOTA_EVIDENCE: QUOTA_EVIDENCE.fetch },
    outboundService: async () => {
      outboundRequests++;
      return new Response(null, { status: 500 });
    },
  });
  try {
    const response = await instance.dispatchFetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'authorization': 'external-ignored' },
      body: JSON.stringify(f.lookup),
    });
    const result = await response.json();
    assert.equal(response.status, 200, JSON.stringify(result));
    assert.equal(result.coverage, 'entire-account-all-routes-from-utc-midnight');
    assert.deepEqual(result.checkpoint, {
      sha256: await digestBytes(f.head),
      opening_sha256: bindings.EXCLUSIVE_QUOTA_OPENING_SHA256,
      unresolved_upper: 300,
      opening_upper: 150,
    });
    assert.equal(result.projection.upper, 1_650);
    assert.equal(result.projection.gap_upper, 100);
    assert.deepEqual(f.calls.map((request) => request.url), [
      'https://quota-evidence.internal/v1/original/' + f.lookup.source_receipt_sha256,
      'https://quota-evidence.internal/v1/checkpoint/current/' + route.account_id,
    ]);
    assert.ok(f.calls.every((request) => !request.headers.has('authorization')));
    assert.equal(outboundRequests, 0);
  } finally {
    await instance.dispose();
  }
});

test('missing current head or opening pin fail closed', async () => {
  const absent = await fixture(undefined, undefined, { missing: true });
  assert.deepEqual(await (await absent.submit()).json(), { error: 'checkpoint_unavailable' });
  const noOpening = await fixture();
  delete noOpening.env.EXCLUSIVE_QUOTA_OPENING_SHA256;
  assert.equal((await noOpening.submit()).status, 503);
  assert.equal(noOpening.calls.length, 0);
});

test('rejects forged, historical, stale, wrong-opening, missing-window, and reduced-liability checkpoints', async () => {
  for (const options of [
    { signer: signClosure },
    { fields: { source_receipt_sha256: h('0') } },
    { fields: { valid_until: epoch + 59 } },
    { fields: { opening_sha256: h('0') } },
    { opening: { outside_carryover_upper: 149 } },
    { fields: { outside_unresolved_upper: 299 } },
    { fields: { windows: [{ from: epoch, through: epoch + 19, usage_upper: 400 },
      { from: epoch + 20, through: epoch + 50, usage_upper: 800 }] } },
    { fields: { windows: [{ from: epoch, through: epoch + 50, usage_upper: 1199 }] } },
  ]) {
    const f = await fixture(undefined, undefined, options);
    assert.equal((await f.submit()).status, 403);
  }
  const wrongPin = await fixture();
  wrongPin.env.EXCLUSIVE_QUOTA_OPENING_SHA256 = h('0');
  assert.equal((await wrongPin.submit()).status, 403);
});

test('producer returns exact deterministic bytes that the private verifier accepts', async () => {
  const f = await fixture();
  const inputs = {
    binding, request: f.lookup, original: f.original, opening: f.opening,
    openingSHA: f.checkpoint.opening_sha256, windows: f.checkpoint.windows, now: epoch + 60,
  };
  const signature = (body) => signLedger(JSON.parse(new TextDecoder().decode(body)));
  const result = await prepareExclusiveQuotaCheckpoint({ ...inputs, signCheckpoint: signature });
  assert.deepEqual(result.head, f.head);
  assert.equal(result.head_sha256, await digestBytes(f.head));
  const observation = await verifyExclusiveQuotaCurrent({ ...inputs, head: result.head });
  assert.equal(observation.checkpoint.unresolved_upper, 300);
  assert.equal(observation.checkpoint.opening_upper, 150);
  assert.deepEqual(await prepareExclusiveQuotaCheckpoint({ ...inputs, signCheckpoint: signature }), result);
});

test('producer rejects invalid evidence before signing and rejects an untrusted signer', async () => {
  const f = await fixture();
  const inputs = {
    binding, request: f.lookup, original: f.original, opening: f.opening,
    openingSHA: f.checkpoint.opening_sha256, windows: f.checkpoint.windows, now: epoch + 60,
  };
  let calls = 0;
  const signCheckpoint = () => { calls += 1; return ''; };
  for (const [changed, code] of [
    [{ request: { ...f.lookup, source_receipt_sha256: h('0') } }, 'quota_original_digest_mismatch'],
    [{ now: epoch + 120 }, 'quota_proof_coverage_unverified'],
    [{ opening: { ...f.opening, outside_carryover_upper: 0 } }, 'quota_checkpoint_binding_unverified'],
    [{ windows: [{ from: epoch + 1, through: epoch + 50, usage_upper: 1200 }] }, 'quota_checkpoint_window_incomplete'],
    [{ windows: [{ from: epoch, through: epoch + 50, usage_upper: 1199 }] }, 'quota_checkpoint_window_incomplete'],
  ]) {
    await assert.rejects(prepareExclusiveQuotaCheckpoint({ ...inputs, ...changed, signCheckpoint }),
      { message: code });
  }
  assert.equal(calls, 0);
  await assert.rejects(prepareExclusiveQuotaCheckpoint({
    ...inputs, signCheckpoint: (bytes) => signClosure(JSON.parse(new TextDecoder().decode(bytes))),
  }), { message: 'quota_proof_signature_invalid' });
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
    ...[300, 301, 302, 303, 304, 307, 308, 399].map((status) =>
      () => new Response(null, { status, headers: { location: 'https://public.example/' } })),
    () => Object.defineProperty(new Response(new Uint8Array(1), {
      headers: { 'content-type': 'application/octet-stream' },
    }), 'redirected', { value: true }),
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

test('a hanging streaming body and blocked cancellation refuse by deadline and retain the original slots',
  { timeout: 1000 }, async () => {
    const cancelResolvers = [];
    let pulsed = 0;
    const f = await fixture(undefined, () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array([0x7b])); },
      pull() { pulsed++; return new Promise(() => {}); },
      cancel() { return new Promise((resolve) => cancelResolvers.push(resolve)); },
    }), { headers: { 'content-type': 'application/octet-stream' } }));
    try {
      const results = await Promise.all(Array.from({ length: 4 }, () => f.submit(f.lookup, url, {}, 40)));
      for (const r of results) {
        assert.equal(r.status, 503);
        assert.deepEqual(await r.json(), { error: 'observation_deadline' });
      }
      assert.equal(pulsed, 4, 'all four calls genuinely stalled during body reads');
      assert.equal(cancelResolvers.length, 4);
      assert.equal(f.calls.length, 4);
      assert.ok(f.calls.every((request) => request.signal.aborted));
      assert.deepEqual(await (await f.submit()).json(), { error: 'verifier_saturated' });
      assert.equal(f.calls.length, 4, 'no additional private store call while originals are unresolved');
    } finally {
      for (const release of cancelResolvers) release();
    }
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal((await (await fixture()).submit()).status, 200);
  });

test('a private binding that ignores header-phase abort stays bounded, then cancels its late body',
  { timeout: 1000 }, async () => {
    let finish;
    let cancelled = false;
    const f = await fixture(undefined, () => new Promise((resolve) => { finish = resolve; }));
    const result = await f.submit(f.lookup, url, {}, 30);
    assert.deepEqual(await result.json(), { error: 'observation_deadline' });
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].signal.aborted, true);
    finish(new Response(new ReadableStream({
      cancel() { cancelled = true; },
    }), { headers: { 'content-type': 'application/octet-stream' } }));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(cancelled, true);
  });

test('a stalled lookup body never dispatches to the private evidence store', { timeout: 1000 }, async () => {
  const f = await fixture();
  const body = new ReadableStream({ pull() { return new Promise(() => {}); } });
  const r = await f.submit(f.lookup, url, { body, duplex: 'half' }, 25);
  assert.deepEqual(await r.json(), { error: 'observation_deadline' });
  assert.equal(f.calls.length, 0);
});
