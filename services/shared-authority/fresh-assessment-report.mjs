// Detached fresh-assessment report projection. Not an HTTP route or owner issuer.
import { canonical, digest, digestBytes, equal, fields, need, object, scopeSource, sha, token, uint, uuid } from './protocol.mjs';

const utf8 = new TextEncoder();
const limit = 64_000;
const labels = {
  approve: 'Assessment report indicates readiness for bounded work.',
  blocked: 'Assessment report indicates blocked work.',
  deferred: 'Assessment report indicates deferred work.',
};

function strictJson(text, assessment = false) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    need(false, 'invalid_assessment_report', 400);
  }
  // JSON.parse validates grammar first but collapses duplicate keys and
  // float/exponent lexical forms. Recover those two facts from exact tokens.
  const stack = [];
  const tokens = /("(?:[^"\\]|\\.)*")([ \t\r\n]*:[ \t\r\n]*(-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)?)?|([{}\[\]])/gs;
  for (const [, string, colon, number, bracket] of text.matchAll(tokens)) {
    const parent = stack.at(-1);
    if (string !== undefined && colon !== undefined) {
      const key = JSON.parse(string);
      need(parent?.keys && !parent.keys.has(key), 'duplicate_assessment_report_key', 400);
      parent.keys.add(key);
      parent.nextIsRouting = stack.length === 1 && key === 'routing';
      if (assessment && parent.routing && key === 'run')
        need(/^[1-9][0-9]*$/.test(number ?? ''), 'assessment_report_binding', 400);
    } else if (bracket === '{' || bracket === '[') {
      const routing = stack.length === 1 && parent?.nextIsRouting === true && bracket === '{';
      stack.push({keys: bracket === '{' ? new Set() : null, routing, nextIsRouting: false});
      if (parent) parent.nextIsRouting = false;
    } else if (bracket === '}' || bracket === ']') {
      stack.pop();
    }
  }
  return parsed;
}

function strictText(raw, maximum) {
  need(raw instanceof Uint8Array && raw.length > 0 && raw.length <= maximum,
    'assessment_report_size', 400);
  try {
    return new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(raw);
  } catch {
    need(false, 'invalid_assessment_report', 400);
  }
}

function exactReport(raw, binding) {
  let text = strictText(raw, limit);
  const fence = /^[ \t\r\n\v\f]*```json[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t\r\n\v\f]*$/i.exec(text);
  text = fence?.[1] ?? text.replace(/^\uFEFF/, '');
  const parsed = strictJson(text, true);
  const routing = parsed?.routing;
  need(object(parsed) && object(routing) && routing.issue === binding.issue_identifier &&
    routing.state === 'assess' && uint(routing.run) && routing.run === binding.run,
    'assessment_report_binding', 400);
  need(typeof parsed.summary === 'string' && !/^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]*$/u.test(parsed.summary) &&
    typeof parsed.verdict === 'string' && Object.hasOwn(labels, parsed.verdict),
    'assessment_report_verdict', 400);
  if (parsed.verdict === 'approve') {
    need(routing.actionable === true && routing.ownership_clear === true &&
      routing.deferred === false && routing.owner_pause === false && routing.uncertain_effects === false &&
      Array.isArray(routing.missing_inputs) && routing.missing_inputs.length === 0,
      'assessment_report_readiness_unsubstantiated', 400);
  }
  return parsed.verdict;
}

async function renderFreshAssessmentComment(raw, {fence, attempt_id, ownerBinding}) {
  const {issue_identifier, run, scope_sha256} = ownerBinding;
  const issue_id = fence?.scope?.key?.issue_id;
  need(uuid(issue_id) && typeof issue_identifier === 'string' && /^[A-Z][A-Z0-9]*-[0-9]+$/.test(issue_identifier)
    && uint(run) && run > 0 && sha(scope_sha256), 'assessment_report_owner_binding', 400);
  // Matches the tracked Python intake stage ID's default JSON separators.
  const attemptDigest = await digestBytes(utf8.encode(
    '[' + ['OffMenu', issue_id, scope_sha256, run].map((item) => JSON.stringify(item)).join(', ') + ']',
  ));
  need(attempt_id === 'cf-intake-' + attemptDigest + '-assess', 'assessment_report_attempt', 400);
  const verdict = exactReport(raw, ownerBinding);
  const label = labels[verdict];
  const report_sha256 = await digestBytes(raw);
  const binding = canonical({attempt_id, issue: issue_identifier, run, state: 'assess', scope_sha256, report_sha256});
  const body = '<!-- stokowski:report -->\n\n### Native routing evidence\n\n```json\n' + binding +
    '\n```\n\n## assess — ' + issue_identifier + '\n\n' + label +
    '\n\nThis report does not authorize a workflow transition.';
  return {
    body, report_sha256, reported_ready: verdict === 'approve',
    operation: {
      fence,
      original_id: await digest({attempt: attempt_id, slot: '_publish_run_report', kind: 'comment', issue: issue_id}),
      payload_sha256: await digest({kind: 'uif.linear-comment/1', attempt_id, issue_id, body}),
    },
  };
}


