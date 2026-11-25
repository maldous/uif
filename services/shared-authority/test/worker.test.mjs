import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Miniflare } from 'miniflare';
import { canonical, digest, digestBytes, wire, models, day } from '../protocol.mjs';

// Real local workerd + SQLite DO. JWKS, quota and physical provider routing use isolated fixtures. All
// network egress is intercepted; no provider, Access or production calls occur.
test('workerd enforces signed Access app identity, issue claim CAS, and durable stop across restart', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'uif-authority-test-')),
    root = fileURLToPath(new URL('../', import.meta.url));
  const rsa = await crypto.subtle.generateKey(
    {
      name: 'RSASSA-PKCS1-v1_5',
      hash: 'SHA-256',
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
    },
    false,
    ['sign', 'verify'],
  );
  const jwk = {
    ...(await crypto.subtle.exportKey('jwk', rsa.publicKey)),
    kid: 'test-key',
    alg: 'RS256',
    use: 'sig',
  };
  const issuer = 'https://test-fixture.cloudflareaccess.com',
    aud = '4'.repeat(64),
    t = Math.floor(Date.now() / 1000);
  const roles = ['native', 'morgan', 'control', 'quota'],
    clients = roles.map((_, i) => String(i + 1).repeat(32) + '.access');
  const peers = Object.fromEntries(roles.map((r, i) => [clients[i], { role: r, session_id: r + '-s' }]));
  const route = {
    account_id: 'a'.repeat(32),
    model: '@cf/zai-org/glm-4.7-flash',
    gateway_id: 'fixture',
    credential_epoch_sha256: 'e'.repeat(64),
  };
  const projection = {
    route,
    utc_day: day(t),
    covered_through: t,
    valid_until: t + 180,
    upper: 0,
    gap_upper: 0,
    free_standard_no_fallback: true,
    completeness_verified: true,
  };
  let physical = 0;
  const newPath = 'docs/public-fixture.md',
    contents = 'bounded public fixture\n',
    toolId = 'fixture-create';
  const tool = {
    id: toolId,
    type: 'function',
    function: { name: 'create_file', arguments: JSON.stringify({ path: newPath, content: contents }) },
  };
  const c = {
    authority_id: 'authority',
    quota_authority_id: 'quota',
    deployment_sha256: 'a'.repeat(64),
    policy_sha256: 'b'.repeat(64),
    repository_id: '1',
    workspace_id: crypto.randomUUID(),
    project_id: crypto.randomUUID(),
    peers,
    route,
    quota_verification_sha256: 'd'.repeat(64),
    lease_seconds: 300,
  };
  const make = () =>
    new Miniflare({
      modules: true,
      scriptPath: join(root, 'worker.mjs'),
      modulesRoot: root,
      compatibilityDate: '2026-07-30',
      durableObjects: { AUTHORITY: { className: 'SharedAuthority', useSQLite: true } },
      durableObjectsPersist: dir,
      bindings: {
        AUTHORITY_CONFIG: JSON.stringify(c),
        ACCESS_ISSUER: issuer,
        ACCESS_AUD: aud,
        WORKERS_AI_TOKEN: 'worker-test',
      },
      serviceBindings: {
        QUOTA_VERIFIER: async (req) => {
          assert.equal(req.url, 'https://quota.internal/v1/observation/read');
          const { source_receipt_sha256 } = await req.json();
          return Response.json({
            binding_sha256: c.quota_verification_sha256,
            source_receipt_sha256,
            projection,
            coverage: 'entire-account-all-routes-from-utc-midnight',
            gap_evidence_sha256: '9'.repeat(64),
          });
        },
      },
      outboundService: async (req) => {
        if (req.url === 'https://api.cloudflare.com' + wire(route).path) {
          physical++;
          assert.equal(req.headers.get('authorization'), 'Bearer worker-test');
          return Response.json(
            {
              model: route.model,
              choices: [{ message: { tool_calls: [tool] } }],
              usage: { neurons: 1 },
            },
            { headers: { 'cf-aig-log-id': 'local-workerd-fixture' } },
          );
        }
        assert.equal(req.url, issuer + '/cdn-cgi/access/certs');
        return Response.json({ keys: [jwk] });
      },
    });
  let instance = make();
  try {
    const sign = async (role, extra = {}, header = { alg: 'RS256', typ: 'JWT', kid: jwk.kid }) => {
      const payload = {
        type: 'app',
        aud: [aud],
        exp: t + 600,
        iat: t - 1,
        iss: issuer,
        common_name: clients[roles.indexOf(role)],
        sub: '',
        ...extra,
      };
      const bytes = [header, payload]
        .map((x) => Buffer.from(JSON.stringify(x)).toString('base64url'))
        .join('.');
      return (
        bytes +
        '.' +
        Buffer.from(
          await crypto.subtle.sign('RSASSA-PKCS1-v1_5', rsa.privateKey, new TextEncoder().encode(bytes)),
        ).toString('base64url')
      );
    };
    const tokens = Object.fromEntries(await Promise.all(roles.map(async (r) => [r, await sign(r)])));
    const call = (jwt, path, body) =>
      instance.dispatchFetch('https://fixture.test/v1/' + path, {
        method: body ? 'POST' : 'GET',
        headers: { 'CF-Access-Jwt-Assertion': jwt, 'content-type': 'application/json' },
        body: body && JSON.stringify(body),
      });
    assert.equal(
      (
        await instance.dispatchFetch('https://fixture.test/v1/status', {
          headers: { 'x-role': 'control', 'cf-access-client-id': clients[2] },
        })
      ).status,
      401,
    );
    for (const audience of [aud, ['other-audience', aud]]) {
      const response = await call(await sign('native', { aud: audience }), 'status');
      assert.equal(response.status, 200);
      assert.equal((await response.json()).stopped, true);
    }
    for (const bad of [
      { sub: 'human' },
      { iss: 'https://elsewhere.test' },
      { type: 'org' },
      { iat: t + 10 },
      { aud: 'wrong' },
      { aud: aud + ' ' },
      { aud: ['wrong'] },
      { aud: [] },
      { aud: [aud, null] },
      { aud: undefined },
      { aud: { aud } },
      { exp: t - 1 },
      { nbf: t + 10 },
      { aud, common_name: 'unregistered.access' },
    ]) {
      const response = await call(await sign('native', bad), 'status');
      assert.equal(response.status, 403);
      assert.deepEqual(await response.json(), { error: 'access_service_identity_invalid' });
    }
    const badsig = tokens.native.split('.');
    badsig[2] = (badsig[2][0] === 'A' ? 'B' : 'A') + badsig[2].slice(1);
    assert.equal((await call(badsig.join('.'), 'status')).status, 401);
    assert.equal(
      (await call(await sign('native', {}, { alg: 'none', kid: jwk.kid, typ: 'JWT' }), 'status')).status,
      401,
    );
    let r = await call(tokens.native, 'status');
    assert.equal(r.status, 200);
    assert.equal((await r.json()).stopped, true);
    const ctl = { request_id: 'go', expected_coordinator_epoch: 1, stopped: false };
    assert.equal((await call(tokens.native, 'control', ctl)).status, 403);
    assert.equal((await call(tokens.control, 'control', ctl)).status, 200);
    const scope = {
      repository_id: c.repository_id,
      key: { workspace_id: c.workspace_id, project_id: c.project_id, issue_id: crypto.randomUUID() },
    };
    const source = {
      repository: 'maldous/offmenu',
      branch: 'develop',
      base: '1'.repeat(40),
      head: '2'.repeat(40),
      tree: '3'.repeat(40),
    };
    const grant = {
      scope,
      source,
      reconstruction_sha256: 'c'.repeat(64),
      valid_until: t + 500,
      attempt_ids: ['one'],
      sessions: ['native-s', 'morgan-s'],
      allow_provider: true,
      result_retention: {
        max_response_bytes: 65536,
        max_retained_results: 16,
        readers: ['native-s', 'morgan-s', 'control-s'],
        evidence_sha256: 'a'.repeat(64),
      },
      allow_comments: false,
      allow_uploads: false,
      review_of: {},
    };
    assert.equal(
      (await call(tokens.control, 'grant', { request_id: 'grant', previous_sha256: null, grant }))
        .status,
      200,
    );
    const claim = {
      request_id: 'claim',
      scope,
      source,
      expected_coordinator_epoch: 2,
      attempt_id: 'one',
      reconstruction_sha256: grant.reconstruction_sha256,
    };
    const race = await Promise.all(
      ['native', 'morgan'].map((role) => call(tokens[role], 'claim', claim)),
    );
    assert.deepEqual(race.map((x) => x.status).sort(), [200, 409]);
    const i = race.findIndex((x) => x.status === 200),
      jwt = tokens[['native', 'morgan'][i]],
      original = await race[i].json();
    const usage = await call(tokens.quota, 'usage/write', {
      request_id: 'telemetry',
      source_receipt_sha256: '1'.repeat(64),
      projection,
    });
    assert.equal(usage.status, 200);
    const quota = (await usage.json()).receipt_sha256,
      raw = Buffer.from(
        canonical({
          model: route.model,
          messages: [{ role: 'user', content: 'fixture' }],
          max_tokens: 2048,
          stream: false,
        }),
      );
    const request_sha256 = await digestBytes(raw),
      maximum_millineurons = models.get(route.model),
      end = t + 60;
    const operation = {
      fence: original.lease.fence,
      original_id: '8'.repeat(64),
      payload_sha256: await digest({
        kind: 'uif.workers-ai-free-send/1',
        route,
        attempt: 'one',
        request_sha256,
        quota,
        not_after: end,
        maximum_millineurons,
        ...wire(route),
      }),
    };
    const send = {
      intent: {
        operation,
        route,
        attempt_id: 'one',
        request_sha256,
        quota_receipt_sha256: quota,
        request_not_after: end,
        maximum_millineurons,
      },
      body_base64: raw.toString('base64'),
    };
    const first = await call(jwt, 'send', send);
    const sent = await first.text();
    assert.equal(first.status, 200, sent);
    assert.equal(physical, 1);
    const receipt = first.headers.get('uif-receipt-sha256');
    assert.match(receipt, /^[0-9a-f]{64}$/);
    assert.equal((await call(jwt, 'send', send)).status, 409);
    const open = { current: original.lease, attempt_id: 'one' };
    const bind = {
      ...open,
      request_id: 'public-source',
      public_source_receipt_sha256: '4'.repeat(64),
      recovery_readers: ['control-s'],
      paths: [
        {
          path: newPath,
          writable: true,
          mode: null,
          base_sha256: null,
          base_git_blob: null,
          base_base64: null,
        },
      ],
    };
    assert.equal((await call(tokens.control, 'source/bind', bind)).status, 200);
    const ref = {
      attempt_id: 'one',
      provider_request_id: operation.original_id,
      tool_call_index: 0,
      tool_call_id: toolId,
    };
    const mutation = {
      ...ref,
      path: newPath,
      expected_revision: 0,
      preimage_sha256: null,
      postimage_sha256: await digestBytes(Buffer.from(contents)),
    };
    const sourceOperation = {
      fence: original.lease.fence,
      original_id: await digest({ kind: 'uif.source-tool-original/1', ...ref }),
      payload_sha256: await digest({ kind: 'uif.native-source-write/1', ...mutation }),
    };
    const sourceInput = {
      operation: sourceOperation,
      ...mutation,
      body_base64: Buffer.from(contents).toString('base64'),
    };
    const edited = await call(jwt, 'source/write', sourceInput);
    const editReceipt = await edited.json();
    assert.equal(edited.status, 200, JSON.stringify(editReceipt));
    assert.deepEqual(await (await call(jwt, 'source/receipt', { ...open, ...ref })).json(), editReceipt);
    const candidate = await (await call(jwt, 'source/checkpoint', { ...open, revision: 1 })).json();
    assert.equal(candidate.candidate.files[0].body_base64, sourceInput.body_base64);

    assert.equal(
      (
        await call(tokens.control, 'control', {
          request_id: 'halt',
          expected_coordinator_epoch: 2,
          stopped: true,
        })
      ).status,
      200,
    );
    await instance.dispose();
    instance = make();
    assert.equal((await (await call(jwt, 'status')).json()).stopped, true);
    const read = {
      fence: original.lease.fence,
      attempt_id: 'one',
      revision: 1,
      expected_candidate_sha256: candidate.candidate_sha256,
    };
    assert.deepEqual(await (await call(tokens.control, 'source/original', read)).json(), {
      ...candidate,
      sealed: true,
    });
    assert.equal((await call(jwt, 'source/write', sourceInput)).status, 409);
    assert.deepEqual(await (await call(jwt, 'claim/observe', claim)).json(), original);
    const recovered = await call(jwt, 'provider/receipt', { operation });
    assert.equal(await recovered.text(), sent);
    assert.equal(recovered.headers.get('uif-receipt-sha256'), receipt);
    assert.equal(physical, 1);
    assert.equal(
      (await call(tokens[['morgan', 'native'][i]], 'provider/receipt', { operation })).status,
      403,
    );
    assert.equal(
      (await call(jwt, 'claim/read', { identifier: 'one', by_attempt: true, project: 'OffMenu' }))
        .status,
      409,
    );
  } finally {
    await instance.dispose();
    await rm(dir, { recursive: true, force: true });
  }
});
