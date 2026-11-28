import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { Authority } from '../authority.mjs';
import { canonical, digest, digestBytes, models, wire, day, identity } from '../protocol.mjs';

const hex = (n) => n.repeat(64),
  git = (n) => n.repeat(40);
const scope = {
  repository_id: 'repository-1',
  key: {
    workspace_id: crypto.randomUUID(),
    project_id: crypto.randomUUID(),
    issue_id: crypto.randomUUID(),
  },
};
const source = {
  repository: 'maldous/offmenu',
  branch: 'develop',
  base: git('1'),
  head: git('2'),
  tree: git('3'),
};
const route = {
  account_id: 'a'.repeat(32),
  model: '@cf/zai-org/glm-4.7-flash',
  gateway_id: 'unit-test',
  credential_epoch_sha256: hex('4'),
};
const start = 1795132800;
function fixture() {
  const db = new DatabaseSync(':memory:');
  const storage = {
    sql: {
      exec: (sql, ...args) =>
        db.prepare(sql).all(...args.map((x) => (x instanceof ArrayBuffer ? new Uint8Array(x) : x))),
    },
    transactionSync(fn) {
      db.exec('BEGIN');
      try {
        const value = fn();
        db.exec('COMMIT');
        return value;
      } catch (e) {
        db.exec('ROLLBACK');
        throw e;
      }
    },
    sync: async () => {},
  };
  const peers = Object.fromEntries(
    ['native', 'morgan', 'control', 'quota'].map((role) => [
      role,
      { role, session_id: role + '-session', expires_at: start + 86400 * 3 },
    ]),
  );
  const config = {
    ...scope.key,
    repository_id: scope.repository_id,
    peers,
    route,
    authority_id: 'realm',
    quota_authority_id: 'quota',
    quota_verification_sha256: hex('d'),
    deployment_sha256: hex('a'),
    policy_sha256: hex('b'),
    lease_seconds: 300,
    upload_hosts: ['storage.googleapis.com'],
  };
  let time = start;
  const calls = [];
  const ctx = {
    storage,
    peers,
    config,
    calls,
    setTime: (t) => {
      time = t;
    },
    clock: () => time,
    respond: async () =>
      Response.json(
        { model: route.model, choices: [{}], usage: { neurons: 0.007 } },
        { headers: { 'cf-aig-log-id': 'physical-id' } },
      ),
  };
  const options = {
    clock: () => time,
    provider: async (req) => {
      calls.push(req);
      return ctx.respond(req);
    },
    providerToken: 'fixture',
    reviewerProviderToken: 'independent-fixture',
    linearAuthorization: 'fixture',
    quotaVerifier: async () => ctx.observation,
  };
  ctx.restart = () => new Authority(storage, config, options);
  ctx.a = ctx.restart();
  ctx.call = (role, path, body, permit) => ctx.a.handle('/v1/' + path, body, peers[role], permit);
  ctx.resume = () =>
    ctx.call('control', 'control', {
      request_id: 'resume',
      expected_coordinator_epoch: 1,
      stopped: false,
    });
  ctx.addGrant = async (s = scope, attempt = 'attempt', until = time + 800, review_of = {},
                        fresh_assessment) => {
    const grant = {
      scope: s,
      source,
      sessions: [peers.native.session_id, peers.morgan.session_id],
      attempt_ids: [attempt, fresh_assessment?.report_binding ?
        attempt.slice(0,-'assess'.length) + 'investigate' : attempt + '-next'],
      reconstruction_sha256: hex('c'),
      valid_until: until,
      allow_comments: true,
      allow_uploads: true,
      review_of,
      allow_provider: true,
      result_retention: {
        max_response_bytes: 65536,
        max_retained_results: 16,
        readers: ['native-session', 'morgan-session', 'control-session'],
        evidence_sha256: 'a'.repeat(64),
      },
    };
    if (fresh_assessment !== undefined) grant.fresh_assessment = fresh_assessment;
    await ctx.call('control', 'grant', { request_id: 'g-' + attempt, grant, previous_sha256: null });
    return {
      scope: s,
      source,
      request_id: 'claim-' + attempt,
      attempt_id: attempt,
      expected_coordinator_epoch: 2,
      reconstruction_sha256: hex('c'),
    };
  };
  ctx.quota = async (upper = 0, selectedRoute = route) => {
    const route = selectedRoute;
    const projection = {
      route,
      utc_day: day(time),
      covered_through: time,
      valid_until: time + 300,
      upper,
      gap_upper: 0,
      free_standard_no_fallback: true,
      completeness_verified: true,
    };
    ctx.observation = {
      binding_sha256: config.quota_verification_sha256,
      source_receipt_sha256: hex('5'),
      projection,
      coverage: 'entire-account-all-routes-from-utc-midnight',
      gap_evidence_sha256: hex('f'),
      checkpoint: { sha256: hex('3'), opening_sha256: hex('4'), unresolved_upper: 0, opening_upper: 0,
        ...ctx.checkpointFixture },
    };
    return ctx.call('quota', 'usage/write', {
      request_id: 'usage-' + time + '-' + route.account_id,
      projection,
      source_receipt_sha256: hex('5'),
    });
  };
  ctx.request = async (
    lease,
    quota,
    { attempt = 'attempt', id = hex('9'), end = time + 60, selectedRoute, prompt = 'fixture' } = {},
  ) => {
    const route = selectedRoute || config.route;
    const bytes = Buffer.from(
      canonical({
        messages: [{ role: 'user', content: prompt }],
        model: route.model,
        max_tokens: 2048,
        stream: false,
      }),
    );
    const request_sha256 = await digestBytes(bytes),
      maximum_millineurons = models.get(route.model);
    const fingerprint = {
      kind: 'uif.workers-ai-free-send/1',
      route,
      attempt,
      request_sha256,
      quota: quota.receipt_sha256,
      not_after: end,
      maximum_millineurons,
      ...wire(route),
    };
    return {
      intent: {
        operation: { fence: lease.fence, original_id: id, payload_sha256: await digest(fingerprint) },
        route,
        attempt_id: attempt,
        request_sha256,
        quota_receipt_sha256: quota.receipt_sha256,
        request_not_after: end,
        maximum_millineurons,
      },
      body_base64: bytes.toString('base64'),
    };
  };
  return ctx;
}
async function admitted(f) {
  await f.resume();
  return (await f.call('native', 'claim', await f.addGrant())).lease;
}

test('competing peers share one immutable claim and replay returns the bound original', async () => {
  const f = fixture();
  await f.resume();
  const req = await f.addGrant();
  const results = await Promise.allSettled(['native', 'morgan'].map((p) => f.call(p, 'claim', req)));
  assert.equal(results.filter((r) => r.status === 'fulfilled').length, 1);
  const i = results.findIndex((r) => r.status === 'fulfilled'),
    role = ['native', 'morgan'][i],
    original = results[i].value;
  assert.deepEqual(await f.call(role, 'claim/observe', req), original);
  assert.deepEqual(await f.call(role, 'claim', req), original);
  const { attempt_id, ...typed } = req;
  assert.equal(
    original.request_sha256,
    await digest({
      kind: 'uif.shared-lease.claim/3',
      request: { ...typed, authority: identity(f.config, f.peers[role]) },
    }),
  );
  await assert.rejects(
    f.call(role, 'claim', { ...req, attempt_id: 'attempt-next' }),
    /original_request_changed/,
  );
  await assert.rejects(
    f.call('quota', 'claim/read', {
      project: 'OffMenu',
      identifier: scope.key.issue_id,
      by_attempt: false,
    }),
    /peer_role/,
  );
  await assert.rejects(
    f.call(role, 'claim', {
      ...req,
      request_id: 'other',
      scope: { ...scope, key: { ...scope.key, project_id: crypto.randomUUID() } },
    }),
    /unbound/,
  );
});

test('distinct peer claims survive scoped expiry and fence a same-issue race', async () => {
  const f = fixture();
  await f.resume();
  const other = () => ({ ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } });
  const nativeReq = await f.addGrant(scope, 'native-first', start + 60),
    morganReq = await f.addGrant(other(), 'morgan-second'),
    raceReq = await f.addGrant(other(), 'same-issue');
  const [native, morgan] = await Promise.all([
    f.call('native', 'claim', nativeReq),
    f.call('morgan', 'claim', morganReq),
  ]);
  assert.equal(native.lease.fence.owner_session, f.peers.native.session_id);
  assert.equal(morgan.lease.fence.owner_session, f.peers.morgan.session_id);
  assert.notEqual(native.lease.fence.lease_id, morgan.lease.fence.lease_id);
  const read = (role, req) => f.call(role, 'claim/read', {
    project: 'OffMenu', identifier: req.scope.key.issue_id, by_attempt: false,
  });
  assert.deepEqual((await read('native', nativeReq)).lease, native.lease);
  assert.deepEqual((await read('morgan', morganReq)).lease, morgan.lease);

  const roles = ['native', 'morgan'];
  const competing = roles.map((role) => ({ ...raceReq, request_id: 'same-issue-' + role }));
  const raced = await Promise.allSettled(roles.map((role, i) => f.call(role, 'claim', competing[i])));
  assert.equal(raced.filter((r) => r.status === 'fulfilled').length, 1);
  const winner = raced.findIndex((r) => r.status === 'fulfilled');
  assert.deepEqual(await f.call(roles[winner], 'claim/observe', competing[winner]), raced[winner].value);

  f.setTime(start + 61);
  f.a = f.restart();
  await assert.rejects(read('native', nativeReq), /expired|grant/);
  assert.deepEqual(await f.call('native', 'claim/observe', nativeReq), native);
  assert.deepEqual((await read('morgan', morganReq)).lease, morgan.lease);
  assert.deepEqual((await read(roles[winner], raceReq)).lease, raced[winner].value.lease);
  assert.equal((await f.call('morgan', 'status', {})).coordinator_epoch, 2);
  assert.equal(f.calls.length, 0);
});

test('durable stop survives reconstruction, original claim is observable, no recovered permission', async () => {
  const f = fixture();
  await f.resume();
  const req = await f.addGrant(),
    { lease } = await f.call('native', 'claim', req);
  await f.call('control', 'control', {
    request_id: 'stop',
    expected_coordinator_epoch: 2,
    stopped: true,
  });
  f.a = f.restart();
  assert.equal((await f.call('native', 'status', {})).stopped, true);
  assert.deepEqual((await f.call('native', 'claim/observe', req)).lease, lease);
  await assert.rejects(
    f.call('native', 'claim/read', { project: 'OffMenu', identifier: 'attempt', by_attempt: true }),
    /stopped/,
  );
});

test('handoff needs independent proof, fences old peer at the actual sender', async () => {
  const f = fixture(),
    lease = await admitted(f),
    quota = await f.quota(),
    send = await f.request(lease, quota);
  const h = {
    current: lease,
    successor_session: f.peers.morgan.session_id,
    expected_coordinator_epoch: 2,
    quiesced_sha256: hex('a'),
    reconstruction_sha256: hex('c'),
  };
  await assert.rejects(f.call('native', 'handoff', { ...h, request_id: 'handoff' }), /evidence_missing/);
  await f.call('control', 'attest', { request_id: 'attest', fact: { kind: 'handoff', ...h } });
  const moved = await f.call('native', 'handoff', { ...h, request_id: 'handoff' });
  assert.equal(moved.lease.fence.generation, 2);
  assert.notEqual(moved.lease.fence.lease_id, lease.fence.lease_id);
  await assert.rejects(f.call('native', 'send', send), /claim_fence_changed/);
  assert.equal(f.calls.length, 0);
  const completion = { fence: moved.lease.fence, accepted: true, receipt_sha256: hex('a') };
  await assert.rejects(
    f.call('morgan', 'release', { completion, request_id: 'rel' }),
    /evidence_missing/,
  );
  await f.call('control', 'attest', { request_id: 'end', fact: { kind: 'completion', completion } });
  assert.deepEqual(
    (await f.call('morgan', 'release', { completion, request_id: 'rel' })).completion,
    completion,
  );
});

