import { Refusal, need, now, readBytes, json } from './protocol.mjs';

function segment(part) {
  need(typeof part === 'string' && /^[A-Za-z0-9_-]+$/.test(part), 'access_assertion_invalid', 401);
  try {
    return Uint8Array.from(atob(part.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
  } catch {
    throw new Refusal('access_assertion_invalid', 401);
  }
}

// Fixed reason codes only. The Worker keeps its existing generic rejection.
export function serviceIdentityFailures(claims, env, peers, t) {
  const checks = {
    issuer: claims?.iss === env.ACCESS_ISSUER,
    audience: Array.isArray(claims?.aud) && claims.aud.includes(env.ACCESS_AUD),
    application_type: claims?.type === 'app',
    empty_subject: claims?.sub === '',
    issued_at: Number.isSafeInteger(claims?.iat) && claims.iat >= 0 && claims.iat <= t,
    expiry: Number.isSafeInteger(claims?.exp) && claims.exp > t,
    not_before: claims?.nbf === undefined ||
      (Number.isSafeInteger(claims.nbf) && claims.nbf <= t),
    registered_client: typeof claims?.common_name === 'string' &&
      Object.hasOwn(peers, claims.common_name),
  };
  return Object.keys(checks).filter((name) => !checks[name]);
}

// Trust only signer keys at the installed team origin. Never follow a JWT URL.
export function accessVerifier(fetchKeys = fetch, clock = now) {
  let cached;
  async function keys(issuer) {
    if (cached?.issuer === issuer && clock() < cached.until) return cached.keys;
    const r = await fetchKeys(issuer + '/cdn-cgi/access/certs', {
      redirect: 'manual',
      signal: AbortSignal.timeout(5000),
    });
    need(r.status === 200, 'access_keys_unavailable', 503);
    const jwks = json(await readBytes(r.body, 128 * 1024));
    need(Array.isArray(jwks.keys) && jwks.keys.length <= 32, 'access_keys_unavailable', 503);
    cached = { issuer, until: clock() + 60, keys: jwks.keys };
    return cached.keys;
  }
  return async function verify(request, env, c) {
    const jwt = request.headers.get('Cf-Access-Jwt-Assertion');
    need(jwt && jwt.length <= 16384, 'access_assertion_required', 401);
    const parts = jwt.split('.');
    need(parts.length === 3, 'access_assertion_invalid', 401);
    const header = json(segment(parts[0])),
      claims = json(segment(parts[1]));
    need(
      header?.alg === 'RS256' && header.typ === 'JWT' && typeof header.kid === 'string' && !header.crit,
      'access_algorithm_invalid',
      401,
    );
    const matches = (await keys(env.ACCESS_ISSUER)).filter(
      (k) =>
        k.kid === header.kid &&
        k.kty === 'RSA' &&
        (!k.alg || k.alg === 'RS256') &&
        (!k.use || k.use === 'sig'),
    );
    need(matches.length === 1, 'access_key_unknown', 401);
    const key = await crypto.subtle.importKey(
      'jwk',
      matches[0],
      { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
      false,
      ['verify'],
    );
    need(
      await crypto.subtle.verify(
        'RSASSA-PKCS1-v1_5',
        key,
        segment(parts[2]),
        new TextEncoder().encode(parts[0] + '.' + parts[1]),
      ),
      'access_signature_invalid',
      401,
    );
    const failures = serviceIdentityFailures(claims, env, c.peers, clock());
    if (failures.length) {
      // Cloudflare receiver console only, after signature verification.
      // Never log claim values, request fields, or peer identifiers.
      console.warn('uif.access.service_identity_invalid:' + failures.join(','));
      throw new Refusal('access_service_identity_invalid', 403);
    }
    return { ...c.peers[claims.common_name], expires_at: claims.exp };
  };
}
