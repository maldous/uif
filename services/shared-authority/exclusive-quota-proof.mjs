// Source-only decoder. No network, signer, store, deployment or self-attestation.
import { bytes64, canonical, day, digest, digestBytes, equal, fields, need, routeOK, sha, uint } from './protocol.mjs';

const closureSchema = 'uif.exclusive-quota.closure/1';
const ledgerSchema = 'uif.exclusive-quota.ledger/1';
const coverage = 'entire-account-all-routes-from-utc-midnight';
const limit = 7_500_000;
const encoder = new TextEncoder();
const originalLimit = 16384;
const withinLimit = (n) => uint(n) && n < limit;

function exact64(value, size) {
  const bytes = bytes64(value, size);
  need(bytes.length === size, 'quota_proof_encoding_invalid');
  return bytes;
}
function timestamp(t) {
  return uint(t) && t <= 4102444800;
}
async function signed(value, signature, publicKey) {
  const key = await crypto.subtle.importKey('raw', exact64(publicKey, 32), 'Ed25519', false, ['verify']);
  need(
    await crypto.subtle.verify('Ed25519', key, exact64(signature, 64), encoder.encode(canonical(value))),
    'quota_proof_signature_invalid',
    403,
  );
}

/**
 * Resolve the exact byte-addressed original for the existing QUOTA_VERIFIER
 * wire contract. The caller supplies bytes from independent retained storage,
 * never a submitter's inline proof. A caller must not install this as an
 * authority until it separately qualifies both provenances and a live store.
 */
export async function verifyExclusiveQuotaOriginal({ binding, request, original, now }) {
  fields(binding, 'route', 'topology_sha256', 'closure_public_key', 'ledger_public_key');
  fields(request, 'source_receipt_sha256', 'route');
  // Snapshot plain caller-owned configuration and lookup inputs before awaits.
  binding = structuredClone(binding);
  request = structuredClone(request);
  routeOK(binding.route);
  routeOK(request.route);
  need(
    sha(binding.topology_sha256) &&
      sha(request.source_receipt_sha256) &&
      equal(binding.route, request.route) &&
      typeof binding.closure_public_key === 'string' &&
      typeof binding.ledger_public_key === 'string' &&
      binding.closure_public_key !== binding.ledger_public_key &&
      timestamp(now),
    'quota_proof_trust_or_route_unbound',
    403,
  );
  need(
    original instanceof Uint8Array &&
      original.byteLength > 0 &&
      original.byteLength <= originalLimit,
    'quota_original_unavailable',
    503,
  );
  // Copy across asynchronous validation to prevent a mutable caller buffer
  // from changing the bytes after the digest but before parsing.
  const raw = new Uint8Array(original);
  need(await digestBytes(raw) === request.source_receipt_sha256, 'quota_original_digest_mismatch', 403);
  const serialized = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
  const proof = JSON.parse(serialized);
  need(canonical(proof) === serialized, 'quota_original_not_canonical', 400);
  fields(proof, 'closure', 'closure_signature', 'ledger', 'ledger_signature');
  const { closure: c, ledger: l } = proof;
  fields(c, 'schema', 'binding_sha256', 'route', 'utc_day', 'covered_from', 'valid_until',
    'inventory_sha256', 'egress_enforcement_sha256', 'carryover_sha256',
    'outside_gap_upper', 'outside_carryover_upper', 'billing_mode');
  fields(l, 'schema', 'closure_sha256', 'chain_sha256', 'covered_through', 'valid_until',
    'account_usage_upper', 'outside_unresolved_upper');
  const bindingSHA = await digest(binding);
  need(
    c.schema === closureSchema &&
      l.schema === ledgerSchema &&
      c.binding_sha256 === bindingSHA &&
      equal(c.route, binding.route) &&
      c.billing_mode === 'workers-free-standard-no-fallback' &&
      sha(c.inventory_sha256) &&
      sha(c.egress_enforcement_sha256) &&
      sha(c.carryover_sha256) &&
      sha(l.chain_sha256) &&
      timestamp(c.covered_from) &&
      timestamp(c.valid_until) &&
      timestamp(l.covered_through) &&
      timestamp(l.valid_until) &&
      c.utc_day === day(now) &&
      day(c.covered_from) === c.utc_day &&
      c.covered_from % 86400 === 0 &&
      c.covered_from <= l.covered_through &&
      l.covered_through <= now &&
      now - l.covered_through <= 90 &&
      now < l.valid_until &&
      l.valid_until <= c.valid_until &&
      day(l.valid_until) === c.utc_day &&
      day(c.valid_until) === c.utc_day &&
      l.closure_sha256 === await digest(c),
    'quota_proof_coverage_unverified',
    403,
  );
  await Promise.all([
    signed(c, proof.closure_signature, binding.closure_public_key),
    signed(l, proof.ledger_signature, binding.ledger_public_key),
  ]);
  need(
    [c.outside_gap_upper, c.outside_carryover_upper, l.account_usage_upper, l.outside_unresolved_upper].every(withinLimit),
    'quota_proof_bound_invalid',
    403,
  );
  const upper = l.account_usage_upper + l.outside_unresolved_upper + c.outside_carryover_upper;
  const gapUpper = c.outside_gap_upper;
  need(upper + gapUpper < limit, 'quota_proof_no_free_headroom', 403);
  return {
    binding_sha256: bindingSHA,
    source_receipt_sha256: request.source_receipt_sha256,
    coverage,
    gap_evidence_sha256: l.closure_sha256,
    projection: {
      route: { ...binding.route },
      utc_day: c.utc_day,
      covered_through: l.covered_through,
      valid_until: l.valid_until,
      upper,
      gap_upper: gapUpper,
      free_standard_no_fallback: true,
      completeness_verified: true,
    },
  };
}