test('stop between durable reservation and physical egress retains original proven no-send receipt', async () => {
  const f = fixture(),
    lease = await admitted(f),
    req = await f.request(lease, await f.quota());
  f.storage.sync = () =>
    f.call('control', 'control', { request_id: 'stop', expected_coordinator_epoch: 2, stopped: true });
  await assert.rejects(f.call('native', 'send', req), /stopped/);
  assert.equal(f.calls.length, 0);
  const r = await f.call('native', 'original', { operation: req.intent.operation });
  assert.equal(r.terminal.status, 'denied');
});

test('one physical send at fixed route, duplicate or changed payload cannot be sent', async () => {
  const f = fixture(),
    lease = await admitted(f),
    body = await f.request(lease, await f.quota());
  const resp = await f.call('native', 'send', body);
  assert.equal(resp.status, 200);
  assert.equal(resp.headers.get('cf-aig-log-id'), 'physical-id');
  const original = await f.call('native', 'original', { operation: body.intent.operation });
  assert.equal(original.terminal.status, 'verified');
  assert.equal(
    f.calls[0].url,
    `https://api.cloudflare.com/client/v4/accounts/${route.account_id}/ai/v1/chat/completions`,
  );
  assert.equal(f.calls[0].headers.get('cf-aig-max-attempts'), '1');
  assert.equal(f.calls[0].redirect, 'manual');
  await assert.rejects(f.call('native', 'send', body), /original_already_recorded/);
  await assert.rejects(
    f.call('native', 'send', { ...body, body_base64: Buffer.from('{}').toString('base64') }),
    /model_body_not_bound/,
  );
  assert.equal(f.calls.length, 1);
});

test('account charges strictly below threshold across two issues and reconstructed object', async () => {
  const f = fixture(),
    l1 = await admitted(f),
    s2 = { ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } };
  const l2 = (await f.call('native', 'claim', await f.addGrant(s2, 'other'))).lease,
    maximum = models.get(route.model);
  const q = await f.quota(7500000 - maximum * 2);
  const first = await f.request(l1, q),
    second = await f.request(l2, q, { id: hex('6'), attempt: 'other' });
  f.respond = async () => {
    throw Error('lost');
  };
  await assert.rejects(f.call('native', 'send', first), /external_effect_unknown/);
  f.a = f.restart();
  await assert.rejects(f.call('native', 'send', second), /headroom/);
  assert.equal(
    (await f.call('native', 'original', { operation: first.intent.operation })).state,
    'unknown',
  );
  assert.equal(f.calls.length, 1);
  await assert.rejects(f.call('native', 'send', first), /original_already_recorded|headroom/);
  await assert.rejects(
    f.call('native', 'claim/attempt', {
      request_id: 'new-stage',
      expected_attempt_id: 'attempt',
      current: l1,
      attempt_id: 'attempt-next',
    }),
    /uncertain_original/,
  );
  f.setTime(start + 86400 + 1);
  const freshScope = { ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } };
  const l3 = (await f.call('native', 'claim', await f.addGrant(freshScope, 'next-day'))).lease;
  const q2 = await f.quota(7500000 - maximum * 2),
    tomorrow = await f.request(l3, q2, { id: hex('7'), attempt: 'next-day' });
  await assert.rejects(f.call('native', 'send', tomorrow), /headroom/);
  assert.equal(f.calls.length, 1);
});

test('only exact, fresh, independently issued Free account evidence permits physical egress', async () => {
  const f = fixture(),
    l = await admitted(f),
    q = await f.quota(),
    body = await f.request(l, q);
  await assert.rejects(f.call('native', 'usage/write', {}), /role/);
  const bad = structuredClone(body);
  bad.intent.route.account_id = '0'.repeat(32);
  await assert.rejects(f.call('native', 'send', bad), /send_intent_not_bound/);
  f.setTime(start + 100);
  await assert.rejects(f.call('native', 'send', body), /headroom/);
  assert.equal(f.calls.length, 0);
});

test('Linear comment has deterministic destination identity, exact result readback and fail-closed state mutation', async () => {
  const f = fixture(),
    l = await admitted(f),
    text = 'original result',
    attempt_id = 'attempt';
  const op = {
    fence: l.fence,
    original_id: 'comment-1',
    payload_sha256: await digest({
      kind: 'uif.linear-comment/1',
      attempt_id,
      issue_id: scope.key.issue_id,
      body: text,
    }),
  };
  f.respond = async (req) => {
    assert.equal(req.url, 'https://api.linear.app/graphql');
    const { variables } = await req.json();
    return Response.json({
      data: {
        commentCreate: {
          success: true,
          comment: {
            id: variables.input.id,
            issue: { id: variables.input.issueId },
            body: variables.input.body,
          },
        },
      },
    });
  };
  const result = await f.call('native', 'linear/comment', { operation: op, attempt_id, body: text });
  assert.match(result.id, /^[0-9a-f-]{36}$/);
  await assert.rejects(f.call('native', 'linear/state', {}), /atomic_state_cas_unavailable/);
  await assert.rejects(f.call('native', 'publication', {}), /sole_git_publisher_required/);
  assert.equal(f.calls.length, 1);
});

test('quota submitter cannot replace original telemetry, roll back a same-day upper or bypass missing verifier', async () => {
  const f = fixture(),
    lease = await admitted(f),
    q = await f.quota(100);
  f.setTime(start + 1);
  await assert.rejects(f.quota(99), /usage_snapshot_not_monotonic/);
  const body = {
    request_id: 'unverified',
    source_receipt_sha256: hex('5'),
    projection: { ...f.observation.projection, upper: 900 },
  };
  await assert.rejects(f.call('quota', 'usage/write', body), /projection_unverified/);
  f.a.quotaVerifier = undefined;
  await assert.rejects(f.call('native', 'send', await f.request(lease, q)), /usage_receipt_not_current/);
  assert.equal(f.calls.length, 0);
});

test('checkpoint survives reconstruction, preserves opening and unknown debt, and does not rewrite an original', async () => {
  const f = fixture();
  const lease = await admitted(f);
  f.checkpointFixture = { unresolved_upper: 100, opening_upper: 50 };
  const result = await f.quota(500);
  const original = {
    request_id: 'usage-' + start + '-' + route.account_id, projection: f.observation.projection,
    source_receipt_sha256: f.observation.source_receipt_sha256,
  };
  f.a = f.restart();
  assert.equal((await f.call('native', 'usage/read', { route })).receipt_sha256, result.receipt_sha256);
  const verifier = f.a.quotaVerifier;
  f.a.quotaVerifier = undefined;
  assert.deepEqual(await f.call('quota', 'usage/write', original), result);
  await assert.rejects(f.call('quota', 'usage/write', {
    ...original, source_receipt_sha256: hex('a'),
  }), /original_request_changed/);
  f.a.quotaVerifier = verifier;
  f.setTime(start + 1);
  f.checkpointFixture = { opening_sha256: hex('d'), unresolved_upper: 100, opening_upper: 50 };
  await assert.rejects(f.quota(900), /quota_checkpoint_opening_changed/);
  f.checkpointFixture = { unresolved_upper: 99, opening_upper: 50 };
  await assert.rejects(f.quota(900), /quota_checkpoint_unresolved_liability_reduced/);
  f.a.put('DELETE FROM usage_checkpoints WHERE account=?', route.account_id);
  await assert.rejects(f.call('native', 'usage/read', { route }), /usage_unavailable/);
  await assert.rejects(f.call('native', 'send', await f.request(lease, result)), /usage_receipt_not_current/);
  assert.equal(f.calls.length, 0);
});

test('reported reservation overrun freezes account including unrelated issue and restart', async () => {
  const f = fixture(),
    lease = await admitted(f),
    q = await f.quota(),
    body = await f.request(lease, q);
  f.respond = async () =>
    Response.json(
      { model: route.model, choices: [{}], usage: { neurons: models.get(route.model) / 1000 + 1 } },
      { headers: { 'cf-aig-log-id': 'valid' } },
    );
  await assert.rejects(f.call('native', 'send', body), /maximum_breached/);
  f.a = f.restart();
  const scope2 = { ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } };
  const lease2 = (await f.call('native', 'claim', await f.addGrant(scope2, 'two'))).lease;
  await assert.rejects(
    f.call('native', 'send', await f.request(lease2, q, { id: hex('2'), attempt: 'two' })),
    /maximum_breached/,
  );
  assert.equal(f.calls.length, 1);
});

test('altering and restoring an issue grant cannot resurrect the original lease', async () => {
  const f = fixture(),
    lease = await admitted(f),
    q = await f.quota();
  const { body } = f.a.row('SELECT body FROM grants');
  const g = JSON.parse(body),
    restricted = { ...g, allow_comments: false };
  await f.call('control', 'grant', {
    request_id: 'restrict',
    previous_sha256: await digest(g),
    grant: restricted,
  });
  await f.call('control', 'grant', {
    request_id: 'restore',
    previous_sha256: await digest(restricted),
    grant: g,
  });
  await assert.rejects(f.call('native', 'send', await f.request(lease, q)), /owner_not_current/);
  assert.equal(f.calls.length, 0);
});

async function uploadInput(f, lease, id = 'upload-one') {
  const raw = Buffer.from('reviewed fixture'),
    attempt_id = 'attempt',
    filename = 'evidence.txt',
    content_type = 'text/plain';
  const payload_sha256 = await digest({
    kind: 'uif.linear-upload/1',
    attempt_id,
    issue_id: scope.key.issue_id,
    filename,
    content_type,
    size: raw.length,
    body_sha256: await digestBytes(raw),
  });
  return {
    operation: { fence: lease.fence, original_id: id, payload_sha256 },
    attempt_id,
    filename,
    content_type,
    body_base64: raw.toString('base64'),
  };
}
function presign(host = 'storage.googleapis.com') {
  return Response.json({
    data: {
      fileUpload: {
        success: true,
        uploadFile: {
          uploadUrl: `https://${host}/private?signature=fixture`,
          assetUrl: `https://uploads.linear.app/${scope.key.workspace_id}/asset`,
          headers: [],
        },
      },
    },
  });
}
test('upload fences both mutations, strips credentials, and recovers exact terminal result after lost ACK', async () => {
  const f = fixture(),
    l = await admitted(f),
    body = await uploadInput(f, l);
  f.respond = async (r) => {
    if (r.method === 'POST') return presign();
    assert.equal(r.method, 'PUT');
    assert.equal(r.headers.has('authorization'), false);
    assert.equal(r.headers.has('cache-control'), false);
    assert.equal(await r.text(), 'reviewed fixture');
    return new Response(null, { status: 201 });
  };
  const result = await f.call('native', 'linear/upload', body);
  f.a = f.restart();
  assert.deepEqual(await f.call('native', 'linear/receipt', { operation: body.operation }), result);
  assert.equal(
    (await f.call('native', 'original', { operation: body.operation })).terminal.receipt_sha256,
    result.receipt_sha256,
  );
  await assert.rejects(f.call('native', 'linear/upload', body), /original_already_recorded/);
  assert.equal(f.calls.length, 2);
});

test('revoke during upload allocation blocks PUT and retains original unknown', async () => {
  const f = fixture(),
    l = await admitted(f),
    body = await uploadInput(f, l);
  f.respond = async () => {
    await f.call('control', 'revoke', { request_id: 'freeze', fence: l.fence });
    return presign();
  };
  await assert.rejects(f.call('native', 'linear/upload', body), /owner_not_current/);
  assert.equal(f.calls.length, 1);
  assert.equal((await f.call('native', 'original', { operation: body.operation })).state, 'unknown');
});

test('unregistered upload target or public ACL cannot receive bytes', async () => {
  for (const mode of ['host', 'acl']) {
    const f = fixture(),
      l = await admitted(f),
      body = await uploadInput(f, l);
    f.respond = async () => {
      if (mode === 'host') return presign('example.invalid');
      const response = await presign().json();
      response.data.fileUpload.uploadFile.headers = [{ key: 'x-amz-acl', value: 'public-read' }];
      return Response.json(response);
    };
    await assert.rejects(
      f.call('native', 'linear/upload', body),
      /unbound_upload_destination|invalid_upload_headers/,
    );
    assert.equal(f.calls.length, 1);
  }
});

