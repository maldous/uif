import { DurableObject } from 'cloudflare:workers';
import { Authority } from './authority.mjs';
import { accessVerifier } from './access.mjs';
import { config, json, readBytes, need, Refusal } from './protocol.mjs';

const verify = accessVerifier();
const SINGLETON = 'uif.shared-effect-authority/1';
const ACCESS_DIAGNOSTIC_SLOT = 'uif:diagnostic:access-reasons:109:v1';
const reply = (value, status = 200) =>
  Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
function error(e) {
  return e instanceof Refusal
    ? reply({ error: e.message }, e.status)
    : reply({ error: 'authority_unavailable' }, 503);
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      need(
        url.protocol === 'https:' && !url.search && url.pathname.startsWith('/v1/'),
        'unknown_route',
        404,
      );
      // Forward only the Access-signed assertion to exactly one private object.
      // Never forward the Access client secret, cookie or caller-chosen principal.
      const headers = new Headers();
      for (const name of ['cf-access-jwt-assertion', 'content-type']) {
        if (request.headers.has(name)) headers.set(name, request.headers.get(name));
      }
      need(headers.has('cf-access-jwt-assertion'), 'access_assertion_required', 401);
      const stub = env.AUTHORITY.get(env.AUTHORITY.idFromName(SINGLETON));
      return await stub.fetch(
        new Request('https://authority.internal' + url.pathname, {
          method: request.method,
          headers,
          body: request.body,
          redirect: 'manual',
        }),
      );
    } catch (e) {
      return error(e);
    }
  },
};

export class SharedAuthority extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
    this.env = env;
    this.config = config(env);
    const quotaVerifier =
      env.QUOTA_VERIFIER &&
      (async (source_receipt_sha256, route) => {
        const response = await env.QUOTA_VERIFIER.fetch('https://quota.internal/v1/observation/read', {
          method: 'POST',
          redirect: 'manual',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ source_receipt_sha256, route }),
          signal: AbortSignal.timeout(5000),
        });
        need(response.status === 200, 'independent_account_observation_unavailable', 503);
        return json(await readBytes(response.body, 32768));
      });
    this.authority = new Authority(ctx.storage, this.config, {
      providerToken: env.WORKERS_AI_TOKEN,
      reviewerProviderToken: env.REVIEW_WORKERS_AI_TOKEN,
      linearAuthorization: env.LINEAR_TOKEN,
      quotaVerifier,
      trust: { issuer: env.ACCESS_ISSUER, audience: env.ACCESS_AUD },
    });
  }
  async logAccessFailureOnce(failures) {
    const storage = this.ctx.storage;
    const reserved = storage.transactionSync(() => {
      if (storage.kv.get(ACCESS_DIAGNOSTIC_SLOT)) return false;
      storage.kv.put(ACCESS_DIAGNOSTIC_SLOT, true);
      return true;
    });
    if (!reserved) return;
    // Commit the one-shot reservation before emitting anything, including across restarts.
    await storage.sync();
    console.warn('uif.access.service_identity_invalid:' + failures.join(','));
  }
  async fetch(request) {
    try {
      need(
        this.ctx.id.equals(this.env.AUTHORITY.idFromName(SINGLETON)),
        'wrong_authority_instance',
        403,
      );
      const path = new URL(request.url).pathname;
      const diagnose =
        path === '/v1/status' && request.method === 'GET'
          ? (failures) => this.logAccessFailureOnce(failures)
          : undefined;
      const peer = await verify(request, this.env, this.config, diagnose);
      let body = {};
      if (path === '/v1/status') need(request.method === 'GET', 'method_not_allowed', 405);
      else {
        need(
          request.method === 'POST' &&
            /^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') || ''),
          'json_post_required',
          415,
        );
        body = json(
          await readBytes(
            request.body,
            path === '/v1/linear/upload'
              ? 12 * 1024 * 1024
              : ['/v1/source/bind', '/v1/source/write'].includes(path)
                ? 768 * 1024
                : 256 * 1024,
          ),
        );
      }
      const value = await this.authority.handle(path, body, peer);
      return value instanceof Response ? value : reply(value);
    } catch (e) {
      return error(e);
    }
  }
}
