// JSON contracts shared with uif.effect_authority and stokowski_cloudflare.shared_admission.
export class Refusal extends Error {
  constructor(code, status = 409) {
    super(code);
    this.status = status;
  }
}
export function need(value, code, status) {
  if (!value) throw new Refusal(code, status);
}
export const object = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
export function fields(x, ...names) {
  need(
    object(x) && names.length === Object.keys(x).length && names.every((k) => Object.hasOwn(x, k)),
    'invalid_fields',
    400,
  );
}
export const token = (x) => typeof x === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(x);
export const sha = (x) => typeof x === 'string' && /^[0-9a-f]{64}$/.test(x);
export const uuid = (x) =>
  typeof x === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(x);
export const uint = (x) => Number.isSafeInteger(x) && x >= 0;
export const now = () => Math.floor(Date.now() / 1000);
export const day = (t) => new Date(t * 1000).toISOString().slice(0, 10);
// All wire keys are ASCII. Python json.dumps(sort_keys=True, ensure_ascii=True).
export function canonical(x) {
  if (Array.isArray(x)) return '[' + x.map(canonical).join(',') + ']';
  if (object(x))
    return (
      '{' +
      Object.keys(x)
        .sort()
        .map((k) => canonical(k) + ':' + canonical(x[k]))
        .join(',') +
      '}'
    );
  const out = JSON.stringify(x);
  need(typeof out === 'string', 'invalid_json', 400);
  return out.replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
}
export const equal = (a, b) => canonical(a) === canonical(b);
export async function digestBytes(data) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), (b) =>
    b.toString(16).padStart(2, '0'),
  ).join('');
}
export const digest = (x) => digestBytes(new TextEncoder().encode(canonical(x)));
export function bytes64(s, limit) {
  need(
    typeof s === 'string' && s.length > 0 && s.length <= 4 * Math.ceil(limit / 3),
    'invalid_body',
    400,
  );
  let raw;
  try {
    raw = atob(s);
  } catch {
    throw new Refusal('invalid_base64', 400);
  }
  need(btoa(raw) === s && raw.length <= limit, 'invalid_base64', 400);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
export async function readBytes(stream, limit) {
  need(stream, 'missing_body', 400);
  const reader = stream.getReader(),
    chunks = [];
  let len = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      len += value.length;
      if (len > limit) {
        await reader.cancel();
        throw new Refusal('body_size', 413);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(len);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}
export function json(bytes) {
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new Refusal('invalid_json', 400);
  }
}
export function scopeSource(c, scope, source) {
  fields(scope, 'repository_id', 'key');
  fields(scope.key, 'workspace_id', 'project_id', 'issue_id');
  fields(source, 'repository', 'branch', 'base', 'head', 'tree');
  need(
    scope.repository_id === c.repository_id &&
      scope.key.workspace_id === c.workspace_id &&
      scope.key.project_id === c.project_id &&
      uuid(scope.key.issue_id) &&
      source.repository === 'maldous/offmenu' &&
      typeof source.branch === 'string' &&
      source.branch.length > 0 &&
      source.branch.length <= 256 &&
      !/[\x00-\x1f]/.test(source.branch) &&
      [source.base, source.head, source.tree].every(
        (s) => typeof s === 'string' && /^[0-9a-f]{40}$/.test(s),
      ),
    'scope_or_source_unbound',
    403,
  );
}
export const models = new Map([
  ['@cf/openai/gpt-oss-20b', 128000 * 19 + 2048 * 28],
  ['@cf/zai-org/glm-4.7-flash', 131072 * 6 + 2048 * 37],
]);
export function routeOK(route) {
  fields(route, 'account_id', 'model', 'gateway_id', 'credential_epoch_sha256');
  need(
    typeof route.account_id === 'string' &&
      /^[a-f0-9]{32}$/.test(route.account_id) &&
      models.has(route.model) &&
      (route.gateway_id === null ||
        (typeof route.gateway_id === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(route.gateway_id))) &&
      sha(route.credential_epoch_sha256),
    'unconfigured_free_route',
    503,
  );
}
export function config(env) {
  let c;
  try {
    c = JSON.parse(env.AUTHORITY_CONFIG);
  } catch {
    throw new Refusal('authority_unconfigured', 503);
  }
  need(
    object(c) &&
      token(c.authority_id) &&
      token(c.quota_authority_id) &&
      c.authority_id !== c.quota_authority_id &&
      sha(c.deployment_sha256) &&
      sha(c.policy_sha256) &&
      token(c.repository_id) &&
      uuid(c.workspace_id) &&
      uuid(c.project_id) &&
      uint(c.lease_seconds) &&
      c.lease_seconds >= 1 &&
      c.lease_seconds <= 300 &&
      object(c.peers),
    'authority_unconfigured',
    503,
  );
  const roles = new Set(),
    sessions = new Set();
  for (const [client, peer] of Object.entries(c.peers)) {
    need(
      /^[0-9a-f]+\.access$/.test(client) &&
        object(peer) &&
        ['native', 'morgan', 'control', 'quota'].includes(peer.role) &&
        token(peer.session_id) &&
        !sessions.has(peer.session_id) &&
        !roles.has(peer.role),
      'ambiguous_access_binding',
      503,
    );
    sessions.add(peer.session_id);
    roles.add(peer.role);
  }
  need(
    roles.size === 4 &&
      typeof env.ACCESS_ISSUER === 'string' &&
      /^https:\/\/[a-z0-9-]+\.cloudflareaccess\.com$/.test(env.ACCESS_ISSUER) &&
      sha(env.ACCESS_AUD),
    'authority_access_unconfigured',
    503,
  );
  if (c.route !== null) routeOK(c.route);
  if (c.reviewer_route != null) {
    routeOK(c.reviewer_route);
    const prior = c.reviewer_opening_liability;
    need(
      c.route &&
        c.reviewer_route.account_id === 'ecc31e887c4ab56a99f0db62e9c7213a' &&
        c.route.account_id !== c.reviewer_route.account_id &&
        c.reviewer_route.model === '@cf/zai-org/glm-4.7-flash' &&
        c.reviewer_route.credential_epoch_sha256 !== c.route.credential_epoch_sha256 &&
        object(prior) &&
        token(prior.original_id) &&
        sha(prior.receipt_sha256) &&
        uint(prior.millineurons) &&
        prior.millineurons >= 2489344,
      'independent_review_route_or_opening_liability_unbound',
      503,
    );
  }
  return c;
}
export function identity(c, peer) {
  return {
    authority_id: peer.role === 'quota' ? c.quota_authority_id : c.authority_id,
    deployment_sha256: c.deployment_sha256,
    policy_sha256: c.policy_sha256,
    session_id: peer.session_id,
  };
}
export function wire(route) {
  const headers = { 'Content-Type': 'application/json' };
  if (route.gateway_id !== null)
    Object.assign(headers, {
      'cf-aig-gateway-id': route.gateway_id,
      'cf-aig-max-attempts': '1',
      'cf-aig-skip-cache': 'true',
    });
  return {
    method: 'POST',
    path: `/client/v4/accounts/${route.account_id}/ai/v1/chat/completions`,
    headers,
  };
}