test('review requires a distinct approved account, original terminal author, exact finalized prompt and preserved initial liability', async () => {
  const f = fixture();
  const review = {
    ...route,
    account_id: 'ecc31e887c4ab56a99f0db62e9c7213a',
    credential_epoch_sha256: hex('7'),
  };
  f.config.reviewer_route = review;
  f.config.reviewer_opening_liability = {
    original_id: 'opening',
    millineurons: 2489344,
    receipt_sha256: hex('4'),
  };
  f.a = f.restart();
  await f.resume();
  const req = await f.addGrant(scope, 'attempt', start + 600, { 'attempt-next': 'attempt' });
  const { lease } = await f.call('native', 'claim', req);
  const authorQuota = await f.quota(),
    author = await f.request(lease, authorQuota);
  const prompt = 'finalized original immutable review invocation';
  const candidate = {
    kind: 'uif.native-review-candidate/1',
    issue_id: scope.key.issue_id,
    scope_sha256: hex('b'),
    author_attempt_id: 'attempt',
    review_attempt_id: 'attempt-next',
    source,
    candidate_tree: git('f'),
    review_prompt_sha256: await digestBytes(Buffer.from('prepared prompt')),
  };
  const bind = {
    request_id: 'review-bind',
    current: lease,
    candidate,
    author_operation: author.intent.operation,
    author_receipt_sha256: hex('8'),
    checkpoint_receipt_sha256: hex('d'),
    send_prompt_sha256: await digestBytes(Buffer.from(prompt)),
  };
  await assert.rejects(f.call('control', 'review/bind', bind), /original_author_not_verified/);
  await f.call('native', 'send', author);
  bind.author_receipt_sha256 = (
    await f.call('native', 'original', { operation: author.intent.operation })
  ).terminal.receipt_sha256;
  await f.call('control', 'review/bind', bind);
  const next = {
    request_id: 'advance',
    current: lease,
    expected_attempt_id: 'attempt',
    attempt_id: 'attempt-next',
  };
  await f.call('native', 'claim/attempt', next);
  await assert.rejects(
    f.call('native', 'claim/attempt', { ...next, request_id: 'late' }),
    /attempt_preimage_changed/,
  );
  const read = await f.call('native', 'review/read', {
    current: lease,
    review_attempt_id: 'attempt-next',
    author_attempt_id: 'attempt',
  });
  assert.equal(read.candidate_sha256, await digest(candidate));
  await assert.rejects(
    f.call(
      'native',
      'send',
      await f.request(lease, authorQuota, { id: hex('3'), attempt: 'attempt-next', prompt }),
    ),
    /account_route_changed/,
  );
  const q = await f.quota(10, review);
  await assert.rejects(
    f.call(
      'native',
      'send',
      await f.request(lease, q, { id: hex('1'), attempt: 'attempt-next', selectedRoute: review }),
    ),
    /review_candidate_unbound/,
  );
  const good = await f.request(lease, q, {
    id: hex('2'),
    attempt: 'attempt-next',
    selectedRoute: review,
    prompt,
  });
  await f.call('native', 'send', good);
  assert.equal(f.calls.length, 2);
  assert.equal(
    f.calls[1].url,
    `https://api.cloudflare.com/client/v4/accounts/${review.account_id}/ai/v1/chat/completions`,
  );
  assert.equal(f.calls[1].headers.get('authorization'), 'Bearer independent-fixture');
  f.setTime(start + 1);
  const threshold = await f.quota(7500000 - 2489344 - models.get(review.model), review);
  f.a = f.restart();
  await assert.rejects(
    f.call(
      'native',
      'send',
      await f.request(lease, threshold, {
        id: hex('3'),
        attempt: 'attempt-next',
        selectedRoute: review,
        prompt,
      }),
    ),
    /headroom/,
  );
  assert.equal(f.calls.length, 2);
});

test('released issue reopens only on exact terminal generation and independently registered fresh reconstruction', async () => {
  const f = fixture(),
    old = await admitted(f),
    completion = { fence: old.fence, accepted: true, receipt_sha256: hex('1') };
  await f.call('control', 'attest', { request_id: 'proof', fact: { kind: 'completion', completion } });
  assert.deepEqual(await f.call('native', 'completion/read', { current: old }), completion);
  await f.call('native', 'release', { request_id: 'release', completion });
  const g = JSON.parse(f.a.row('SELECT body FROM grants').body),
    changed = {
      ...g,
      source: { ...source, tree: git('9') },
      reconstruction_sha256: hex('7'),
      attempt_ids: ['new-scope'],
    };
  await f.call('control', 'grant', {
    request_id: 'new-grant',
    grant: changed,
    previous_sha256: await digest(g),
  });
  const input = {
    request_id: 'new-claim',
    previous: old,
    scope,
    source: changed.source,
    reconstruction_sha256: hex('7'),
    expected_coordinator_epoch: 2,
    attempt_id: 'new-scope',
    previous_completion_sha256: hex('1'),
  };
  await assert.rejects(
    f.call('morgan', 'reclaim', { ...input, previous_completion_sha256: hex('0') }),
    /prior_completion/,
  );
  const next = await f.call('morgan', 'reclaim', input);
  assert.equal(next.lease.fence.generation, 2);
  assert.equal(next.lease.fence.owner_session, 'morgan-session');
  assert.deepEqual(await f.call('morgan', 'reclaim', input), next);
  await assert.rejects(
    f.call('native', 'reclaim', { ...input, request_id: 'competing' }),
    /prior_claim_not_released/,
  );
  const generations = [
    ...f.storage.sql.exec('SELECT generation FROM claim_generations ORDER BY generation'),
  ];
  assert.deepEqual(
    generations.map((r) => r.generation),
    [1, 2],
  );
});

test('retention requires issue-specific consent and explicit consumer acknowledgment before pruning', async () => {
  const f = fixture(),
    lease = await admitted(f),
    request = await f.request(lease, await f.quota());
  const response = await f.call('native', 'send', request),
    raw = new Uint8Array(await response.arrayBuffer());
  const input = {
    operation: request.intent.operation,
    receipt_sha256: response.headers.get('uif-receipt-sha256'),
    body_sha256: await digestBytes(raw),
  };
  await assert.rejects(
    f.call('control', 'provider/prune', { ...input, request_id: 'prune' }),
    /consumer_ack_required/,
  );
  await assert.rejects(
    f.call('morgan', 'provider/ack', { ...input, request_id: 'ack' }),
    /original_owner_required/,
  );
  await f.call('native', 'provider/ack', { ...input, request_id: 'ack' });
  await f.call('control', 'provider/prune', { ...input, request_id: 'prune' });
  await assert.rejects(
    f.call('native', 'provider/receipt', { operation: input.operation }),
    /unresolved/,
  );
  assert.equal(
    (await f.call('native', 'original', { operation: input.operation })).terminal.receipt_sha256,
    input.receipt_sha256,
  );
  assert.equal(f.calls.length, 1);
  const other = fixture();
  await other.resume();
  const claim = await other.addGrant();
  const grant = JSON.parse(other.a.row('SELECT body FROM grants').body);
  const denied = { ...grant };
  delete denied.result_retention;
  await other.call('control', 'grant', {
    grant: denied,
    previous_sha256: await digest(grant),
    request_id: 'deny-retention',
  });
  const l = (await other.call('native', 'claim', claim)).lease;
  await assert.rejects(
    other.call('native', 'send', await other.request(l, await other.quota())),
    /retention_unbound/,
  );
  assert.equal(other.calls.length, 0);
});

test('a new deployment cannot inherit startup, unconsumed grants, control replays or original effect authority', async () => {
  const f = fixture();
  await f.resume();
  const initial = await f.addGrant();
  const orig = await f.call('native', 'claim', initial);
  const secondScope = { ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } },
    other = await f.addGrant(secondScope, 'unused');
  const startup = {
    request_id: 'startup',
    unit: 'unit',
    stage: 'dispatch',
    session_id: f.peers.native.session_id,
    coordinator_epoch: 2,
    valid_until: start + 300,
    evidence_sha256: hex('3'),
  };
  await f.call('control', 'startup/bind', startup);
  const read = { unit: 'unit', stage: 'dispatch', nonce: 'challenge' };
  assert.equal((await f.call('native', 'startup/read', read)).deployment_sha256, hex('a'));
  const send = await f.request(orig.lease, await f.quota());
  const operation = send.intent.operation;
  f.respond = async () => {
    throw Error('lost original response');
  };
  await assert.rejects(f.call('native', 'send', send), /external_effect_unknown/);
  const prior = (await f.call('native', 'status', {})).installation_sha256;
  f.config.deployment_sha256 = hex('f');
  f.a = f.restart();
  let status = await f.call('control', 'status', {});
  assert.equal(status.stopped, true);
  assert.equal(status.commissioned_sha256, prior);
  assert.notEqual(status.installation_sha256, prior);
  await assert.rejects(f.call('native', 'startup/read', read), /recommission/);
  await assert.rejects(f.call('native', 'claim', other), /recommission/);
  await f.call('control', 'commission', {
    request_id: 'upgrade',
    previous_realm: prior,
    new_realm: status.installation_sha256,
    expected_coordinator_epoch: 2,
    reconstruction_sha256: hex('9'),
  });
  assert.equal((await f.call('native', 'original', { operation })).state, 'unknown');
  const original = { operation, status: 'failed', destination_receipt_sha256: hex('6') };
  await assert.rejects(
    f.call('control', 'reconcile', { request_id: 'terminal', ...original }),
    /evidence_missing/,
  );
  await f.call('control', 'attest', {
    request_id: 'original-fact',
    fact: { kind: 'original-terminal', ...original },
  });
  await f.call('control', 'reconcile', { request_id: 'terminal', ...original });
  assert.equal(
    (await f.call('native', 'original', { operation })).terminal.receipt_sha256,
    original.destination_receipt_sha256,
  );
  await assert.rejects(f.call('native', 'send', send), /operation_unbound/);
  assert.equal(f.calls.length, 1);
  await assert.rejects(f.resume(), /control_epoch_changed/);
  await f.call('control', 'control', {
    request_id: 'resume-v2',
    expected_coordinator_epoch: 3,
    stopped: false,
  });
  await assert.rejects(f.call('native', 'startup/read', read), /startup_not_bound/);
  await assert.rejects(
    f.call('native', 'claim', { ...other, expected_coordinator_epoch: 4 }),
    /issue_grant_missing/,
  );
  assert.deepEqual(
    await f.call('native', 'transition/original', {
      session_id: f.peers.native.session_id,
      request_id: initial.request_id,
      installation_sha256: prior,
    }),
    orig,
  );
});

