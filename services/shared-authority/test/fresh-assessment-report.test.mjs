import test from 'node:test';
import assert from 'node:assert/strict';
import { canonical, digest, digestBytes } from '../protocol.mjs';
import { deriveFreshAssessmentCommentFromVerifiedOriginal as derive } from '../fresh-assessment-report.mjs';

const scope = {repository_id:'repository-1',key:{
  workspace_id:'00000000-0000-4000-8000-000000000001',
  project_id:'00000000-0000-4000-8000-000000000002',
  issue_id:'00000000-0000-4000-8000-000000000003',
}};
const config = {...scope.key,repository_id:scope.repository_id,authority_id:'realm',
  deployment_sha256:'b'.repeat(64),policy_sha256:'c'.repeat(64),
  peers:{native:{role:'native',session_id:'native-session'}}};
const fence = {scope,source:{repository:'maldous/offmenu',branch:'develop',base:'1'.repeat(40),
    head:'2'.repeat(40),tree:'3'.repeat(40)},owner_session:'native-session',
  coordinator_epoch:2,generation:1,lease_id:'native-lease',authority_id:config.authority_id,
  deployment_sha256:config.deployment_sha256,policy_sha256:config.policy_sha256};
const ownerBinding = {scope,issue_identifier:'OFF-1',run:2,scope_sha256:'a'.repeat(64)};
const attempt_id = 'cf-intake-b07701d6d35d05a8eced5b0be5f02e90a24fe60d9321676a154633fbd423cdb0-assess';
const sealedProviderOperation = {fence,original_id:'e'.repeat(64),payload_sha256:'f'.repeat(64)};
const raw = canonical({summary:'hidden @private', verdict:'approve', routing:{
  issue:'OFF-1',state:'assess',run:2,actionable:true,ownership_clear:true,deferred:false,owner_pause:false,
  uncertain_effects:false,missing_inputs:[],
}});
const bytes = (text) => new TextEncoder().encode(text);
async function retainedFixture(content) {
  const responseBytes = bytes(canonical({choices:[{message:{content}}]}));
  const retainedResponse = {body_sha256:await digestBytes(responseBytes),gateway_receipt:'fixture-gateway'};
  const providerEffect = {operation:sealedProviderOperation,attempt_id,status:'verified',
    receipt_sha256:await digest({operation:sealedProviderOperation,...retainedResponse,status:'verified'})};
  return {config,currentFence:fence,ownerBinding,ownerBindingSha256:await digest(ownerBinding),
    attempt_id,sealedProviderOperation,providerEffect,retainedResponse,responseBytes};
}
const render = async (text, overrides = {}) => {
  const input = await retainedFixture(text);
  const resolved = {...input,...overrides};
  if (overrides.ownerBinding) resolved.ownerBindingSha256 = await digest(overrides.ownerBinding);
  return derive(resolved);
};

test('detached renderer matches actual pinned Python report and operation bytes', async () => {
  const result = await render('```json\n' + raw + '\n```');
  assert.equal(result.report_sha256, '9176efed9384533d70843bae2d2af90d151db5e556afaa51a8cb40ae3ffcb4d2');
  assert.equal(await digestBytes(bytes(result.body)), '688f07a9ba8c60c161a497549e98958129350c19b42328fb53f01695fbe3ed42');
  assert.equal(result.operation.original_id, 'aa90495666948b73fe4dfe9c665e6b27736b999468b403e807e9e058b481f57c');
  assert.equal(result.operation.payload_sha256, '1498d9d8e4fff476e7a34c18f76b25046b17cc8d021f15e5d4cb6143ae173f29');
  assert.doesNotMatch(result.body, /hidden|@private/);
  const changed = await render(raw.replace('hidden','other'));
  assert.equal(changed.operation.original_id, result.operation.original_id);
  assert.notEqual(changed.operation.payload_sha256,result.operation.payload_sha256);
});

