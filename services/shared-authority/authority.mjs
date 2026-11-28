import { deriveFreshAssessmentCommentFromVerifiedOriginal } from './fresh-assessment-report.mjs';
import {
  need,
  Refusal,
  canonical,
  equal,
  digest,
  digestBytes,
  fields,
  uint,
  sha,
  token,
  uuid,
  now,
  day,
  scopeSource,
  identity,
  wire,
  models,
  bytes64,
  readBytes,
  json,
} from './protocol.mjs';

import { sourceTables, sourceRoute } from './source-overlay.mjs';

const tables = [
  ...sourceTables,
  'CREATE TABLE IF NOT EXISTS installation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), realm TEXT NOT NULL, anchor TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS authorizations (kind TEXT NOT NULL,id TEXT NOT NULL,realm TEXT NOT NULL,PRIMARY KEY(kind,id))',
  'CREATE TABLE IF NOT EXISTS control (singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL, stopped INTEGER NOT NULL)',
  'INSERT OR IGNORE INTO control VALUES (1,1,1)',
  'CREATE TABLE IF NOT EXISTS startup (unit TEXT NOT NULL, stage TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(unit,stage))',
  'CREATE TABLE IF NOT EXISTS grants (issue TEXT PRIMARY KEY, body TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS claims (issue TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL, lease TEXT NOT NULL, state TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS receipts (peer TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(peer,id))',
  'CREATE TABLE IF NOT EXISTS completions (fence TEXT PRIMARY KEY, completion TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS claim_generations (issue TEXT NOT NULL,generation INTEGER NOT NULL,record TEXT NOT NULL,PRIMARY KEY(issue,generation))',
  'CREATE TABLE IF NOT EXISTS provider_results (id TEXT PRIMARY KEY,response BLOB NOT NULL,gateway TEXT,issue TEXT NOT NULL,policy TEXT NOT NULL,body_sha TEXT NOT NULL,ack TEXT)',
  'CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, issue TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS assessment_dispatch (attempt TEXT PRIMARY KEY, issue TEXT NOT NULL, intent_sha TEXT NOT NULL, fence TEXT, permit_sha TEXT, provider_op TEXT, report_binding_sha TEXT)',
  'CREATE TABLE IF NOT EXISTS next_attempts (fence TEXT NOT NULL, previous TEXT NOT NULL, successor TEXT NOT NULL, PRIMARY KEY(fence,previous,successor))',
  'CREATE TABLE IF NOT EXISTS review_bindings (attempt TEXT PRIMARY KEY, body TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS carryovers (account TEXT NOT NULL,id TEXT NOT NULL,amount INTEGER NOT NULL,receipt TEXT NOT NULL, PRIMARY KEY(account,id))',
  'CREATE TABLE IF NOT EXISTS facts (id TEXT PRIMARY KEY, body TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS destination_results (id TEXT PRIMARY KEY, result TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS budget_breaches (account TEXT PRIMARY KEY, original TEXT NOT NULL, observed INTEGER NOT NULL)',
  'CREATE TABLE IF NOT EXISTS usage (account TEXT PRIMARY KEY, receipt TEXT NOT NULL)',
  `CREATE TABLE IF NOT EXISTS usage_checkpoints (
     account TEXT PRIMARY KEY, receipt_sha TEXT NOT NULL, checkpoint_sha TEXT NOT NULL,
     opening_sha TEXT NOT NULL, unresolved_upper INTEGER NOT NULL, opening_upper INTEGER NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS effects (id TEXT PRIMARY KEY, issue TEXT NOT NULL, op TEXT NOT NULL, attempt TEXT NOT NULL,
     account TEXT, amount INTEGER NOT NULL, state TEXT NOT NULL, receipt TEXT, settled_at INTEGER)`,
  'CREATE INDEX IF NOT EXISTS effects_issue ON effects(issue,state)',
  'CREATE INDEX IF NOT EXISTS effects_account ON effects(account,state,settled_at)',
];
const LIMIT = 7500000;
const NATIVE_UNIT = 'stokowski-foo.service';
const NORMAL_STAGE = /^uif1313-normal-[a-z0-9][a-z0-9.-]{1,65}$/;
const peerRoles = ['native', 'morgan'];
const leaseKind = (name) => 'uif.shared-lease.' + name + '/3';
const issueKey = (s) => s.key.workspace_id + ':' + s.key.issue_id;
const boundedRequest = (deadline) =>
  Number.isSafeInteger(deadline) && deadline > 0 && deadline <= 8640000000000;
function only(peer, ...roles) {
  need(roles.includes(peer.role), 'peer_role_not_granted', 403);
}
function list(a, check, max) {
  return (
    Array.isArray(a) && a.length > 0 && a.length <= max && a.every(check) && new Set(a).size === a.length
  );
}
function fenceShape(c, f, historical = false) {
  fields(
    f,
    'scope',
    'source',
    'owner_session',
    'coordinator_epoch',
    'generation',
    'lease_id',
    'authority_id',
    'deployment_sha256',
    'policy_sha256',
  );
  scopeSource(c, f.scope, f.source);
  need(
    token(f.owner_session) &&
      token(f.lease_id) &&
      uint(f.generation) &&
      f.generation > 0 &&
      uint(f.coordinator_epoch) &&
      f.coordinator_epoch > 0 &&
      f.authority_id === c.authority_id &&
      sha(f.deployment_sha256) &&
      sha(f.policy_sha256) &&
      (historical ||
        (f.deployment_sha256 === c.deployment_sha256 && f.policy_sha256 === c.policy_sha256)),
    'operation_unbound',
  );
}
function operationShape(c, op, historical = false) {
  fields(op, 'fence', 'original_id', 'payload_sha256');
  fenceShape(c, op.fence, historical);
  need(token(op.original_id) && sha(op.payload_sha256), 'operation_unbound');
}
function projectionOK(p, route, t, end, liability, maximum) {
  need(
    route &&
      equal(p.route, route) &&
      p.free_standard_no_fallback === true &&
      p.completeness_verified === true &&
      [p.upper, p.gap_upper, liability, maximum].every((x) => uint(x) && x < LIMIT) &&
      maximum > 0 &&
      p.upper + p.gap_upper + liability + maximum < LIMIT &&
      boundedRequest(p.covered_through) &&
      boundedRequest(p.valid_until) &&
      p.covered_through <= t &&
      t - p.covered_through <= 90 &&
      t < end &&
      end <= p.valid_until &&
      p.utc_day === day(t) &&
      day(p.covered_through) === day(t) &&
      day(end) === day(t),
    'free_headroom_or_evidence_unavailable',
  );
}