const b64 = (text) => Buffer.from(text).toString('base64');
async function sourceMember(path, text, writable = true) {
  const bytes = Buffer.from(text ?? '');
  const blob = await crypto.subtle.digest(
    'SHA-1',
    Buffer.concat([Buffer.from('blob ' + bytes.length + '\0'), bytes]),
  );
  return {
    path,
    writable,
    mode: text === null ? null : '100644',
    base_sha256: text === null ? null : await digestBytes(bytes),
    base_git_blob: text === null ? null : Buffer.from(blob).toString('hex'),
    base_base64: text === null ? null : b64(text),
  };
}
async function overlayFixture(base = 'old\n') {
  const f = fixture();
  await f.resume();
  const claim = await f.addGrant(scope, 'attempt', start + 600, { 'attempt-next': 'attempt' });
  const { lease } = await f.call('native', 'claim', claim);
  const open = { current: lease, attempt_id: 'attempt' },
    path = 'src/menu.js',
    added = 'docs/new.md';
  const bind = {
    ...open,
    request_id: 'public-src',
    public_source_receipt_sha256: hex('3'),
    paths: [
      await sourceMember(path, base),
      await sourceMember(added, null),
      await sourceMember('docs/existing.md', 'reference', false),
    ],
  };
  const calls = [
    {
      id: 'original-edit',
      type: 'function',
      function: {
        name: 'replace_text',
        arguments: JSON.stringify({ path, old: 'old', replacement: 'new' }),
      },
    },
    {
      id: 'original-create',
      type: 'function',
      function: { name: 'create_file', arguments: JSON.stringify({ path: added, content: 'é\n' }) },
    },
    {
      id: 'other-edit',
      type: 'function',
      function: {
        name: 'replace_text',
        arguments: JSON.stringify({ path, old: 'new', replacement: 'newer' }),
      },
    },
    {
      id: 'readonly-edit',
      type: 'function',
      function: {
        name: 'replace_text',
        arguments: JSON.stringify({ path: 'docs/existing.md', old: 'reference', replacement: 'change' }),
      },
    },
  ];
  f.respond = async () =>
    Response.json(
      { model: route.model, choices: [{ message: { tool_calls: calls } }], usage: { neurons: 0.007 } },
      { headers: { 'cf-aig-log-id': 'source-fixture' } },
    );
  const id = await digestBytes(Buffer.from('attempt:1'));
  const author = await f.request(lease, await f.quota(), { id });
  const send = await f.call('native', 'send', author);
  const authorReceipt = send.headers.get('uif-receipt-sha256');
  const input = async (index, revision, before, after) => {
    const tool = calls[index];
    const ref = {
      attempt_id: 'attempt',
      provider_request_id: id,
      tool_call_index: index,
      tool_call_id: tool.id,
    };
    const data = {
      ...ref,
      path: JSON.parse(tool.function.arguments).path,
      expected_revision: revision,
      preimage_sha256: before === null ? null : await digestBytes(Buffer.from(before)),
      postimage_sha256: await digestBytes(Buffer.from(after)),
    };
    return {
      operation: {
        fence: lease.fence,
        original_id: await digest({ kind: 'uif.source-tool-original/1', ...ref }),
        payload_sha256: await digest({ kind: 'uif.native-source-write/1', ...data }),
      },
      ...data,
      body_base64: b64(after),
    };
  };
  return { f, lease, open, path, added, bind, calls, input, id, author, authorReceipt };
}
test('bounded source admission verifies original public bytes, safe regular members, explicit absence and authority', async () => {
  const { f, bind, open, path, added } = await overlayFixture();
  await assert.rejects(f.call('native', 'source/bind', bind), /control_required/);
  for (const bad of [
    '../unsafe',
    'a//b',
    '/abs',
    '.git/refs',
    'data/.STOKOWSKI/run',
    'bad\\file',
    'CON',
    'bad.',
  ]) {
    await assert.rejects(
      f.call('control', 'source/bind', { ...bind, paths: [{ ...bind.paths[0], path: bad }] }),
      /invalid_source_path/,
    );
  }
  for (const names of [
    ['src', 'src/file'],
    ['src/file', 'SRC/other'],
    ['doc', 'DOC'],
  ]) {
    await assert.rejects(
      f.call('control', 'source/bind', {
        ...bind,
        paths: names.map((path) => ({ ...bind.paths[0], path })),
      }),
      /ambiguous_source_path/,
    );
  }
  for (const fields of [
    { mode: '120000' },
    { base_git_blob: git('0') },
    { base_sha256: hex('0') },
    { base_base64: b64('\0bad') },
    { base_base64: '/w==' },
  ]) {
    await assert.rejects(
      f.call('control', 'source/bind', { ...bind, paths: [{ ...bind.paths[0], ...fields }] }),
      /regular_|pin_mismatch/,
    );
  }
  await f.call('control', 'source/bind', bind);
  await assert.rejects(
    f.call('control', 'source/bind', { ...bind, request_id: 'rebind' }),
    /immutable_source_binding/,
  );
  assert.deepEqual((await f.call('native', 'source/list', open)).writable_paths, [added, path]);
  assert.equal((await f.call('native', 'source/read', { ...open, path: added })).sha256, null);
  await assert.rejects(f.call('morgan', 'source/read', { ...open, path }), /owner_not_current/);
  await assert.rejects(f.call('native', 'source/read', { ...open, path: 'other' }), /not_granted/);
  await assert.rejects(f.call('native', 'source/checkpoint', { ...open, revision: 0 }), /no_delta/);
});
test('source edits join the original model instruction, CAS the DO bytes, survive restart and bind review to the immutable delta', async () => {
  const large = 'old\n' + 'x'.repeat(118300),
    post = 'new\n' + large.slice(4);
  const { f, open, lease, bind, input, id, path, added, calls, author, authorReceipt } =
    await overlayFixture(large);
  await f.call('control', 'source/bind', bind);
  const write = (b) => f.call('native', 'source/write', b),
    checkpoint = (revision) => f.call('native', 'source/checkpoint', { ...open, revision });
  await assert.rejects(write(await input(0, 0, large, 'invented')), /original_replace_not_matched/);
  await assert.rejects(write(await input(3, 0, 'reference', 'change')), /revision_or_preimage_changed/);
  const edit = await input(0, 0, large, post);
  const create = await input(1, 0, null, 'é\n');
  const race = await Promise.allSettled([write(edit), write(create)]);
  assert.equal(race.filter((v) => v.status === 'fulfilled').length, 1);
  const editFirst = race[0].status === 'fulfilled';
  const accepted = editFirst ? edit : create;
  assert.equal((await write(accepted)).revision, 1);
  const next = editFirst ? await input(1, 1, null, 'é\n') : await input(0, 1, large, post);
  const result = await write(next);
  assert.equal(result.revision, 2);
  f.a = f.restart();
  const read = await f.call('native', 'source/receipt', {
    ...open,
    provider_request_id: id,
    tool_call_index: next.tool_call_index,
    tool_call_id: next.tool_call_id,
  });
  assert.deepEqual(read, result);
  assert.equal(
    result.arguments_sha256,
    await digest(JSON.parse(calls[next.tool_call_index].function.arguments)),
  );
  assert.equal((await f.call('native', 'source/read', { ...open, path })).body_base64, b64(post));
  await assert.rejects(checkpoint(1), /revision_changed/);
  const sealed = await checkpoint(2);
  assert.deepEqual(
    sealed.candidate.files.map((x) => x.path),
    [added, path],
  );
  assert.equal(sealed.candidate.files[0].preimage_sha256, null);
  assert.equal(sealed.candidate.files[1].base_base64, b64(large));
  assert.equal(sealed.candidate.files[1].body_base64, b64(post));
  assert.equal(sealed.candidate_sha256, await digest(sealed.candidate));
  await assert.rejects(
    write(await input(2, 2, post, 'newer\n' + large.slice(4))),
    /revision_or_preimage_changed/,
  );
  const candidate = {
    kind: 'uif.native-review-candidate/1',
    issue_id: scope.key.issue_id,
    scope_sha256: hex('c'),
    author_attempt_id: 'attempt',
    review_attempt_id: 'attempt-next',
    source,
    candidate_tree: null,
    review_prompt_sha256: hex('8'),
  };
  const review = {
    request_id: 'bind-source-review',
    current: lease,
    author_operation: author.intent.operation,
    author_receipt_sha256: authorReceipt,
    checkpoint_receipt_sha256: hex('7'),
    send_prompt_sha256: hex('8'),
  };
  for (const c of [
    candidate,
    { ...candidate, source_candidate_sha256: hex('1') },
    { ...candidate, source_candidate_sha256: sealed.candidate_sha256, candidate_tree: git('3') },
  ]) {
    await assert.rejects(
      f.call('control', 'review/bind', { ...review, candidate: c }),
      /frozen_source_candidate_required/,
    );
  }
  await f.call('control', 'review/bind', {
    ...review,
    candidate: { ...candidate, source_candidate_sha256: sealed.candidate_sha256 },
  });
  await f.call('native', 'claim/attempt', {
    request_id: 'review-sealed',
    current: lease,
    expected_attempt_id: 'attempt',
    attempt_id: 'attempt-next',
  });
  assert.deepEqual(await checkpoint(2), sealed);
  await assert.rejects(write(edit), /source_attempt_not_current/);
  assert.equal(f.calls.length, 1);
});
test('stop while a source-write payload hash is awaited prevents the physical edit and receipt', async (t) => {
  const { f, open, bind, input } = await overlayFixture();
  await f.call('control', 'source/bind', bind);
  const body = await input(0, 0, 'old\n', 'new\n');
  const entered = Promise.withResolvers(),
    release = Promise.withResolvers();
  const original = crypto.subtle.digest.bind(crypto.subtle);
  t.mock.method(crypto.subtle, 'digest', async (kind, bytes) => {
    if (Buffer.from(bytes).equals(Buffer.from('new\n'))) {
      entered.resolve();
      await release.promise;
    }
    return original(kind, bytes);
  });
  const pending = f.call('native', 'source/write', body);
  await entered.promise;
  await f.call('control', 'control', {
    request_id: 'stop-during-original',
    expected_coordinator_epoch: 2,
    stopped: true,
  });
  release.resolve();
  await assert.rejects(pending, /stopped/);
  assert.equal(
    f.a.row('SELECT revision FROM source_attempts WHERE attempt=?', open.attempt_id).revision,
    0,
  );
  assert.equal(f.a.row('SELECT id FROM effects WHERE id=?', body.operation.original_id), undefined);
});

test('named recovery custodian reopens only exact retained source after stop and recommission, without reissuing a lease', async () => {
  const { f, open, bind, input, lease } = await overlayFixture();
  await f.call('control', 'source/bind', { ...bind, recovery_readers: ['control-session'] });
  await f.call('native', 'source/write', await input(0, 0, 'old\n', 'new\n'));
  const known = {
    fence: lease.fence,
    attempt_id: 'attempt',
    revision: 1,
    expected_candidate_sha256: null,
  };
  const original = async (body) => f.call('control', 'source/original', body);
  const first = await original(known);
  assert.equal(first.sealed, false);
  await f.call('native', 'source/write', await input(1, 1, null, 'é\n'));
  const sealed = await f.call('native', 'source/checkpoint', { ...open, revision: 2 });
  await f.call('control', 'control', {
    request_id: 'stop',
    expected_coordinator_epoch: 2,
    stopped: true,
  });
  await assert.rejects(f.call('native', 'source/checkpoint', { ...open, revision: 2 }), /stopped/);
  const before = f.a.realm;
  f.config.deployment_sha256 = hex('6');
  f.a = f.restart();
  const desired = await f.a.realmReady;
  await f.call('control', 'commission', {
    request_id: 'replacement',
    previous_realm: before,
    new_realm: desired,
    expected_coordinator_epoch: 3,
    reconstruction_sha256: hex('7'),
  });
  assert.deepEqual(await original({ ...known, revision: null }), {
    revision: 2,
    sealed_sha256: sealed.candidate_sha256,
  });
  assert.deepEqual(
    await original({ ...known, expected_candidate_sha256: first.candidate_sha256 }),
    first,
  );
  assert.deepEqual(
    await original({ ...known, revision: 2, expected_candidate_sha256: sealed.candidate_sha256 }),
    { ...sealed, sealed: true },
  );
  await assert.rejects(original({ ...known, expected_candidate_sha256: hex('7') }), /digest_changed/);
  await assert.rejects(f.call('morgan', 'source/original', known), /reader_not_granted/);
  await assert.rejects(
    original({ ...known, fence: { ...lease.fence, lease_id: 'other' } }),
    /binding_not_matched/,
  );
  await f.call('control', 'source/recovery/revoke', {
    request_id: 'end-recovery',
    fence: lease.fence,
    attempt_id: 'attempt',
    session_id: 'control-session',
  });
  await assert.rejects(original(known), /reader_not_granted/);
});

test('existing atomic journal reserves a synthetic vendor original before fake egress and retains unknown', async () => {
  const x = fixture();
  const key = 'fixture-groq-account';
  const operation = {
    original_id: hex('6'),
    payload_sha256: hex('7'),
    fence: { scope },
  };
  const req = new Request('https://fixture.invalid/api');
  const qualifies = (include) => {
    assert.equal(x.a.accountReservationDebt(key, start, include ? operation.original_id : ''), 0);
  };
  x.respond = async () => {
    assert.equal(x.a.accountReservationDebt(key, start), 20);
    throw new Error('fixture uncertain reply');
  };
  await assert.rejects(x.a.physical(operation, 'fixture-attempt', key, 20, qualifies, req),
    /original_external_effect_unknown/);
  assert.equal(x.a.accountReservationDebt(key, start + 86400), 20);
  assert.equal(x.restart().accountReservationDebt(key, start + 86400), 20);
  await assert.rejects(x.a.physical(operation, 'fixture-attempt', key, 20, () => {}, req),
    /original_already_recorded/);
  assert.equal(x.calls.length, 1);
});