test('detached projection rejects wrong owner bindings and ambiguous readiness encodings', async () => {
  await assert.rejects(render(raw,{attempt_id:'other'}), /assessment_report_provider_original/);
  await assert.rejects(render(raw,{ownerBinding:{...ownerBinding,issue_identifier:'OFF-2'}}),
    /assessment_report_binding/);
  await assert.rejects(render(raw,{ownerBinding:{...ownerBinding,scope_sha256:'b'.repeat(64)}}),
    /assessment_report_attempt/);
  for (const encoded of ['2.0','2e0','true']) {
    await assert.rejects(render(raw.replace('"run":2','"run":'+encoded)), /assessment_report_binding/);
  }
  await assert.rejects(render(raw.replace('"actionable":true','"act\\u0069onable":false,"actionable":true')),
    /duplicate_assessment_report_key/);
  await assert.rejects(render(raw.replace('"actionable":true','"actionable":1')),
    /assessment_report_readiness_unsubstantiated/);
  await assert.rejects(render(raw.replace('"summary":"hidden @private"','"summary":"\\u0085"')),
    /assessment_report_verdict/);
  assert.match((await render(raw.replace('"verdict":"approve"','"verdict":"blocked"'))).body, /blocked work/);
});


test('full current fence and exact provider bytes must match protected originals', async () => {
  const input = await retainedFixture(raw);
  await assert.rejects(derive({...input,currentFence:{scope}}), /invalid_fields/);
  for (const delta of [
    {owner_session:'other-session'}, {coordinator_epoch:3}, {generation:2},
    {source:{...fence.source,head:'7'.repeat(40)}},
  ]) await assert.rejects(derive({...input,currentFence:{...fence,...delta}}),
    /assessment_report_current_fence|assessment_report_provider_original/);
  const otherScope = {...scope,key:{...scope.key,issue_id:'00000000-0000-4000-8000-000000000004'}};
  const otherOwner = {...ownerBinding,scope:otherScope};
  await assert.rejects(derive({...input,ownerBinding:otherOwner,ownerBindingSha256:await digest(otherOwner)}),
    /assessment_report_owner_binding/);
  await assert.rejects(derive({...input,responseBytes:(await retainedFixture(raw + ' ')).responseBytes}),
    /assessment_report_provider_receipt/);
  await assert.rejects(derive({...input,providerEffect:{...input.providerEffect,receipt_sha256:'0'.repeat(64)}}),
    /assessment_report_provider_receipt/);
  const result = await derive(input);
  assert.deepEqual(result.operation.fence,fence);
  assert.notEqual(result.operation.fence,input.currentFence);
});

const investigate = (await import('../fresh-assessment-report.mjs')).deriveInvestigationCommentFromVerifiedOriginal;
async function investigationFixture(content) {
  const input = await retainedFixture(content);
  input.attempt_id = attempt_id.replace(/assess$/, 'investigate');
  input.providerEffect.attempt_id = input.attempt_id;
  return input;
}
test('investigation projection preserves bounded Unicode summary with inert witness escapes', async () => {
  const raw = canonical({summary:'Résumé 😀 ` <b> @owner',verdict:'blocked'});
  const input = await investigationFixture(raw);
  const result = await investigate(input);
  assert.match(result.body,/Résumé 😀/);
  assert.match(result.body,/\\u0060 \\u003cb\\u003e \\u0040owner/);
  assert.match(result.body,/Independent review is required before a workflow transition\.$/);
  assert.equal(result.report_sha256,await digestBytes(bytes(raw)));
  assert.equal(result.operation.original_id,await digest({attempt:input.attempt_id,
    slot:'_publish_run_report',kind:'comment',issue:scope.key.issue_id}));

});
test('investigation projection rejects malformed summaries, routing and multiple provider choices', async () => {
  for (const content of [
    '{"summary":" \\t","verdict":"blocked"}',
    '{"summary":"\\ud800","verdict":"blocked"}',
    canonical({summary:'é'.repeat(1025),verdict:'blocked'}),
    '{"summary":"x","verdict":"blocked","verdict":"approve"}',
    canonical({summary:'x',verdict:'unknown'}),
    '{"summary":"x","verdict":"blocked","routing":{"issue":"OFF-1","state":"investigate","run":2.0}}',
    canonical({summary:'x',verdict:'blocked',routing:{issue:'OFF-2',state:'investigate',run:2}}),
  ]) await assert.rejects(investigate(await investigationFixture(content)));
  const input = await investigationFixture('{"summary":"x","verdict":"approve"}');
  input.responseBytes = bytes(canonical({choices:[{message:{content:'{}'}},{message:{content:'{}'}}]}));
  input.retainedResponse.body_sha256 = await digestBytes(input.responseBytes);
  input.providerEffect.receipt_sha256 = await digest({operation:input.sealedProviderOperation,...input.retainedResponse,status:'verified'});
  await assert.rejects(investigate(input),/investigation_report_provider_content/);
});
