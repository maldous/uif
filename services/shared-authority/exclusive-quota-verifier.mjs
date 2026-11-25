// Binding-only quota verifier entry point. No public route or provider traffic.
import { verifyExclusiveQuotaOriginal } from './exclusive-quota-proof.mjs';
import { bytes64, equal, fields, readBytes, routeOK, sha } from './protocol.mjs';

const observationURL = 'https://quota.internal/v1/observation/read';
const originalPrefix = 'https://quota-evidence.internal/v1/original/';
const nowSeconds = () => Math.floor(Date.now() / 1000);

function reply(status, value) {
  return Response.json(value, {
    status,
    headers: { 'cache-control': 'private, no-store' },
  });
}
const denied = (status, code) => reply(status, { error: code });

export async function resolveObservation(request, env, clock = nowSeconds) {
  if (request.url !== observationURL) return denied(404, 'not_found');
  if (request.method !== 'POST') return denied(405, 'method_not_allowed');
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') ?? ''))
    return denied(415, 'json_required');

  let binding;
  try {
    binding = JSON.parse(env.EXCLUSIVE_QUOTA_BINDING);
    fields(binding, 'route', 'topology_sha256', 'closure_public_key', 'ledger_public_key');
    routeOK(binding.route);
    if (
      !sha(binding.topology_sha256) ||
      bytes64(binding.closure_public_key, 32).length !== 32 ||
      bytes64(binding.ledger_public_key, 32).length !== 32 ||
      binding.closure_public_key === binding.ledger_public_key ||
      typeof env.QUOTA_EVIDENCE?.fetch !== 'function'
    )
      return denied(503, 'verifier_unbound');
  } catch {
    return denied(503, 'verifier_unbound');
  }

  let query;
  try {
    const raw = await readBytes(request.body, 2048);
    query = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
    fields(query, 'source_receipt_sha256', 'route');
    routeOK(query.route);
    if (!sha(query.source_receipt_sha256)) return denied(400, 'invalid_lookup');
  } catch {
    return denied(400, 'invalid_lookup');
  }
  if (!equal(query.route, binding.route)) return denied(403, 'route_unbound');

  let original;
  try {
    const response = await env.QUOTA_EVIDENCE.fetch(
      new Request(originalPrefix + query.source_receipt_sha256, {
        method: 'GET',
        redirect: 'error',
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(3000)]),
      }),
    );
    // Never read a redirect or a non-proof/error body. No caller headers are
    // forwarded; the storage service capability is the only dependency.
    if (response.status !== 200 || response.redirected ||
        response.headers.get('content-type') !== 'application/octet-stream') {
      await response.body?.cancel();
      return denied(503, 'original_unavailable');
    }
    original = await readBytes(response.body, 16384);
  } catch {
    return denied(503, 'original_unavailable');
  }

  try {
    const out = await verifyExclusiveQuotaOriginal({ binding, request: query, original, now: clock() });
    return reply(200, out);
  } catch {
    return denied(403, 'original_unverified');
  }
}

export default { fetch: (request, env) => resolveObservation(request, env) };