test('disabled Gemma preparation reserves one exact original before fake upstream', async () => {
  const x = fixture();
  await x.resume();
  const lease = (await x.call('native', 'claim', await x.addGrant())).lease;
  const project = hex('6'), attempt = 'attempt', deadline = start + 60;
  const prompt = 'synthetic fixture', credential = 'fixture-nonsecret';
  const model = 'gemma-4-31b-it';
  const payload = canonical({
    contents: [{ role: 'user', parts: [{ text: prompt }] }],
    generationConfig: { maxOutputTokens: 256, candidateCount: 1 },
  });
  const operation = { fence: lease.fence, original_id: hex('7'), payload_sha256: await digest({
    schema: 'uif.gemma-prepared-public-call/1', project, model, attempt, deadline, payload,
  }) };
  const evidence = { publicDataOnly: true, noPaidRoute: true, coveredThrough: start,
    validUntil: start + 100, upperUsed: 1, limit: 2 };
  const params = { operation, attempt, project, deadline, prompt, credential,
    readQuote: () => evidence, verifySettlement: () => null };
  await assert.rejects(x.a.dispatchGemmaPrepared({ ...params, readQuote: () => null }, x.peers.native),
    /gemma_owner_quote_unavailable/);
  await assert.rejects(x.a.dispatchGemmaPrepared({ ...params, prompt: 'different' }, x.peers.native),
    /gemma_payload_changed/);
  assert.equal(x.calls.length, 0);
  x.respond = async (req) => {
    assert.equal(x.a.accountReservationDebt('gemma-project:' + project, start), 1);
    assert.equal(x.calls[0].url,
      'https://generativelanguage.googleapis.com/v1beta/models/gemma-4-31b-it:generateContent');
    assert.deepEqual(await x.calls[0].json(), JSON.parse(payload));
    throw new Error('synthetic upstream uncertainty');
  };
  await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native),
    /original_external_effect_unknown/);
  assert.equal(x.restart().accountReservationDebt('gemma-project:' + project, start + 86400), 1);
  await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native), /gemma_window_exhausted|original_already_recorded/);
  assert.equal(x.calls.length, 1);
});

test('disabled included Responses pins free grant and reserves once on the shared journal', async () => {
  const x = fixture();
  await x.resume();
  const lease = (await x.call('native', 'claim', await x.addGrant())).lease;
  const organization = 'fixture-linked-org', project = 'fixture-project', credentialEpoch = hex('6');
  const input = 'synthetic test', deadline = start + 60, attempt = 'attempt', maximumMicrousd = 100;
  const model = 'gpt-6.2-sol', intent = 'new', credential = 'fixture-nonsecret';
  const payload = canonical({ model, input, max_output_tokens: 256, store: false });
  const binding = await digest({ schema: 'uif.included-responses-prepared/1', organization, project,
    credentialEpoch, attempt, deadline, maximumMicrousd, intent, payload });
  const operation = { fence: lease.fence, original_id: hex('7'), payload_sha256: binding };
  const proof = { binding, organization, project, credentialEpoch, model, includedOnly: true,
    hardNoOverage: true, purchasedCreditsAbsent: true, autoRechargeDisabled: true, completeBinding: true,
    observedAt: start, coveredThrough: start, validUntil: start + 100, expiresAt: start + 200,
    totalMicrousd: 1000, balanceLowerMicrousd: 500, requestUpperMicrousd: 100, unsettledUpperMicrousd: 0 };
  const args = { operation, organization, project, credentialEpoch, attempt, deadline, maximumMicrousd,
    input, optIn: true, intent, credential, readGrant: () => proof };
  const call = (changes = {}) => x.a.dispatchIncludedResponsesPrepared({ ...args, ...changes }, x.peers.native);
  for (const altered of [null, { ...proof, purchasedCreditsAbsent: false },
    { ...proof, autoRechargeDisabled: false }, { ...proof, project: 'wrong' }]) {
    await assert.rejects(call({ readGrant: () => altered }), /responses_authenticated_grant_unavailable/);
  }
  await assert.rejects(call({ optIn: false }), /responses_preparation_unbound/);
  await assert.rejects(call({ input: 'changed request' }), /responses_payload_changed/);
  await assert.rejects(call({ readGrant: () => ({ ...proof, balanceLowerMicrousd: 250 }) }), /responses_included_cutoff/);
  assert.equal(x.calls.length, 0);
  const account = 'openai-responses-org:' + await digest({ organization });
  x.respond = async () => {
    assert.equal(x.a.accountReservationDebt(account, start), 100);
    const req = x.calls[0];
    assert.equal(req.url, 'https://api.openai.com/v1/responses');
    assert.equal(req.headers.get('OpenAI-Organization'), organization);
    assert.deepEqual(await req.json(), JSON.parse(payload));
    throw new Error('synthetic uncertain reply');
  };
  await assert.rejects(call(), /original_external_effect_unknown/);
  assert.equal(x.restart().accountReservationDebt(account, start + 86400), 100);
  await assert.rejects(call(), /original_already_recorded/);
  assert.equal(x.calls.length, 1);
});

test('Responses exact original settles through authenticated, scope-bound receipt only', async () => {
  const x = fixture();
  await x.resume();
  const lease = (await x.call('native', 'claim', await x.addGrant())).lease;
  const organization = 'fixture-org', project = 'fixture-project', credentialEpoch = hex('6');
  const input = 'synthetic fixture', deadline = start + 60, attempt = 'attempt', maximumMicrousd = 100;
  const model = 'gpt-6.2-sol', intent = 'new', credential = 'fixture-only';
  const payload = canonical({ model, input, max_output_tokens: 256, store: false });
  const binding = await digest({ schema: 'uif.included-responses-prepared/1', organization, project,
    credentialEpoch, attempt, deadline, maximumMicrousd, intent, payload });
  const operation = { fence: lease.fence, original_id: hex('7'), payload_sha256: binding };
  const proof = { binding, organization, project, credentialEpoch, model, includedOnly: true,
    hardNoOverage: true, purchasedCreditsAbsent: true, autoRechargeDisabled: true, completeBinding: true,
    observedAt: start, coveredThrough: start, validUntil: start + 100, expiresAt: start + 200,
    totalMicrousd: 1000, balanceLowerMicrousd: 500, requestUpperMicrousd: 100, unsettledUpperMicrousd: 0 };
  const args = { operation, organization, project, credentialEpoch, attempt, deadline, maximumMicrousd,
    input, optIn: true, intent, credential, readGrant: () => proof };
  const account = 'openai-responses-org:' + await digest({ organization });
  x.respond = async () => Response.json({ id: 'resp_exact', model, status: 'completed' },
    { headers: {'x-request-id':'req_exact'} });
  const res = await x.a.dispatchIncludedResponsesPrepared({ ...args, verifySettlement: async (subject) => {
    assert.equal(subject.project, project);
    assert.equal(subject.providerRequestId, 'req_exact');
    assert.equal(x.a.accountReservationDebt(account, start), 100);
    return { statementHash: await digest(subject), receiptSha256: hex('8'), actualMicrousd: 47 };
  } }, x.peers.native);
  assert.equal((await res.json()).id, 'resp_exact');
  assert.equal(x.a.accountReservationDebt(account, start), 47);
  assert.equal(x.restart().accountReservationDebt(account, start + 1), 0);
  await assert.rejects(x.a.dispatchIncludedResponsesPrepared(args, x.peers.native), /original_already_recorded/);
  assert.equal(x.calls.length, 1);
});


async function gemmaSettlementFixture() {
  const x = fixture();
  await x.resume();
  const lease = (await x.call('native', 'claim', await x.addGrant())).lease;
  const project = hex('6'), attempt = 'attempt', deadline = start + 60;
  const model = 'gemma-4-31b-it', prompt = 'synthetic public fixture';
  const payload = canonical({
    contents: [{ role: 'user', parts: [{ text: prompt }] }],
    generationConfig: { maxOutputTokens: 256, candidateCount: 1 },
  });
  const operation = { fence: lease.fence, original_id: hex('7'), payload_sha256: await digest({
    schema: 'uif.gemma-prepared-public-call/1', project, model, attempt, deadline, payload,
  }) };
  const response = { responseId: 'example_1', modelVersion: model, candidates: [
    { finishReason: 'STOP', content: { role: 'model', parts: [{ text: 'synthetic output' }] } },
  ] };
  const account = 'gemma-project:' + project;
  x.respond = async () => Response.json(response);
  const verifier = async (subject) => {
    assert.deepEqual(subject, { operation_id: operation.original_id, payloadSHA: operation.payload_sha256,
      project, model, responseId: response.responseId,
      bodySHA: await digestBytes(new TextEncoder().encode(JSON.stringify(response))) });
    assert.equal(x.a.accountReservationDebt(account, start), 1);
    return { statementHash: await digest(subject), receiptSha256: hex('8'), consumedRequests: 1 };
  };
  const params = { operation, attempt, project, deadline, prompt, credential: 'fixture-nonsecret',
    readQuote: () => ({ publicDataOnly: true, noPaidRoute: true, coveredThrough: start,
      validUntil: start + 100, upperUsed: 0, limit: 2 }), verifySettlement: verifier };
  return { x, params, operation, response, verifier, account };
}

test('Gemma settles exact bounded original and recovers bytes without re-egress after restart', async () => {
  const f = await gemmaSettlementFixture();
  const { x, params, operation, response, account } = f;
  await assert.rejects(x.a.dispatchGemmaPrepared({ ...params, verifySettlement: undefined }, x.peers.native),
    /gemma_preparation_unbound/);
  assert.equal(x.calls.length, 0);
  const settled = await x.a.dispatchGemmaPrepared(params, x.peers.native);
  const receipt = settled.headers.get('uif-receipt-sha256');
  assert.match(receipt, /^[0-9a-f]{64}$/);
  assert.deepEqual(await settled.json(), response);
  assert.equal(x.a.accountReservationDebt(account, start), 1);
  x.a = x.restart();
  const recovered = await x.a.handle('/v1/provider/receipt', { operation }, x.peers.native);
  assert.equal(recovered.headers.get('uif-receipt-sha256'), receipt);
  assert.deepEqual(await recovered.json(), response);
  assert.equal(x.a.accountReservationDebt(account, start + 1), 0);
  await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native), /original_already_recorded/);
  assert.equal(x.calls.length, 1);
});

test('Gemma unknown verification or invalid response never retains bytes or releases the charge', async () => {
  for (const problem of ['unbound', 'wrong-id', 'wrong-model', 'wrong-count', 'wrong-finish', 'oversize']) {
    const { x, params, operation, response, account } = await gemmaSettlementFixture();
    const verify = params.verifySettlement;
    params.verifySettlement = async (subject) => {
      if (problem === 'unbound') throw new Error('fixture private detail');
      if (problem === 'wrong-id')
        return { statementHash: hex('f'), receiptSha256: hex('8'), consumedRequests: 1 };
      if (problem === 'wrong-count')
        return { statementHash: await digest(subject), receiptSha256: hex('8'), consumedRequests: 0 };
      return verify(subject);
    };
    if (problem === 'wrong-model') response.modelVersion = 'other';
    if (problem === 'wrong-finish') response.candidates[0].finishReason = 'SAFETY';
    if (problem === 'oversize') response.candidates[0].content.parts[0].text = 'x'.repeat(65537);
    await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native),
      /gemma_original_requires_verified_settlement|gemma_original_result_unbound|body_size/);
    assert.equal(x.a.row('SELECT id FROM provider_results'), undefined);
    assert.equal(x.restart().accountReservationDebt(account, start + 1), 1);
    await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native), /original_already_recorded/);
    assert.equal(x.calls.length, 1);
    assert.equal(x.a.row('SELECT state FROM effects WHERE id=?', operation.original_id).state, 'sending');
  }
});

