import {
  bytes64,
  canonical,
  digest,
  digestBytes,
  equal,
  fields,
  json,
  need,
  sha,
  token,
  uint,
} from './protocol.mjs';

// The existing authority owns the candidate bytes. There is no checkout, shell,
// filesystem pointer, deferred write token or second owner on this path.
export const recoveryEntryMutations = true;
export const sourceTables = [
  'CREATE TABLE IF NOT EXISTS source_attempts (attempt TEXT PRIMARY KEY, fence TEXT NOT NULL, approval TEXT NOT NULL, revision INTEGER NOT NULL, byte_count INTEGER NOT NULL, history_bytes INTEGER NOT NULL, sealed TEXT)',
  'CREATE TABLE IF NOT EXISTS source_recovery (attempt TEXT NOT NULL,session TEXT NOT NULL,anchor TEXT NOT NULL,PRIMARY KEY(attempt,session))',
  'CREATE TABLE IF NOT EXISTS source_paths (attempt TEXT NOT NULL,path TEXT NOT NULL,writable INTEGER NOT NULL,mode TEXT,base_sha TEXT,git_blob TEXT,body TEXT,PRIMARY KEY(attempt,path))',
  'CREATE TABLE IF NOT EXISTS source_changes (attempt TEXT NOT NULL,revision INTEGER NOT NULL,path TEXT NOT NULL,post_sha TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(attempt,revision))',
  'CREATE INDEX IF NOT EXISTS source_path_version ON source_changes(attempt,path,revision DESC)',
];
const fileLimit = 256 * 1024,
  totalLimit = 512 * 1024,
  historyLimit = 8 * 1024 * 1024;
const encoder = new TextEncoder(),
  decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