function nativeFence(config, fence) {
  fields(fence, 'scope', 'source', 'owner_session', 'coordinator_epoch', 'generation', 'lease_id',
    'authority_id', 'deployment_sha256', 'policy_sha256');
  scopeSource(config, fence.scope, fence.source);
  need(token(fence.owner_session) && token(fence.lease_id) && uint(fence.coordinator_epoch) &&
    fence.coordinator_epoch > 0 && uint(fence.generation) && fence.generation > 0 &&
    fence.authority_id === config.authority_id && fence.deployment_sha256 === config.deployment_sha256 &&
    fence.policy_sha256 === config.policy_sha256 && Object.values(config.peers).some((peer) =>
      peer.role === 'native' && peer.session_id === fence.owner_session),
  'assessment_report_current_fence', 403);
}

export async function deriveFreshAssessmentCommentFromVerifiedOriginal({
  config, currentFence, ownerBinding, ownerBindingSha256, attempt_id, sealedProviderOperation,
  providerEffect, retainedResponse, responseBytes,
}) {
  // Private join only. The real authority must first authenticate the caller,
  // verify the current native fence and resolve the protected owner binding
  // and both original provider records from its own custody. No HTTP inputs
  // may supply these records; hashes alone do not authenticate an issuer.
  const clone = (value) => JSON.parse(canonical(value));
  const fence = clone(currentFence);
  const binding = clone(ownerBinding);
  const operation = clone(sealedProviderOperation);
  const effect = clone(providerEffect);
  const retained = clone(retainedResponse);
  nativeFence(config, fence);
  fields(binding, 'scope', 'issue_identifier', 'run', 'scope_sha256');
  fields(operation, 'fence', 'original_id', 'payload_sha256');
  fields(effect, 'operation', 'attempt_id', 'status', 'receipt_sha256');
  fields(retained, 'body_sha256', 'gateway_receipt');
  const raw = responseBytes instanceof Uint8Array ? new Uint8Array(responseBytes) : undefined;
  const text = strictText(raw, 256 * 1024);
  need(sha(ownerBindingSha256) && equal(binding.scope, fence.scope) &&
    await digest(binding) === ownerBindingSha256, 'assessment_report_owner_binding', 403);
  nativeFence(config, operation.fence);
  need(equal(operation.fence, fence) && sha(operation.original_id) && sha(operation.payload_sha256) &&
    equal(effect.operation, operation) && effect.attempt_id === attempt_id &&
    effect.status === 'verified' && sha(effect.receipt_sha256), 'assessment_report_provider_original', 403);
  need(sha(retained.body_sha256) && (retained.gateway_receipt === null ||
    (typeof retained.gateway_receipt === 'string' && /^[A-Za-z0-9_-]{1,256}$/.test(retained.gateway_receipt))) &&
    await digestBytes(raw) === retained.body_sha256 && await digest({
      operation, body_sha256: retained.body_sha256, gateway_receipt: retained.gateway_receipt, status: 'verified',
    }) === effect.receipt_sha256, 'assessment_report_provider_receipt', 403);
  const response = strictJson(text);
  const message = response?.choices?.[0]?.message;
  need(object(message) && (message.tool_calls == null ||
    Array.isArray(message.tool_calls) && message.tool_calls.length === 0) &&
    typeof message.content === 'string' && !/[\uD800-\uDFFF]/u.test(message.content),
  'assessment_report_provider_content', 400);
  return renderFreshAssessmentComment(utf8.encode(message.content), {fence, attempt_id, ownerBinding: binding});
}