test('Gemma consent revoked during verifier preserves exact charge but discloses no body', async () => {
  const { x, params, operation, account } = await gemmaSettlementFixture();
  const verify = params.verifySettlement;
  params.verifySettlement = async (subject) => {
    await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native), /original_in_flight/);
    const proof = await verify(subject);
    const grant = JSON.parse(x.a.row('SELECT body FROM grants').body);
    const changed = { ...grant };
    delete changed.result_retention;
    await x.call('control', 'grant', { grant: changed, previous_sha256: await digest(grant),
      request_id: 'drop-gemma-retention' });
    return proof;
  };
  await assert.rejects(x.a.dispatchGemmaPrepared(params, x.peers.native), /private_result_grant_changed/);
  assert.equal(x.a.row('SELECT state FROM effects WHERE id=?', operation.original_id).state, 'verified');
  assert.equal(x.a.accountReservationDebt(account, start), 1);
  assert.equal(x.a.row('SELECT id FROM provider_results'), undefined);
  assert.equal(x.calls.length, 1);
});

test('Gemma recovery refuses modified durable settlement fact rather than exposing a retained body', async () => {
  const { x, params, operation } = await gemmaSettlementFixture();
  const response = await x.a.dispatchGemmaPrepared(params, x.peers.native);
  const receipt = response.headers.get('uif-receipt-sha256');
  const id = 'gemma-settlement:' + receipt;
  const fact = JSON.parse(x.a.row('SELECT body FROM facts WHERE id=?', id).body);
  x.a.put('UPDATE facts SET body=? WHERE id=?', canonical({ ...fact, provider_receipt: hex('f') }), id);
  await assert.rejects(x.restart().handle('/v1/provider/receipt', { operation }, x.peers.native),
    /original_provider_result_changed/);
  assert.equal(x.calls.length, 1);
});

test('normal startup bind reconciles the same authenticated original without rebinding', async () => {
  const f = fixture();
  const { coordinator_epoch, installation_sha256 } = await f.resume().then(() =>
    f.call('control', 'status', {}));
  const body = {
    request_id: 'normal-commission-fixture',
    unit: 'stokowski-foo.service',
    stage: 'fixture-normal-release',
    session_id: f.peers.native.session_id,
    coordinator_epoch,
    valid_until: start + 300,
    evidence_sha256: hex('3'),
  };
  const read = { unit: body.unit, stage: body.stage, nonce: 'fresh-start-challenge' };
  await assert.rejects(f.call('native', 'startup/bind', body), /peer_role_not_granted/);
  await assert.rejects(f.call('native', 'startup/read', read), /startup_not_bound/);
  // Discard the first response as if the caller lost its acknowledgement.
  await f.call('control', 'startup/bind', body);
  f.a = f.restart();
  const original = { installation_sha256, session_id: f.peers.control.session_id,
    request_id: body.request_id };
  const receipt = { request_id: body.request_id, unit: body.unit, stage: body.stage };
  await assert.rejects(f.call('native', 'transition/original', original), /original_owner_required/);
  assert.deepEqual(await f.call('control', 'transition/original', original), receipt);
  await assert.rejects(f.call('control', 'startup/bind',
    { ...body, evidence_sha256: hex('4') }), /original|conflict/);
  const reply = await f.call('native', 'startup/read', read);
  assert.equal(reply.nonce, read.nonce);
  assert.equal(reply.session_id, body.session_id);
  assert.equal(reply.coordinator_epoch, coordinator_epoch);
  assert.equal(reply.expires_at, start + 5);
});

test('peer transfer and return keep one claim; an independent hold does not stop refill', async () => {
  const f = fixture(), first = await admitted(f);
  const nextScope = () => ({ ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } });
  const heldReq = await f.addGrant(nextScope(), 'held');
  const held = (await f.call('native', 'claim', heldReq)).lease;
  const witness = (current, to) => ({ current, successor_session: f.peers[to].session_id,
    expected_coordinator_epoch: 2, quiesced_sha256: hex('a'), reconstruction_sha256: hex('c') });
  const transfer = async (from, to, current, id) => {
    const h = witness(current, to);
    await f.call('control', 'attest', { request_id: 'attest-' + id, fact: { kind: 'handoff', ...h } });
    return (await f.call(from, 'handoff', { ...h, request_id: id })).lease;
  };
  f.setTime(start + 301);
  f.a = f.restart();
  await assert.rejects(f.call('native', 'handoff', { ...witness(held, 'morgan'), request_id: 'held-move' }),
    /independent_original_evidence_missing/);
  await assert.rejects(f.call('morgan', 'claim', { ...heldReq, request_id: 'held-duplicate' }),
    /issue_or_attempt_already_claimed/);
  const middle = await transfer('native', 'morgan', first, 'failover');
  assert.equal(middle.fence.owner_session, f.peers.morgan.session_id);
  await assert.rejects(f.call('native', 'renew', { current: first, request_id: 'first-stale' }),
    /claim_fence_changed/);
  const refillReq = await f.addGrant(nextScope(), 'refill');
  const refill = await f.call('morgan', 'claim', refillReq);
  assert.equal(refill.lease.fence.owner_session, f.peers.morgan.session_id);
  f.a = f.restart();
  const last = await transfer('morgan', 'native', middle, 'return');
  assert.equal(last.fence.owner_session, f.peers.native.session_id);
  assert.deepEqual([first, middle, last].map(x => x.fence.generation), [1, 2, 3]);
  assert.equal(new Set([first, middle, last].map(x => x.fence.lease_id)).size, 3);
  await assert.rejects(f.call('morgan', 'renew', { current: middle, request_id: 'middle-stale' }),
    /claim_fence_changed/);
  assert.deepEqual(await f.call('morgan', 'claim/observe', refillReq), refill);
  assert.deepEqual((await f.call('native', 'claim/observe', heldReq)).lease, held);
  assert.equal(f.calls.length, 0);
});

test('publication completion hashes the protected outcome, not the observer receipt', async () => {
  const f = fixture();
  const lease = await admitted(f);
  // Synthetic owner and destination observations only: the real control
  // caller must obtain these independently, outside this protocol.
  const outcome = {
    schema: 'uif.offmenu-publisher-outcome/1',
    publisher: 'offmenu-cloud',
    handoff_sha256: hex('b'),
    fence: lease.fence,
    publication_original_id: 'same-offmenu-original',
    repository: source.repository,
    base: source.branch,
    publication_receipt_sha256: hex('c'),
  };
  const completion = { fence: lease.fence, accepted: true, receipt_sha256: await digest(outcome) };
  const fact = { kind: 'completion', completion };
  await assert.rejects(f.call('native', 'attest', {request_id: 'not-control', fact}));
  await assert.rejects(f.call('native', 'release', {request_id: 'not-attested', completion}));
  await f.call('control', 'attest', {request_id: 'publication-control', fact});
  assert.deepEqual(await f.call('native', 'completion/read', {current: lease}), completion);
  for (const receipt_sha256 of [outcome.publication_receipt_sha256,
    await digest({...outcome, publication_original_id: 'other-original'})]) {
    await assert.rejects(f.call('native', 'release', {
      request_id: 'wrong-' + receipt_sha256, completion: {...completion, receipt_sha256},
    }));
  }
  assert.deepEqual((await f.call('native', 'release', {
    request_id: 'bound-same-original', completion,
  })).completion, completion);
});

test('publication fence observation uses existing control role and no writes', async () => {
  const f = fixture();
  await f.resume();
  const request = await f.addGrant();
  const { lease } = await f.call('native', 'claim', request);
  const query = { fence: lease.fence, attempt_id: request.attempt_id };
  const path = 'publication/fence/read';
  const mutations = [];
  const put = f.a.put.bind(f.a);
  f.a.put = (...args) => { mutations.push(args[0]); return put(...args); };
  assert.deepEqual(await f.call('control', path, query), { current: true });
  assert.deepEqual(await f.call('control', path, { ...query, attempt_id: 'other' }), { current: false });
  assert.deepEqual(await f.call('control', path, {
    ...query, fence: { ...lease.fence, generation: 2 },
  }), { current: false });
  for (const role of ['native', 'morgan', 'quota']) {
    await assert.rejects(f.call(role, path, query), { message: 'peer_role_not_granted' });
  }
  await assert.rejects(f.a.handle('/v1/' + path, query, {
    ...f.peers.control, session_id: 'unknown',
  }), { message: 'peer_role_not_granted' });
  await assert.rejects(f.call('control', path, { ...query, extra: false }), { message: 'invalid_fields' });
  assert.deepEqual(mutations, []);
  await f.call('control', 'control', {
    request_id: 'stop-publication-fixture', expected_coordinator_epoch: 2, stopped: true,
  });
  assert.deepEqual(await f.call('control', path, query), { current: false });
});

test('new assessment claim cannot advance across restart without its original terminal proof', async () => {
  const f = fixture();
  await f.resume();
  const assess = await f.addGrant(scope, 'assess');
  const current = (await f.call('native', 'claim', assess)).lease;
  const transition = {
    request_id: 'assess-successor', current,
    expected_attempt_id: assess.attempt_id, attempt_id: assess.attempt_id + '-next',
  };
  await assert.rejects(f.call('native', 'claim/attempt', transition),
    /prior_attempt_terminal_unverified/);
  f.a = f.restart();
  await assert.rejects(f.call('native', 'claim/attempt', transition),
    /prior_attempt_terminal_unverified/);
  assert.deepEqual(await f.call('native', 'claim/read', {
    identifier: scope.key.issue_id, by_attempt: false, project: 'OffMenu',
  }), { project: 'OffMenu', attempt_id: assess.attempt_id, lease: current });
  assert.deepEqual((await f.call('native', 'claim/observe', assess)).lease, current);
});


test('control compares exact retained attempt attestation without re-attesting', async () => {
  const f = fixture();
  await f.resume();
  const request = await f.addGrant(scope, 'assess');
  const { lease } = await f.call('native', 'claim', request);
  const original = {
    request_id: 'assess-attest-fixture', fence: lease.fence,
    previous: 'assess', successor: 'assess-next', terminal_receipt_sha256: hex('b'),
  };
  const path = 'attempt/attest/original';
  assert.deepEqual(await f.call('control', path, original), { matches: false });
  await f.call('control', 'attempt/attest', original);
  const mutations = [];
  const trackWrites = () => {
    const put = f.a.put.bind(f.a);
    f.a.put = (...args) => { mutations.push(args[0]); return put(...args); };
  };
  trackWrites();
  assert.deepEqual(await f.call('control', path, original), { matches: true });
  for (const delta of [
    { request_id: 'resume' }, { request_id: 'unknown' },
    { previous: 'wrong' }, { successor: 'wrong' },
    { terminal_receipt_sha256: hex('e') },
    { fence: { ...lease.fence, generation: 2 } },
    { fence: { ...lease.fence, coordinator_epoch: 3 } },
    { fence: { ...lease.fence, deployment_sha256: hex('e') } },
  ]) {
    assert.deepEqual(await f.call('control', path, { ...original, ...delta }), { matches: false });
  }
  await assert.rejects(f.call('control', path, { ...original, extra: 1 }), /invalid_fields/);
  for (const role of ['native', 'morgan', 'quota']) {
    await assert.rejects(f.call(role, path, original), /peer_role_not_granted/);
  }
  await assert.rejects(f.a.handle('/v1/' + path, original, { ...f.peers.control, session_id: 'other' }),
    /peer_role_not_granted/);
  assert.deepEqual(mutations, []);
  await f.call('control', 'control', {
    request_id: 'stop-attest-fixture', expected_coordinator_epoch: 2, stopped: true,
  });
  f.a = f.restart();
  mutations.length = 0;
  trackWrites();
  // An exact historical fact survives stop/restart, but is never a fresh authorization.
  assert.deepEqual(await f.call('control', path, original), { matches: true });
  assert.deepEqual(mutations, []);
});