function textBytes(b64) {
  const bytes = b64 === '' ? new Uint8Array() : bytes64(b64, fileLimit);
  let value;
  try {
    value = decoder.decode(bytes);
  } catch {}
  need(typeof value === 'string' && !value.includes('\0'), 'regular_utf8_text_required', 400);
  return { bytes, value };
}
function pathOK(path) {
  return (
    typeof path === 'string' &&
    path.length <= 240 &&
    path
      .split('/')
      .every(
        (p) =>
          /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}$/.test(p) &&
          !p.endsWith('.') &&
          !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p),
      )
  );
}
async function gitBlob(bytes) {
  const prefix = encoder.encode('blob ' + bytes.length + '\0');
  const joined = new Uint8Array(prefix.length + bytes.length);
  joined.set(prefix);
  joined.set(bytes, prefix.length);
  return [...new Uint8Array(await crypto.subtle.digest('SHA-1', joined))]
    .map((x) => x.toString(16).padStart(2, '0'))
    .join('');
}
function live(a, peer, f) {
  need(peer.role === 'native' || peer.role === 'morgan', 'source_peer_required', 403);
  return a.owner(peer, f);
}
function authorize(a, peer, f, attempt, current, forWrite = false) {
  const { row, lease } = live(a, peer, f);
  need(!current || equal(lease, current), 'claim_fence_changed');
  const overlay = a.row('SELECT * FROM source_attempts WHERE attempt=?', attempt);
  need(overlay && overlay.fence === canonical(f), 'source_attempt_not_bound');
  if (row.attempt !== attempt) {
    const grant = a.grant(f.scope, f.source, peer);
    need(
      !forWrite && overlay.sealed && grant.review_of[row.attempt] === attempt,
      'source_attempt_not_current',
    );
  }
  return overlay;
}
function member(a, attempt, path, revision = 64) {
  need(pathOK(path), 'invalid_source_path', 400);
  const base = a.row('SELECT * FROM source_paths WHERE attempt=? AND path=?', attempt, path);
  need(base, 'source_path_not_granted', 403);
  const change = a.row(
    'SELECT * FROM source_changes WHERE attempt=? AND path=? AND revision<=? ORDER BY revision DESC LIMIT 1',
    attempt,
    path,
    revision,
  );
  return {
    base,
    mode: change ? (base.mode ?? '100644') : base.mode,
    sha256: change?.post_sha ?? base.base_sha,
    body_base64: change?.body ?? base.body,
  };
}
function reference(b) {
  const { attempt_id, provider_request_id, tool_call_index, tool_call_id } = b;
  need(
    token(attempt_id) &&
      sha(provider_request_id) &&
      uint(tool_call_index) &&
      tool_call_index < 64 &&
      token(tool_call_id),
    'invalid_source_tool_reference',
    400,
  );
  return { attempt_id, provider_request_id, tool_call_index, tool_call_id };
}
function stored(a, id) {
  const row = a.row(
    "SELECT e.op,e.receipt,r.result FROM effects e JOIN destination_results r ON r.id=e.id WHERE e.id=? AND e.state='verified' AND e.account IS NULL",
    id,
  );
  const result = row && JSON.parse(row.result);
  if (!result || result.kind !== 'source-write') return null;
  return { operation: JSON.parse(row.op), ...result.value, receipt_sha256: row.receipt };
}
function instruction(a, peer, f, ref, path) {
  const r = a.row(
    "SELECT e.op,e.attempt,e.account,p.response,p.policy FROM effects e JOIN provider_results p ON p.id=e.id WHERE e.id=? AND e.state='verified'",
    ref.provider_request_id,
  );
  need(
    r &&
      r.account === a.c.route?.account_id &&
      r.attempt === ref.attempt_id &&
      equal(JSON.parse(r.op).fence, f),
    'original_author_tool_result_required',
  );
  a.bodyReader(peer, JSON.parse(r.op), JSON.parse(r.policy));
  const calls = json(r.response).choices?.[0]?.message?.tool_calls;
  const call = calls?.[ref.tool_call_index];
  need(
    Array.isArray(calls) &&
      calls.filter((c) => c?.id === ref.tool_call_id).length === 1 &&
      call?.type === 'function' &&
      call.id === ref.tool_call_id &&
      ['replace_text', 'create_file'].includes(call.function?.name),
    'original_source_tool_not_matched',
  );
  need(typeof call.function.arguments === 'string', 'invalid_original_tool_arguments');
  let args;
  try {
    args = JSON.parse(call.function.arguments);
  } catch {}
  const name = call.function.name;
  fields(args, 'path', ...(name === 'create_file' ? ['content'] : ['old', 'replacement']));
  need(
    Object.values(args).every((x) => typeof x === 'string') && args.path === path,
    'original_source_tool_path_changed',
  );
  return { name, args };
}
function changeMatches(current, after, tool) {
  const { name, args } = tool;
  if (name === 'create_file') {
    need(current.sha256 === null && after === args.content, 'original_create_not_matched');
    return;
  }
  need(current.sha256 !== null && args.old.length > 0, 'original_replace_not_matched');
  const before = textBytes(current.body_base64).value,
    i = before.indexOf(args.old);
  need(
    i >= 0 &&
      before.indexOf(args.old, i + args.old.length) === -1 &&
      after === before.slice(0, i) + args.replacement + before.slice(i + args.old.length) &&
      after !== before,
    'original_replace_not_matched',
  );
}
async function bind(a, b, peer) {
  need(peer.role === 'control', 'source_binding_control_required', 403);
  fields(
    b,
    'request_id',
    'current',
    'attempt_id',
    'public_source_receipt_sha256',
    'paths',
    ...(Object.hasOwn(b, 'recovery_readers') ? ['recovery_readers'] : []),
  );
  const readers = Object.hasOwn(b, 'recovery_readers') ? b.recovery_readers : [];
  need(
    Array.isArray(readers) &&
      readers.length <= 2 &&
      new Set(readers).size === readers.length &&
      readers.every((id) => id === peer.session_id || id === b.current.fence.owner_session),
    'invalid_source_recovery_readers',
  );
  need(
    token(b.attempt_id) &&
      sha(b.public_source_receipt_sha256) &&
      Array.isArray(b.paths) &&
      b.paths.length > 0 &&
      b.paths.length <= 32,
    'invalid_source_binding',
    400,
  );
  const names = new Map();
  let size = 0;
  for (const m of b.paths) {
    fields(m, 'path', 'writable', 'mode', 'base_sha256', 'base_git_blob', 'base_base64');
    need(pathOK(m.path) && typeof m.writable === 'boolean', 'invalid_source_path', 400);
    // Reject directory case aliases as well as file aliases and file/parent collisions.
    const pieces = m.path.split('/');
    for (let i = 1; i <= pieces.length; i++) {
      const p = pieces.slice(0, i).join('/'),
        key = p.toLowerCase(),
        leaf = i === pieces.length,
        prior = names.get(key);
      need(!prior || (!leaf && !prior.leaf && prior.path === p), 'ambiguous_source_path', 400);
      names.set(key, { leaf, path: p });
    }
    if (m.mode === null) {
      need(
        m.writable && [m.base_sha256, m.base_git_blob, m.base_base64].every((x) => x === null),
        'unverified_absence',
        400,
      );
    } else {
      need(
        ['100644', '100755'].includes(m.mode) && sha(m.base_sha256),
        'regular_source_file_required',
        400,
      );
      const { bytes } = textBytes(m.base_base64);
      size += bytes.length;
      need(
        size <= totalLimit &&
          (await digestBytes(bytes)) === m.base_sha256 &&
          (await gitBlob(bytes)) === m.base_git_blob,
        'source_base_pin_mismatch',
        400,
      );
    }
  }
  // Only this independently authenticated control may bind repository membership,
  // selected public contents, verified absence and regular tree ancestors.
  return a.receipt(peer, b.request_id, '/v1/source/bind', b, () => {
    const f = b.current.fence;
    const principal = Object.values(a.c.peers).find((p) => p.session_id === f.owner_session);
    need(principal, 'owner_not_current');
    const { row, lease } = a.owner({ ...principal, expires_at: peer.expires_at }, f);
    const grant = a.grant(f.scope, f.source, principal);
    need(
      equal(lease, b.current) &&
        row.attempt === b.attempt_id &&
        !Object.hasOwn(grant.review_of, b.attempt_id) &&
        grant.allow_provider === true &&
        grant.result_retention?.readers.includes(f.owner_session),
      'source_author_not_granted',
    );
    need(
      !a.row('SELECT attempt FROM source_attempts WHERE attempt=?', b.attempt_id),
      'immutable_source_binding_exists',
    );
    a.put(
      'INSERT INTO source_attempts VALUES (?,?,?,0,?,0,NULL)',
      b.attempt_id,
      canonical(f),
      b.public_source_receipt_sha256,
      size,
    );
    for (const session of readers)
      a.put('INSERT INTO source_recovery VALUES (?,?,?)', b.attempt_id, session, a.anchor);
    for (const m of b.paths)
      a.put(
        'INSERT INTO source_paths VALUES (?,?,?,?,?,?,?)',
        b.attempt_id,
        m.path,
        Number(m.writable),
        m.mode,
        m.base_sha256,
        m.base_git_blob,
        m.base_base64,
      );
    return {
      attempt_id: b.attempt_id,
      revision: 0,
      public_source_receipt_sha256: b.public_source_receipt_sha256,
    };
  });
}
function sizeOf(s) {
  return s === null ? 0 : (s.length / 4) * 3 - (s.endsWith('==') ? 2 : s.endsWith('=') ? 1 : 0);
}
async function write(a, b, peer, operationShape, rawPermit) {
  fields(
    b,
    'operation',
    'attempt_id',
    'provider_request_id',
    'tool_call_index',
    'tool_call_id',
    'path',
    'expected_revision',
    'preimage_sha256',
    'postimage_sha256',
    'body_base64',
  );
  const op = b.operation;
  operationShape(a.c, op);
  const f = op.fence,
    ref = reference(b);
  authorize(a, peer, f, ref.attempt_id, null, true);
  const after = textBytes(b.body_base64);
  const id = await digest({ kind: 'uif.source-tool-original/1', ...ref });
  const payload = {
    kind: 'uif.native-source-write/1',
    ...ref,
    path: b.path,
    expected_revision: b.expected_revision,
    preimage_sha256: b.preimage_sha256,
    postimage_sha256: b.postimage_sha256,
  };
  need(
    pathOK(b.path) &&
      uint(b.expected_revision) &&
      (b.preimage_sha256 === null || sha(b.preimage_sha256)) &&
      id === op.original_id &&
      (await digest(payload)) === op.payload_sha256 &&
      (await digestBytes(after.bytes)) === b.postimage_sha256,
    'source_write_fingerprint_changed',
  );
  authorize(a, peer, f, ref.attempt_id, null, true);
  const previous = stored(a, id);
  if (previous) {
    need(equal(previous.operation, op), 'original_operation_changed');
    return previous;
  }
  const permitHash = await a.recoveryPermitHash(f, ref.attempt_id, peer, rawPermit);
  const tool = instruction(a, peer, f, ref, b.path);
  const result = {
    revision: b.expected_revision + 1,
    path: b.path,
    sha256: b.postimage_sha256,
    tool_name: tool.name,
    arguments_sha256: await digest(tool.args),
  };
  const receipt = await digest({ kind: 'uif.native-source-receipt/1', operation: op, ...result });
  return a.store.transactionSync(() => {
    const overlay = authorize(a, peer, f, ref.attempt_id, null, true),
      prior = stored(a, id);
    if (prior) {
      need(equal(prior.operation, op), 'original_operation_changed');
      return prior;
    }
    need(!a.row('SELECT id FROM effects WHERE id=?', id), 'original_operation_changed');
    const current = member(a, ref.attempt_id, b.path);
    need(
      !overlay.sealed &&
        current.base.writable === 1 &&
        overlay.revision === b.expected_revision &&
        current.sha256 === b.preimage_sha256,
      'source_revision_or_preimage_changed',
    );
    const size = overlay.byte_count - sizeOf(current.body_base64) + after.bytes.length;
    need(
      size <= totalLimit &&
        overlay.revision < 64 &&
        overlay.history_bytes + after.bytes.length <= historyLimit,
      'source_storage_bound',
    );
    need(equal(tool, instruction(a, peer, f, ref, b.path)), 'original_source_tool_changed');
    changeMatches(current, after.value, tool);
    authorize(a, peer, f, ref.attempt_id, null, true);
    a.requireRecoveryPermit(f, ref.attempt_id, permitHash);
    a.put(
      "INSERT INTO effects VALUES (?,?,?,?,NULL,0,'verified',?,?)",
      id,
      f.scope.key.workspace_id + ':' + f.scope.key.issue_id,
      canonical(op),
      ref.attempt_id,
      receipt,
      a.clock(),
    );
    a.put(
      'INSERT INTO destination_results VALUES (?,?)',
      id,
      canonical({ kind: 'source-write', value: result }),
    );
    a.put(
      'INSERT INTO source_changes VALUES (?,?,?,?,?)',
      ref.attempt_id,
      result.revision,
      b.path,
      result.sha256,
      b.body_base64,
    );
    a.put(
      'UPDATE source_attempts SET revision=?,byte_count=?,history_bytes=history_bytes+? WHERE attempt=?',
      result.revision,
      size,
      after.bytes.length,
      ref.attempt_id,
    );
    return { operation: op, ...result, receipt_sha256: receipt };
  });
}
function candidate(a, b, o) {
  const files = [];
  for (const { path } of a.sql.exec(
    'SELECT path FROM source_paths WHERE attempt=? ORDER BY path',
    b.attempt_id,
  )) {
    const m = member(a, b.attempt_id, path, o.revision);
    if (m.sha256 === m.base.base_sha) continue;
    files.push({
      path,
      base_mode: m.base.mode,
      mode: m.mode,
      base_git_blob: m.base.git_blob,
      preimage_sha256: m.base.base_sha,
      base_base64: m.base.body,
      postimage_sha256: m.sha256,
      body_base64: m.body_base64,
    });
  }
  need(files.length > 0, 'source_candidate_has_no_delta');
  return {
    schema: 'uif.source-candidate/1',
    fence: b.current.fence,
    source: b.current.fence.source,
    author_attempt_id: b.attempt_id,
    revision: o.revision,
    public_source_receipt_sha256: o.approval,
    files,
  };
}
// Historical lookup is read-only and bound to the original control's explicit
// source recovery ACL. Expired ownership cannot issue edits or freeze a candidate.
async function historical(a, path, b, peer, operationShape) {
  const revoke = path === '/v1/source/recovery/revoke';
  fields(
    b,
    'attempt_id',
    'fence',
    ...(revoke ? ['request_id', 'session_id'] : ['revision', 'expected_candidate_sha256']),
  );
  const { fence, attempt_id } = b;
  operationShape(a.c, { fence, original_id: attempt_id, payload_sha256: '0'.repeat(64) }, true);
  const original = () => {
    a.live(peer);
    need(a.commissioned(), 'installation_recommission_required');
    const o = a.row('SELECT * FROM source_attempts WHERE attempt=?', attempt_id);
    need(o && o.fence === canonical(fence), 'original_source_binding_not_matched');
    return o;
  };
  if (revoke) {
    need(peer.role === 'control' && token(b.session_id), 'source_binding_control_required', 403);
    return a.receipt(peer, b.request_id, path, b, () => {
      original();
      a.put(
        'DELETE FROM source_recovery WHERE attempt=? AND session=? AND anchor=?',
        attempt_id,
        b.session_id,
        a.anchor,
      );
      return { revoked: true, attempt_id, session_id: b.session_id };
    });
  }
  const allowed = () => {
    need(
      a.row(
        'SELECT session FROM source_recovery WHERE attempt=? AND session=? AND anchor=?',
        attempt_id,
        peer.session_id,
        a.anchor,
      ),
      'source_recovery_reader_not_granted',
      403,
    );
    return original();
  };
  const o = allowed();
  if (b.revision === null) {
    need(b.expected_candidate_sha256 === null, 'source_revision_required');
    return { revision: o.revision, sealed_sha256: o.sealed };
  }
  need(
    uint(b.revision) &&
      b.revision <= o.revision &&
      (b.expected_candidate_sha256 === null || sha(b.expected_candidate_sha256)),
    'invalid_source_revision',
  );
  const artifact = candidate(a, { current: { fence }, attempt_id }, { ...o, revision: b.revision });
  const hash = await digest(artifact),
    latest = allowed();
  need(
    b.expected_candidate_sha256 === null || hash === b.expected_candidate_sha256,
    'original_source_digest_changed',
  );
  return {
    candidate: artifact,
    candidate_sha256: hash,
    sealed: latest.revision === b.revision && latest.sealed === hash,
  };
}
export async function sourceRoute(a, path, b, peer, operationShape, rawPermit) {
  if (path === '/v1/source/original' || path === '/v1/source/recovery/revoke')
    return historical(a, path, b, peer, operationShape);
  if (path === '/v1/source/bind') return bind(a, b, peer);
  if (path === '/v1/source/write') return write(a, b, peer, operationShape, rawPermit);
  const action = path.slice('/v1/source/'.length),
    pairs = {
      list: [],
      read: ['path'],
      checkpoint: ['revision'],
      receipt: ['provider_request_id', 'tool_call_index', 'tool_call_id'],
    };
  need(Object.hasOwn(pairs, action), 'unknown_source_route', 404);
  fields(b, 'current', 'attempt_id', ...pairs[action]);
  const f = b.current.fence,
    context = () => authorize(a, peer, f, b.attempt_id, b.current);
  const o = context();
  if (action === 'list') {
    const members = [
      ...a.sql.exec(
        'SELECT path,writable FROM source_paths WHERE attempt=? ORDER BY path',
        b.attempt_id,
      ),
    ];
    return {
      revision: o.revision,
      public_source_receipt_sha256: o.approval,
      paths: members.map((m) => m.path),
      writable_paths: members.filter((m) => m.writable === 1).map((m) => m.path),
      sealed: !!o.sealed,
    };
  }
  if (action === 'read') {
    const { mode, sha256, body_base64 } = member(a, b.attempt_id, b.path);
    return { revision: o.revision, path: b.path, mode, sha256, body_base64 };
  }
  if (action === 'receipt') {
    const id = await digest({ kind: 'uif.source-tool-original/1', ...reference(b) });
    context();
    const r = stored(a, id);
    need(r && equal(r.operation.fence, f), 'original_source_write_absent', 404);
    return r;
  }
  need(uint(b.revision) && o.revision === b.revision, 'source_revision_changed');
  // An already sealed candidate is readback, including for an admitted reviewer.
  const artifact = candidate(a, b, o),
    candidate_sha256 = await digest(artifact),
    permitHash = o.sealed ? undefined : await a.recoveryPermitHash(f, b.attempt_id, peer, rawPermit);
  return a.store.transactionSync(() => {
    const latest = context();
    need(
      latest.revision === b.revision && (!latest.sealed || latest.sealed === candidate_sha256),
      'source_revision_changed',
    );
    if (!latest.sealed) {
      a.requireRecoveryPermit(f, b.attempt_id, permitHash);
      a.put('UPDATE source_attempts SET sealed=? WHERE attempt=?', candidate_sha256, b.attempt_id);
    }
    return { candidate: artifact, candidate_sha256 };
  });
}