// One object for ALL peers and accounts. No network I/O occurs inside transactionSync.
export class Authority {
  constructor(
    storage,
    c,
    {
      clock = now,
      provider = (request) => fetch(request),
      providerToken,
      reviewerProviderToken,
      linearAuthorization,
      quotaVerifier,
      trust = null,
    } = {},
  ) {
    this.store = storage;
    this.sql = storage.sql;
    this.c = c;
    this.realmReady = digest({ configuration: c, trust }).then((hash) => (this.realm = hash));
    this.anchor = canonical({
      authority: c.authority_id,
      workspace: c.workspace_id,
      project: c.project_id,
      repository: c.repository_id,
      peers: c.peers,
      trust,
    });
    this.clock = clock;
    this.provider = provider;
    this.providerToken = providerToken;
    this.reviewerProviderToken = reviewerProviderToken;
    this.linearAuthorization = linearAuthorization;
    this.inflight = new Set();
    this.quotaVerifier = quotaVerifier;
    storage.transactionSync(() => {
      for (const statement of tables) this.sql.exec(statement);
      if (c.reviewer_route) {
        const a = c.reviewer_route.account_id,
          p = c.reviewer_opening_liability;
        need(
          p &&
            token(p.original_id) &&
            sha(p.receipt_sha256) &&
            uint(p.millineurons) &&
            p.millineurons >= 2489344,
          'opening_review_liability_unbound',
        );
        this.put(
          'INSERT OR IGNORE INTO carryovers VALUES (?,?,?,?)',
          a,
          p.original_id,
          p.millineurons,
          p.receipt_sha256,
        );
        const row = this.row('SELECT * FROM carryovers WHERE account=? AND id=?', a, p.original_id);
        need(
          row.amount === p.millineurons && row.receipt === p.receipt_sha256,
          'opening_liability_changed',
        );
      }
    });
  }
  row(query, ...args) {
    return [...this.sql.exec(query, ...args)][0];
  }
  put(query, ...args) {
    this.sql.exec(query, ...args);
  }
  commissioned() {
    return this.row('SELECT realm FROM installation WHERE singleton=1')?.realm === this.realm;
  }
  authorized(kind, id) {
    return (
      this.row('SELECT realm FROM authorizations WHERE kind=? AND id=?', kind, id)?.realm === this.realm
    );
  }
  stamp(kind, id) {
    this.put(
      'INSERT INTO authorizations VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET realm=excluded.realm',
      kind,
      id,
      this.realm,
    );
  }
  bindStartup(binding) {
    const { unit, stage } = binding;
    this.put(
      'INSERT INTO startup VALUES (?,?,?) ON CONFLICT(unit,stage) DO UPDATE SET body=excluded.body',
      unit,
      stage,
      canonical(binding),
    );
    this.stamp('startup', canonical([unit, stage]));
  }
  normalStartup() {
    const row = this.row(
      "SELECT stage,body FROM startup WHERE unit=? AND json_type(body,'$.valid_until')='null'",
      NATIVE_UNIT,
    );
    return row && this.authorized('startup', canonical([NATIVE_UNIT, row.stage]))
      ? JSON.parse(row.body) : null;
  }
  peerKey(peer) {
    return this.realm + ':' + peer.session_id;
  }
  control() {
    return this.row('SELECT epoch,stopped FROM control WHERE singleton=1');
  }
  live(peer) {
    need(peer.expires_at > this.clock(), 'access_assertion_expired', 403);
  }
  grant(scope, source, peer) {
    const r = this.row('SELECT body FROM grants WHERE issue=?', issueKey(scope));
    need(
      r && this.commissioned() && this.authorized('grant', issueKey(scope)),
      'issue_grant_missing',
      403,
    );
    const g = JSON.parse(r.body);
    need(
      equal(g.scope, scope) &&
        equal(g.source, source) &&
        g.valid_until > this.clock() &&
        g.sessions.includes(peer.session_id),
      'issue_grant_not_current',
      403,
    );
    return g;
  }
  owner(peer, fence, live = true) {
    this.live(peer);
    return this.ownerFence(peer.session_id, fence, live);
  }
  // Internal observation only. All callers authenticate their actual principal
  // first; matching a configured owner session is not authentication as it.
  ownerFence(sessionId, fence, live = true) {
    scopeSource(this.c, fence.scope, fence.source);
    const row = this.row('SELECT * FROM claims WHERE issue=?', issueKey(fence.scope));
    need(row && row.state === 'active' && fence.owner_session === sessionId, 'owner_not_current');
    const lease = JSON.parse(row.lease);
    need(equal(lease.fence, fence), 'claim_fence_changed');
    if (live) {
      need(
        fence.authority_id === this.c.authority_id &&
          fence.deployment_sha256 === this.c.deployment_sha256 &&
          fence.policy_sha256 === this.c.policy_sha256,
        'authority_pins_changed',
      );
      const ctl = this.control();
      this.grant(fence.scope, fence.source, { session_id: sessionId });
      need(
        !ctl.stopped && ctl.epoch === fence.coordinator_epoch && lease.expires_at > this.clock(),
        'stopped_expired_or_superseded',
      );
    }
    return { row, lease };
  }
  reconciled(scope) {
    need(
      !this.row(
        "SELECT 1 AS held FROM effects WHERE issue=? AND state='sending' LIMIT 1",
        issueKey(scope),
      ),
      'uncertain_original_effect',
    );
  }
  requireUnstartedAttempt(current, attempt) {
    const f = current.fence, issue = issueKey(f.scope), key = canonical(f);
    const history = this.row('SELECT record FROM claim_generations WHERE issue=? AND generation=1', issue);
    const grant = this.grant(f.scope, f.source, { session_id: f.owner_session });
    const initial = history && JSON.parse(history.record);
    const ctl = this.control();
    need(
      f.generation === 1 && initial && equal(initial.lease, current) && equal(initial.grant, grant)
        && grant.sessions.length === 2 && !Object.hasOwn(grant, 'fresh_assessment')
        && f.coordinator_epoch === ctl.epoch && !ctl.stopped,
      'unstarted_initial_claim_changed',
    );
    // Only the original, never-advanced attempt. A settled effect is still work.
    // Stage advancement always retains its next_attempts edge for this fence.
    // Control attests workspace quiescence outside the authority separately.
    need(!this.row(`SELECT 1 FROM effects WHERE issue=?
      UNION ALL SELECT 1 FROM source_attempts WHERE attempt=?
      UNION ALL SELECT 1 FROM next_attempts WHERE fence=?
      UNION ALL SELECT 1 FROM review_bindings WHERE attempt=?
      UNION ALL SELECT 1 FROM completions WHERE fence=? LIMIT 1`,
    issue, attempt, key, attempt, key), 'attempt_already_started_or_unreconciled');
  }
  requireUnstartedRevocation(fact) {
    const { current, recovery } = fact, f = current.fence;
    fields(recovery, 'attempt_id', 'revocation_id');
    need(token(recovery.attempt_id) && token(recovery.revocation_id), 'invalid_recovery_original', 400);
    const row = this.row('SELECT state,attempt,lease FROM claims WHERE issue=?', issueKey(f.scope));
    const control = Object.values(this.c.peers).find((p) => p.role === 'control');
    const original = this.row('SELECT result FROM receipts WHERE peer=? AND id=?',
      this.peerKey(control), recovery.revocation_id);
    need(row?.state === 'revoked-unstarted' && row.attempt === recovery.attempt_id
      && equal(JSON.parse(row.lease), current) && original
      && equal(JSON.parse(original.result), { revoked: f, unstarted_attempt_id: recovery.attempt_id }),
    'exact_unstarted_revocation_required');
    this.requireUnstartedAttempt(current, recovery.attempt_id);
  }
  fact(id, expected) {
    const r = this.row('SELECT body FROM facts WHERE id=?', id);
    need(
      r && this.authorized('fact', id) && equal(JSON.parse(r.body), expected),
      'independent_original_evidence_missing',
    );
  }
  originalReceipt(peer, id, fingerprint) {
    const prev = this.row(
      'SELECT fingerprint,result FROM receipts WHERE peer=? AND id=?',
      this.peerKey(peer), id,
    );
    if (prev) {
      need(prev.fingerprint === fingerprint, 'original_request_changed');
      return JSON.parse(prev.result);
    }
  }
  async receipt(peer, id, path, body, apply, commission = false) {
    need(token(id), 'invalid_request_id', 400);
    const fingerprint = await digest({ path, body });
    return this.store.transactionSync(() => {
      this.live(peer);
      need(commission || this.commissioned(), 'installation_recommission_required');
      const prev = this.originalReceipt(peer, id, fingerprint);
      if (prev !== undefined) return prev;
      const result = apply();
      this.put(
        'INSERT INTO receipts VALUES (?,?,?,?)',
        this.peerKey(peer),
        id,
        fingerprint,
        canonical(result),
      );
      return result;
    });
  }
  async assessmentDispatch(path, body, peer) {
    only(peer, 'native');
    need(Object.values(this.c.peers).some((p) => p.role === 'native' &&
      p.session_id === peer.session_id), 'peer_role_not_granted', 403);
    fields(body, 'current', 'attempt_id', 'first_intent_sha256');
    fields(body.current, 'fence', 'expires_at');
    const fence = body.current.fence;
    fenceShape(this.c, fence);
    need(token(body.attempt_id) && sha(body.first_intent_sha256), 'invalid_dispatch_intent', 400);
    const read = () => {
      const { row, lease } = this.owner(peer, fence);
      const selected = this.grant(fence.scope, fence.source, peer).fresh_assessment;
      const retained = this.row('SELECT * FROM assessment_dispatch WHERE attempt=?', body.attempt_id);
      need(equal(lease, body.current) && row.attempt === body.attempt_id &&
        selected?.attempt_id === body.attempt_id && selected.first_intent_sha256 === body.first_intent_sha256 &&
        retained?.issue === issueKey(fence.scope) && retained.intent_sha === body.first_intent_sha256,
        'assessment_dispatch_not_selected');
      return retained;
    };
    if (path === '/v1/dispatch/observe') {
      const prior = read();
      return { entered: prior.fence === canonical(fence) };
    }
    const capability = Array.from(crypto.getRandomValues(new Uint8Array(32)),
      (byte) => byte.toString(16).padStart(2, '0')).join('');
    const hash = await digest(capability);
    const won = this.store.transactionSync(() => {
      const prior = read();
      if (prior.fence !== null) return false;
      const history = this.row('SELECT record FROM claim_generations WHERE issue=? AND generation=1',
        prior.issue);
      const first = history && JSON.parse(history.record);
      need(first?.first_intent_sha256 === prior.intent_sha && equal(first.lease.fence, fence),
        'assessment_first_claim_original_required');
      this.put('UPDATE assessment_dispatch SET fence=?,permit_sha=? WHERE attempt=? AND fence IS NULL',
        canonical(fence), hash, body.attempt_id);
      return true;
    });
    if (!won) return { entered: false };
    await this.store.sync();
    const current = read();
    need(current.fence === canonical(fence) && current.permit_sha === hash,
      'assessment_dispatch_original_changed');
    // Not a replayable receipt: only this committing invocation sees it.
    return { entered: true, permit: capability };
  }
  async assessmentPermitHash(path, operation, attempt, peer, permit) {
    if (!token(attempt)) return; // existing endpoint validates its request shape
    const retained = this.row('SELECT * FROM assessment_dispatch WHERE attempt=?', attempt);
    if (!retained) return; // unflagged/legacy and all other stages remain unchanged
    // No arbitrary report/comment/upload may inherit a provider permit.
    need(path === '/v1/send' || (path === '/v1/linear/comment' && retained.report_binding_sha),
      'assessment_dispatch_linear_original_unbound', 403);
    const fence = operation?.fence;
    need(sha(permit) && fence && retained.fence === canonical(fence) &&
      peer.role === 'native' && peer.session_id === fence.owner_session,
      'assessment_dispatch_permit_required', 403);
    const hash = await digest(permit);
    need(retained.permit_sha === hash, 'assessment_dispatch_permit_required', 403);
    return hash;
  }
  bindAssessmentProviderOperation(peer, op, attempt, permitHash, confirm) {
    const retained = this.row('SELECT * FROM assessment_dispatch WHERE attempt=?', attempt);
    if (!retained) return;
    need(sha(permitHash) && retained.permit_sha === permitHash &&
      retained.issue === issueKey(op.fence.scope) && retained.fence === canonical(op.fence) &&
      peer.role === 'native' && peer.session_id === op.fence.owner_session,
      'assessment_dispatch_permit_required', 403);
    const expected = canonical(op);
    if (!confirm && retained.provider_op === null) {
      // First exact operation pin and physical reservation are in one durable transaction.
      this.put('UPDATE assessment_dispatch SET provider_op=? WHERE attempt=? AND provider_op IS NULL',
        expected, attempt);
      return;
    }
    need(retained.provider_op === expected, 'assessment_dispatch_provider_original_changed');
  }
  async handle(path, body, peer, dispatchPermit) {
    await this.realmReady;
    this.live(peer);
    if (path === '/v1/dispatch/enter' || path === '/v1/dispatch/observe')
      return this.assessmentDispatch(path, body, peer);
    if (path === '/v1/publication/fence/read') {
      only(peer, 'control');
      need(Object.values(this.c.peers).some((x) => x.role === 'control' &&
        x.session_id === peer.session_id), 'peer_role_not_granted', 403);
      fields(body, 'fence', 'attempt_id');
      const fence = body.fence;
      fenceShape(this.c, fence, true);
      need(token(body.attempt_id), 'invalid_lookup', 400);
      if (!Object.values(this.c.peers).some((x) => x.role === 'native' &&
        x.session_id === fence.owner_session)) return { current: false };
      try {
        const { row } = this.ownerFence(fence.owner_session, fence);
        return { current: row.attempt === body.attempt_id };
      } catch (error) {
        if (error instanceof Refusal && [
          'owner_not_current', 'claim_fence_changed', 'authority_pins_changed',
          'issue_grant_missing', 'issue_grant_not_current', 'stopped_expired_or_superseded',
        ].includes(error.message)) return { current: false };
        throw error;
      }
    }
    if (path === '/v1/attempt/attest/native' || path === '/v1/attempt/successor/read') {
      const consumed = path === '/v1/attempt/successor/read';
      only(peer, 'native');
      need(Object.values(this.c.peers).some((x) => x.role === 'native' &&
        x.session_id === peer.session_id), 'peer_role_not_granted', 403);
      fields(body, 'current', 'original', ...(consumed ? ['advance'] : []));
      const { current, original, advance } = body;
      fields(current, 'fence', 'expires_at');
      fields(original, 'request_id', 'fence', 'previous', 'successor', 'terminal_receipt_sha256');
      fenceShape(this.c, current.fence, true);
      fenceShape(this.c, original.fence, true);
      need(token(original.request_id) && token(original.previous) && token(original.successor) &&
        original.previous !== original.successor && sha(original.terminal_receipt_sha256),
      'invalid_stage_transition', 400);
      if (!equal(current.fence, original.fence)) return { matches: false };
      const expected = await digest({ path: '/v1/attempt/attest', body: original });
      let advanceFingerprint;
      if (consumed) {
        fields(advance, 'request_id', 'current', 'expected_attempt_id', 'attempt_id');
        fields(advance.current, 'fence', 'expires_at');
        need(token(advance.request_id) && uint(advance.current.expires_at), 'invalid_stage_transition', 400);
        if (!equal(advance.current.fence, current.fence) || advance.expected_attempt_id !== original.previous ||
            advance.attempt_id !== original.successor) return { matches: false };
        advanceFingerprint = await digest({path: '/v1/claim/attempt', body: advance});
      }
      // No asynchronous boundary after the live owner/lease/grant recheck.
      // The tuple alone may come from another path and cannot prove this hash.
      this.live(peer);
      try {
        const { row, lease } = this.owner(peer, current.fence);
        const grant = this.grant(lease.fence.scope, lease.fence.source, peer);
        if (!equal(lease, current) || !grant.attempt_ids.includes(original.successor) ||
            !this.row('SELECT 1 FROM next_attempts WHERE fence=? AND previous=? AND successor=?',
              canonical(lease.fence), original.previous, original.successor)) return { matches: false };
        const successor = this.row('SELECT issue FROM attempts WHERE id=?', original.successor);
        if (consumed) {
          // After consumption, only the exact native CAS receipt for the same
          // control-attested edge proves how this still-current successor entered.
          if (row.attempt !== original.successor || successor?.issue !== issueKey(current.fence.scope) ||
              grant.fresh_assessment?.attempt_id !== original.previous ||
              !grant.fresh_assessment.report_binding) return { matches: false };
          const receipt = this.row('SELECT fingerprint,result FROM receipts WHERE peer=? AND id=?',
            this.peerKey(peer), advance.request_id);
          if (receipt?.fingerprint !== advanceFingerprint || !equal(JSON.parse(receipt.result),
              {project: 'OffMenu', attempt_id: original.successor, lease: advance.current})) return { matches: false };
        } else if (row.attempt !== original.previous || successor) return { matches: false };
        const control = Object.values(this.c.peers).filter((x) => x.role === 'control');
        if (control.length !== 1) return { matches: false };
        const receipt = this.row('SELECT fingerprint,result FROM receipts WHERE peer=? AND id=?',
          this.peerKey(control[0]), original.request_id);
        return { matches: receipt?.fingerprint === expected &&
          equal(JSON.parse(receipt.result), { terminal_receipt_sha256: original.terminal_receipt_sha256 }) };
      } catch (error) {
        if (error instanceof Refusal && [
          'owner_not_current', 'claim_fence_changed', 'authority_pins_changed',
          'issue_grant_missing', 'issue_grant_not_current', 'stopped_expired_or_superseded',
        ].includes(error.message)) return { matches: false };
        throw error;
      }
    }
    if (path === '/v1/attempt/attest/original') {
      only(peer, 'control');
      need(Object.values(this.c.peers).some((x) => x.role === 'control' &&
        x.session_id === peer.session_id), 'peer_role_not_granted', 403);
      fields(body, 'request_id', 'fence', 'previous', 'successor', 'terminal_receipt_sha256');
      fenceShape(this.c, body.fence, true);
      need(token(body.request_id) && token(body.previous) && token(body.successor) &&
        body.previous !== body.successor && sha(body.terminal_receipt_sha256),
      'invalid_stage_transition', 400);
      const expected = await digest({ path: '/v1/attempt/attest', body });
      this.live(peer);
      if (!this.commissioned()) return { matches: false };
      const row = this.row('SELECT fingerprint,result FROM receipts WHERE peer=? AND id=?',
        this.peerKey(peer), body.request_id);
      return { matches: row?.fingerprint === expected &&
        equal(JSON.parse(row.result), { terminal_receipt_sha256: body.terminal_receipt_sha256 }) };
    }
    this.put('INSERT OR IGNORE INTO installation VALUES (1,?,?)', this.realm, this.anchor);
    if (path === '/v1/commission') {
      only(peer, 'control');
      fields(
        body,
        'request_id',
        'previous_realm',
        'new_realm',
        'expected_coordinator_epoch',
        'reconstruction_sha256',
      );
      need(
        sha(body.previous_realm) &&
          body.new_realm === this.realm &&
          body.new_realm !== body.previous_realm &&
          sha(body.reconstruction_sha256),
        'invalid_recommission',
      );
      return this.receipt(
        peer,
        body.request_id,
        path,
        body,
        () => {
          const installed = this.row('SELECT realm,anchor FROM installation WHERE singleton=1'),
            ctl = this.control();
          need(
            installed.realm === body.previous_realm &&
              installed.anchor === this.anchor &&
              ctl.epoch === body.expected_coordinator_epoch,
            'prior_realm_or_trust_anchor_changed',
          );
          // Keep every original, claim generation, receipt and liability; require new
          // issue-specific grants and startup authority before admitting fresh effects.
          this.put('UPDATE installation SET realm=? WHERE singleton=1', this.realm);
          this.put('UPDATE control SET stopped=1,epoch=epoch+1 WHERE singleton=1');
          this.put("UPDATE claims SET state='revoked' WHERE state IN ('active','revoked-unstarted')");
          return { realm: this.realm, coordinator_epoch: ctl.epoch + 1, stopped: true };
        },
        true,
      );
    }
    need(path === '/v1/status' || this.commissioned(), 'installation_recommission_required');
    const c = this.c;
    if (path.startsWith('/v1/source/')) return sourceRoute(this, path, body, peer, operationShape);
    if (path === '/v1/transition/original') {
      fields(body, 'installation_sha256', 'session_id', 'request_id');
      need(
        sha(body.installation_sha256) &&
          token(body.session_id) &&
          token(body.request_id) &&
          (peer.role === 'control' || body.session_id === peer.session_id),
        'original_owner_required',
        403,
      );
      const r = this.row(
        'SELECT result FROM receipts WHERE peer=? AND id=?',
        body.installation_sha256 + ':' + body.session_id,
        body.request_id,
      );
      need(r, 'original_transition_absent', 404);
      return JSON.parse(r.result);
    }
    if (path === '/v1/status') {
      const ctl = this.control(),
        quota = Object.values(c.peers).find((p) => p.role === 'quota');
      return {
        ...identity(c, peer),
        role: peer.role,
        coordinator_epoch: ctl.epoch,
        stopped: !!ctl.stopped || !this.commissioned(),
        installation_sha256: this.realm,
        commissioned_sha256: this.row('SELECT realm FROM installation WHERE singleton=1').realm,
        lease_seconds: c.lease_seconds,
        quota_issuer: identity(c, quota),
        ...(peer.role === 'control' && {
          normal_startup_request_id: this.normalStartup()?.request_id ?? null,
        }),
      };
    }
    if (path === '/v1/startup/bind') {
      only(peer, 'control');
      fields(
        body,
        'request_id',
        'unit',
        'stage',
        'session_id',
        'coordinator_epoch',
        'valid_until',
        'evidence_sha256',
      );
      need(
        token(body.unit) &&
          token(body.stage) &&
          Object.values(c.peers).some((p) => p.role === 'native' && p.session_id === body.session_id) &&
          boundedRequest(body.valid_until) &&
          sha(body.evidence_sha256),
        'invalid_startup_binding',
        400,
      );
      return this.receipt(peer, body.request_id, path, body, () => {
        need(
          body.coordinator_epoch === this.control().epoch && body.valid_until > this.clock(),
          'startup_binding_changed',
        );
        need(body.unit !== NATIVE_UNIT || !this.normalStartup(), 'normal_startup_already_selected');
        this.bindStartup(body);
        return { request_id: body.request_id, unit: body.unit, stage: body.stage };
      });
    }
    if (path === '/v1/startup/read') {
      only(peer, 'native');
      fields(body, 'unit', 'stage', 'nonce');
      need(token(body.nonce), 'invalid_challenge', 400);
      const ctl = this.control(),
        r = this.row('SELECT body FROM startup WHERE unit=? AND stage=?', body.unit, body.stage);
      const b = r && JSON.parse(r.body),
        t = this.clock(),
        validUntil = b?.valid_until === null ? Infinity : b?.valid_until;
      need(
        b &&
          this.authorized('startup', canonical([body.unit, body.stage])) &&
          !ctl.stopped &&
          b.coordinator_epoch === ctl.epoch &&
          b.session_id === peer.session_id &&
          validUntil > t,
        'startup_not_bound',
      );
      return {
        ...body,
        ...identity(c, peer),
        coordinator_epoch: ctl.epoch,
        expires_at: Math.min(t + 5, validUntil, peer.expires_at),
      };
    }
    if (path === '/v1/control') {
      only(peer, 'control');
      const bindsStartup = Object.hasOwn(body ?? {}, 'startup');
      fields(body, 'request_id', 'expected_coordinator_epoch', 'stopped',
        ...(bindsStartup ? ['startup'] : []));
      need(typeof body.stopped === 'boolean', 'invalid_control', 400);
      if (bindsStartup) {
        fields(body.startup, 'stage', 'session_id', 'evidence_sha256',
          'coordinator_epoch', 'previous_request_id');
        const { stage, session_id, evidence_sha256, coordinator_epoch, previous_request_id } = body.startup;
        need(!body.stopped && token(stage) && NORMAL_STAGE.test(stage) && sha(evidence_sha256) &&
          uint(coordinator_epoch) && coordinator_epoch > 0 &&
          (previous_request_id === null || token(previous_request_id)) &&
          Object.values(c.peers).some((p) => p.role === 'native' && p.session_id === session_id),
        'invalid_startup_binding', 400);
      }
      return this.receipt(peer, body.request_id, path, body, () => {
        const ctl = this.control();
        need(ctl.epoch === body.expected_coordinator_epoch, 'control_epoch_changed');
        // A new explicit stop supersedes prepared activations even when already stopped.
        // receipt() has already returned any matching original before this transaction body.
        const epoch = body.stopped || ctl.stopped ? ctl.epoch + 1 : ctl.epoch;
        if (bindsStartup) {
          need(body.startup.coordinator_epoch === epoch &&
            body.startup.previous_request_id === (this.normalStartup()?.request_id ?? null),
          'normal_startup_selection_changed');
          // The protected release action selects one installed stage. Its original
          // receipt is retained even when a later selection supersedes this row.
          this.put('DELETE FROM startup WHERE unit=? AND stage<>?', NATIVE_UNIT, body.startup.stage);
          this.bindStartup({ ...body.startup, request_id: body.request_id, unit: NATIVE_UNIT,
            valid_until: null });
        }
        this.put('UPDATE control SET epoch=?,stopped=? WHERE singleton=1', epoch, Number(body.stopped));
        const result = { coordinator_epoch: epoch, stopped: body.stopped };
        return bindsStartup ? { ...result, expected_coordinator_epoch: body.expected_coordinator_epoch,
          startup: { ...body.startup, unit: NATIVE_UNIT } } : result;
      });
    }
    if (path === '/v1/grant') {
      only(peer, 'control');
      fields(body, 'request_id', 'grant', 'previous_sha256');
      const g = body.grant;
      fields(
        g,
        'scope',
        'source',
        'sessions',
        'attempt_ids',
        'reconstruction_sha256',
        'valid_until',
        'allow_comments',
        'allow_uploads',
        'allow_provider',
        'review_of',
        ...(Object.hasOwn(g, 'result_retention') ? ['result_retention'] : []),
        ...(Object.hasOwn(g, 'fresh_assessment') ? ['fresh_assessment'] : []),
      );
      scopeSource(c, g.scope, g.source);
      if (g.result_retention != null) {
        const p = g.result_retention;
        fields(p, 'max_response_bytes', 'max_retained_results', 'readers', 'evidence_sha256');
        need(
          uint(p.max_response_bytes) &&
            p.max_response_bytes > 0 &&
            p.max_response_bytes <= 256 * 1024 &&
            uint(p.max_retained_results) &&
            p.max_retained_results > 0 &&
            p.max_retained_results <= 128 &&
            sha(p.evidence_sha256) &&
            list(
              p.readers,
              (id) => Object.values(c.peers).some((x) => x.session_id === id && x.role !== 'quota'),
              3,
            ),
          'invalid_result_retention_grant',
        );
      }
      const workers = Object.values(c.peers)
        .filter((p) => peerRoles.includes(p.role))
        .map((p) => p.session_id);
      need(
        typeof g.allow_provider === 'boolean' &&
          typeof g.allow_comments === 'boolean' &&
          typeof g.allow_uploads === 'boolean' &&
          list(g.sessions, (s) => workers.includes(s), 2) &&
          list(g.attempt_ids, token, 200) &&
          g.review_of &&
          typeof g.review_of === 'object' &&
          !Array.isArray(g.review_of) &&
          Object.entries(g.review_of).every(
            ([r, a]) =>
              r !== a &&
              g.attempt_ids.includes(r) &&
              g.attempt_ids.includes(a) &&
              !Object.hasOwn(g.review_of, a),
          ) &&
          sha(g.reconstruction_sha256) &&
          boundedRequest(g.valid_until) &&
          (body.previous_sha256 === null || sha(body.previous_sha256)),
        'invalid_grant',
        400,
      );
      const fresh = Object.hasOwn(g, 'fresh_assessment') ? g.fresh_assessment : undefined;
      let reportBindingSHA = null;
      if (fresh !== undefined) {
        fields(fresh, 'attempt_id', 'first_intent_sha256',
          ...(Object.hasOwn(fresh, 'report_binding') ? ['report_binding'] : []));
        need(/^cf-intake-[a-f0-9]{64}-assess$/.test(fresh.attempt_id) &&
          fresh.attempt_id === g.attempt_ids[0] && !Object.hasOwn(g.review_of, fresh.attempt_id) &&
          sha(fresh.first_intent_sha256) && Object.values(c.peers).some((p) =>
            p.role === 'native' && g.sessions.includes(p.session_id)), 'invalid_fresh_assessment_grant', 400);
        if (Object.hasOwn(fresh, 'report_binding')) {
          const b = fresh.report_binding;
          fields(b, 'scope', 'issue_identifier', 'run', 'scope_sha256');
          need(equal(b.scope, g.scope) && typeof b.issue_identifier === 'string' &&
            /^[A-Z][A-Z0-9]*-[0-9]+$/.test(b.issue_identifier) && uint(b.run) && b.run > 0 &&
            sha(b.scope_sha256) && g.allow_comments && g.allow_provider && g.result_retention,
            'invalid_fresh_assessment_report_binding', 400);
          const bytes = new TextEncoder().encode('[' +
            ['OffMenu', g.scope.key.issue_id, b.scope_sha256, b.run].map(x => JSON.stringify(x)).join(', ') + ']');
          need(fresh.attempt_id === 'cf-intake-' + await digestBytes(bytes) + '-assess',
            'invalid_fresh_assessment_report_binding', 400);
          reportBindingSHA = await digest(b);
        }
      }
      const id = issueKey(g.scope),
        blob = canonical(g),
        grantSHA = await digest(g);
      const prior = this.row('SELECT body FROM grants WHERE issue=?', id)?.body;
      const priorSHA = prior ? await digest(JSON.parse(prior)) : null;
      return this.receipt(peer, body.request_id, path, body, () => {
        need(
          g.valid_until > this.clock() &&
            body.previous_sha256 === priorSHA &&
            this.row('SELECT body FROM grants WHERE issue=?', id)?.body === prior,
          'grant_preimage_changed',
        );
        const marked = this.row('SELECT attempt FROM assessment_dispatch WHERE issue=?', id);
        need(!marked || this.row('SELECT 1 FROM claims WHERE issue=?', id) ||
          fresh?.attempt_id === marked.attempt,
          'assessment_dispatch_original_changed');
        if (fresh !== undefined) {
          const existing = this.row('SELECT * FROM assessment_dispatch WHERE attempt=?', fresh.attempt_id);
          if (existing) {
            need(existing.issue === id && existing.intent_sha === fresh.first_intent_sha256 &&
              existing.report_binding_sha === reportBindingSHA,
              'assessment_dispatch_original_changed');
          } else {
            need(!this.row('SELECT 1 FROM claims WHERE issue=?', id) &&
              !this.row('SELECT 1 FROM attempts WHERE id=?', fresh.attempt_id),
              'assessment_dispatch_not_fresh');
            this.put('INSERT INTO assessment_dispatch VALUES (?,?,?,NULL,NULL,NULL,?)',
              fresh.attempt_id, id, fresh.first_intent_sha256, reportBindingSHA);
          }
        }
        // Changed scope, permissions or reconstruction must never be inherited by
        // an already-issued fence, including after a peer is removed and re-added.
        if (prior && prior !== blob)
          this.put("UPDATE claims SET state='revoked' WHERE issue=? AND state IN ('active','revoked-unstarted')", id);
        this.put(
          'INSERT INTO grants VALUES (?,?) ON CONFLICT(issue) DO UPDATE SET body=excluded.body',
          id,
          blob,
        );
        this.stamp('grant', id);
        return { grant_sha256: grantSHA };
      });
    }
    if (path === '/v1/attest') {
      only(peer, 'control');
      fields(body, 'request_id', 'fact');
      const f = body.fact;
      need(
        ['handoff', 'completion', 'original-terminal'].includes(f?.kind),
        'unsupported_attestation',
        400,
      );
      if (f.kind === 'completion') {
        fields(f, 'kind', 'completion');
        const x = f.completion;
        fields(x, 'fence', 'accepted', 'receipt_sha256');
        operationShape(
          c,
          {
            fence: x.fence,
            original_id: 'completion-attestation',
            payload_sha256: x.receipt_sha256,
          },
          true,
        );
        need(typeof x.accepted === 'boolean', 'invalid_completion', 400);
      } else if (f.kind === 'original-terminal') {
        fields(f, 'kind', 'operation', 'status', 'destination_receipt_sha256');
        operationShape(c, f.operation, true);
        need(
          ['verified', 'failed', 'denied'].includes(f.status) && sha(f.destination_receipt_sha256),
          'invalid_original_attestation',
          400,
        );
      } else {
        fields(
          f,
          'kind',
          'current',
          'successor_session',
          'expected_coordinator_epoch',
          'quiesced_sha256',
          'reconstruction_sha256',
          ...(Object.hasOwn(f, 'recovery') ? ['recovery'] : []),
        );
        operationShape(
          c,
          {
            fence: f.current.fence,
            original_id: 'handoff-attestation',
            payload_sha256: f.reconstruction_sha256,
          },
          true,
        );
        need(
          sha(f.quiesced_sha256) && token(f.successor_session) && uint(f.expected_coordinator_epoch),
          'invalid_handoff_attestation',
          400,
        );
      }
      const id = await digest(f);
      return this.receipt(peer, body.request_id, path, body, () => {
        if (f.kind === 'handoff' && Object.hasOwn(f, 'recovery')) this.requireUnstartedRevocation(f);
        if (f.kind === 'completion') {
          const key = canonical(f.completion.fence),
            blob = canonical(f.completion);
          const prev = this.row('SELECT completion FROM completions WHERE fence=?', key);
          need(!prev || prev.completion === blob, 'completion_already_bound');
          this.put('INSERT OR IGNORE INTO completions VALUES (?,?)', key, blob);
        }
        this.put('INSERT OR IGNORE INTO facts VALUES (?,?)', id, canonical(f));
        this.stamp('fact', id);
        return { fact_sha256: id };
      });
    }
    if (path === '/v1/reclaim' || path === '/v1/reclaim/observe') {
      only(peer, ...peerRoles);
      fields(
        body,
        'request_id',
        'previous',
        'scope',
        'source',
        'reconstruction_sha256',
        'expected_coordinator_epoch',
        'attempt_id',
        'previous_completion_sha256',
      );
      scopeSource(c, body.scope, body.source);
      const previous = body.previous,
        f = previous.fence;
      need(
        issueKey(f.scope) === issueKey(body.scope) &&
          token(body.attempt_id) &&
          sha(body.previous_completion_sha256) &&
          sha(body.reconstruction_sha256),
        'new_attempt_unbound',
      );
      const kind = 'uif.shared-lease.reclaim/1',
        authority = identity(c, peer),
        request_sha256 = await digest({ kind, request: { ...body, authority } });
      if (path === '/v1/reclaim/observe') {
        const r = this.row(
          'SELECT * FROM receipts WHERE peer=? AND id=?',
          this.peerKey(peer),
          body.request_id,
        );
        need(r, 'original_reclaim_absent', 404);
        need(
          r.fingerprint === (await digest({ path: '/v1/reclaim', body })),
          'original_request_changed',
        );
        return JSON.parse(r.result);
      }
      return this.receipt(peer, body.request_id, path, body, () => {
        const id = issueKey(body.scope),
          ctl = this.control(),
          old = this.row('SELECT * FROM claims WHERE issue=?', id);
        need(
          old?.state === 'released' &&
            equal(JSON.parse(old.lease), previous) &&
            !ctl.stopped &&
            body.expected_coordinator_epoch === ctl.epoch,
          'prior_claim_not_released_or_changed',
        );
        this.reconciled(body.scope);
        const completed = this.row('SELECT completion FROM completions WHERE fence=?', canonical(f));
        const history = this.row(
          'SELECT record FROM claim_generations WHERE issue=? AND generation=?',
          id,
          f.generation,
        );
        need(
          completed &&
            JSON.parse(completed.completion).receipt_sha256 === body.previous_completion_sha256 &&
            history &&
            JSON.parse(history.record).grant.reconstruction_sha256 !== body.reconstruction_sha256,
          'prior_completion_or_new_reconstruction_unverified',
        );
        const g = this.grant(body.scope, body.source, peer);
        need(
          g.reconstruction_sha256 === body.reconstruction_sha256 &&
            g.attempt_ids.includes(body.attempt_id) &&
            !Object.hasOwn(g.review_of, body.attempt_id) &&
            !this.row('SELECT id FROM attempts WHERE id=?', body.attempt_id),
          'fresh_attempt_unverified',
        );
        const { session_id, ...realm } = authority;
        const lease = {
          fence: {
            scope: body.scope,
            source: body.source,
            owner_session: session_id,
            coordinator_epoch: ctl.epoch,
            generation: f.generation + 1,
            lease_id: crypto.randomUUID(),
            ...realm,
          },
          expires_at: Math.min(this.clock() + c.lease_seconds, g.valid_until),
        };
        this.put('INSERT INTO attempts VALUES (?,?)', body.attempt_id, id);
        this.put(
          "UPDATE claims SET attempt=?,lease=?,state='active' WHERE issue=?",
          body.attempt_id,
          canonical(lease),
          id,
        );
        this.put(
          'INSERT INTO claim_generations VALUES (?,?,?)',
          id,
          lease.fence.generation,
          canonical({ grant: g, lease, request_id: body.request_id }),
        );
        return { request_id: body.request_id, request_kind: kind, request_sha256, lease };
      });
    }
    if (path === '/v1/completion/read') {
      only(peer, ...peerRoles);
      fields(body, 'current');
      const l = body.current;
      operationShape(c, {
        fence: l.fence,
        original_id: 'completion-read',
        payload_sha256: c.policy_sha256,
      });
      need(l.fence.owner_session === peer.session_id, 'original_owner_required', 403);
      const r = this.row('SELECT completion FROM completions WHERE fence=?', canonical(l.fence));
      need(r, 'original_completion_absent', 404);
      return JSON.parse(r.completion);
    }
    if (path === '/v1/revoke') {
      only(peer, 'control');
      const recover = Object.hasOwn(body ?? {}, 'unstarted_attempt_id');
      fields(body, 'request_id', 'fence', ...(recover ? ['unstarted_attempt_id'] : []));
      return this.receipt(peer, body.request_id, path, body, () => {
        const id = issueKey(body.fence.scope),
          r = this.row('SELECT lease,state,attempt FROM claims WHERE issue=?', id);
        need(r && (r.state === 'active' || (!recover && r.state === 'revoked-unstarted'))
          && equal(JSON.parse(r.lease).fence, body.fence), 'claim_fence_changed');
        if (recover) {
          need(r.attempt === body.unstarted_attempt_id, 'attempt_preimage_changed');
          this.requireUnstartedAttempt(JSON.parse(r.lease), r.attempt);
          this.put("UPDATE claims SET state='revoked-unstarted' WHERE issue=?", id);
          return { revoked: body.fence, unstarted_attempt_id: r.attempt };
        }
        this.put("UPDATE claims SET state='revoked' WHERE issue=?", id);
        return { revoked: body.fence };
      });
    }
    if (path === '/v1/review/bind') {
      only(peer, 'control');
      fields(
        body,
        'request_id',
        'current',
        'candidate',
        'author_operation',
        'author_receipt_sha256',
        'checkpoint_receipt_sha256',
        'send_prompt_sha256',
      );
      const { current, candidate, author_operation } = body;
      fields(
        candidate,
        'kind',
        'issue_id',
        'scope_sha256',
        'author_attempt_id',
        'review_attempt_id',
        'source',
        'candidate_tree',
        'review_prompt_sha256',
        ...(Object.hasOwn(candidate, 'source_candidate_sha256') ? ['source_candidate_sha256'] : []),
      );
      operationShape(c, author_operation);
      const f = current.fence;
      need(
        candidate.kind === 'uif.native-review-candidate/1' &&
          candidate.issue_id === f.scope.key.issue_id &&
          equal(candidate.source, f.source) &&
          sha(body.send_prompt_sha256) &&
          sha(candidate.scope_sha256) &&
          sha(candidate.review_prompt_sha256) &&
          sha(body.checkpoint_receipt_sha256) &&
          sha(body.author_receipt_sha256) &&
          (candidate.candidate_tree === null ||
            (typeof candidate.candidate_tree === 'string' &&
              /^[0-9a-f]{40}$/.test(candidate.candidate_tree))) &&
          equal(author_operation.fence, f),
        'review_candidate_not_bound',
      );
      const record = {
        fence: f,
        send_prompt_sha256: body.send_prompt_sha256,
        candidate,
        candidate_sha256: await digest(candidate),
        author_operation,
        author_receipt_sha256: body.author_receipt_sha256,
        checkpoint_receipt_sha256: body.checkpoint_receipt_sha256,
      };
      return this.receipt(peer, body.request_id, path, body, () => {
        const id = issueKey(f.scope),
          r = this.row('SELECT * FROM claims WHERE issue=?', id),
          g = this.row('SELECT body FROM grants WHERE issue=?', id);
        need(
          r?.state === 'active' &&
            equal(JSON.parse(r.lease), current) &&
            [candidate.author_attempt_id, candidate.review_attempt_id].includes(r.attempt) &&
            g &&
            JSON.parse(g.body).review_of[candidate.review_attempt_id] === candidate.author_attempt_id,
          'review_attempt_not_bound',
        );
        // A flagged fresh assess can leave its stage only by the separately
        // authenticated control attestation, never by the review-edge writer.
        need(!this.row('SELECT 1 FROM assessment_dispatch WHERE attempt=?',
          candidate.author_attempt_id), 'assessment_report_control_attestation_required');
        const overlay = this.row(
          'SELECT sealed,fence FROM source_attempts WHERE attempt=?',
          candidate.author_attempt_id,
        );
        if (overlay || Object.hasOwn(candidate, 'source_candidate_sha256')) {
          need(
            overlay &&
              overlay.fence === canonical(f) &&
              sha(overlay.sealed) &&
              candidate.candidate_tree === null &&
              candidate.source_candidate_sha256 === overlay.sealed,
            'frozen_source_candidate_required',
          );
        }
        this.reconciled(f.scope);
        const original = this.row('SELECT * FROM effects WHERE id=?', author_operation.original_id);
        need(
          original &&
            equal(JSON.parse(original.op), author_operation) &&
            original.state === 'verified' &&
            original.receipt === body.author_receipt_sha256 &&
            original.account === c.route?.account_id &&
            original.attempt === candidate.author_attempt_id,
          'original_author_not_verified',
        );
        const prior = this.row(
          'SELECT body FROM review_bindings WHERE attempt=?',
          candidate.review_attempt_id,
        );
        need(!prior || equal(JSON.parse(prior.body), record), 'review_candidate_already_bound');
        this.put(
          'INSERT OR IGNORE INTO review_bindings VALUES (?,?)',
          candidate.review_attempt_id,
          canonical(record),
        );
        this.put(
          'INSERT OR IGNORE INTO next_attempts VALUES (?,?,?)',
          canonical(f),
          candidate.author_attempt_id,
          candidate.review_attempt_id,
        );
        return record;
      });
    }
    if (path === '/v1/review/read') {
      only(peer, ...peerRoles);
      fields(body, 'current', 'review_attempt_id', 'author_attempt_id');
      const { lease } = this.owner(peer, body.current.fence);
      need(equal(body.current, lease), 'claim_fence_changed');
      const row = this.row('SELECT body FROM review_bindings WHERE attempt=?', body.review_attempt_id);
      need(row, 'original_review_binding_absent', 404);
      const record = JSON.parse(row.body);
      need(
        equal(record.fence, lease.fence) &&
          record.candidate.author_attempt_id === body.author_attempt_id,
        'review_author_or_generation_changed',
      );
      return record;
    }
    if (path === '/v1/attempt/attest') {
      only(peer, 'control');
      fields(body, 'request_id', 'fence', 'previous', 'successor', 'terminal_receipt_sha256');
      operationShape(c, {
        fence: body.fence,
        original_id: body.previous,
        payload_sha256: body.terminal_receipt_sha256,
      });
      need(token(body.successor) && body.previous !== body.successor, 'invalid_stage_transition');
      let checkFreshReport;
      if (this.row('SELECT 1 FROM assessment_dispatch WHERE attempt=?', body.previous)) {
        // Preserve same-original control replay after the stage has moved.
        const fingerprint = await digest({path, body});
        this.live(peer);
        need(this.commissioned(), 'installation_recommission_required');
        const prior = this.originalReceipt(peer, body.request_id, fingerprint);
        if (prior !== undefined) return prior;
        checkFreshReport = await this.freshAssessmentAttestationCheck(body);
      }
      return this.receipt(peer, body.request_id, path, body, () => {
        const r = this.row('SELECT * FROM claims WHERE issue=?', issueKey(body.fence.scope));
        need(
          r?.state === 'active' &&
            r.attempt === body.previous &&
            equal(JSON.parse(r.lease).fence, body.fence),
          'attempt_preimage_changed',
        );
        this.reconciled(body.fence.scope);
        // The report, current fence and admitted successor are reread inside
        // the same transaction that creates the independently controlled edge.
        checkFreshReport?.();
        this.put(
          'INSERT OR IGNORE INTO next_attempts VALUES (?,?,?)',
          canonical(body.fence),
          body.previous,
          body.successor,
        );
        return { terminal_receipt_sha256: body.terminal_receipt_sha256 };
      });
    }
    if (path === '/v1/usage/write') {
      only(peer, 'quota');
      fields(body, 'request_id', 'projection', 'source_receipt_sha256');
      const p = body.projection;
      fields(
        p,
        'route',
        'utc_day',
        'covered_through',
        'valid_until',
        'upper',
        'gap_upper',
        'free_standard_no_fallback',
        'completeness_verified',
      );
      need(
        this.allowedRoute(p.route) &&
          sha(body.source_receipt_sha256) &&
          boundedRequest(p.covered_through) &&
          boundedRequest(p.valid_until) &&
          p.covered_through <= this.clock() &&
          p.covered_through < p.valid_until &&
          p.utc_day === day(p.covered_through) &&
          uint(p.upper) &&
          uint(p.gap_upper) &&
          typeof p.free_standard_no_fallback === 'boolean' &&
          typeof p.completeness_verified === 'boolean',
        'invalid_usage_attestation',
        400,
      );
      need(token(body.request_id), 'invalid_request_id', 400);
      const original = this.originalReceipt(peer, body.request_id, await digest({ path, body }));
      if (original !== undefined) {
        this.live(peer);
        need(this.commissioned(), 'installation_recommission_required');
        return original;
      }
      // A service token identifies the submitter; it cannot verify account-wide
      // telemetry or bound uncoordinated spend. Read the independently installed
      // verifier's original observation via a private, fixed service binding.
      need(
        sha(c.quota_verification_sha256) && this.quotaVerifier,
        'independent_account_observer_unbound',
        503,
      );
      const verified = await this.quotaVerifier(body.source_receipt_sha256, p.route);
      fields(
        verified,
        'binding_sha256',
        'source_receipt_sha256',
        'projection',
        'coverage',
        'gap_evidence_sha256',
        'checkpoint',
      );
      fields(verified.checkpoint, 'sha256', 'opening_sha256', 'unresolved_upper', 'opening_upper');
      need(
        verified.binding_sha256 === c.quota_verification_sha256 &&
          verified.source_receipt_sha256 === body.source_receipt_sha256 &&
          sha(verified.gap_evidence_sha256) &&
          sha(verified.checkpoint.sha256) &&
          sha(verified.checkpoint.opening_sha256) &&
          uint(verified.checkpoint.unresolved_upper) && uint(verified.checkpoint.opening_upper) &&
          verified.checkpoint.unresolved_upper + verified.checkpoint.opening_upper <= p.upper &&
          verified.coverage === 'entire-account-all-routes-from-utc-midnight' &&
          equal(verified.projection, p),
        'independent_account_projection_unverified',
      );
      const issuer = identity(c, peer),
        provenance = {
          source_receipt_sha256: body.source_receipt_sha256,
          admission_binding_sha256: c.quota_verification_sha256,
        };
      const hash = await digest({
        issuer,
        projection: p,
        ...provenance,
        gap_evidence_sha256: verified.gap_evidence_sha256,
        checkpoint: verified.checkpoint,
      });
      const receipt = { issuer, receipt_sha256: hash, projection: p, ...provenance };
      return this.receipt(peer, body.request_id, path, body, () => {
        const prior = this.usage(p.route);
        const previousCheckpoint = this.checkpoint(p.route.account_id);
        need(!previousCheckpoint ||
          (previousCheckpoint.opening_sha === verified.checkpoint.opening_sha256 &&
            previousCheckpoint.opening_upper === verified.checkpoint.opening_upper),
          'quota_checkpoint_opening_changed');
        need(!previousCheckpoint || previousCheckpoint.unresolved_upper <= verified.checkpoint.unresolved_upper,
          'quota_checkpoint_unresolved_liability_reduced');
        need(
          !prior ||
            equal(receipt, prior) ||
            (p.covered_through > prior.projection.covered_through &&
              (p.utc_day !== prior.projection.utc_day || p.upper >= prior.projection.upper)),
          'usage_snapshot_not_monotonic',
        );
        this.put(
          'INSERT INTO usage VALUES (?,?) ON CONFLICT(account) DO UPDATE SET receipt=excluded.receipt',
          p.route.account_id,
          canonical(receipt),
        );
        this.put(
          `INSERT INTO usage_checkpoints VALUES (?,?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET
           receipt_sha=excluded.receipt_sha, checkpoint_sha=excluded.checkpoint_sha,
           unresolved_upper=excluded.unresolved_upper`,
          p.route.account_id, hash, verified.checkpoint.sha256, verified.checkpoint.opening_sha256,
          verified.checkpoint.unresolved_upper, verified.checkpoint.opening_upper,
        );
        this.stamp('quota', p.route.account_id);
        return { receipt_sha256: hash };
      });
    }
    if (path === '/v1/usage/read') {
      only(peer, 'native', 'morgan', 'quota');
      fields(body, 'route');
      const receipt = this.usage(body.route);
      need(
        this.allowedRoute(body.route) && this.authorized('quota', body.route.account_id) && receipt &&
          this.checkpointMatches(body.route.account_id, receipt),
        'usage_unavailable',
        404,
      );
      return receipt;
    }
    if (path === '/v1/linear/state') throw new Refusal('linear_atomic_state_cas_unavailable');
    if (path === '/v1/publication') throw new Refusal('offmenu_sole_git_publisher_required');
    if (['/v1/send', '/v1/linear/comment', '/v1/linear/upload'].includes(path)) {
      only(peer, ...peerRoles);
      const operation = path === '/v1/send' ? body.intent?.operation : body.operation;
      const attempt = path === '/v1/send' ? body.intent?.attempt_id : body.attempt_id;
      const permitHash = await this.assessmentPermitHash(path, operation, attempt, peer, dispatchPermit);
      const id = operation?.original_id;
      need(token(id) && !this.inflight.has(id), 'original_in_flight');
      this.inflight.add(id);
      try {
        if (path === '/v1/send') return await this.send(body, peer, permitHash);
        if (path === '/v1/linear/upload') return await this.upload(body, peer);
        return await this.comment(body, peer, permitHash);
      } finally {
        this.inflight.delete(id);
      }
    }
    if (path === '/v1/reconcile') {
      only(peer, 'control');
      fields(body, 'request_id', 'operation', 'status', 'destination_receipt_sha256');
      const op = body.operation;
      operationShape(c, op, true);
      need(
        ['verified', 'failed', 'denied'].includes(body.status) && sha(body.destination_receipt_sha256),
        'invalid_terminal',
      );
      const fact = {
        kind: 'original-terminal',
        operation: op,
        status: body.status,
        destination_receipt_sha256: body.destination_receipt_sha256,
      };
      const factId = await digest(fact);
      return this.receipt(peer, body.request_id, path, body, () => {
        const r = this.row('SELECT * FROM effects WHERE id=?', op.original_id);
        need(
          r && equal(JSON.parse(r.op), op) && !this.inflight.has(op.original_id),
          'original_not_reconcilable',
        );
        this.fact(factId, fact);
        need(
          r.state === 'sending' ||
            (r.state === body.status && r.receipt === body.destination_receipt_sha256),
          'terminal_receipt_immutable',
        );
        if (r.state === 'sending')
          this.put(
            'UPDATE effects SET state=?,settled_at=?,receipt=? WHERE id=?',
            body.status,
            this.clock(),
            body.destination_receipt_sha256,
            op.original_id,
          );
        return { operation: op, status: body.status, receipt_sha256: body.destination_receipt_sha256 };
      });
    }
    if (path === '/v1/provider/ack' || path === '/v1/provider/prune') {
      const prune = path.endsWith('/prune');
      only(peer, ...(prune ? ['control'] : peerRoles));
      fields(body, 'request_id', 'operation', 'receipt_sha256', 'body_sha256');
      const op = body.operation;
      operationShape(c, op, true);
      return this.receipt(peer, body.request_id, path, body, () => {
        const result = this.row('SELECT * FROM provider_results WHERE id=?', op.original_id),
          r = this.row('SELECT * FROM effects WHERE id=?', op.original_id);
        need(
          result &&
            r?.state === 'verified' &&
            equal(JSON.parse(r.op), op) &&
            r.receipt === body.receipt_sha256 &&
            result.body_sha === body.body_sha256,
          'original_verified_body_required',
        );
        this.bodyReader(peer, op, JSON.parse(result.policy));
        if (prune) {
          const sourceAttempt = this.row(
            'SELECT sealed FROM source_attempts WHERE attempt=?',
            r.attempt,
          );
          need(!sourceAttempt || sourceAttempt.sealed, 'unsealed_source_attempt_needs_original_tools');
          need(result.ack === op.fence.owner_session, 'consumer_ack_required');
          this.put('DELETE FROM provider_results WHERE id=?', op.original_id);
          return { pruned: true, receipt_sha256: r.receipt };
        }
        this.put('UPDATE provider_results SET ack=? WHERE id=?', peer.session_id, op.original_id);
        return { acknowledged: true, receipt_sha256: r.receipt };
      });
    }
    if (path === '/v1/provider/receipt') {
      only(peer, ...peerRoles, 'control');
      fields(body, 'operation');
      const op = body.operation;
      operationShape(c, op, true);
      if (peer.role !== 'control') {
        need(op.fence.owner_session === peer.session_id, 'original_owner_required', 403);
        this.grant(op.fence.scope, op.fence.source, peer);
      }
      const r = this.row('SELECT * FROM effects WHERE id=?', op.original_id);
      need(r, 'original_absent', 404);
      need(equal(JSON.parse(r.op), op), 'original_operation_changed');
      const prior = this.row('SELECT * FROM provider_results WHERE id=?', op.original_id);
      need(r.state === 'verified' && prior, 'original_provider_result_unresolved');
      const retention = JSON.parse(prior.policy);
      this.bodyReader(peer, op, retention);
      const raw = new Uint8Array(prior.response);
      const bodySHA = await digestBytes(raw);
      let expected;
      if (r.account?.startsWith('gemma-project:')) {
        // Separate internal fact namespace: no provider ID is passed off as a
        // Cloudflare Gateway receipt and no custody/admission fact is granted.
        const stored = this.row('SELECT body FROM facts WHERE id=?', 'gemma-settlement:' + r.receipt);
        const fact = stored && JSON.parse(stored.body);
        need(fact && fact.schema === 'uif.gemma-settlement/1' &&
          equal(fact.operation, op) && fact.body_sha256 === bodySHA &&
          fact.status === 'verified' && fact.consumed_requests === 1 && r.amount === 1 &&
          sha(fact.provider_receipt) && prior.gateway === null, 'original_provider_result_changed');
        expected = await digest(fact);
      } else {
        expected = await digest({
          operation: op, body_sha256: bodySHA, gateway_receipt: prior.gateway, status: 'verified',
        });
      }
      need(raw.length <= 256 * 1024 && expected === r.receipt, 'original_provider_result_changed');
      // Reading/hashing a large result yields. Recheck read authorization at release.
      this.bodyReader(peer, op, retention);
      return this.providerResponse(raw, prior.gateway, r.receipt);
    }
    if (path === '/v1/linear/receipt') {
      only(peer, ...peerRoles, 'control');
      fields(body, 'operation');
      const op = body.operation;
      operationShape(c, op, true);
      need(
        peer.role === 'control' || op.fence.owner_session === peer.session_id,
        'original_owner_required',
        403,
      );
      const r = this.row('SELECT * FROM effects WHERE id=?', op.original_id);
      need(r, 'original_absent', 404);
      need(equal(JSON.parse(r.op), op), 'original_operation_changed');
      const result = this.row('SELECT result FROM destination_results WHERE id=?', op.original_id);
      need(r.state === 'verified' && result, 'original_destination_unresolved');
      return JSON.parse(result.result);
    }
    if (path === '/v1/original') {
      only(peer, ...peerRoles, 'control');
      fields(body, 'operation');
      const op = body.operation;
      operationShape(c, op, true);
      need(
        peer.role === 'control' || op.fence.owner_session === peer.session_id,
        'original_owner_required',
        403,
      );
      const r = this.row('SELECT * FROM effects WHERE id=?', op.original_id);
      if (!r) return { operation: op, state: 'unknown', terminal: null };
      need(equal(JSON.parse(r.op), op), 'original_operation_changed');
      return r.receipt
        ? {
            operation: op,
            state: 'terminal',
            terminal: { operation: op, status: r.state, receipt_sha256: r.receipt },
          }
        : { operation: op, state: 'unknown', terminal: null };
    }
    if (path === '/v1/claim/read') {
      only(peer, ...peerRoles);
      fields(body, 'identifier', 'by_attempt', 'project');
      need(
        body.project === 'OffMenu' && token(body.identifier) && typeof body.by_attempt === 'boolean',
        'invalid_lookup',
        400,
      );
      const row = body.by_attempt
        ? this.row('SELECT * FROM claims WHERE attempt=?', body.identifier)
        : this.row('SELECT * FROM claims WHERE issue=?', c.workspace_id + ':' + body.identifier);
      need(row, 'claim_absent', 404);
      const lease = JSON.parse(row.lease);
      this.owner(peer, lease.fence);
      return { project: 'OffMenu', attempt_id: row.attempt, lease };
    }
    if (path === '/v1/claim/attempt') {
      only(peer, ...peerRoles);
      fields(body, 'request_id', 'current', 'expected_attempt_id', 'attempt_id');
      return this.receipt(peer, body.request_id, path, body, () => {
        const l = body.current,
          { lease, row } = this.owner(peer, l.fence);
        need(equal(lease, l), 'claim_fence_changed');
        const g = this.grant(l.fence.scope, l.fence.source, peer);
        need(
          g.attempt_ids.includes(body.attempt_id) && row.attempt === body.expected_attempt_id,
          'attempt_preimage_changed',
        );
        this.reconciled(l.fence.scope);
        need(
          this.row(
            'SELECT 1 FROM next_attempts WHERE fence=? AND previous=? AND successor=?',
            canonical(l.fence),
            row.attempt,
            body.attempt_id,
          ),
          'prior_attempt_terminal_unverified',
        );
        need(
          !this.row('SELECT 1 FROM attempts WHERE id=?', body.attempt_id),
          'attempt_already_consumed',
        );
        this.put('INSERT INTO attempts VALUES (?,?)', body.attempt_id, issueKey(l.fence.scope));
        this.put('UPDATE claims SET attempt=? WHERE issue=?', body.attempt_id, issueKey(l.fence.scope));
        return { project: 'OffMenu', attempt_id: body.attempt_id, lease };
      });
    }
    if (['/v1/claim', '/v1/claim/observe', '/v1/renew', '/v1/handoff', '/v1/release'].includes(path))
      return this.transition(path, body, peer);
    throw new Refusal('unknown_route', 404);
  }
  allowedRoute(route) {
    return !!route && [this.c.route, this.c.reviewer_route].some((r) => r && equal(r, route));
  }
  usage(route) {
    const r =
      this.allowedRoute(route) &&
      this.row('SELECT receipt FROM usage WHERE account=?', route.account_id);
    return r ? JSON.parse(r.receipt) : null;
  }
  checkpoint(account) {
    return this.row('SELECT * FROM usage_checkpoints WHERE account=?', account);
  }
  checkpointMatches(account, receipt) {
    const row = this.checkpoint(account);
    return row && row.receipt_sha === receipt.receipt_sha256 && sha(row.checkpoint_sha) && sha(row.opening_sha);
  }
  async transition(path, body, peer) {
    only(peer, ...peerRoles);
    const c = this.c,
      observe = path === '/v1/claim/observe';
    const action = observe ? 'claim' : path.slice(4),
      recover = action === 'handoff' && Object.hasOwn(body ?? {}, 'recovery'),
      kind = recover ? 'uif.shared-lease.recover-unstarted/1' : leaseKind(action),
      authority = identity(c, peer);
    const markedClaim = action === 'claim' && Object.hasOwn(body ?? {}, 'first_intent_sha256');
    let expectedMarkedId;
    const typed = { ...body, authority };
    if (action === 'claim') {
      fields(
        body,
        'request_id',
        'expected_coordinator_epoch',
        'scope',
        'source',
        'reconstruction_sha256',
        'attempt_id',
        ...(markedClaim ? ['first_intent_sha256'] : []),
      );
      scopeSource(c, body.scope, body.source);
      if (markedClaim) {
        need(sha(body.first_intent_sha256), 'invalid_dispatch_intent', 400);
        need(peer.role === 'native' && Object.values(c.peers).some(
          (p) => p.role === 'native' && p.session_id === peer.session_id), 'peer_role_not_granted', 403);
        expectedMarkedId = await digest({
          claim: body.scope, recovery: body.reconstruction_sha256, authority, epoch: body.expected_coordinator_epoch,
        });
        // These two transport fields are bound by the full request fingerprint in receipts.
        delete typed.first_intent_sha256;
      }
      delete typed.attempt_id;
    } else if (action === 'renew') fields(body, 'request_id', 'current');
    else if (action === 'handoff')
      fields(
        body,
        'request_id',
        'current',
        'successor_session',
        'expected_coordinator_epoch',
        'quiesced_sha256',
        'reconstruction_sha256',
        ...(recover ? ['recovery'] : []),
      );
    else fields(body, 'request_id', 'completion');
    const hash = await digest({ kind, request: typed });
    if (observe) {
      const prev = this.row(
        'SELECT * FROM receipts WHERE peer=? AND id=?',
        this.peerKey(peer),
        body.request_id,
      );
      need(prev, 'original_claim_not_found', 404);
      need(prev.fingerprint === (await digest({ path: '/v1/claim', body })), 'original_request_changed');
      return JSON.parse(prev.result);
    }
    const handoffFact =
      action === 'handoff'
        ? {
            kind: 'handoff',
            ...(recover ? { recovery: body.recovery } : {}),
            current: body.current,
            successor_session: body.successor_session,
            expected_coordinator_epoch: body.expected_coordinator_epoch,
            quiesced_sha256: body.quiesced_sha256,
            reconstruction_sha256: body.reconstruction_sha256,
          }
        : null;
    const completionFact =
      action === 'release' ? { kind: 'completion', completion: body.completion } : null;
    const fact = handoffFact || completionFact,
      factId = fact && (await digest(fact));
    return this.receipt(peer, body.request_id, path, body, () => {
      const t = this.clock(),
        ctl = this.control(),
        result = { request_kind: kind, request_id: body.request_id, request_sha256: hash };
      if (action === 'claim') {
        const g = this.grant(body.scope, body.source, peer),
          id = issueKey(body.scope);
        need(
          !ctl.stopped && body.expected_coordinator_epoch === ctl.epoch,
          'admission_stopped_or_epoch_changed',
        );
        need(
          g.reconstruction_sha256 === body.reconstruction_sha256 &&
            g.attempt_ids.includes(body.attempt_id),
          'reconstruction_or_attempt_unverified',
        );
        if (g.fresh_assessment) {
          need(body.attempt_id === g.fresh_assessment.attempt_id, 'assessment_initial_attempt_required');
          const enrolled = this.row('SELECT issue,intent_sha FROM assessment_dispatch WHERE attempt=?',
            body.attempt_id);
          need(markedClaim && body.request_id === expectedMarkedId && enrolled?.issue === id &&
            enrolled.intent_sha === body.first_intent_sha256 &&
            g.fresh_assessment.first_intent_sha256 === body.first_intent_sha256,
            'assessment_first_claim_original_required');
        } else {
          need(!markedClaim, 'assessment_first_claim_not_enrolled');
        }
        need(
          !this.row('SELECT issue FROM claims WHERE issue=? OR attempt=?', id, body.attempt_id),
          'issue_or_attempt_already_claimed',
        );
        need(
          !Object.hasOwn(g.review_of, body.attempt_id) &&
            !this.row('SELECT 1 FROM attempts WHERE id=?', body.attempt_id),
          'initial_author_attempt_required',
        );
        this.put('INSERT INTO attempts VALUES (?,?)', body.attempt_id, id);
        const { session_id, ...realm } = authority;
        const fence = {
          scope: body.scope,
          source: body.source,
          owner_session: session_id,
          generation: 1,
          coordinator_epoch: ctl.epoch,
          lease_id: crypto.randomUUID(),
          ...realm,
        };
        const lease = { fence, expires_at: Math.min(t + c.lease_seconds, g.valid_until) };
        this.put("INSERT INTO claims VALUES (?,?,?,'active')", id, body.attempt_id, canonical(lease));
        this.put(
          'INSERT INTO claim_generations VALUES (?,?,?)',
          id,
          lease.fence.generation,
          canonical({ grant: g, lease, request_id: body.request_id,
            ...(markedClaim ? {first_intent_sha256: body.first_intent_sha256} : {}) }),
        );
        return { ...result, lease };
      }
      const current = body.current || { fence: body.completion.fence },
        f = current.fence,
        id = issueKey(f.scope);
      if (action === 'renew') {
        const { lease } = this.owner(peer, f);
        const g = this.grant(f.scope, f.source, peer);
        const end = Math.min(t + c.lease_seconds, g.valid_until);
        need(equal(current, lease) && end > current.expires_at, 'lease_preimage_or_expiry_changed');
        const renewed = { fence: f, expires_at: end };
        this.put('UPDATE claims SET lease=? WHERE issue=?', canonical(renewed), id);
        return { ...result, lease: renewed };
      }
      const r = this.row('SELECT * FROM claims WHERE issue=?', id);
      if (recover) {
        need(peer.session_id === body.successor_session && peer.session_id !== f.owner_session,
          'named_successor_required', 403);
        this.requireUnstartedRevocation(handoffFact);
      } else {
        need(r && ['active', 'revoked'].includes(r.state), 'claim_not_reconcilable');
      }
      const prior = JSON.parse(r.lease);
      need(equal(prior.fence, f) && (recover || f.owner_session === peer.session_id), 'original_owner_required');
      this.reconciled(f.scope);
      this.fact(factId, fact);
      if (action === 'release') {
        fields(body.completion, 'fence', 'accepted', 'receipt_sha256');
        need(
          typeof body.completion.accepted === 'boolean' && sha(body.completion.receipt_sha256),
          'invalid_completion',
        );
        this.put("UPDATE claims SET state='released' WHERE issue=?", id);
        return { ...result, completion: body.completion };
      }
      const next = Object.values(c.peers).find(
        (p) => p.session_id === body.successor_session && peerRoles.includes(p.role),
      );
      need(
        next &&
          next.session_id !== f.owner_session &&
          !ctl.stopped &&
          ctl.epoch === body.expected_coordinator_epoch &&
          equal(current, prior) &&
          sha(body.quiesced_sha256),
        'handoff_not_bound',
      );
      const g = this.grant(f.scope, f.source, next);
      need(
        g.reconstruction_sha256 === body.reconstruction_sha256 && g.attempt_ids.includes(r.attempt),
        'successor_not_qualified',
      );
      const lease = {
        fence: {
          ...f,
          authority_id: c.authority_id,
          deployment_sha256: c.deployment_sha256,
          policy_sha256: c.policy_sha256,
          owner_session: next.session_id,
          coordinator_epoch: ctl.epoch,
          generation: f.generation + 1,
          lease_id: crypto.randomUUID(),
        },
        expires_at: Math.min(t + c.lease_seconds, g.valid_until),
      };
      this.put("UPDATE claims SET lease=?,state='active' WHERE issue=?", canonical(lease), id);
      this.put(
        'INSERT INTO claim_generations VALUES (?,?,?)',
        id,
        lease.fence.generation,
        canonical({ grant: g, lease, request_id: body.request_id }),
      );
      return recover ? { ...result, lease, attempt_id: r.attempt } : { ...result, lease };
    });
  }
  recordDestination(op, result) {
    return this.store.transactionSync(() => {
      this.put(
        "UPDATE effects SET state='verified',settled_at=?,receipt=? WHERE id=? AND state='sending'",
        this.clock(),
        result.receipt_sha256,
        op.original_id,
      );
      this.put('INSERT INTO destination_results VALUES (?,?)', op.original_id, canonical(result));
      return result;
    });
  }
  linearRequest(query, variables) {
    need(
      typeof this.linearAuthorization === 'string' &&
        /^(?:Bearer )?[!-~]+$/.test(this.linearAuthorization),
      'linear_receiver_unbound',
      503,
    );
    return new Request('https://api.linear.app/graphql', {
      method: 'POST',
      redirect: 'manual',
      headers: { 'content-type': 'application/json', Authorization: this.linearAuthorization },
      body: JSON.stringify({ query, variables }),
      signal: AbortSignal.timeout(15000),
    });
  }
  linearGrant(peer, op, attempt, permission) {
    const { row } = this.owner(peer, op.fence),
      g = this.grant(op.fence.scope, op.fence.source, peer);
    need(
      g.attempt_ids.includes(row.attempt) && row.attempt === attempt && g[permission] === true,
      'linear_action_not_granted',
    );
  }
  assessmentCommentRecords(fence, attempt) {
    const {row} = this.ownerFence(fence.owner_session, fence);
    need(row.attempt === attempt, 'assessment_dispatch_report_original_unbound', 403);
    const retained = this.row('SELECT * FROM assessment_dispatch WHERE attempt=?', attempt);
    const grant = this.grant(fence.scope, fence.source, {session_id:fence.owner_session});
    const binding = grant.fresh_assessment?.report_binding;
    const issue = issueKey(fence.scope);
    need(retained && sha(retained.report_binding_sha) &&
      retained.issue === issue && retained.fence === canonical(fence) && retained.provider_op &&
      grant.fresh_assessment?.attempt_id === attempt &&
      grant.fresh_assessment.first_intent_sha256 === retained.intent_sha &&
      binding && Object.values(this.c.peers).some((peer) =>
        peer.role === 'native' && peer.session_id === fence.owner_session),
      'assessment_dispatch_report_original_unbound', 403);
    const operation = JSON.parse(retained.provider_op);
    const effect = this.row('SELECT * FROM effects WHERE id=?', operation.original_id);
    const result = this.row('SELECT * FROM provider_results WHERE id=?', operation.original_id);
    need(effect?.op === retained.provider_op && effect?.attempt === attempt &&
      effect.issue === issue && effect.state === 'verified' && result?.issue === issue &&
      grant.result_retention && result.policy === canonical(grant.result_retention) &&
      grant.result_retention.readers.includes(fence.owner_session) &&
      (result.response instanceof ArrayBuffer || result.response instanceof Uint8Array) &&
      result.response.byteLength <= 256 * 1024,
      'assessment_dispatch_report_result_unavailable', 403);
    // Clone the BLOB before any suspension; ACK is intentionally not identity.
    const bytes = new Uint8Array(result.response).slice();
    return {
      binding, bindingSHA: retained.report_binding_sha, operation, bytes,
      receipt: effect.receipt, bodySHA: result.body_sha, gateway: result.gateway,
    };
  }
  assessmentReportSnapshotUnchanged(before, after) {
    need(before.bindingSHA === after.bindingSHA && equal(before.binding, after.binding) &&
      equal(before.operation, after.operation) && before.receipt === after.receipt &&
      before.bodySHA === after.bodySHA && before.gateway === after.gateway &&
      before.bytes.length === after.bytes.length && before.bytes.every((x, i) => x === after.bytes[i]),
      'assessment_dispatch_report_evidence_changed', 403);
  }
  deriveAssessmentReport(fence, attempt, before) {
    return deriveFreshAssessmentCommentFromVerifiedOriginal({
      config: this.c, currentFence: fence, ownerBinding: before.binding,
      ownerBindingSha256: before.bindingSHA, attempt_id: attempt,
      sealedProviderOperation: before.operation,
      providerEffect: { operation: before.operation, status: 'verified', attempt_id: attempt,
        receipt_sha256: before.receipt },
      retainedResponse: {body_sha256: before.bodySHA, gateway_receipt: before.gateway},
      responseBytes: before.bytes,
    });
  }
  async assessmentCommentCheck(peer, op, attempt, permitHash, text) {
    const read = () => {
      const permit = this.row('SELECT permit_sha FROM assessment_dispatch WHERE attempt=?', attempt);
      need(peer.role === 'native' && peer.session_id === op.fence.owner_session &&
        sha(permitHash) && permit?.permit_sha === permitHash, 'assessment_dispatch_permit_required', 403);
      return this.assessmentCommentRecords(op.fence, attempt);
    };
    const before = read();
    const expected = await this.deriveAssessmentReport(op.fence, attempt, before);
    need(expected.body === text && equal(expected.operation, op),
      'assessment_dispatch_report_original_changed', 403);
    return () => this.assessmentReportSnapshotUnchanged(before, read());
  }
  async freshAssessmentAttestationCheck(body) {
    const {fence, previous, successor, terminal_receipt_sha256} = body;
    const readInputs = () => {
      const g = this.grant(fence.scope, fence.source, {session_id:fence.owner_session});
      need(/^cf-intake-[a-f0-9]{64}-assess$/.test(previous) &&
        successor === previous.slice(0, -'assess'.length) + 'investigate' &&
        g.attempt_ids.includes(successor) && !Object.hasOwn(g.review_of, successor) &&
        !this.row('SELECT 1 FROM attempts WHERE id=?', successor),
      'assessment_successor_not_admitted');
      return this.assessmentCommentRecords(fence, previous);
    };
    const before = readInputs();
    const report = await this.deriveAssessmentReport(fence, previous, before);
    // Readiness only describes the exact report. It never grants independent
    // approval; the configured control caller still owns that decision.
    need(report.reported_ready === true, 'assessment_terminal_report_not_ready');
    const op = report.operation;
    const hash = await digest({kind:'uif.linear-comment-id/1', operation:op});
    const id = `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}`;
    const readPosted = () => {
      const effect = this.row('SELECT * FROM effects WHERE id=?', op.original_id);
      const destination = this.row('SELECT result FROM destination_results WHERE id=?', op.original_id);
      need(effect?.state === 'verified' && effect.attempt === previous &&
        effect.issue === issueKey(fence.scope) && effect.account === null &&
        effect.op === canonical(op) && sha(effect.receipt) &&
        destination?.result === canonical({id, receipt_sha256:effect.receipt}),
      'assessment_terminal_report_unverified');
      return effect.receipt;
    };
    const receipt = readPosted();
    const {issue_identifier, run, scope_sha256} = before.binding;
    const expected = await digest({
      schema:'uif.fresh-offmenu-assessment-terminal/1',
      issue_id:fence.scope.key.issue_id, issue_identifier, run, state:'assess',
      successor_state:'investigate', scope_sha256, previous, successor,
      report_sha256:report.report_sha256, posted_comment_id:id,
      posted_effect:{operation:op, status:'verified', receipt_sha256:receipt},
    });
    need(expected === terminal_receipt_sha256, 'assessment_terminal_digest_changed');
    return () => {
      this.assessmentReportSnapshotUnchanged(before, readInputs());
      need(readPosted() === receipt, 'assessment_terminal_report_unverified');
    };
  }
  async comment(body, peer, permitHash) {
    fields(body, 'operation', 'attempt_id', 'body');
    const op = body.operation;
    operationShape(this.c, op);
    const issue = op.fence.scope.key.issue_id,
      text = body.body;
    need(
      token(body.attempt_id) && typeof text === 'string' && text.length > 0 && text.length <= 60000,
      'invalid_linear_intent',
      400,
    );
    need(
      (await digest({
        kind: 'uif.linear-comment/1',
        attempt_id: body.attempt_id,
        issue_id: issue,
        body: text,
      })) === op.payload_sha256,
      'linear_fingerprint_changed',
    );
    const qualify = () => this.linearGrant(peer, op, body.attempt_id, 'allow_comments');
    let reportCheck;
    if (this.row('SELECT 1 FROM assessment_dispatch WHERE attempt=?', body.attempt_id)) {
      qualify();
      reportCheck = await this.assessmentCommentCheck(peer, op, body.attempt_id, permitHash, text);
    }
    const hash = await digest({ kind: 'uif.linear-comment-id/1', operation: op });
    const id = `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20, 32)}`;
    const out = this.linearRequest(
      'mutation Add($input:CommentCreateInput!){commentCreate(input:$input){success comment{id body issue{id}}}}',
      { input: { id, issueId: issue, body: text } },
    );
    const response = await this.physical(
      op,
      body.attempt_id,
      null,
      0,
      () => { qualify(); reportCheck?.(); },
      out,
    );
    need(response.status === 200, 'original_linear_outcome_unknown', 502);
    const raw = await readBytes(response.body, 768000),
      doc = json(raw),
      r = doc.data?.commentCreate;
    need(
      !doc.errors &&
        r?.success === true &&
        r.comment?.id === id &&
        r.comment?.issue?.id === issue &&
        r.comment?.body === text,
      'original_linear_outcome_unknown',
      502,
    );
    const receipt_sha256 = await digest({ operation: op, id, body_sha256: await digestBytes(raw) });
    return this.recordDestination(op, { id, receipt_sha256 });
  }
  async upload(body, peer) {
    fields(body, 'operation', 'attempt_id', 'filename', 'content_type', 'body_base64');
    const { operation: op, attempt_id, filename, content_type } = body;
    operationShape(this.c, op);
    need(
      token(attempt_id) &&
        typeof filename === 'string' &&
        /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$/.test(filename) &&
        [
          'application/json',
          'text/plain',
          'text/markdown',
          'text/csv',
          'application/octet-stream',
          'application/zip',
          'image/png',
        ].includes(content_type),
      'invalid_attachment',
      400,
    );
    const raw = bytes64(body.body_base64, 8 * 1024 * 1024),
      body_sha256 = await digestBytes(raw),
      size = raw.length;
    need(
      (await digest({
        kind: 'uif.linear-upload/1',
        attempt_id,
        issue_id: op.fence.scope.key.issue_id,
        filename,
        content_type,
        size,
        body_sha256,
      })) === op.payload_sha256,
      'upload_fingerprint_changed',
    );
    const qualify = () => this.linearGrant(peer, op, attempt_id, 'allow_uploads');
    const out = this.linearRequest(
      'mutation Upload($type:String!,$name:String!,$size:Int!){fileUpload(contentType:$type,filename:$name,size:$size){success uploadFile{uploadUrl assetUrl headers{key value}}}}',
      { type: content_type, name: filename, size },
    );
    const response = await this.physical(op, attempt_id, null, 0, qualify, out);
    need(response.status === 200, 'upload_allocation_unknown', 502);
    const doc = json(await readBytes(response.body, 32768)),
      allocation = doc.data?.fileUpload;
    need(!doc.errors && allocation?.success === true, 'upload_allocation_unknown', 502);
    const file = allocation.uploadFile,
      target = new URL(file.uploadUrl),
      asset = new URL(file.assetUrl);
    const hosts = this.c.upload_hosts || [];
    need(
      target.protocol === 'https:' &&
        !target.username &&
        !target.password &&
        !target.port &&
        !target.hash &&
        hosts.includes(target.hostname) &&
        asset.origin === 'https://uploads.linear.app' &&
        !asset.username &&
        !asset.password &&
        !asset.search &&
        !asset.hash &&
        asset.pathname.startsWith('/' + this.c.workspace_id + '/'),
      'unbound_upload_destination',
      502,
    );
    const headers = new Headers({ 'content-type': content_type });
    need(Array.isArray(file.headers) && file.headers.length <= 16, 'invalid_upload_headers', 502);
    for (const { key, value } of file.headers) {
      need(
        typeof key === 'string' &&
          /^(content-type|cache-control|x-goog-content-length-range|x-(goog|amz)-meta-[a-z0-9-]+)$/i.test(
            key,
          ) &&
          typeof value === 'string' &&
          value.length <= 2048 &&
          !/[\x00-\x1f]/.test(value),
        'invalid_upload_headers',
        502,
      );
      if (key.toLowerCase() === 'cache-control')
        need(
          /^(private|no-store)(,\s*(private|no-store))*$/i.test(value),
          'upload_cache_policy_unbound',
        );
      headers.set(key, value);
    }
    need(headers.get('content-type') === content_type, 'upload_content_type_changed', 502);
    const put = new Request(target, {
      method: 'PUT',
      redirect: 'manual',
      headers,
      body: raw,
      signal: AbortSignal.timeout(30000),
    });
    // First mutation already reached the provider. A revoke or crash here leaves
    // this same original unknown. Never request another signed URL or PUT again.
    qualify();
    const stored = await this.provider(put);
    need(stored.ok, 'original_upload_unknown', 502);
    const receipt_sha256 = await digest({
      operation: op,
      asset_url: asset.href,
      body_sha256,
      size,
      status: stored.status,
    });
    return this.recordDestination(op, { asset_url: asset.href, receipt_sha256 });
  }
  async physical(op, attempt, account, amount, qualify, outgoing) {
    this.store.transactionSync(() => {
      qualify(false);
      need(
        !this.row('SELECT 1 AS found FROM effects WHERE id=?', op.original_id),
        'original_already_recorded',
      );
      this.reconciled(op.fence.scope);
      this.put(
        "INSERT INTO effects VALUES (?,?,?,?,?,?,'sending',NULL,NULL)",
        op.original_id,
        issueKey(op.fence.scope),
        canonical(op),
        attempt,
        account,
        amount,
      );
    });
    // Re-evaluate after storage confirmation. No task switch/await, queue, proxy
    // callback or caller-controlled URL exists between the final check and fetch.
    await this.store.sync();
    try {
      need(
        this.row('SELECT state FROM effects WHERE id=?', op.original_id)?.state === 'sending',
        'original_state_changed',
      );
      qualify(true);
    } catch (e) {
      const hash = await digest({ operation: op, status: 'denied', kind: 'pre-egress-fence' });
      this.put(
        "UPDATE effects SET state='denied',amount=0,settled_at=?,receipt=? WHERE id=? AND state='sending'",
        this.clock(),
        hash,
        op.original_id,
      );
      throw e;
    }
    try {
      return await this.provider(outgoing);
    } catch {
      throw new Refusal('original_external_effect_unknown', 502);
    }
  }
  bodyReader(peer, op, retention) {
    this.live(peer);
    need(retention.readers.includes(peer.session_id), 'private_result_reader_not_granted', 403);
    if (peer.role !== 'control') {
      need(op.fence.owner_session === peer.session_id, 'original_owner_required', 403);
      const g = this.grant(op.fence.scope, op.fence.source, peer);
      need(
        g.result_retention && equal(g.result_retention, retention),
        'private_result_grant_changed',
        403,
      );
    }
  }
  // Reuse the same journal for every qualified account namespace.
  accountReservationDebt(account, coveredThrough, excludeOriginal = '') {
    const { debt } = this.row(
      `SELECT COALESCE(SUM(amount),0) AS debt FROM effects WHERE account=?
      AND (state='sending' OR settled_at>=?) AND id!=?`,
      account,
      coveredThrough,
      excludeOriginal,
    );
    const { opening } = this.row(
      'SELECT COALESCE(SUM(amount),0) AS opening FROM carryovers WHERE account=?',
      account,
    );
    return debt + opening;
  }
  qualifySend(peer, intent, promptSHA, includeThis = false) {
    const op = intent.operation,
      { row, lease } = this.owner(peer, op.fence),
      g = this.grant(op.fence.scope, op.fence.source, peer);
    need(
      g.allow_provider &&
        row.attempt === intent.attempt_id &&
        g.attempt_ids.includes(row.attempt) &&
        intent.request_not_after <= lease.expires_at,
      'attempt_or_deadline_not_current',
    );
    const retention = g.result_retention;
    need(retention && retention.readers.includes(peer.session_id), 'private_original_retention_unbound');
    need(
      this.row('SELECT COUNT(*) AS n FROM provider_results WHERE issue=?', issueKey(op.fence.scope)).n <
        retention.max_retained_results,
      'private_result_storage_bound',
    );
    const reviewOf = Object.hasOwn(g.review_of, row.attempt) ? g.review_of[row.attempt] : null;
    const wanted = reviewOf ? this.c.reviewer_route : this.c.route;
    need(wanted && equal(wanted, intent.route), 'attempt_account_route_changed');
    if (reviewOf) {
      const bound = this.row('SELECT body FROM review_bindings WHERE attempt=?', row.attempt);
      const r = bound && JSON.parse(bound.body);
      need(
        r &&
          equal(r.fence, op.fence) &&
          r.candidate.author_attempt_id === reviewOf &&
          r.send_prompt_sha256 === promptSHA,
        'independent_review_candidate_unbound',
      );
    }
    const receipt = this.usage(intent.route);
    need(
      this.quotaVerifier &&
        this.authorized('quota', intent.route.account_id) &&
        sha(this.c.quota_verification_sha256) &&
        receipt &&
        this.checkpointMatches(intent.route.account_id, receipt) &&
        receipt.admission_binding_sha256 === this.c.quota_verification_sha256 &&
        receipt.receipt_sha256 === intent.quota_receipt_sha256,
      'usage_receipt_not_current',
    );
    need(
      !this.row('SELECT original FROM budget_breaches WHERE account=?', intent.route.account_id),
      'account_reservation_maximum_breached',
    );
    const p = receipt.projection;
    // Unknown effects count across midnight and process restarts. A later complete
    // account observation may cover a verified terminal charge, never an unknown one.
    const outstanding = this.accountReservationDebt(
      intent.route.account_id, p.covered_through, includeThis ? op.original_id : '',
    );
    projectionOK(
      p,
      intent.route,
      this.clock(),
      intent.request_not_after,
      outstanding,
      intent.maximum_millineurons,
    );
  }
  // Disabled preparation seam: handle() deliberately does not expose this.
  // readQuote is an installed, owner-qualified vendor adapter, not request
  // JSON. It authenticates exact project/window/operation and certifies the
  // limit and its coverage. No such issuer or private-data grant is installed.
  async dispatchGemmaPrepared({ operation, attempt, project, prompt, deadline, credential,
    readQuote, verifySettlement }, peer) {
    operationShape(this.c, operation);
    need(
      token(attempt) && sha(project) && typeof prompt === 'string' &&
      new TextEncoder().encode(prompt).byteLength <= 4096 && prompt.length > 0 &&
      boundedRequest(deadline) && typeof readQuote === 'function' &&
      typeof verifySettlement === 'function' &&
      typeof credential === 'string' && /^[!-~]+$/.test(credential),
      'gemma_preparation_unbound',
    );
    const model = 'gemma-4-31b-it',
      url = 'https://generativelanguage.googleapis.com/v1beta/models/gemma-4-31b-it:generateContent',
      account = 'gemma-project:' + project,
      payload = canonical({
        contents: [{ role: 'user', parts: [{ text: prompt }] }],
        generationConfig: { maxOutputTokens: 256, candidateCount: 1 },
      });
    const payloadSHA = await digest({
      schema: 'uif.gemma-prepared-public-call/1', project, model, attempt, deadline, payload,
    });
    need(payloadSHA === operation.payload_sha256, 'gemma_payload_changed');
    const issue = issueKey(operation.fence.scope);
    const retention = this.grant(operation.fence.scope, operation.fence.source, peer).result_retention;
    const hasSpace = () =>
      this.row('SELECT COUNT(*) AS n FROM provider_results WHERE issue=?', issue).n <
        retention.max_retained_results;
    const qualify = (reserved) => {
      const { row, lease } = this.owner(peer, operation.fence);
      const g = this.grant(operation.fence.scope, operation.fence.source, peer);
      need(retention && g.result_retention && equal(g.result_retention, retention) &&
        retention.readers.includes(peer.session_id), 'private_original_retention_unbound');
      need(hasSpace(), 'private_result_storage_bound');
      need(g.allow_provider && row.attempt === attempt && g.attempt_ids.includes(attempt) &&
        deadline <= lease.expires_at && this.clock() < deadline,
      'gemma_attempt_not_current');
      const evidence = readQuote(operation, { project, model, deadline, payloadSHA });
      // Exact authenticated request-count window is only a preparation dimension;
      // the installed quote adapter must also account for all other applicable
      // quota windows and classify externally permitted public/synthetic input.
      need(evidence && evidence.publicDataOnly === true && evidence.noPaidRoute === true &&
        Number.isSafeInteger(evidence.coveredThrough) && evidence.coveredThrough <= this.clock() &&
        Number.isSafeInteger(evidence.validUntil) && deadline <= evidence.validUntil &&
        Number.isSafeInteger(evidence.upperUsed) && evidence.upperUsed >= 0 &&
        Number.isSafeInteger(evidence.limit) && evidence.limit > 0,
      'gemma_owner_quote_unavailable');
      const debt = this.accountReservationDebt(
        account, evidence.coveredThrough, reserved ? operation.original_id : '',
      );
      need(evidence.upperUsed + debt + 1 <= evidence.limit, 'gemma_window_exhausted');
    };
    const outgoing = new Request(url, {
      method: 'POST', redirect: 'manual',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': credential },
      body: payload,
      signal: AbortSignal.timeout(Math.min(60000, Math.max(1, (deadline - this.clock()) * 1000))),
    });
    // Preparation only. The verifier is supplied by a trusted installed
    // receiver, never a caller or the provider response on its own.
    const id = operation.original_id;
    need(!this.inflight.has(id), 'original_in_flight');
    this.inflight.add(id);
    try {
      const response = await this.physical(operation, attempt, account, 1, qualify, outgoing);
      if (response.status !== 200) {
        await response.body?.cancel();
        throw new Refusal('gemma_original_requires_verified_settlement', 502);
      }
      const bytes = await readBytes(response.body, retention.max_response_bytes);
      const result = json(bytes);
      const candidate = result?.candidates?.[0];
      need(token(result?.responseId) && result.modelVersion === model &&
        Array.isArray(result.candidates) && result.candidates.length === 1 &&
        candidate?.finishReason === 'STOP' && !result.promptFeedback?.blockReason &&
        candidate.content?.role === 'model' && Array.isArray(candidate.content.parts) &&
        candidate.content.parts.length > 0 && candidate.content.parts.every((part) =>
          part && Object.keys(part).length === 1 && typeof part.text === 'string' && part.text.length > 0),
        'gemma_original_result_unbound', 502);
      const bodySHA = await digestBytes(bytes);
      const subject = { operation_id: operation.original_id, payloadSHA, project, model,
        responseId: result.responseId, bodySHA };
      const statementHash = await digest(subject);
      let proof;
      try {
        proof = await verifySettlement(Object.freeze(subject));
      } catch {
        throw new Refusal('gemma_original_requires_verified_settlement', 502);
      }
      need(proof && proof.statementHash === statementHash && sha(proof.receiptSha256) &&
        proof.consumedRequests === 1, 'gemma_original_requires_verified_settlement', 502);
      const fact = { schema: 'uif.gemma-settlement/1', operation, body_sha256: bodySHA,
        provider_receipt: proof.receiptSha256, status: 'verified', consumed_requests: 1 };
      const hash = await digest(fact);

      // Preserve the verified one-request charge even when retention consent
      // changes. Retention and transition are otherwise one private commit.
      let disposition;
      this.store.transactionSync(() => {
        const stored = this.row('SELECT op,state,amount,account FROM effects WHERE id=?', operation.original_id);
        need(stored?.state === 'sending' && stored.op === canonical(operation) &&
          stored.amount === 1 && stored.account === account, 'gemma_original_record_changed');
        const row = this.row('SELECT body FROM grants WHERE issue=?', issue);
        const current = row && JSON.parse(row.body);
        const consent = this.commissioned() && this.authorized('grant', issue) &&
          current && equal(current.source, operation.fence.source) && current.valid_until > this.clock() &&
          current.result_retention && equal(current.result_retention, retention);
        disposition = !consent ? 'private_result_grant_changed'
          : !hasSpace() ? 'private_result_storage_bound' : null;
        this.put("UPDATE effects SET state='verified',settled_at=?,receipt=? WHERE id=?",
          this.clock(), hash, operation.original_id);
        this.put('INSERT INTO facts VALUES (?,?)', 'gemma-settlement:' + hash, canonical(fact));
        if (!disposition)
          this.put('INSERT INTO provider_results VALUES (?,?,?,?,?,?,NULL)', operation.original_id,
            bytes.buffer, null, issue, canonical(retention), bodySHA);
      });
      need(!disposition, disposition, disposition === 'private_result_grant_changed' ? 403 : 409);
      this.bodyReader(peer, operation, retention);
      return this.providerResponse(bytes, null, hash);
    } finally {
      this.inflight.delete(id);
    }
  }

