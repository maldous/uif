import assert from 'node:assert/strict';
import test from 'node:test';
import { matchChatPhysicalOperation, prepareChatToolCall, prepareChatToolResult }
  from '../chat-tool-envelope.mjs';

const route = { provider: 'openrouter', model: 'tested/model:free', upstream: 'fixture/fp8/us',
  account: 'free-account', credentialEpoch: 'e'.repeat(64) };
const first = { route, attempt: 'attempt', originalId: 'a'.repeat(64), deadline: 1800,
  maxOutputTokens: 512, messages: [{ role: 'user', content: 'Synthetic question' }],
  tools: [{ name: 'lookup', description: 'Look up synthetic fixture' }] };
const result = { route, attempt: 'attempt', originalId: 'b'.repeat(64), deadline: 1820,
  maxOutputTokens: 128 };
const call = { id: 'call_x', type: 'function', function: { name: 'lookup', arguments: '{"input":"synthetic"}' } };
const reply = { model: route.model, choices: [{ finish_reason: 'tool_calls',
  message: { role: 'assistant', content: null, tool_calls: [call] } }] };
const results = [{ id: call.id, content: 'Synthetic value' }];
const op = (x) => ({ original_id: x.originalId, payload_sha256: x.payloadSHA256 });

test('separately bound two-leg OpenRouter fixture; second leg carries only the exact paired local result', async () => {
  const a = await prepareChatToolCall(first);
  const b = await prepareChatToolResult(result, first, reply, results);
  matchChatPhysicalOperation(op(a), a);
  matchChatPhysicalOperation(op(b), b);
  assert.throws(() => matchChatPhysicalOperation(op(a), b));
  assert.throws(() => matchChatPhysicalOperation({ ...op(b), payload_sha256: a.payloadSHA256 }, b));
  const body = JSON.parse(b.payload);
  const provider = { allow_fallbacks: false, require_parameters: true,
    max_price: { completion: 0, prompt: 0, request: 0 }, only: ['fixture/fp8/us'] };
  assert.deepEqual(JSON.parse(a.payload).provider, provider);
  assert.deepEqual(body.provider, provider);
  assert.equal(body.stream, false);
  assert.equal(body.tools, undefined);
  assert.deepEqual(body.messages.slice(-2), [{ role: 'assistant', content: null, tool_calls: [call] },
    { role: 'tool', tool_call_id: call.id, content: 'Synthetic value' }]);
});

test('Groq exact fixed endpoint has no hosted tools/route knobs', async () => {
  const groq = { ...route, provider: 'groq', model: 'qwen/example', upstream: null };
  const r = await prepareChatToolCall({ ...first, route: groq });
  assert.equal(r.url, 'https://api.groq.com/openai/v1/chat/completions');
  assert.equal(JSON.parse(r.payload).provider, undefined);
  assert.throws(() => prepareChatToolCall({ ...first, plugins: ['chargeable'] }));
  assert.throws(() => prepareChatToolCall({ ...first, route: { ...route, model: 'tested/model' } }));
  assert.throws(() => prepareChatToolCall({ ...first, route: { ...route, model: 'openrouter/auto:free' } }));
  assert.throws(() => prepareChatToolCall({ ...first, route: { ...groq, model: 'default' } }));
  assert.throws(() => prepareChatToolCall({ ...first, route: { ...route, model: route.model + '\n' } }));
  assert.throws(() => prepareChatToolCall({ ...first, route: { ...groq, model: groq.model + '\n' } }));
  for (const upstream of ['fixture//us', 'fixture/us\n', 'fixture/us?price=paid', 'a/b/c/d/e']) {
    assert.throws(() => prepareChatToolCall({ ...first, route: { ...route, upstream } }));
  }
});

test('second leg rejects replay, route or attempt substitution, unknown and counterfeit pairing', async () => {
  for (const override of [{ originalId: first.originalId }, { attempt: 'foreign' },
    { route: { ...route, account: 'other' } }]) {
    await assert.rejects(prepareChatToolResult({ ...result, ...override }, first, reply, results));
  }
  for (const value of [[], [{ id: 'fake', content: 'x' }], [{ id: call.id, content: 'x'.repeat(4097) }]]) {
    await assert.rejects(prepareChatToolResult(result, first, reply, value));
  }
  for (const invalid of [{ ...call, function: { ...call.function, name: 'other' } },
    { ...call, function: { ...call.function, arguments: '{"input":"x","hosted":true}' } }]) {
    const modified = structuredClone(reply);
    modified.choices[0].message.tool_calls = [invalid];
    await assert.rejects(prepareChatToolResult(result, first, modified, results));
  }
});

test('change first transcript, tool result or credential epoch and original binding changes', async () => {
  const a = await prepareChatToolResult(result, first, reply, results);
  const b = await prepareChatToolResult(result, { ...first, messages: [{ role: 'user', content: 'Other' }] },
    reply, results);
  const c = await prepareChatToolResult(result, first, reply, [{ id: call.id, content: 'Other' }]);
  const alternate = { ...route, credentialEpoch: 'c'.repeat(64) };
  const d = await prepareChatToolResult({ ...result, route: alternate }, { ...first, route: alternate },
    reply, results);
  assert.equal(new Set([a, b, c, d].map(x => x.payloadSHA256)).size, 4);
});

test('duplicated calls and unsupported mixed response fail closed', async () => {
  const duplicate = structuredClone(reply);
  duplicate.choices[0].message.tool_calls = [call, call];
  await assert.rejects(prepareChatToolResult(result, first, duplicate, [...results, ...results]));
  const textAndCalls = structuredClone(reply);
  textAndCalls.choices[0].message.content = 'Additional instructions';
  await assert.rejects(prepareChatToolResult(result, first, textAndCalls, results));
});

test('exact single JSON member rejects duplicate and escaped-duplicate input, accepts one escaped member', async () => {
  const withArguments = (args) => {
    const response = structuredClone(reply);
    response.choices[0].message.tool_calls[0].function.arguments = args;
    return response;
  };
  for (const args of [String.raw`{"input":"a","input":"b"}`,
    String.raw`{"input":"a","\u0069nput":"b"}`, String.raw`{"\u0069nput":"a","input":"b"}`,
    '{"input":"a"}\u2028', '{"input":"a"}{}']) {
    await assert.rejects(prepareChatToolResult(result, first, withArguments(args), results));
  }
  const normalized = await prepareChatToolResult(result, first,
    withArguments(String.raw`{"\u0069nput":"a,b:\"quoted\""}`), results);
  const args = JSON.parse(normalized.payload).messages.at(-2).tool_calls[0].function.arguments;
  assert.equal(JSON.parse(args).input, 'a,b:"quoted"');
});