test('native reads only its live exact existing control attempt original', async () => {
  const f = fixture();
  await f.resume();
  const request = await f.addGrant(scope, 'assess');
  const current = (await f.call('native', 'claim', request)).lease;
  const original = {
    request_id: 'native-exact-assess', fence: current.fence,
    previous: 'assess', successor: 'assess-next', terminal_receipt_sha256: hex('b'),
  };
  const path = 'attempt/attest/native';
  const read = (value = original, lease = current) => f.call('native', path, {current:lease, original:value});
  f.a.put('INSERT INTO next_attempts VALUES (?,?,?)',
    canonical(current.fence), original.previous, original.successor);
  assert.deepEqual(await read(), {matches:false}, 'bare tuple is not a control receipt');
  await f.call('control', 'attempt/attest', original);
  const writes = [], put = f.a.put.bind(f.a);
  f.a.put = (...args) => { writes.push(args[0]); return put(...args); };
  assert.deepEqual(await read(), {matches:true});
  for (const delta of [
    {terminal_receipt_sha256:hex('c')}, {request_id:'wrong-original'}, {previous:'other'},
    {successor:'other'}, {fence:{...current.fence,generation:2}},
  ]) assert.deepEqual(await read({...original,...delta}), {matches:false});
  assert.deepEqual(await read(original,{...current,expires_at:current.expires_at+1}), {matches:false});
  for (const role of ['control','quota','morgan']) {
    await assert.rejects(f.call(role,path,{original,current}), /peer_role_not_granted/);
  }
  await assert.rejects(f.a.handle('/v1/'+path,{original,current},
    {...f.peers.native,session_id:'retired'}), /peer_role_not_granted/);
  assert.deepEqual(writes, []);
  await f.call('native','claim/attempt',
    {current,expected_attempt_id:'assess',attempt_id:'assess-next',request_id:'native-successor'});
  assert.deepEqual(await read(), {matches:false}, 'consumed attestation is historical only');
});

async function freshAssessment(f, attempt_id, first_intent_sha256 = hex('a')) {
  const original = await f.addGrant(scope, attempt_id, start + 800, {}, {attempt_id, first_intent_sha256});
  return {
    current: (await f.call('native', 'claim', original)).lease, attempt_id, first_intent_sha256,
  };
}

test('fresh assessment emits only from the winning scoped dispatch; duplicate is observe-only', async () => {
  const f = fixture();
  await f.resume();
  const original = await freshAssessment(f, 'cf-intake-' + hex('e') + '-assess');
  assert.deepEqual(await f.call('native', 'dispatch/observe', original), {entered:false});
  await assert.rejects(f.call('native', 'dispatch/enter', {...original, first_intent_sha256:hex('b')}),
    /assessment_dispatch_not_selected/);
  await assert.rejects(f.call('native', 'dispatch/enter', {...original, current: {
    ...original.current, fence: {...original.current.fence, generation:2},
  }}), /claim_fence_changed/);
  for (const role of ['control','morgan','quota']) {
    await assert.rejects(f.call(role, 'dispatch/enter', original), /peer_role_not_granted/);
  }
  const admitted = await Promise.all(Array.from({length: 5}, () =>
    f.call('native', 'dispatch/enter', original)));
  const winners = admitted.filter((x) => x.entered);
  assert.equal(winners.length, 1);
  assert.equal(admitted.filter((x) => Object.hasOwn(x, 'permit')).length, 1);
  const {permit} = winners[0];
  assert.match(permit, /^[a-f0-9]{64}$/);
  const stored = f.a.row('SELECT * FROM assessment_dispatch WHERE attempt=?', original.attempt_id);
  assert.equal(stored.permit_sha, await digest(permit));
  assert.ok(!canonical(stored).includes(permit));
  f.a = f.restart();
  assert.deepEqual(await f.call('native', 'dispatch/enter', original), {entered:false});
  assert.deepEqual(await f.call('native', 'dispatch/observe', original), {entered:true});

  const quota = await f.quota();
  const send = await f.request(original.current, quota, {attempt: original.attempt_id});
  for (const p of [undefined, hex('f')]) {
    await assert.rejects(f.call('native', 'send', send, p), /assessment_dispatch_permit_required/);
  }
  for (const path of ['linear/comment','linear/upload']) {
    for (const input of [undefined, permit]) {
      await assert.rejects(f.call('native', path, {
        operation: send.intent.operation, attempt_id: original.attempt_id,
      }, input), /assessment_dispatch_linear_original_unbound/);
    }
  }
  assert.equal(f.calls.length, 0);
  const wrongAttempt = await f.request(original.current, quota, {attempt: original.attempt_id + '-next'});
  await assert.rejects(f.call('native', 'send', wrongAttempt, permit), /attempt_or_deadline_not_current/);
  assert.equal(f.calls.length, 0);
  const alternate = await f.request(original.current, quota, {
    attempt: original.attempt_id, id: hex('a'), prompt: 'different valid provider original',
  });
  const races = await Promise.allSettled([send, alternate].map((input) =>
    f.call('native','send',input,permit)));
  const winnerIndex = races.findIndex((result) => result.status === 'fulfilled');
  assert.ok(winnerIndex >= 0);
  assert.equal(races[winnerIndex].value.status, 200);
  assert.equal(races[1 - winnerIndex].status, 'rejected');
  assert.match(races[1 - winnerIndex].reason.message, /assessment_dispatch_provider_original_changed/);
  const first = [send, alternate][winnerIndex];
  assert.equal(f.a.row('SELECT provider_op FROM assessment_dispatch WHERE attempt=?',
    original.attempt_id).provider_op, canonical(first.intent.operation));
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].headers.get('uif-dispatch-permit'), null);
  await assert.rejects(f.call('native','send',first,permit), /original_already_recorded/);
  const changedBody = await f.request(original.current,quota,{
    attempt:original.attempt_id,id:first.intent.operation.original_id,prompt:'changed bytes',
  });
  await assert.rejects(f.call('native','send',changedBody,permit), /provider_original_changed/);
  const wrongRoute = await f.request(original.current,quota,{attempt:original.attempt_id,
    selectedRoute:{...route,gateway_id:'other-route'}});
  await assert.rejects(f.call('native','send',wrongRoute,permit), /send_intent_not_bound/);
  assert.equal(f.calls.length, 1);
  assert.ok(!JSON.stringify([...f.storage.sql.exec('SELECT * FROM receipts')]).includes(permit));

  // Provider-only marking is not a report or an independently admitted
  // investigate successor, and cannot advance via the control effect.
  const successor = original.attempt_id + '-next';
  await assert.rejects(f.call('control', 'attempt/attest', {request_id:'dispatch-existing-successor',
    fence:original.current.fence, previous:original.attempt_id, successor, terminal_receipt_sha256:hex('f')}),
    /assessment_successor_not_admitted/);
  assert.equal(f.calls.length,1);
});

test('lost or unconfirmed dispatch acknowledgement never reissues permit', async () => {
  const f = fixture();
  await f.resume();
  const original = await freshAssessment(f, 'cf-intake-' + hex('d') + '-assess');
  const sync = f.storage.sync;
  f.storage.sync = async () => { throw new Error('synthetic lost durability acknowledgement'); };
  await assert.rejects(f.call('native','dispatch/enter',original), /lost durability acknowledgement/);
  f.storage.sync = sync;
  f.a = f.restart();
  assert.deepEqual(await f.call('native','dispatch/observe',original), {entered:true});
  assert.deepEqual(await f.call('native','dispatch/enter',original), {entered:false});
  const quota = await f.quota();
  const send = await f.request(original.current,quota,{attempt:original.attempt_id});
  await assert.rejects(f.call('native','send',send), /assessment_dispatch_permit_required/);
  const stop = {request_id:'dispatch-stop', expected_coordinator_epoch:2, stopped:true};
  await f.call('control','control',stop);
  await assert.rejects(f.call('native','dispatch/observe',original), /stopped_expired_or_superseded/);
  assert.equal(f.calls.length, 0);
});


test('fresh dispatch policy cannot be retrofitted onto an existing claim or other project', async () => {
  const f = fixture();
  await f.resume();
  const attempt_id = 'cf-intake-' + hex('b') + '-assess';
  const original = await f.addGrant(scope, attempt_id);
  await f.call('native', 'claim', original);
  const old = JSON.parse(f.a.row('SELECT body FROM grants').body);
  const grant = {...old, fresh_assessment:{attempt_id,first_intent_sha256:hex('a')}};
  await assert.rejects(f.call('control','grant', {
    request_id:'retrofit-fresh-dispatch', previous_sha256:await digest(old), grant,
  }), /assessment_dispatch_not_fresh/);
  await assert.rejects(f.call('control','grant', {
    request_id:'outside-offmenu-dispatch', previous_sha256:null,
    grant:{...grant,source:{...source,repository:'maldous/circular-quay'}},
  }), /scope_or_source_unbound/);
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM assessment_dispatch').n,0);
});


test('fresh assessed provider seal is rechecked after reservation durability and before fetch', async () => {
  const f = fixture();
  await f.resume();
  const original = await freshAssessment(f, 'cf-intake-' + hex('c') + '-assess');
  const {permit} = await f.call('native','dispatch/enter',original);
  const quota = await f.quota();
  const send = await f.request(original.current,quota,{attempt:original.attempt_id});
  const sync = f.storage.sync;
  f.storage.sync = async () => {
    await sync();
    f.a.put('UPDATE assessment_dispatch SET provider_op=? WHERE attempt=?',
      canonical({...send.intent.operation,original_id:hex('d')}), original.attempt_id);
  };
  await assert.rejects(f.call('native','send',send,permit), /assessment_dispatch_provider_original_changed/);
  assert.equal(f.calls.length,0);
  assert.equal(f.a.row('SELECT state FROM effects WHERE id=?',send.intent.operation.original_id).state,
    'denied');
});

async function adjudicatedAssessment(f, verdict = 'blocked') {
  await f.resume();
  const report_binding = {scope, issue_identifier:'OFF-37', run:1, scope_sha256:hex('1')};
  const attempt = 'cf-intake-' + await digestBytes(Buffer.from(
    `["OffMenu", "${scope.key.issue_id}", "${report_binding.scope_sha256}", 1]`)) + '-assess';
  const claim = await f.addGrant(scope, attempt, start + 800, {}, {
    attempt_id:attempt, first_intent_sha256:hex('a'), report_binding,
  });
  const successor = attempt.slice(0,-'assess'.length) + 'investigate';
  await assert.rejects(f.call('native','claim',{...claim,attempt_id:successor}),
    /assessment_initial_attempt_required/);
  const current = (await f.call('native','claim',claim)).lease;
  const {permit} = await f.call('native','dispatch/enter',{
    attempt_id:attempt, first_intent_sha256:hex('a'), current,
  });
  const send = await f.request(current, await f.quota(), {attempt});
  const raw = canonical({summary:'fixture private raw text', verdict,
    routing:{issue:'OFF-37',run:1,state:'assess',actionable:true,ownership_clear:true,deferred:false,
      owner_pause:false,uncertain_effects:false,missing_inputs:[]}});
  f.respond = async req => {
    assert.notEqual(new URL(req.url).hostname, 'api.linear.app');
    return Response.json({model:route.model,choices:[{message:{content:raw}}],
      usage:{neurons:0.007}}, {headers:{'cf-aig-log-id':'fixture-result'}});
  };
  await f.call('native','send',send,permit);
  const witness = canonical({attempt_id:attempt, issue:'OFF-37', run:1,state:'assess',
    scope_sha256:hex('1'),report_sha256:await digestBytes(Buffer.from(raw))});
  const body = '<!-- stokowski:report -->\n\n### Native routing evidence\n\n```json\n' + witness +
    '\n```\n\n## assess — OFF-37\n\nAssessment report indicates ' +
    (verdict === 'approve' ? 'readiness for bounded work.' : 'blocked work.') +
    '\n\nThis report does not authorize a workflow transition.';
  const op = async (text, id) => ({
    fence:current.fence,
    original_id:id ?? await digest({attempt,slot:'_publish_run_report',kind:'comment',issue:scope.key.issue_id}),
    payload_sha256:await digest({kind:'uif.linear-comment/1',attempt_id:attempt,issue_id:scope.key.issue_id,body:text}),
  });
  return {permit, current, attempt, send, op, successor, sha:await digestBytes(Buffer.from(raw)),
    canonicalInput:{operation:await op(body),attempt_id:attempt,body}};
}