// The retained store supplies its account's current head, independently of
// the requested original. The head can prove completeness, never erase debt.
export async function verifyExclusiveQuotaCurrent({ binding, request, original, head, openingSHA, now }) {
  need(sha(openingSHA) && head instanceof Uint8Array && head.byteLength > 0 &&
    head.byteLength <= originalLimit, 'quota_checkpoint_unavailable', 503);
  need(original instanceof Uint8Array && original.byteLength <= originalLimit, 'quota_original_unavailable', 503);
  original = new Uint8Array(original);
  binding = structuredClone(binding);
  const headBytes = new Uint8Array(head);
  const observation = await verifyExclusiveQuotaOriginal({ binding, request, original, now });
  const serialized = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(headBytes);
  const current = JSON.parse(serialized);
  need(canonical(current) === serialized, 'quota_checkpoint_not_canonical');
  fields(current, 'checkpoint', 'signature', 'opening');
  const c = current.checkpoint;
  const opening = current.opening;
  fields(opening, 'schema', 'binding_sha256', 'outside_carryover_upper');
  fields(c, 'schema', 'binding_sha256', 'opening_sha256', 'source_receipt_sha256',
    'ledger_chain_sha256', 'utc_day', 'valid_until', 'windows', 'outside_unresolved_upper');
  const { closure, ledger } = JSON.parse(new TextDecoder().decode(original));
  need(c.schema === 'uif.exclusive-quota.current-checkpoint/1' &&
    c.binding_sha256 === observation.binding_sha256 &&
    c.opening_sha256 === openingSHA && closure.carryover_sha256 === openingSHA &&
    opening.schema === 'uif.exclusive-quota.opening/1' &&
    opening.binding_sha256 === observation.binding_sha256 &&
    opening.outside_carryover_upper === closure.outside_carryover_upper &&
    await digest(opening) === openingSHA &&
    c.source_receipt_sha256 === observation.source_receipt_sha256 &&
    c.ledger_chain_sha256 === ledger.chain_sha256 && c.utc_day === closure.utc_day &&
    c.valid_until === ledger.valid_until && now < c.valid_until &&
    c.outside_unresolved_upper === ledger.outside_unresolved_upper &&
    Array.isArray(c.windows) && c.windows.length > 0 && c.windows.length <= 240,
    'quota_checkpoint_binding_unverified', 403);
  await signed(c, current.signature, binding.ledger_public_key);
  let end = closure.covered_from;
  let usage = 0;
  for (const window of c.windows) {
    fields(window, 'from', 'through', 'usage_upper');
    need(window.from === end && timestamp(window.through) && window.through > end &&
      window.through <= ledger.covered_through && withinLimit(window.usage_upper),
      'quota_checkpoint_window_incomplete', 403);
    end = window.through;
    usage += window.usage_upper;
    need(withinLimit(usage), 'quota_checkpoint_usage_invalid', 403);
  }
  need(end === ledger.covered_through && usage === ledger.account_usage_upper,
    'quota_checkpoint_window_incomplete', 403);
  return {
    ...observation,
    checkpoint: {
      sha256: await digestBytes(headBytes), opening_sha256: openingSHA,
      unresolved_upper: c.outside_unresolved_upper, opening_upper: closure.outside_carryover_upper,
    },
  };
}
