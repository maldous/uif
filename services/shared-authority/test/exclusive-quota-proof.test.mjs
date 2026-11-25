import assert from 'node:assert/strict';
import { createPrivateKey, createPublicKey, sign } from 'node:crypto';
import test from 'node:test';
import { verifyExclusiveQuotaOriginal } from '../exclusive-quota-proof.mjs';
import { canonical, digest, digestBytes } from '../protocol.mjs';

const enc = new TextEncoder();
const now = Date.parse('2026-11-25T00:10:01Z') / 1000;
const h = (digit) => digit.repeat(64);

function fixtureKey(n) {
  // Deliberately public synthetic seeds; no generated or production credentials.
  const pkcs8 = Buffer.from('302e020100300506032b657004220420' + n.repeat(64), 'hex');
  const privateKey = createPrivateKey({ key: pkcs8, format: 'der', type: 'pkcs8' });
  const publicKey = createPublicKey(privateKey).export({ format: 'jwk' }).x;
  return {
    publicKey: Buffer.from(publicKey, 'base64url').toString('base64'),
    sign: (value) => sign(null, enc.encode(canonical(value)), privateKey).toString('base64'),
  };
}
const closureKey = fixtureKey('1');
const ledgerKey = fixtureKey('2');
const route = {
  account_id: 'a'.repeat(32),
  model: '@cf/openai/gpt-oss-20b',
  gateway_id: 'synthetic',
  credential_epoch_sha256: h('b'),
};
const binding = {
  route,
  topology_sha256: h('c'),
  closure_public_key: closureKey.publicKey,
  ledger_public_key: ledgerKey.publicKey,
};

async function fixture(c = {}, l = {}, change = (x) => x, serialize = canonical) {
  const closure = {
    schema: 'uif.exclusive-quota.closure/1',
    binding_sha256: await digest(binding),
    route,
    utc_day: '2026-11-25',
    covered_from: now - 601,
    valid_until: now + 180,
    inventory_sha256: h('d'),
    egress_enforcement_sha256: h('e'),
    carryover_sha256: h('f'),
    outside_gap_upper: 150,
    outside_carryover_upper: 125,
    billing_mode: 'workers-free-standard-no-fallback',
    ...c,
  };
  const ledger = {
    schema: 'uif.exclusive-quota.ledger/1',
    closure_sha256: await digest(closure),
    chain_sha256: h('3'),
    covered_through: now - 10,
    valid_until: now + 20,
    account_usage_upper: 1_000_000,
    outside_unresolved_upper: 100,
    ...l,
  };
  const proof = change({
    closure,
    closure_signature: closureKey.sign(closure),
    ledger,
    ledger_signature: ledgerKey.sign(ledger),
  });
  const original = enc.encode(serialize(proof));
  return { binding, now, original, request: { source_receipt_sha256: await digestBytes(original), route } };
}

test('independent signed proof yields only the bounded original authority projection', async () => {
  const input = await fixture();
  const r = await verifyExclusiveQuotaOriginal(input);
  assert.deepEqual(r, {
    binding_sha256: await digest(binding),
    source_receipt_sha256: input.request.source_receipt_sha256,
    coverage: 'entire-account-all-routes-from-utc-midnight',
    gap_evidence_sha256: JSON.parse(new TextDecoder().decode(input.original)).ledger.closure_sha256,
    projection: {
      route,
      utc_day: '2026-11-25',
      covered_through: now - 10,
      valid_until: now + 20,
      upper: 1_000_225,
      gap_upper: 150,
      free_standard_no_fallback: true,
      completeness_verified: true,
    },
  });
});

test('wrong original, caller override, route substitution and self-witness fail closed', async () => {
  const f = await fixture();
  await assert.rejects(verifyExclusiveQuotaOriginal({ ...f, original: undefined }), /original_unavailable/);
  await assert.rejects(verifyExclusiveQuotaOriginal({
    ...f, request: { ...f.request, source_receipt_sha256: h('0') },
  }), /digest_mismatch/);
  await assert.rejects(verifyExclusiveQuotaOriginal({
    ...f, request: { ...f.request, projection: {} },
  }), /invalid_fields/);
  await assert.rejects(verifyExclusiveQuotaOriginal({
    ...f, request: { ...f.request, route: { ...route, gateway_id: null } },
  }), /trust_or_route/);
  await assert.rejects(verifyExclusiveQuotaOriginal({
    ...f, binding: { ...binding, ledger_public_key: binding.closure_public_key },
  }), /trust_or_route/);
});

test('deterministic independent producer signatures and proof binding are required', async () => {
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({}, {}, (p) => ({
    ...p, ledger_signature: closureKey.sign(p.ledger),
  }))), /signature_invalid/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({}, {}, (p) => ({
    ...p, ledger: { ...p.ledger, account_usage_upper: 0 },
  }))), /signature_invalid/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({}, { closure_sha256: h('0') })),
    /coverage_unverified/);
  const f = await fixture();
  await assert.rejects(verifyExclusiveQuotaOriginal({
    ...f, binding: { ...binding, topology_sha256: h('0') },
  }), /coverage_unverified/);
});

test('sampled/unverified shapes, noncanonical bytes, and paid claims do not qualify', async () => {
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({ sampling: 'adaptive' })), /invalid_fields/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({}, {}, (x) => x,
    (x) => canonical(x).replace('{"closure":', '{"closure":null,"closure":'))), /not_canonical/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({ billing_mode: 'unified' })), /coverage_unverified/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({ egress_enforcement_sha256: null })),
    /coverage_unverified/);
});

test('receipt cannot skip the UTC opening interval, be stale, or cross reset', async () => {
  await verifyExclusiveQuotaOriginal(await fixture({}, { covered_through: now - 90 }));
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({ covered_from: now - 600 })), /coverage_unverified/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({}, { covered_through: now - 91 })), /coverage_unverified/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({}, { valid_until: now + 181 })), /coverage_unverified/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture(
    { valid_until: now + 86_400 }, { valid_until: now + 86_400 },
  )), /coverage_unverified/);
});

test('upper includes unresolved carryover and gap; equality at cap never opens', async () => {
  const input = await fixture(
    { outside_gap_upper: 2_000_000, outside_carryover_upper: 2_000_000 },
    { account_usage_upper: 3_000_000, outside_unresolved_upper: 499_999 },
  );
  const r = await verifyExclusiveQuotaOriginal(input);
  assert.equal(r.projection.upper + r.projection.gap_upper, 7_499_999);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture(
    { outside_gap_upper: 2_000_000, outside_carryover_upper: 2_000_000 },
    { account_usage_upper: 3_000_000, outside_unresolved_upper: 500_000 },
  )), /no_free_headroom/);
  await assert.rejects(verifyExclusiveQuotaOriginal(await fixture({ outside_carryover_upper: -1 })), /bound_invalid/);
});