test('control-bound fresh assessment reserves and posts exactly the authority-derived retained report', async () => {
  const f = fixture();
  const {permit,attempt,op,canonicalInput} = await adjudicatedAssessment(f);
  const wrong = canonicalInput.body + ' arbitrary unbound text';
  for (const invalid of [
    {...canonicalInput, body:wrong, operation:await op(wrong)},
    {...canonicalInput, operation:await op(canonicalInput.body,hex('9'))},
    {...canonicalInput, operation:{...canonicalInput.operation,
      fence:{...canonicalInput.operation.fence,generation:2}}},
  ]) await assert.rejects(f.call('native','linear/comment',invalid,permit),
    /assessment_dispatch_report_original_changed|assessment_dispatch_permit_required/);
  await assert.rejects(f.call('native','linear/comment',canonicalInput), /assessment_dispatch_permit_required/);
  await assert.rejects(f.call('native','linear/upload',{
    operation:canonicalInput.operation,attempt_id:attempt,
  },permit), /assessment_dispatch_linear_original_unbound/);
  assert.equal(f.calls.length,1);
  f.respond = async req => {
    assert.equal(req.url,'https://api.linear.app/graphql');
    assert.equal(req.headers.has('uif-dispatch-permit'),false);
    const { variables:{input} } = await req.json();
    assert.equal(input.issueId,scope.key.issue_id);
    assert.equal(input.body,canonicalInput.body);
    assert.doesNotMatch(input.body,/fixture private raw text/);
    return Response.json({data:{commentCreate:{success:true,
      comment:{id:input.id,body:input.body,issue:{id:input.issueId}}}}});
  };
  const result = await f.call('native','linear/comment',canonicalInput,permit);
  f.a = f.restart();
  await assert.rejects(f.call('native','linear/comment',canonicalInput,permit), /original_already_recorded/);
  assert.deepEqual(await f.call('native','linear/receipt',{operation:canonicalInput.operation}), result);
  const observed = await f.call('native','original',{operation:canonicalInput.operation});
  assert.equal(observed.state,'terminal');
  assert.equal(observed.terminal.status,'verified');
  assert.equal(observed.terminal.receipt_sha256,result.receipt_sha256);
  assert.equal(f.calls.length,2);
});

test('fresh report refuses missing, changed, or expired retained evidence without physical comment', async () => {
  for (const loss of ['missing', 'tamper', 'expire']) {
    const f = fixture();
    const {permit,current,send,canonicalInput} = await adjudicatedAssessment(f);
    if (loss === 'missing')
      f.storage.sql.exec('DELETE FROM provider_results WHERE id=?', send.intent.operation.original_id);
    if (loss === 'tamper')
      f.storage.sync = async () => {
        f.storage.sql.exec('UPDATE provider_results SET response=? WHERE id=?',
          Buffer.from('{"fixture":"changed after reservation"}'), send.intent.operation.original_id);
      };
    if (loss === 'expire') f.storage.sync = async () => { f.setTime(current.expires_at); };
    await assert.rejects(f.call('native','linear/comment',canonicalInput,permit),
      /assessment_dispatch_report_result_unavailable|assessment_dispatch_report_evidence_changed|stopped_expired_or_superseded/);
    assert.equal(f.calls.length,1);
  }
});

test('fresh report unknown GraphQL result remains one physical original across restart', async () => {
  const f = fixture();
  const {permit,canonicalInput} = await adjudicatedAssessment(f);
  f.respond = async () => { throw Error('synthetic ambiguous response'); };
  await assert.rejects(f.call('native','linear/comment',canonicalInput,permit),
    /original_external_effect_unknown/);
  f.a = f.restart();
  const original = await f.call('native','original',{operation:canonicalInput.operation});
  assert.equal(original.state,'unknown');
  await assert.rejects(f.call('native','linear/comment',canonicalInput,permit), /original_already_recorded/);
  assert.equal(f.calls.length,2);
});

test('only the configured control original can create an exact same-scope report binding', async () => {
  const f = fixture();
  await f.resume();
  const scope_sha256 = hex('1');
  const attempt_id = 'cf-intake-' + await digestBytes(Buffer.from(
    `["OffMenu", "${scope.key.issue_id}", "${scope_sha256}", 1]`)) + '-assess';
  const report_binding = {scope,issue_identifier:'OFF-37',run:1,scope_sha256};
  const fresh = {attempt_id,first_intent_sha256:hex('a'),report_binding};
  const other = {...scope,key:{...scope.key,issue_id:crypto.randomUUID()}};
  for (const delta of [{scope:other},{run:2},{scope_sha256:hex('2')}])
    await assert.rejects(f.addGrant(scope,attempt_id,start+800,{},{
      ...fresh,report_binding:{...report_binding,...delta},
    }), /invalid_fresh_assessment_report_binding/);
  await f.addGrant(scope,attempt_id,start+800,{},fresh);
  const grant = JSON.parse(f.a.row('SELECT body FROM grants').body);
  const input = {request_id:'rebind-first-assessment',previous_sha256:await digest(grant),
    grant:{...grant,fresh_assessment:{...fresh,report_binding:{...report_binding,issue_identifier:'OFF-99'}}}};
  await assert.rejects(f.call('native','grant',input), /peer_role_not_granted/);
  await assert.rejects(f.call('control','grant',input), /assessment_dispatch_original_changed/);
  assert.equal(f.calls.length,0);
});

async function postExactAssessment(f, a) {
  f.respond = async req => {
    assert.equal(req.url, 'https://api.linear.app/graphql');
    const {variables:{input}} = await req.json();
    return Response.json({data:{commentCreate:{success:true,
      comment:{id:input.id,body:input.body,issue:{id:input.issueId}}}}});
  };
  return f.call('native','linear/comment',a.canonicalInput,a.permit);
}
async function assessmentTransition(a, posted = {id:crypto.randomUUID(),receipt_sha256:hex('4')}) {
  const fence = a.current.fence;
  const terminal = {
    schema:'uif.fresh-offmenu-assessment-terminal/1',
    issue_id:fence.scope.key.issue_id,issue_identifier:'OFF-37',run:1,state:'assess',
    successor_state:'investigate',scope_sha256:hex('1'),previous:a.attempt,successor:a.successor,
    report_sha256:a.sha,posted_comment_id:posted.id,
    posted_effect:{operation:a.canonicalInput.operation,status:'verified',receipt_sha256:posted.receipt_sha256},
  };
  return {
    original:{request_id:'exact-assess-posted-attestation',fence,previous:a.attempt,successor:a.successor,
      terminal_receipt_sha256:await digest(terminal)},
    advance:{request_id:'exact-investigate-transition',current:a.current,expected_attempt_id:a.attempt,
      attempt_id:a.successor},
  };
}

test('native reads the consumed fresh assessment edge after CAS reply loss with no new effect', async () => {
  const f = fixture();
  const a = await adjudicatedAssessment(f, 'approve');
  const posted = await postExactAssessment(f, a);
  const {original, advance} = await assessmentTransition(a, posted);
  const request = {current: a.current, original, advance};
  const observe = (body = request) => f.call('native', 'attempt/successor/read', body);
  await f.call('control', 'attempt/attest', original);
  assert.deepEqual(await observe(), {matches: false});
  await f.call('native', 'claim/attempt', advance); // The claimant loses the successful CAS reply.
  f.a = f.restart();
  const totalChanges = () => f.a.row('SELECT total_changes() AS n').n;
  const before = totalChanges();
  assert.deepEqual(await observe(), {matches: true});
  for (const changed of [
    {...request, original:{...original,request_id:'other-control-original'}},
    {...request, original:{...original,terminal_receipt_sha256:hex('0')}},
    {...request, advance:{...advance,request_id:'other-native-original'}},
    {...request, advance:{...advance,current:{...a.current,expires_at:a.current.expires_at+1}}},
    {...request, advance:{...advance,expected_attempt_id:'some-other-attempt'}},
    {...request, current:{...a.current,expires_at:a.current.expires_at+1}},
  ]) assert.deepEqual(await observe(changed), {matches: false});
  for (const other of ['morgan', 'control'])
    await assert.rejects(f.call(other,'attempt/successor/read',request), /peer_role_not_granted/);
  assert.deepEqual(await f.call('native','attempt/attest/native',{current:a.current,original}), {matches:false});
  assert.equal(totalChanges(), before);
  assert.equal(f.calls.length, 2);
  f.setTime(start + 10);
  const renewed = await f.call('native', 'renew', {request_id:'renew-successor', current:a.current});
  assert.deepEqual(await observe(), {matches:false});
  assert.deepEqual(await observe({...request,current:renewed.lease}), {matches:true});
  await f.call('control','revoke',{request_id:'revoke-successor',fence:a.current.fence});
  assert.deepEqual(await observe({...request,current:renewed.lease}), {matches:false});
});

test('only exact independently attested posted ready report allows investigate after reopen', async () => {
  const f = fixture();
  const a = await adjudicatedAssessment(f,'approve');
  const before = await assessmentTransition(a);
  await assert.rejects(f.call('control','attempt/attest',before.original), /assessment_terminal_report_unverified/);
  await assert.rejects(f.call('native','claim/attempt',before.advance), /prior_attempt_terminal_unverified/);
  await postExactAssessment(f,a); // ignore returned result; read after cold reopen.
  f.a = f.restart();
  const observed = await f.call('native','linear/receipt',{operation:a.canonicalInput.operation});
  const {original,advance} = await assessmentTransition(a,observed);
  await assert.rejects(f.call('native','attempt/attest',original), /peer_role_not_granted/);
  await assert.rejects(f.call('control','attempt/attest',{...original,terminal_receipt_sha256:hex('5')}),
    /assessment_terminal_digest_changed/);
  await assert.rejects(f.call('control','attempt/attest',{...original,successor:a.attempt+'-next'}),
    /assessment_successor_not_admitted/);
  await assert.rejects(f.call('native','claim/attempt',advance), /prior_attempt_terminal_unverified/);
  assert.deepEqual(await f.call('control','attempt/attest',original),
    {terminal_receipt_sha256:original.terminal_receipt_sha256});
  f.a = f.restart();
  assert.deepEqual(await f.call('native','attempt/attest/native',{current:a.current,original}), {matches:true});
  const consumed = await f.call('native','claim/attempt',advance);
  assert.equal(await f.call('native','claim/read',{project:'OffMenu',identifier:a.successor,by_attempt:true})
    .then(x=>x.attempt_id),a.successor);
  assert.deepEqual(await f.call('native','attempt/attest/native',{current:a.current,original}), {matches:false});
  assert.deepEqual(await f.call('native','claim/attempt',advance),consumed);
  assert.deepEqual(await f.call('control','attempt/attest',original),
    {terminal_receipt_sha256:original.terminal_receipt_sha256});
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM attempts WHERE id=?',a.successor).n,1);
  // Ordinary provider edge for later, newly admitted investigate is unchanged.
  const next = await f.request(a.current,await f.quota(),{attempt:a.successor,id:hex('b')});
  f.respond = async () => Response.json({model:route.model,choices:[{}],usage:{neurons:0.007}},
    {headers:{'cf-aig-log-id':'fixture-follow-on'}});
  assert.equal((await f.call('native','send',next)).status,200);
  assert.equal(f.calls.length,3);
});

test('blocked or unknown report cannot produce an assess successor', async () => {
  for (const mode of ['blocked','unknown']) {
    const f = fixture();
    const a = await adjudicatedAssessment(f,mode === 'blocked' ? 'blocked' : 'approve');
    if (mode === 'blocked') {
      const posted = await postExactAssessment(f,a);
      const {original} = await assessmentTransition(a,posted);
      await assert.rejects(f.call('control','attempt/attest',original),/assessment_terminal_report_not_ready/);
    } else {
      f.respond = async () => { throw Error('synthetic lost response after external accept'); };
      await assert.rejects(f.call('native','linear/comment',a.canonicalInput,a.permit),/original_external_effect_unknown/);
      f.a = f.restart();
      const {original} = await assessmentTransition(a);
      await assert.rejects(f.call('control','attempt/attest',original),/assessment_terminal_report_unverified/);
    }
    const {advance} = await assessmentTransition(a);
    await assert.rejects(f.call('native','claim/attempt',advance),
      /prior_attempt_terminal_unverified|uncertain_original_effect/);
    assert.equal(f.a.row('SELECT COUNT(*) AS n FROM next_attempts').n,0);
    assert.equal(f.calls.length,2);
  }
});
