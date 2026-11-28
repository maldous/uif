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
      request_id: fresh_assessment ? await digest({claim:s, recovery:grant.reconstruction_sha256,
        authority:identity(config, peers.native), epoch:2}) : 'claim-' + attempt,
      ...(fresh_assessment ? {first_intent_sha256:fresh_assessment.first_intent_sha256} : {}),
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

test('two qualified account sends overlap a Morgan original and fence a competing claim',
  { timeout: 5000 }, async () => {
    const f = fixture();
    const reviewRoute = { ...route, account_id: 'b'.repeat(32), credential_epoch_sha256: hex('7') };
    f.config.reviewer_route = reviewRoute;
    f.config.reviewer_opening_liability = {
      original_id: 'fixture-opening', millineurons: 2489344, receipt_sha256: hex('4'),
    };
    f.a = f.restart();
    await f.resume();

    const newIssue = () => ({ ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } });
    const authorClaim = await f.addGrant(scope, 'raced-author');
    const otherClaim = { ...authorClaim, request_id: 'competing-author' };
    const movedClaim = { ...authorClaim, request_id: 'moved-author',
      scope: { ...scope, key: { ...scope.key, project_id: crypto.randomUUID() } } };
    const races = await Promise.allSettled([
      f.call('native', 'claim', authorClaim), f.call('morgan', 'claim', otherClaim),
      f.call('morgan', 'claim', movedClaim),
    ]);
    assert.equal(races[2].status, 'rejected');
    assert.match(races[2].reason.message, /scope_or_source_unbound/);
    const winners = races.slice(0, 2).map((result, index) => result.status === 'fulfilled' ? index : -1)
      .filter((index) => index !== -1);
    assert.equal(winners.length, 1);
    const [winner] = winners, loser = 1 - winner;
    const roles = ['native', 'morgan'], original = [authorClaim, otherClaim][winner];
    const authorLease = races[winner].value.lease;
    assert.match(races[loser].reason.message, /issue_or_attempt_already_claimed/);
    assert.deepEqual(await f.call(roles[winner], 'claim/observe', original), races[winner].value);

    const reviewScope = newIssue(), reviewPeer = roles[loser];
    const reviewClaim = await f.addGrant(reviewScope, 'review-author', start + 600,
      { 'review-author-next': 'review-author' });
    const { lease: reviewLease } = await f.call(reviewPeer, 'claim', reviewClaim);
    const authorQuota = await f.quota();
    const preparation = await f.request(reviewLease, authorQuota,
      { attempt: 'review-author', id: hex('8') });
    await f.call(reviewPeer, 'send', preparation);
    const authorReceipt = await f.call(reviewPeer, 'original', { operation: preparation.intent.operation });
    const prompt = 'exact independent synthetic review';
    await f.call('control', 'review/bind', {
      request_id: 'fixture-review-bind', current: reviewLease,
      candidate: { kind: 'uif.native-review-candidate/1', issue_id: reviewScope.key.issue_id,
        scope_sha256: hex('c'), author_attempt_id: 'review-author', review_attempt_id: 'review-author-next',
        source, candidate_tree: null, review_prompt_sha256: await digestBytes(Buffer.from('prepared review')) },
      author_operation: preparation.intent.operation, author_receipt_sha256: authorReceipt.terminal.receipt_sha256,
      checkpoint_receipt_sha256: hex('d'), send_prompt_sha256: await digestBytes(Buffer.from(prompt)),
    });
    await f.call(reviewPeer, 'claim/attempt', { request_id: 'fixture-review-advance', current: reviewLease,
      expected_attempt_id: 'review-author', attempt_id: 'review-author-next' });
    const reviewerQuota = await f.quota(0, reviewRoute);
    const author = await f.request(authorLease, authorQuota, { attempt: 'raced-author', id: hex('1') });
    const reviewer = await f.request(reviewLease, reviewerQuota,
      { attempt: 'review-author-next', id: hex('2'), selectedRoute: reviewRoute, prompt });

    const morganScope = newIssue(), morganAttempt = 'morgan-independent';
    const morganClaim = await f.addGrant(morganScope, morganAttempt);
    const { lease: morganLease } = await f.call('morgan', 'claim', morganClaim);
    const report = 'synthetic deterministic check result';
    const comment = { operation: { fence: morganLease.fence, original_id: 'fixture-morgan-comment',
      payload_sha256: await digest({ kind: 'uif.linear-comment/1', attempt_id: morganAttempt,
        issue_id: morganScope.key.issue_id, body: report }) }, attempt_id: morganAttempt, body: report };

    const bothEntered = Promise.withResolvers(), finish = Promise.withResolvers(), accounts = new Set();
    f.respond = async (outgoing) => {
      if (outgoing.url === 'https://api.linear.app/graphql') {
        assert.equal(accounts.size, 2);
        const { variables: { input } } = await outgoing.json();
        assert.deepEqual([input.issueId, input.body], [morganScope.key.issue_id, report]);
        return Response.json({ data: { commentCreate: { success: true,
          comment: { id: input.id, body: input.body, issue: { id: input.issueId } } } } });
      }
      const account = new URL(outgoing.url).pathname.split('/')[4];
      assert.ok([route.account_id, reviewRoute.account_id].includes(account));
      assert.ok(!accounts.has(account));
      accounts.add(account);
      if (accounts.size === 2) bothEntered.resolve();
      await finish.promise;
      return Response.json({ model: route.model, choices: [{}], usage: { neurons: 0.007 } },
        { headers: { 'cf-aig-log-id': 'fixture-' + account } });
    };

    const sends = Promise.all([
      f.call(roles[winner], 'send', author), f.call(reviewPeer, 'send', reviewer),
    ]);
    try {
      await Promise.race([bothEntered.promise, sends]);
      assert.equal(accounts.size, 2);
      await assert.rejects(f.call(roles[loser], 'send', author), /original_in_flight/);
      const duplicate = await f.request(authorLease, authorQuota, { attempt: 'raced-author', id: hex('3') });
      duplicate.intent.operation.fence = { ...authorLease.fence, owner_session: f.peers[roles[loser]].session_id };
      await assert.rejects(f.call(roles[loser], 'send', duplicate), /claim_fence_changed/);
      for (const { intent } of [author, reviewer]) {
        assert.deepEqual({ ...f.a.row('SELECT account,amount,state FROM effects WHERE id=?',
          intent.operation.original_id) }, { account: intent.route.account_id,
          amount: intent.maximum_millineurons, state: 'sending' });
      }
      const racerKey = `${scope.key.workspace_id}:${scope.key.issue_id}`;
      assert.equal(f.a.row('SELECT COUNT(*) AS n FROM claims WHERE issue=?', racerKey).n, 1);
      assert.equal(f.a.row('SELECT COUNT(*) AS n FROM effects WHERE issue=? AND account IS NOT NULL', racerKey).n, 1);
      const result = await f.call('morgan', 'linear/comment', comment);
      assert.deepEqual(await f.call('morgan', 'linear/receipt', { operation: comment.operation }), result);
    } finally {
      finish.resolve();
      await sends;
    }
    for (const [peer, body] of [[roles[winner], author], [reviewPeer, reviewer]]) {
      const settled = await f.call(peer, 'original', { operation: body.intent.operation });
      assert.equal(settled.terminal.status, 'verified');
    }
    assert.equal(f.calls.length, 4);  // prerequisite author, two overlapping sends, Morgan comment
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

test('quota read reports only current account reservation debt without altering its durable receipt', async () => {
  const f = fixture();
  const review = { ...route, account_id: 'b'.repeat(32), credential_epoch_sha256: hex('7') };
  const opening = 2489344;
  f.config.reviewer_route = review;
  f.config.reviewer_opening_liability = { original_id: 'opening', millineurons: opening,
    receipt_sha256: hex('4') };
  f.a = f.restart();
  const first = await admitted(f);
  const authorQuota = await f.quota(500);
  const reviewQuota = await f.quota(500, review);
  const original = f.a.usage(route);
  const firstRead = (peer, exact = route) => f.call(peer, 'usage/read', { route: exact });
  for (const role of ['native', 'morgan', 'quota']) {
    assert.deepEqual(await firstRead(role), { ...original, reservation_snapshot: {
      receipt_sha256: authorQuota.receipt_sha256, observed_at: start,
      debt_millineurons: 0, ceiling_breached: false,
    } });
  }
  await assert.rejects(firstRead('control'), /peer_role_not_granted/);
  await assert.rejects(firstRead('native', { ...route, gateway_id: 'wrong' }), /usage_unavailable/);

  f.setTime(start + 1);
  await f.call('native', 'send', await f.request(first, authorQuota));
  const scope2 = { ...scope, key: { ...scope.key, issue_id: crypto.randomUUID() } };
  const second = (await f.call('native', 'claim', await f.addGrant(scope2, 'two'))).lease;
  f.respond = async () => { throw new Error('fixture lost reply'); };
  await assert.rejects(f.call('native', 'send', await f.request(second, authorQuota,
    { id: hex('2'), attempt: 'two' })), /original_external_effect_unknown/);
  f.a = f.restart();
  assert.deepEqual((await firstRead('native')).reservation_snapshot, {
    receipt_sha256: authorQuota.receipt_sha256, observed_at: start + 1,
    debt_millineurons: 2 * models.get(route.model), ceiling_breached: false,
  });
  assert.deepEqual((await firstRead('native', review)).reservation_snapshot, {
    receipt_sha256: reviewQuota.receipt_sha256, observed_at: start + 1,
    debt_millineurons: opening, ceiling_breached: false,
  });
  assert.deepEqual(f.a.usage(route), original);
  f.setTime(start + 2);
  const refreshed = await f.quota(501);
  assert.deepEqual((await firstRead('native')).reservation_snapshot, {
    receipt_sha256: refreshed.receipt_sha256, observed_at: start + 2,
    debt_millineurons: models.get(route.model), ceiling_breached: false,
  });
  f.setTime(start + 93);
  assert.deepEqual(await firstRead('native'), f.a.usage(route));
  assert.equal(f.calls.length, 2);
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
  assert.deepEqual((await f.call('native', 'usage/read', { route })).reservation_snapshot, {
    receipt_sha256: q.receipt_sha256, observed_at: start,
    debt_millineurons: models.get(route.model) + 1000, ceiling_breached: true,
  });
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

test('a later explicit stop fences a prepared normal activation when already stopped', async () => {
  const f = fixture();
  const activate = {
    request_id: 'prepared-normal', expected_coordinator_epoch: 1, stopped: false,
    startup: { stage: 'uif1313-normal-fixture', session_id: f.peers.native.session_id,
      evidence_sha256: hex('3'), coordinator_epoch: 2, previous_request_id: null },
  };
  const stop = {request_id: 'operator-stop', expected_coordinator_epoch: 1, stopped: true};
  const stopped = await f.call('control', 'control', stop);
  f.a = f.restart();
  await assert.rejects(f.call('control', 'control', activate), /control_epoch_changed/);
  assert.deepEqual(stopped, {coordinator_epoch: 2, stopped: true});
  assert.deepEqual(await f.call('control', 'control', stop), stopped);
  const challenge = {unit: 'stokowski-foo.service', stage: activate.startup.stage, nonce: 'cold-start'};
  await assert.rejects(f.call('native', 'startup/read', challenge), /startup_not_bound/);

  const again = {...stop, request_id: 'operator-stop-again', expected_coordinator_epoch: 2};
  assert.deepEqual(await f.call('control', 'control', again), {coordinator_epoch: 3, stopped: true});
  assert.deepEqual(await f.call('control', 'control', stop), stopped);
  const state = await f.call('control', 'status', {});
  assert.equal(state.coordinator_epoch, 3);
  assert.equal(state.stopped, true);
  assert.equal(state.normal_startup_request_id, null);

  const current = {...activate, request_id: 'new-normal-activation', expected_coordinator_epoch: 3,
    startup: {...activate.startup, coordinator_epoch: 4}};
  const started = await f.call('control', 'control', current);
  assert.equal(started.coordinator_epoch, 4);
  // Reading the historical stop receipt never stops the new commissioned run.
  assert.deepEqual(await f.call('control', 'control', again), {coordinator_epoch: 3, stopped: true});
  f.a = f.restart();
  assert.equal((await f.call('native', 'startup/read', challenge)).coordinator_epoch, 4);
});

test('one control action activates the installed normal release for unattended restarts', async () => {
  const f = fixture();
  const unit = 'stokowski-foo.service';
  const startup = {
    stage: 'uif1313-normal-fixture',
    session_id: f.peers.native.session_id,
    evidence_sha256: hex('3'),
    coordinator_epoch: 2,
    previous_request_id: null,
  };
  const activate = {
    request_id: 'activate-installed-release',
    expected_coordinator_epoch: 1,
    stopped: false,
    startup,
  };
  const challenge = { unit, stage: startup.stage, nonce: 'native-restart' };
  await assert.rejects(f.call('native', 'control', activate), /peer_role_not_granted/);
  await assert.rejects(f.call('control', 'control', { ...activate, stopped: true }), /invalid_startup_binding/);
  await assert.rejects(f.call('control', 'control', {
    ...activate, startup: { ...startup, session_id: f.peers.morgan.session_id },
  }), /invalid_startup_binding/);
  await assert.rejects(f.call('control', 'control', {
    ...activate, startup: { ...startup, stage: 'uif1313-idle-a22fbb2' },
  }), /invalid_startup_binding/);
  await assert.rejects(f.call('control', 'control', {
    ...activate, startup: { ...startup, coordinator_epoch: 1 },
  }), /normal_startup_selection_changed/);
  await assert.rejects(f.call('control', 'control', { ...activate, expected_coordinator_epoch: 2 }), /control_epoch_changed/);
  assert.equal((await f.call('control', 'status', {})).stopped, true);

  const result = await f.call('control', 'control', activate);
  assert.deepEqual(result, { coordinator_epoch: 2, stopped: false, expected_coordinator_epoch: 1,
    startup: { ...startup, unit } });
  f.a = f.restart();
  assert.deepEqual(await f.call('control', 'control', activate), result);
  f.setTime(start + 86_400);
  const proof = await f.call('native', 'startup/read', challenge);
  assert.equal(proof.coordinator_epoch, 2);
  assert.equal(proof.expires_at, f.clock() + 5);
  assert.equal((await f.call('control', 'status', {})).normal_startup_request_id, activate.request_id);
  await assert.rejects(f.call('control', 'startup/bind', {
    request_id: 'old-binder', unit, stage: startup.stage, session_id: startup.session_id,
    coordinator_epoch: 2, valid_until: f.clock() + 300, evidence_sha256: hex('3'),
  }), /normal_startup_already_selected/);
  await assert.rejects(f.call('native', 'startup/read', { ...challenge, stage: 'other-release' }), /startup_not_bound/);
  await assert.rejects(f.call('morgan', 'startup/read', challenge), /peer_role_not_granted/);

  const replacement = { ...startup, stage: 'uif1313-normal-replacement',
    previous_request_id: activate.request_id };
  const replace = { ...activate, request_id: 'activate-replacement',
    expected_coordinator_epoch: 2, startup: replacement };
  await f.call('control', 'control', replace);
  await assert.rejects(f.call('control', 'control', {
    ...replace, request_id: 'delayed-old-release', startup: { ...replacement, stage: startup.stage },
  }), /normal_startup_selection_changed/);
  await assert.rejects(f.call('native', 'startup/read', challenge), /startup_not_bound/);
  const replacementChallenge = { ...challenge, stage: replacement.stage };
  assert.equal((await f.call('native', 'startup/read', replacementChallenge)).coordinator_epoch, 2);

  await f.call('control', 'control', {
    request_id: 'operator-stop', expected_coordinator_epoch: 2, stopped: true,
  });
  f.a = f.restart();
  await assert.rejects(f.call('native', 'startup/read', replacementChallenge), /startup_not_bound/);
  assert.deepEqual(await f.call('control', 'control', activate), result);
  assert.equal((await f.call('control', 'status', {})).stopped, true);
  await f.call('control', 'control', {
    ...replace, request_id: 'resume-installed', expected_coordinator_epoch: 3,
    startup: { ...replacement, coordinator_epoch: 4, previous_request_id: replace.request_id },
  });
  assert.equal((await f.call('native', 'startup/read', replacementChallenge)).coordinator_epoch, 4);
  f.setTime(f.peers.native.expires_at);
  await assert.rejects(f.call('native', 'startup/read', replacementChallenge), /access_assertion_expired/);
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

test('ordinary handoff preserves predecessor work and its original stays with the submitting peer', async () => {
  for (const effect of ['absent', 'verified', 'sending']) {
    const f = fixture();
    await f.resume();
    const claim = await f.addGrant();
    const prior = (await f.call('morgan', 'claim', claim)).lease;
    if (effect !== 'absent') {
      const request = await f.request(prior, await f.quota(), { attempt: claim.attempt_id });
      if (effect === 'sending') {
        f.respond = async () => { throw new Error('simulated destination response loss'); };
        await assert.rejects(f.call('morgan', 'send', request), /external_effect_unknown/);
      } else {
        await f.call('morgan', 'send', request);
      }
    }
    const handoff = {current: prior, successor_session: f.peers.native.session_id,
      expected_coordinator_epoch: prior.fence.coordinator_epoch,
      quiesced_sha256: hex('7'), reconstruction_sha256: claim.reconstruction_sha256};
    const fact = {kind: 'handoff', ...handoff};
    await assert.rejects(f.call('control', 'attest', {request_id: 'invented-purpose',
      fact: {...fact, first_dispatch_purpose: 'continue'}}), /invalid_fields/);
    await f.call('control', 'attest', {request_id: 'witness-transfer', fact});
    const body = {...handoff, request_id: 'peer-transfer'};
    const {installation_sha256} = await f.call('native', 'status', {});
    const lookup = {installation_sha256, session_id: f.peers.morgan.session_id, request_id: body.request_id};
    await assert.rejects(f.call('native', 'handoff', body), /original_owner_required/);
    if (effect === 'sending') {
      await assert.rejects(f.call('morgan', 'handoff', body), /uncertain_original_effect/);
      await assert.rejects(f.call('control', 'transition/original', lookup), /original_transition_absent/);
    } else {
      const moved = await f.call('morgan', 'handoff', body);
      f.a = f.restart();
      assert.equal(moved.request_kind, 'uif.shared-lease.handoff/3');
      assert.deepEqual(Object.keys(moved).sort(), ['lease', 'request_id', 'request_kind', 'request_sha256']);
      assert.deepEqual(await f.call('control', 'transition/original', lookup), moved);
      assert.deepEqual(await f.call('morgan', 'transition/original', lookup), moved);
      await assert.rejects(f.call('native', 'transition/original', lookup), /original_owner_required/);
      await assert.rejects(f.call('native', 'transition/original', {...lookup, session_id: f.peers.native.session_id}),
        /original_transition_absent/);
      assert.deepEqual(await f.call('native', 'claim/read', {
        project: 'OffMenu', identifier: claim.attempt_id, by_attempt: true}),
      {project: 'OffMenu', attempt_id: claim.attempt_id, lease: moved.lease});
    }
    assert.equal(f.calls.length, effect === 'absent' ? 0 : 1);
  }
});

async function unstartedFixture(from = 'native') {
  const f = fixture(), to = from === 'native' ? 'morgan' : 'native';
  await f.resume();
  const claim = await f.addGrant(), current = (await f.call(from, 'claim', claim)).lease;
  const revoke = { request_id: 'freeze-original', fence: current.fence, unstarted_attempt_id: claim.attempt_id };
  const handoff = {
    current, successor_session: f.peers[to].session_id, expected_coordinator_epoch: 2,
    quiesced_sha256: hex('7'), reconstruction_sha256: claim.reconstruction_sha256,
    recovery: { attempt_id: claim.attempt_id, revocation_id: revoke.request_id },
  };
  const attest = { request_id: 'witness', fact: { kind: 'handoff', ...handoff } };
  const accept = { request_id: 'accept', ...handoff };
  return { f, from, to, claim, current, revoke, attest, accept };
}

for (const fromRole of ['native', 'morgan']) {
  test('named successor recovers an unstarted assignment from an absent ' + fromRole, async () => {
    const { f, to, claim, current, revoke, attest, accept } = await unstartedFixture(fromRole);
    f.setTime(current.expires_at + 1);
    await assert.rejects(f.call(to, 'handoff', accept), /exact_unstarted_revocation_required/);
    await assert.rejects(f.call('control', 'attest', attest), /exact_unstarted_revocation_required/);
    const revoked = await f.call('control', 'revoke', revoke);
    f.a = f.restart();
    assert.deepEqual(await f.call('control', 'revoke', revoke), revoked);
    await assert.rejects(f.call(to, 'handoff', accept), /independent_original_evidence_missing/);
    await assert.rejects(f.call('control', 'attest', { ...attest,
      fact: { ...attest.fact, recovery: { ...accept.recovery, revocation_id: 'another-revocation' } } }),
      /exact_unstarted_revocation_required/);
    await f.call('control', 'attest', attest);
    await assert.rejects(f.call('quota', 'handoff', accept), /peer_role_not_granted/);
    await assert.rejects(f.call(to, 'handoff', { ...accept, successor_session: f.peers[fromRole].session_id }),
      /named_successor_required/);
    await assert.rejects(f.call(to, 'handoff', { ...accept, quiesced_sha256: hex('8') }),
      /independent_original_evidence_missing/);
    const races = await Promise.allSettled([accept, { ...accept, request_id: 'competing-accept' }]
      .map((request) => f.call(to, 'handoff', request)));
    assert.equal(races.filter((r) => r.status === 'fulfilled').length, 1);
    const moved = races.find((r) => r.status === 'fulfilled').value;
    assert.equal(moved.request_kind, 'uif.shared-lease.recover-unstarted/1');
    assert.equal(moved.attempt_id, claim.attempt_id);
    assert.equal(moved.lease.fence.generation, 2);
    assert.equal(moved.lease.fence.owner_session, f.peers[to].session_id);
    assert.notEqual(moved.lease.fence.lease_id, current.fence.lease_id);
    f.a = f.restart();
    const status = await f.call(to, 'status', {});
    const lookup = { session_id: f.peers[to].session_id, request_id: moved.request_id,
      installation_sha256: status.installation_sha256 };
    assert.deepEqual(await f.call(to, 'transition/original', lookup), moved);
    assert.deepEqual(await f.call(to, 'handoff', { ...accept, request_id: moved.request_id }), moved);
    assert.deepEqual(await f.call(to, 'claim/read', { project: 'OffMenu', by_attempt: true,
      identifier: claim.attempt_id }), { project: 'OffMenu', attempt_id: claim.attempt_id, lease: moved.lease });
    // No predecessor call was needed after its original claim.
    await assert.rejects(f.call(fromRole, 'renew', { current, request_id: 'late' }), /claim_fence_changed/);
    assert.equal(f.calls.length, 0);
  });
}

test('a recovered native recipient cannot borrow the marked assessment entry mechanism', async () => {
  const { f, to, claim, revoke, attest, accept } = await unstartedFixture('morgan');
  await f.call('control', 'revoke', revoke);
  await f.call('control', 'attest', attest);
  const moved = await f.call(to, 'handoff', accept);
  f.a = f.restart();
  const current = await f.call(to, 'claim/read', {
    project: 'OffMenu', identifier: claim.attempt_id, by_attempt: true,
  });
  assert.deepEqual(current.lease, moved.lease);
  const input = {current: current.lease, attempt_id: current.attempt_id, first_intent_sha256: moved.request_sha256};
  await assert.rejects(f.call(to, 'dispatch/observe', input), /assessment_dispatch_not_selected/);
  await assert.rejects(f.call(to, 'dispatch/enter', input), /assessment_dispatch_not_selected/);
  assert.equal(f.a.row('SELECT count(*) AS n FROM assessment_dispatch').n, 0);
  assert.equal(f.calls.length, 0);
});

async function recoveredNative() {
  const { f, revoke, attest, accept } = await unstartedFixture('morgan');
  await f.call('control','revoke',revoke);
  await f.call('control','attest',attest);
  const moved = await f.call('native','handoff',accept);
  return {f, moved, input:{current:moved.lease,attempt_id:moved.attempt_id,original:{
    installation_sha256:f.a.realm,session_id:f.peers.native.session_id,
    request_id:moved.request_id,request_sha256:moved.request_sha256,
  }}};
}

test('only the new native recovery entrant receives permission; an uncertain reply is observe-only', async () => {
  const { f, input, moved } = await recoveredNative();
  const enter = value => f.call('native','recovery/enter',value);
  const observe = () => f.call('native','recovery/observe',input);
  await assert.rejects(f.call('morgan','recovery/enter',input), /peer_role_not_granted/);
  for (const key of Object.keys(input.original)) {
    await assert.rejects(enter({...input, original:{...input.original, [key]:hex('e')}}), /native_recovery_original_not_current/);
  }
  await assert.rejects(enter({...input,attempt_id:'other'}), /native_recovery_original_not_current/);
  assert.deepEqual(await observe(), {entered:false});
  const oldLease = input.current;
  f.setTime(start + 1);
  input.current = (await f.call('native','renew',{current:oldLease,request_id:'extend'})).lease;
  await assert.rejects(enter({...input,current:oldLease}), /native_recovery_original_not_current/);
  const entrants = await Promise.all([enter(input),enter(input)]);
  assert.equal(entrants.filter(r => r.entered).length, 1);
  assert.deepEqual(entrants.find(r => !r.entered), {entered:false});
  const {permit} = entrants.find(r => r.entered);
  assert.match(permit,/^[0-9a-f]{64}$/);
  assert.equal(f.a.row('SELECT permit_sha FROM recovery_entries').permit_sha,await digest(permit));
  f.a = f.restart();
  assert.deepEqual(await observe(), {entered:true});
  assert.deepEqual(await enter(input), {entered:false});
  const {request_sha256, ...lookup} = input.original;
  assert.deepEqual(await f.call('native','transition/original',lookup),moved);
  const request = await f.request(input.current, await f.quota());
  for (const token of [undefined, hex('9'), request_sha256])
    await assert.rejects(f.call('native','send',request,token), /native_recovery_permit_required/);
  assert.equal(f.calls.length, 0);
  await f.call('native','send',request,permit);
  assert.equal(f.calls.length, 1);
});

test('lost durable recovery entry acknowledgment never issues a replacement capability', async () => {
  const {f,input} = await recoveredNative();
  f.storage.sync = async () => {throw new Error('lost reply');};
  await assert.rejects(f.call('native','recovery/enter',input), /lost reply/);
  f.a = f.restart();
  assert.deepEqual(await f.call('native','recovery/observe',input),{entered:true});
  assert.deepEqual(await f.call('native','recovery/enter',input),{entered:false});
  const upload = await uploadInput(f,input.current);
  await assert.rejects(f.call('native','linear/upload',upload), /native_recovery_permit_required/);
  assert.equal(f.calls.length, 0);
});

test('recovered native upload rechecks the winner before PUT and retains the uncertain allocation', async () => {
  const {f,input} = await recoveredNative();
  const upload = await uploadInput(f,input.current);
  const {permit} = await f.call('native','recovery/enter',input);
  f.respond = async req => {
    assert.equal(req.method,'POST');
    await f.call('control','revoke',{request_id:'stop-upload',fence:input.current.fence});
    return presign();
  };
  await assert.rejects(f.call('native','linear/upload',upload,permit), /owner_not_current/);
  assert.equal(f.calls.length,1);
  assert.equal(f.a.row('SELECT state FROM effects WHERE id=?',upload.operation.original_id).state,'sending');
});

test('recovery cannot open an older generation, spend on a detached route, or advance without the winner', async () => {
  const {f,input} = await recoveredNative();
  const {fence} = input.current;
  const op = {fence,original_id:'prepared',payload_sha256:hex('f')};
  for (const submit of ['dispatchGemmaPrepared','dispatchIncludedResponsesPrepared'])
    await assert.rejects(f.a[submit]({operation:op,attempt:input.attempt_id},f.peers.native), /native_recovery_permit_required/);
  await assert.rejects(f.call('native','linear/comment',{attempt_id:input.attempt_id,operation:op}), /native_recovery_permit_required/);
  const advance = {request_id:'next',current:input.current,expected_attempt_id:input.attempt_id,attempt_id:'attempt-next'};
  await f.call('control','attempt/attest',{request_id:'attest-next',fence,previous:input.attempt_id,
    successor:advance.attempt_id,terminal_receipt_sha256:hex('6')});
  await assert.rejects(f.call('native','claim/attempt',advance), /native_recovery_permit_required/);
  const {permit} = await f.call('native','recovery/enter',input);
  assert.equal((await f.call('native','claim/attempt',advance,permit)).attempt_id,advance.attempt_id);
  assert.equal((await f.call('native','claim/attempt',advance)).attempt_id,advance.attempt_id);
  await assert.rejects(f.call('native','recovery/enter',input), /native_recovery_original_not_current/);
  const other = await recoveredNative();
  // Previous versions recorded only the exact handoff+lease. A new process may
  // still read them, but a migration must never create first-entry permission.
  other.f.a.put('DELETE FROM recovery_entries');
  other.f.a = other.f.restart();
  await assert.rejects(other.f.call('native','recovery/enter',other.input), /native_recovery_original_not_current/);
  const oldSend = await other.f.request(other.input.current, await other.f.quota());
  await assert.rejects(other.f.call('native','send',oldSend), /native_recovery_permit_required/);
  assert.equal(other.f.calls.length,0);
});

test('independently attested release and revoked handoff can settle a recovered fence without a lost permit', async () => {
  for (const action of ['release', 'handoff']) {
    const {f,input} = await recoveredNative();
    const {current} = input, {fence} = current;
    const completion = {fence,accepted:false,receipt_sha256:hex('7')};
    const handoff = {current,expected_coordinator_epoch:fence.coordinator_epoch,successor_session:f.peers.morgan.session_id,
      reconstruction_sha256:hex('c'),quiesced_sha256:hex('b')};
    const body = {request_id:'settle',...(action === 'release' ? {completion} : handoff)};
    if (action === 'handoff') await assert.rejects(f.call('native', action, body), /native_recovery_permit_required/);
    await f.call('control','revoke',{request_id:'revoke-return',fence});
    await assert.rejects(f.call('native',action,body), /independent_original_evidence_missing/);
    const fact = action === 'release' ? {kind:'completion',completion} : {kind:'handoff',...handoff};
    await f.call('control','attest',{request_id:'approved-release-or-move',fact});
    const result = await f.call('native',action,body);
    if (action === 'release') assert.deepEqual(result.completion,completion);
    else assert.equal(result.lease.fence.owner_session,f.peers.morgan.session_id);
    assert.deepEqual(await f.call('native',action,body),result);
    await assert.rejects(f.call('native','recovery/enter',input), /owner_not_current|claim_fence_changed/);
  }
});

test('retained handoff and live recipient claim do not distinguish first entry from an interrupted recipient', async () => {
  for (const uncertain of [false, true]) {
    const { f, to, claim, revoke, attest, accept } = await unstartedFixture('morgan');
    await f.call('control', 'revoke', revoke);
    await f.call('control', 'attest', attest);
    const moved = await f.call(to, 'handoff', accept);
    const status = await f.call(to, 'status', {});
    const lookup = { installation_sha256: status.installation_sha256,
      session_id: f.peers[to].session_id, request_id: accept.request_id };
    const entry = {current:moved.lease, attempt_id:claim.attempt_id,
      original:{...lookup, request_sha256:moved.request_sha256}};
    const {permit} = await f.call(to, 'recovery/enter', entry);
    const claimLookup = { project: 'OffMenu', by_attempt: true, identifier: claim.attempt_id };
    const current = await f.call(to, 'claim/read', claimLookup);
    const request = await f.request(current.lease, await f.quota(), { attempt: claim.attempt_id });
    const operation = { operation: request.intent.operation };
    assert.equal((await f.call(to, 'original', operation)).state, 'unknown');
    if (uncertain) {
      f.respond = async () => { throw new Error('simulated destination response loss'); };
      await assert.rejects(f.call(to, 'send', request, permit), /external_effect_unknown/);
    } else {
      await f.call(to, 'send', request, permit);
    }
    f.a = f.restart();
    assert.deepEqual(await f.call(to, 'transition/original', lookup), moved);
    assert.deepEqual(await f.call(to, 'claim/read', claimLookup), current);
    assert.equal(f.a.row('SELECT state FROM effects WHERE id=?', operation.operation.original_id).state,
      uncertain ? 'sending' : 'verified');
    assert.equal((await f.call(to, 'original', operation)).state, uncertain ? 'unknown' : 'terminal');
    assert.equal(f.calls.length, 1);
    await f.call('control', 'revoke', { request_id: 'hold-recipient', fence: current.lease.fence });
    assert.deepEqual(await f.call(to, 'transition/original', lookup), moved);
    await assert.rejects(f.call(to, 'claim/read', claimLookup), /owner_not_current/);
    assert.equal(f.calls.length, 1);
  }
});

test('ordinary revocation, new hold, same-epoch grant replacement and stop exclude unstarted recovery', async () => {
  for (const reason of ['ordinary', 'explicit-hold', 'grant-replaced-restored', 'stop']) {
    const { f, to, current, revoke, attest, accept } = await unstartedFixture();
    if (reason === 'ordinary') {
      await f.call('control', 'revoke', { request_id: revoke.request_id, fence: current.fence });
      await assert.rejects(f.call('control', 'attest', attest), /exact_unstarted_revocation_required/);
      continue;
    }
    await f.call('control', 'revoke', revoke);
    await f.call('control', 'attest', attest);
    if (reason === 'explicit-hold') {
      await f.call('control', 'revoke', { request_id: 'hold', fence: current.fence });
    } else if (reason === 'grant-replaced-restored') {
      const original = f.a.grant(scope, source, f.peers.native);
      const changed = { ...original, allow_uploads: false };
      for (const [id, previous, grant] of [['replace', original, changed], ['restore', changed, original]]) {
        await f.call('control', 'grant', { request_id: id, previous_sha256: await digest(previous), grant });
      }
    } else {
      await f.call('control', 'control', { request_id: 'stop', expected_coordinator_epoch: 2, stopped: true });
    }
    f.a = f.restart();
    await assert.rejects(f.call(to, 'handoff', accept),
      /exact_unstarted_revocation_required|unstarted_initial_claim_changed/);
  }
});

test('unstarted recovery preserves ambiguous effects, terminal effects and source or stage evidence', async () => {
  for (const state of ['unknown', 'terminal', 'source', 'stage', 'completion', 'renewed']) {
    const { f, current, revoke } = await unstartedFixture();
    if (state === 'unknown' || state === 'terminal') {
      const request = await f.request(current, await f.quota());
      if (state === 'unknown') {
        f.respond = async () => { throw Error('fixture-lost-response'); };
        await assert.rejects(f.call('native', 'send', request));
      } else {
        await f.call('native', 'send', request);
      }
      const before = await f.call('native', 'original', { operation: request.intent.operation });
      assert.equal(before.state, state);
      await assert.rejects(f.call('control', 'revoke', revoke), /attempt_already_started_or_unreconciled/);
      assert.deepEqual(await f.call('native', 'original', { operation: request.intent.operation }), before);
      assert.equal(f.calls.length, 1);
      continue;
    }
    if (state === 'source') {
      await f.call('control', 'source/bind', {
        request_id: 'bind-source', current, attempt_id: 'attempt', public_source_receipt_sha256: hex('2'),
        paths: [{ path: 'work.py', writable: true, mode: null,
          base_sha256: null, base_base64: null, base_git_blob: null }],
      });
    } else if (state === 'completion') {
      await f.call('control', 'attest', { request_id: 'completion', fact: { kind: 'completion',
        completion: { fence: current.fence, accepted: true, receipt_sha256: hex('3') } } });
    } else if (state === 'stage') {
      await f.call('control', 'attempt/attest', { request_id: 'next', fence: current.fence,
        previous: 'attempt', successor: 'attempt-next', terminal_receipt_sha256: hex('3') });
    } else {
      f.setTime(start + 10);
      await f.call('native', 'renew', { request_id: 'renew', current });
    }
    await assert.rejects(f.call('control', 'revoke', revoke),
      /attempt_already_started_or_unreconciled|unstarted_initial_claim_changed/);
    assert.equal(f.calls.length, 0);
  }
});

test('a grant restricted to one peer cannot enter unstarted recovery', async () => {
  const f = fixture();
  await f.resume();
  const claim = await f.addGrant();
  const grant = f.a.grant(scope, source, f.peers.native);
  await f.call('control', 'grant', { request_id: 'native-only', previous_sha256: await digest(grant),
    grant: { ...grant, sessions: [f.peers.native.session_id] } });
  const { lease } = await f.call('native', 'claim', claim);
  await assert.rejects(f.call('control', 'revoke', { request_id: 'transfer-native-only',
    fence: lease.fence, unstarted_attempt_id: 'attempt' }), /unstarted_initial_claim_changed/);
  assert.deepEqual((await f.call('native', 'claim/read', {
    project: 'OffMenu', identifier: 'attempt', by_attempt: true,
  })).lease, lease);
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

test('only the exact enrolled native first-claim original can acquire a marked issue', async () => {
  const f = fixture();
  await f.resume();
  const attempt_id = 'cf-intake-' + hex('1') + '-assess', first_intent_sha256 = hex('d');
  const marked = await f.addGrant(scope, attempt_id, start + 800, {}, {attempt_id, first_intent_sha256});
  const {first_intent_sha256: marker, ...plain} = marked;
  const deny = (patch) => f.call('native', 'claim', {...marked, ...patch});
  await assert.rejects(f.call('native', 'claim', plain), /assessment_first_claim_original_required/);
  await assert.rejects(deny({first_intent_sha256:hex('b')}), /assessment_first_claim_original_required/);
  await assert.rejects(deny({request_id:'other-original'}), /assessment_first_claim_original_required/);
  await assert.rejects(f.call('morgan', 'claim', marked), /peer_role_not_granted/);
  await assert.rejects(f.a.handle('/v1/claim', marked, {...f.peers.native, session_id:'other'}),
    /peer_role_not_granted/);
  await assert.rejects(f.call('native', 'claim/observe', marked), /original_claim_not_found/);
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM claims').n, 0);
  const results = await Promise.all([f.call('native','claim',marked), f.call('native','claim',marked)]);
  assert.deepEqual(results[0], results[1]);
  const {attempt_id: attempt, ...typed} = plain;
  assert.equal(results[0].request_sha256, await digest({kind:'uif.shared-lease.claim/3',
    request:{...typed, authority:identity(f.config,f.peers.native)}}));
  f.a = f.restart();
  assert.deepEqual(await f.call('native','claim/observe',marked), results[0]);
  for (const path of ['claim', 'claim/observe']) {
    await assert.rejects(f.call('native',path,plain), /original_request_changed/);
    await assert.rejects(f.call('native',path,{...marked,first_intent_sha256:hex('c')}), /original_request_changed/);
  }
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM claim_generations').n, 1);
  assert.equal(f.a.row('SELECT fence FROM assessment_dispatch WHERE attempt=?', attempt_id).fence, null);
  const grant = JSON.parse(f.a.row('SELECT body FROM grants').body);
  const reconstruction = hex('f');
  await f.call('control','grant',{request_id:'rotate-active-original',previous_sha256:await digest(grant),
    grant:{...grant,reconstruction_sha256:reconstruction}});
  const rotated = {...marked, reconstruction_sha256:reconstruction, request_id:await digest({
    claim:scope,recovery:reconstruction,authority:identity(f.config,f.peers.native),epoch:2})};
  await assert.rejects(f.call('native','claim',rotated), /issue_or_attempt_already_claimed/);
  assert.deepEqual(await f.call('native','claim/observe',marked),results[0]);
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM claim_generations').n, 1);
  const otherScope = {...scope,key:{...scope.key,issue_id:crypto.randomUUID()}};
  const other = await f.addGrant(otherScope, 'unmarked');
  await assert.rejects(f.call('native','claim',{...other, first_intent_sha256:marker}), /assessment_first_claim_not_enrolled/);
  const existing = await f.call('morgan','claim',other);
  const {attempt_id: unmarkedAttempt, ...ordinary} = other;
  assert.equal(existing.request_sha256, await digest({kind:'uif.shared-lease.claim/3',
    request:{...ordinary, authority:identity(f.config,f.peers.morgan)}}));
});

test('a waiting marked claim sees a changed control epoch or grant at commit', async () => {
  for (const change of ['stop', 'rotate']) {
    const f = fixture();
    await f.resume();
    const attempt_id = 'cf-intake-' + hex('3') + '-assess';
    const claim = await f.addGrant(scope, attempt_id, start + 800, {},
      {attempt_id, first_intent_sha256:hex('a')});
    const receipt = f.a.receipt.bind(f.a);
    f.a.receipt = async (...args) => {
      if (args[2] === '/v1/claim') {
        if (change === 'stop') {
          await f.call('control','control',{request_id:'stop', expected_coordinator_epoch:2, stopped:true});
          await f.call('control','control',{request_id:'resume-later', expected_coordinator_epoch:3, stopped:false});
          assert.deepEqual({...f.a.control()}, {epoch:4, stopped:0});
        } else {
          const grant = JSON.parse(f.a.row('SELECT body FROM grants').body);
          await f.call('control','grant',{request_id:'rotate',previous_sha256:await digest(grant),
            grant:{...grant,reconstruction_sha256:hex('d')}});
        }
      }
      return receipt(...args);
    };
    await assert.rejects(f.call('native','claim',claim),
      change === 'stop' ? /admission_stopped_or_epoch_changed/ : /reconstruction_or_attempt_unverified/);
    await assert.rejects(f.call('native','claim/observe',claim), /original_claim_not_found/);
    assert.equal(f.a.row('SELECT COUNT(*) AS n FROM claim_generations').n, 0);
  }
});

test('pre-upgrade plain marked claim remains observable without acquiring first-entry provenance', async () => {
  const f = fixture();
  await f.resume();
  const attempt_id = 'cf-intake-' + hex('2') + '-assess', first_intent_sha256 = hex('a');
  const marked = await f.addGrant(scope, attempt_id, start + 800, {}, {attempt_id, first_intent_sha256});
  const {first_intent_sha256: marker, ...plain} = marked;
  const old = await f.call('native', 'claim', marked);
  // Reopen the exact pre-upgrade on-disk shapes: plain full request fingerprint,
  // and a generation record containing the enrolled grant, lease and request ID.
  const priorGeneration = JSON.parse(f.a.row('SELECT record FROM claim_generations').record);
  delete priorGeneration.first_intent_sha256;
  f.a.put('UPDATE claim_generations SET record=?', canonical(priorGeneration));
  f.a.put('UPDATE receipts SET fingerprint=? WHERE peer=? AND id=?',
    await digest({path:'/v1/claim', body:plain}), f.a.peerKey(f.peers.native), plain.request_id);
  f.a = f.restart();
  assert.deepEqual(await f.call('native','claim/observe',plain), old);
  for (const path of ['claim','claim/observe'])
    await assert.rejects(f.call('native',path,marked), /original_request_changed/);
  const entry = {current:old.lease,attempt_id,first_intent_sha256:marker};
  assert.deepEqual(await f.call('native','dispatch/observe',entry), {entered:false});
  await assert.rejects(f.call('native','dispatch/enter',entry), /assessment_first_claim_original_required/);
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM claim_generations').n, 1);
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
    advance:{request_id:await digest({lease:fence.lease_id,attempt:a.successor}),current:a.current,expected_attempt_id:a.attempt,
      attempt_id:a.successor},
  };
}

test('an independently attested assessment CAS arms only its single native investigation entrant', async () => {
  const f = fixture(), a = await adjudicatedAssessment(f,'approve');
  const {original,advance} = await assessmentTransition(a,await postExactAssessment(f,a));
  const approval = {current:a.current,previous:a.attempt,successor:a.successor};
  const input = {current:a.current,advance};
  const enter = (body = input) => f.call('native','attempt/successor/enter',body);
  const observe = (body = input) => f.call('native','attempt/successor/observe',body);
  assert.deepEqual(await f.call('native','attempt/attest/native',approval),{matches:false});
  await assert.rejects(enter(), /assessment_successor_entry_not_current/);
  await f.call('control','attempt/attest',original);
  assert.deepEqual(await f.call('native','attempt/attest/native',approval),{matches:true});
  await assert.rejects(f.call('native','claim/attempt',{...advance,request_id:'different-native-cas'}),
    /assessment_successor_original_unverified/);
  assert.equal(f.a.row('SELECT COUNT(*) AS n FROM recovery_entries').n,0);
  await f.call('native','claim/attempt',advance); // lose the CAS reply and reopen before entry
  f.a = f.restart();
  assert.deepEqual(await observe(), {entered:false});
  assert.deepEqual(await f.call('native','attempt/attest/native',approval), {matches:false});
  for (const role of ['morgan','control','quota'])
    await assert.rejects(f.call(role,'attempt/successor/enter',input),/peer_role_not_granted/);
  await assert.rejects(enter({...input,advance:{...advance,request_id:'other-cas'}}), /assessment_successor_entry_not_current/);
  await assert.rejects(f.call('native','recovery/enter',{current:a.current,attempt_id:a.successor,original:{
    request_id:advance.request_id, request_sha256:hex('b'), installation_sha256:f.a.realm, session_id:f.peers.native.session_id}}),
    /native_recovery_original_not_current/);
  const send = await f.request(a.current,await f.quota(),{attempt:a.successor,id:hex('b')});
  for (const candidate of [undefined,hex('a'),a.permit])
    await assert.rejects(f.call('native','send',send,candidate), /native_recovery_permit_required/);
  const entries = await Promise.all([enter(),enter(),enter()]);
  const winners = entries.filter(x => x.entered);
  assert.equal(winners.length,1);
  assert.match(winners[0].permit,/^[0-9a-f]{64}$/);
  assert.deepEqual(entries.filter(x => !x.entered),[{entered:false},{entered:false}]);
  assert.deepEqual(await observe(),{entered:true});
  assert.deepEqual(await enter(),{entered:false});
  f.respond = async () => {throw Error('original investigation reply missing');};
  await assert.rejects(f.call('native','send',send,winners[0].permit),/original_external_effect_unknown/);
  f.a = f.restart();
  const after = f.calls.length;
  const different = await f.request(a.current,await f.quota(),{attempt:a.successor,id:hex('c')});
  await assert.rejects(f.call('native','send',different,winners[0].permit),/uncertain_original_effect/);
  await assert.rejects(f.call('native','send',send,winners[0].permit),/original_already_recorded/);
  assert.equal(f.calls.length,after);
  assert.equal(f.a.row('SELECT state FROM effects WHERE id=?',send.intent.operation.original_id).state,'sending');
});

test('uncertain investigation entry and historical consumed CAS never mint replacement permission', async () => {
  for (const mode of ['lost-entry','historical','revoked-during-entry']) {
    const f = fixture(), a = await adjudicatedAssessment(f,'approve');
    const {original,advance} = await assessmentTransition(a,await postExactAssessment(f,a));
    await f.call('control','attempt/attest',original);
    await f.call('native','claim/attempt',advance);
    const input = {current:a.current,advance};
    if (mode === 'historical') {
      f.a.put('DELETE FROM recovery_entries WHERE fence=?',canonical(a.current.fence));
      assert.deepEqual(await f.call('native','attempt/successor/read',input),{matches:true});
      await assert.rejects(f.call('native','attempt/successor/enter',input),/assessment_successor_entry_not_current/);
    } else {
      f.storage.sync = async () => {
        if (mode === 'lost-entry') throw Error('lost reply');
        await f.call('control','revoke',{request_id:'revoke',fence:a.current.fence});
      };
      await assert.rejects(f.call('native','attempt/successor/enter',input),/lost reply|assessment_successor_entry_not_current/);
    }
    f.a = f.restart();
    if (mode === 'lost-entry') {
      assert.deepEqual(await f.call('native','attempt/successor/observe',input),{entered:true});
      assert.deepEqual(await f.call('native','attempt/successor/enter',input),{entered:false});
    } else await assert.rejects(f.call('native','attempt/successor/enter',input),/assessment_successor_entry_not_current/);
    const send = await f.request(a.current,await f.quota(),{attempt:a.successor,id:hex('b')});
    await assert.rejects(f.call('native','send',send), /native_recovery_permit_required/);
    assert.equal(f.calls.length,2);
  }
});
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

test('native recovers the consumed assessment edge using only its current lease and exact original CAS', async () => {
  const f = fixture(), a = await adjudicatedAssessment(f, 'approve');
  const posted = await postExactAssessment(f, a);
  const {original, advance} = await assessmentTransition(a, posted);
  const query = {current:a.current,advance};
  const read = (input = query) => f.call('native','attempt/successor/read',input);
  assert.deepEqual(await read(), {matches:false});
  await f.call('control','attempt/attest',original); // control loses its first ACK
  assert.deepEqual(await read(), {matches:false});
  // A separately accepted equivalent original must not replace the first writer.
  await f.call('control','attempt/attest',{...original,request_id:'second-independent-original'});
  const record = f.a.row('SELECT issuer,body FROM assessment_attestations');
  assert.equal(record.issuer,f.a.peerKey(f.peers.control));
  assert.equal(record.body,canonical(original));
  await f.call('native','claim/attempt',advance); // native loses its CAS ACK
  f.a = f.restart();
  assert.deepEqual(await f.call('control','attempt/attest',original),
    {terminal_receipt_sha256:original.terminal_receipt_sha256});
  const changes = () => f.a.row('SELECT total_changes() AS n').n;
  const before = changes();
  assert.deepEqual(await read(),{matches:true});
  for (const other of ['morgan','control','quota'])
    await assert.rejects(f.call(other,'attempt/successor/read',query),/peer_role_not_granted/);
  await assert.rejects(f.a.handle('/v1/attempt/successor/read',query,{...f.peers.native,session_id:'retired'}),
    /peer_role_not_granted/);
  assert.deepEqual(await read({...query,advance:{...advance,request_id:'unknown-native-original'}}),{matches:false});
  assert.deepEqual(await read({...query,advance:{...advance,expected_attempt_id:'other'}}),{matches:false});
  assert.equal(changes(),before);
  f.setTime(start + 10);
  const renewed = (await f.call('native','renew',{request_id:'renew-owner',current:a.current})).lease;
  assert.deepEqual(await read(),{matches:false});
  assert.deepEqual(await read({current:renewed,advance}),{matches:true});
  const onward = {current:renewed,expected_coordinator_epoch:renewed.fence.coordinator_epoch,
    successor_session:f.peers.morgan.session_id,quiesced_sha256:hex('7'),reconstruction_sha256:hex('c')};
  await f.call('control','attest',{request_id:'witness-onward',fact:{kind:'handoff',...onward}});
  const winner = await f.call('native','attempt/successor/enter',{current:renewed,advance});
  const moved = (await f.call('native','handoff',{request_id:'handoff-onward',...onward},winner.permit)).lease;
  assert.deepEqual(await read({current:renewed,advance}),{matches:false});
  assert.deepEqual(await read({current:moved,advance}),{matches:false});
  assert.equal(f.calls.length,2);
});

test('historical edge, mismatched control original and next-epoch owner cannot supply marked attestation custody', async () => {
  for (const mode of ['historical','stop','changed-issuer','changed-hash']) {
    const f = fixture(), a = await adjudicatedAssessment(f, 'approve');
    const {original,advance} = await assessmentTransition(a,await postExactAssessment(f,a));
    await f.call('control','attempt/attest',original);
    if (mode === 'historical') {
      await f.call('native','claim/attempt',advance);
      // Older releases retained the edge and original control receipt only.
      f.a.put('DELETE FROM assessment_attestations');
      await f.call('control','attempt/attest',original);
      assert.equal(f.a.row('SELECT COUNT(*) AS n FROM assessment_attestations').n,0);
    } else if (mode === 'changed-issuer') {
      f.a.put('UPDATE assessment_attestations SET issuer=?',f.a.peerKey(f.peers.native));
    } else if (mode === 'changed-hash') {
      f.a.put('UPDATE assessment_attestations SET body=?',canonical({...original,terminal_receipt_sha256:hex('e')}));
    }
    if (mode === 'changed-issuer' || mode === 'changed-hash')
      await assert.rejects(f.call('native','claim/attempt',advance),/assessment_successor_original_unverified/);
    if (mode === 'stop') {
      await f.call('native','claim/attempt',advance);
      await f.call('control','control',{request_id:'stop',stopped:true,expected_coordinator_epoch:2});
      await f.call('control','control',{request_id:'resume-later',stopped:false,expected_coordinator_epoch:3});
    }
    f.a = f.restart();
    const input = {current:a.current,advance};
    assert.deepEqual(await f.call('native','attempt/successor/read',input),{matches:false});
    if (mode === 'historical')
      assert.deepEqual(await f.call('native','attempt/successor/read',{...input,original}),{matches:true});
  }
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
  // Only the investigation entrant can begin the successor provider work.
  const entry = await f.call('native','attempt/successor/enter',{current:a.current,advance});
  const next = await f.request(a.current,await f.quota(),{attempt:a.successor,id:hex('b')});
  f.respond = async () => Response.json({model:route.model,choices:[{}],usage:{neurons:0.007}},
    {headers:{'cf-aig-log-id':'fixture-follow-on'}});
  assert.equal((await f.call('native','send',next,entry.permit)).status,200);
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
