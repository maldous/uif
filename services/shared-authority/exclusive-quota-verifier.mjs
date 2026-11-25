// Binding-only quota verifier entry point. No public route or provider traffic.
import { verifyExclusiveQuotaCurrent } from './exclusive-quota-proof.mjs';
import { bytes64, equal, fields, routeOK, sha } from './protocol.mjs';

const observationURL = 'https://quota.internal/v1/observation/read';
const originalPrefix = 'https://quota-evidence.internal/v1/original/';
const checkpointPrefix = 'https://quota-evidence.internal/v1/checkpoint/current/';
const nowSeconds = () => Math.floor(Date.now() / 1000);
const maximumConcurrent = 4;
const maximumDeadlineMs = 3000;
let inFlight = 0;

// Keep an in-flight slot until the original operation and cancellation
// settle, even if the caller already received a deadline refusal.
function trackCancel(operation, cleanups) {
  try {
    cleanups.push(Promise.resolve(operation()).catch(() => {}));
  } catch {
    // Synchronous cancellation failure is already terminal for this reader.
  }
}
async function boundedRead(stream, limit, signal, cleanups) {
  signal.throwIfAborted();
  if (!stream) throw new Error('body absent');
  const reader = stream.getReader();
  const cancel = () => trackCancel(() => reader.cancel(), cleanups);
  signal.addEventListener('abort', cancel, { once: true });
  const result = new Uint8Array(limit);
  let used = 0;
  try {
    for (;;) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) return result.slice(0, used);
      if (!(value instanceof Uint8Array) || value.byteLength === 0 || value.byteLength > limit - used) {
        cancel();
        throw new Error('body limit');
      }
      result.set(value, used);
      used += value.byteLength;
    }
  } finally {
    signal.removeEventListener('abort', cancel);
    reader.releaseLock();
  }
}

function reply(status, value) {
  return Response.json(value, {
    status,
    headers: { 'cache-control': 'private, no-store' },
  });
}
const denied = (status, code) => reply(status, { error: code });

export async function resolveObservation(
  request, env, clock = nowSeconds, deadlineMs = maximumDeadlineMs, ctx,
) {
  if (request.url !== observationURL) return denied(404, 'not_found');
  if (request.method !== 'POST') return denied(405, 'method_not_allowed');
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') ?? ''))
    return denied(415, 'json_required');
  if (inFlight >= maximumConcurrent) return denied(503, 'verifier_saturated');
  if (!Number.isSafeInteger(deadlineMs) || deadlineMs < 1 || deadlineMs > maximumDeadlineMs)
    return denied(503, 'verifier_unbound');
  if (request.signal.aborted) return denied(503, 'observation_deadline');

  inFlight++;
  const controller = new AbortController();
  const abort = () => controller.abort();
  const cleanups = [];
  const interrupted = new Promise((resolve) => {
    controller.signal.addEventListener('abort', () => resolve(denied(503, 'observation_deadline')), { once: true });
  });
  const timer = setTimeout(abort, deadlineMs);
  request.signal.addEventListener('abort', abort, { once: true });
  const work = resolveBounded(request, env, clock, controller.signal, cleanups)
    .catch(() => denied(503, 'observation_unavailable'));
  // Nothing awaits a potentially stalled cancellation on the response path.
  const settled = work.then(async () => {
    await Promise.all(cleanups);
    inFlight--;
  });
  // Keep cleanup alive when the platform allows; it never gates the reply.
  ctx?.waitUntil(settled);
  try {
    return await Promise.race([work, interrupted]);
  } finally {
    clearTimeout(timer);
    request.signal.removeEventListener('abort', abort);
  }
}

async function resolveBounded(request, env, clock, signal, cleanups) {
  let binding;
  const openingSHA = env.EXCLUSIVE_QUOTA_OPENING_SHA256;
  try {
    binding = JSON.parse(env.EXCLUSIVE_QUOTA_BINDING);
    fields(binding, 'route', 'topology_sha256', 'closure_public_key', 'ledger_public_key');
    routeOK(binding.route);
    if (
      !sha(binding.topology_sha256) || !sha(openingSHA) ||
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
    const raw = await boundedRead(request.body, 2048, signal, cleanups);
    query = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
    fields(query, 'source_receipt_sha256', 'route');
    routeOK(query.route);
    if (!sha(query.source_receipt_sha256)) return denied(400, 'invalid_lookup');
  } catch {
    return denied(400, 'invalid_lookup');
  }
  if (!equal(query.route, binding.route)) return denied(403, 'route_unbound');

  async function retained(url) {
    signal.throwIfAborted();
    const response = await env.QUOTA_EVIDENCE.fetch(
      new Request(url, {
        method: 'GET',
        // Workerd supports manual redirects here; the response gate below
        // rejects redirects before reading any evidence bytes.
        redirect: 'manual',
        signal,
      }),
    );
    // Never read a redirect or a non-proof/error body. No caller headers are
    // forwarded; the storage service capability is the only dependency.
    if (signal.aborted || response.status !== 200 || response.redirected ||
        response.headers.get('content-type') !== 'application/octet-stream') {
      if (response.body) trackCancel(() => response.body.cancel(), cleanups);
      throw new Error('retained bytes unavailable');
    }
    return boundedRead(response.body, 16384, signal, cleanups);
  }
  let original;
  try {
    original = await retained(originalPrefix + query.source_receipt_sha256);
  } catch {
    return denied(503, 'original_unavailable');
  }
  let head;
  try {
    head = await retained(checkpointPrefix + binding.route.account_id);
  } catch {
    return denied(503, 'checkpoint_unavailable');
  }

  try {
    signal.throwIfAborted();
    const out = await verifyExclusiveQuotaCurrent({
      binding, request: query, original, head, openingSHA, now: clock(),
    });
    signal.throwIfAborted();
    return reply(200, out);
  } catch {
    return denied(403, 'original_unverified');
  }
}

export default {
  fetch: (request, env, ctx) => resolveObservation(request, env, nowSeconds, maximumDeadlineMs, ctx),
};