  // Source preparation only: no HTTP route, runtime default or Codex fallback.
  // The owner-supplied quote adapter must authenticate the linked grant and
  // whole-org liabilities. Request JSON cannot supply that adapter.
  async dispatchIncludedResponsesPrepared({ operation, attempt, organization, project,
    credentialEpoch, credential, input, deadline, maximumMicrousd, optIn, intent, readGrant, verifySettlement }, peer) {
    operationShape(this.c, operation);
    const identifier = (s) => typeof s === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(s);
    need(token(attempt) && identifier(organization) && identifier(project) &&
      sha(credentialEpoch) && typeof input === 'string' && input.length > 0 &&
      new TextEncoder().encode(input).byteLength <= 4096 && boundedRequest(deadline) &&
      Number.isSafeInteger(maximumMicrousd) && maximumMicrousd > 0 &&
      optIn === true && ['new', 'continuation', 'retry'].includes(intent) &&
      typeof credential === 'string' && /^[!-~]+$/.test(credential) && typeof readGrant === 'function',
      'responses_preparation_unbound');
    const model = 'gpt-6.2-sol',
      account = 'openai-responses-org:' + await digest({ organization }),
      payload = canonical({ model, input, max_output_tokens: 256, store: false }),
      binding = { schema: 'uif.included-responses-prepared/1', organization, project, credentialEpoch,
        attempt, deadline, maximumMicrousd, intent, payload };
    const fingerprint = await digest(binding);
    need(fingerprint === operation.payload_sha256, 'responses_payload_changed');
    const qualify = (reserved) => {
      const { row, lease } = this.owner(peer, operation.fence);
      const g = this.grant(operation.fence.scope, operation.fence.source, peer);
      const now = this.clock();
      need(g.allow_provider && row.attempt === attempt && g.attempt_ids.includes(attempt) &&
        deadline <= lease.expires_at && now < deadline, 'responses_attempt_not_current');
      // The existing advisory opt-in does not authenticate these billing facts.
      const proof = readGrant(operation, fingerprint);
      const natural = (n) => Number.isSafeInteger(n) && n >= 0;
      need(proof && proof.binding === fingerprint && proof.organization === organization &&
        proof.project === project && proof.credentialEpoch === credentialEpoch && proof.model === model &&
        proof.includedOnly === true && proof.hardNoOverage === true && proof.purchasedCreditsAbsent === true &&
        proof.autoRechargeDisabled === true && proof.completeBinding === true &&
        [proof.observedAt, proof.validUntil, proof.expiresAt, proof.coveredThrough, proof.totalMicrousd,
          proof.balanceLowerMicrousd, proof.requestUpperMicrousd, proof.unsettledUpperMicrousd].every(natural) &&
        now - 90 <= proof.observedAt && proof.observedAt <= now && proof.coveredThrough <= proof.observedAt &&
        deadline <= proof.validUntil && deadline < proof.expiresAt &&
        proof.totalMicrousd > 0 && proof.balanceLowerMicrousd <= proof.totalMicrousd &&
        maximumMicrousd === proof.requestUpperMicrousd, 'responses_authenticated_grant_unavailable');
      need(!this.row('SELECT original FROM budget_breaches WHERE account=?', account),
        'account_reservation_maximum_breached');
      const outstanding = this.accountReservationDebt(
        account, proof.coveredThrough, reserved ? operation.original_id : '',
      );
      need(natural(outstanding), 'responses_reservation_bound_overflow');
      const balance = BigInt(proof.balanceLowerMicrousd) - BigInt(proof.unsettledUpperMicrousd) -
        BigInt(outstanding) - BigInt(maximumMicrousd);
      need(100n * balance > BigInt(intent === 'new' ? 15 : 10) * BigInt(proof.totalMicrousd),
        'responses_included_cutoff');
    };
    const outgoing = new Request('https://api.openai.com/v1/responses', {
      method: 'POST', redirect: 'manual', body: payload,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + credential,
        'OpenAI-Organization': organization, 'OpenAI-Project': project },
      signal: AbortSignal.timeout(Math.min(60000, Math.max(1, (deadline - this.clock()) * 1000))),
    });
    const retention = this.grant(operation.fence.scope, operation.fence.source, peer).result_retention;
    const response = await this.physical(operation, attempt, account, maximumMicrousd, qualify, outgoing);
    if (typeof verifySettlement !== 'function' || !retention ||
        !retention.readers.includes(peer.session_id) || response.status !== 200) {
      await response.body?.cancel();
      throw new Refusal('responses_original_requires_verified_settlement', 502);
    }
    const bytes = await readBytes(response.body, retention.max_response_bytes);
    const result = json(bytes);
    const providerRequestId = response.headers.get('x-request-id');
    need(typeof providerRequestId === 'string' && /^[A-Za-z0-9_-]{1,192}$/.test(providerRequestId) &&
      typeof result.id === 'string' && /^resp_[A-Za-z0-9_-]{1,192}$/.test(result.id) &&
      result.model === model && result.status === 'completed', 'responses_original_result_unbound', 502);
    const bodySHA = await digestBytes(bytes);
    // This is a trusted, installed verifier callback, not evidence obtained
    // from a caller, a dashboard screenshot, a model GET, or HTTP 200 alone.
    // It authenticates the actual linked project/credential and charge for
    // the recorded response, independent of the supplied prompt/result.
    const subject = { fingerprint, organization, project, credentialEpoch, model,
      operation_id: operation.original_id, providerRequestId, responseId: result.id, bodySHA };
    const statementHash = await digest(subject);
    const proof = await verifySettlement(subject);
    need(proof && proof.statementHash === statementHash && sha(proof.receiptSha256) &&
      Number.isSafeInteger(proof.actualMicrousd) && proof.actualMicrousd >= 0,
      'responses_original_requires_verified_settlement', 502);
    const hash = await digest({ operation, provider_receipt: proof.receiptSha256,
      response_sha256: bodySHA, actual_microusd: proof.actualMicrousd });
    const match = this.row('SELECT body FROM grants WHERE issue=?', issueKey(operation.fence.scope));
    const current = match && JSON.parse(match.body);
    const consent = this.commissioned() && this.authorized('grant', issueKey(operation.fence.scope)) &&
      current && equal(current.source, operation.fence.source) && current.valid_until > this.clock() &&
      current.result_retention && equal(current.result_retention, retention);
    this.store.transactionSync(() => {
      const stored = this.row('SELECT op,state,amount FROM effects WHERE id=?', operation.original_id);
      need(stored?.state === 'sending' && stored.op === canonical(operation) &&
        stored.amount === maximumMicrousd, 'responses_original_record_changed', 409);
      if (proof.actualMicrousd > maximumMicrousd) {
        this.put('INSERT OR IGNORE INTO budget_breaches VALUES (?,?,?)',
          account, operation.original_id, proof.actualMicrousd);
        this.put('UPDATE effects SET amount=? WHERE id=?', proof.actualMicrousd, operation.original_id);
        return;
      }
      this.put("UPDATE effects SET state='verified',amount=?,settled_at=?,receipt=? WHERE id=?",
        proof.actualMicrousd, this.clock(), hash, operation.original_id);
      if (consent)
        this.put('INSERT INTO provider_results VALUES (?,?,?,?,?,?,NULL)', operation.original_id,
          bytes.buffer, null, issueKey(operation.fence.scope), canonical(retention), bodySHA);
    });
    need(proof.actualMicrousd <= maximumMicrousd, 'account_reservation_maximum_breached', 502);
    need(consent, 'private_result_grant_changed', 403);
    this.bodyReader(peer, operation, retention);
    return this.providerResponse(bytes, null, hash);
  }

  async send(body, peer, permitHash) {
    fields(body, 'intent', 'body_base64');
    const i = body.intent;
    fields(
      i,
      'operation',
      'route',
      'attempt_id',
      'request_sha256',
      'quota_receipt_sha256',
      'request_not_after',
      'maximum_millineurons',
    );
    const op = i.operation;
    operationShape(this.c, op);
    need(
      this.allowedRoute(i.route) &&
        sha(op.original_id) &&
        sha(i.request_sha256) &&
        sha(i.quota_receipt_sha256) &&
        boundedRequest(i.request_not_after) &&
        token(i.attempt_id) &&
        models.get(i.route.model) === i.maximum_millineurons,
      'send_intent_not_bound',
    );
    const raw = bytes64(body.body_base64, 128000),
      request = json(raw);
    const { method, path, headers } = wire(i.route);
    // Accept only the native canonical request. Duplicate keys, provider overrides,
    // streams, paid fallback and caller-chosen transport headers cannot reach egress.
    need(
      canonical(request) === new TextDecoder().decode(raw) &&
        Object.keys(request).every((k) =>
          [
            'model',
            'messages',
            'max_tokens',
            'stream',
            'tools',
            'chat_template_kwargs',
            'response_format',
          ].includes(k),
        ) &&
        request.model === i.route.model &&
        request.max_tokens === 2048 &&
        request.stream === false &&
        Array.isArray(request.messages) &&
        request.messages.length > 0 &&
        (await digestBytes(raw)) === i.request_sha256,
      'model_body_not_bound',
      400,
    );
    const payload = await digest({
      kind: 'uif.workers-ai-free-send/1',
      route: i.route,
      attempt: i.attempt_id,
      request_sha256: i.request_sha256,
      quota: i.quota_receipt_sha256,
      not_after: i.request_not_after,
      maximum_millineurons: i.maximum_millineurons,
      method,
      path,
      headers,
    });
    need(
      payload === op.payload_sha256 && op.fence.owner_session === peer.session_id,
      'send_fingerprint_changed',
    );
    const reviewRoute = !!this.c.reviewer_route && equal(i.route, this.c.reviewer_route),
      credential = reviewRoute ? this.reviewerProviderToken : this.providerToken;
    const first = request.messages[0];
    need(first?.role === 'user' && typeof first.content === 'string', 'original_prompt_unbound');
    const promptSHA = await digestBytes(new TextEncoder().encode(first.content));
    need(
      typeof credential === 'string' &&
        /^[!-~]+$/.test(credential) &&
        (!reviewRoute || credential !== this.providerToken),
      'physical_sender_unbound',
      503,
    );
    const outgoing = new Request('https://api.cloudflare.com' + path, {
      method,
      redirect: 'manual',
      headers: { ...headers, Authorization: 'Bearer ' + credential },
      body: raw,
      signal: AbortSignal.timeout(
        Math.min(600000, Math.max(1, (i.request_not_after - this.clock()) * 1000)),
      ),
    });
    const retention = this.grant(op.fence.scope, op.fence.source, peer).result_retention;
    const response = await this.physical(
      op,
      i.attempt_id,
      i.route.account_id,
      i.maximum_millineurons,
      (include) => {
        this.qualifySend(peer, i, promptSHA, include);
        this.bindAssessmentProviderOperation(peer, op, i.attempt_id, permitHash, include);
      },
      outgoing,
    );
    // Read and authenticate response at the same fixed origin. HTTP errors,
    // timeout, malformed/missing usage, and missing gateway IDs retain maximum.
    if (response.status !== 200) {
      await response.body?.cancel();
      throw new Refusal('original_provider_effect_unknown', 502);
    }
    const gatewayReceipt = response.headers.get('cf-aig-log-id');
    need(
      i.route.gateway_id === null ||
        (typeof gatewayReceipt === 'string' && /^[A-Za-z0-9_-]{1,256}$/.test(gatewayReceipt)),
      'original_gateway_receipt_missing',
      502,
    );
    const responseBytes = await readBytes(response.body, retention.max_response_bytes);
    const result = json(responseBytes);
    const neurons = result.usage?.neurons;
    need(
      result.model === i.route.model &&
        Array.isArray(result.choices) &&
        result.choices.length > 0 &&
        typeof neurons === 'number' &&
        Number.isFinite(neurons) &&
        neurons >= 0,
      'original_usage_unknown',
      502,
    );
    if (neurons * 1000 > i.maximum_millineurons) {
      const observed = Math.min(Number.MAX_SAFE_INTEGER, Math.ceil(neurons * 1000));
      this.store.transactionSync(() => {
        this.put(
          'INSERT OR IGNORE INTO budget_breaches VALUES (?,?,?)',
          i.route.account_id,
          op.original_id,
          observed,
        );
        this.put('UPDATE effects SET amount=? WHERE id=?', observed, op.original_id);
      });
      throw new Refusal('account_reservation_maximum_breached', 502);
    }
    const bodySHA = await digestBytes(responseBytes);
    const hash = await digest({
      operation: op,
      body_sha256: bodySHA,
      gateway_receipt: gatewayReceipt,
      status: 'verified',
    });
    // Retain the verified bytes in private authority storage in the same commit as
    // the terminal status. A lost client ACK can recover the original without cost.
    const currentGrant = this.row('SELECT body FROM grants WHERE issue=?', issueKey(op.fence.scope));
    const g = currentGrant && JSON.parse(currentGrant.body);
    const consent =
      this.commissioned() &&
      this.authorized('grant', issueKey(op.fence.scope)) &&
      g &&
      equal(g.source, op.fence.source) &&
      g.valid_until > this.clock() &&
      g.result_retention &&
      equal(g.result_retention, retention);
    this.store.transactionSync(() => {
      this.put(
        "UPDATE effects SET state='verified',settled_at=?,receipt=? WHERE id=? AND state='sending'",
        this.clock(),
        hash,
        op.original_id,
      );
      if (consent)
        this.put(
          'INSERT INTO provider_results VALUES (?,?,?,?,?,?,NULL)',
          op.original_id,
          responseBytes.buffer,
          gatewayReceipt,
          issueKey(op.fence.scope),
          canonical(retention),
          bodySHA,
        );
    });
    need(consent, 'private_result_grant_changed', 403);
    this.bodyReader(peer, op, retention);
    return this.providerResponse(responseBytes, gatewayReceipt, hash);
  }
  providerResponse(responseBytes, gatewayReceipt, hash) {
    return new Response(responseBytes, {
      headers: {
        'content-type': 'application/json',
        'uif-receipt-sha256': hash,
        'cache-control': 'no-store',
        ...(gatewayReceipt ? { 'cf-aig-log-id': gatewayReceipt } : {}),
      },
    });
  }
}
