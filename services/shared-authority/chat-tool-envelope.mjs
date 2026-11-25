// Disabled source preparation. No HTTP registration, provider I/O, tool
// execution, or claim of provider quota/provenance. A future installed receiver
// must authenticate its quote, retain the first provider result, and use
// Authority.physical independently for both operation IDs.
import { canonical, digest, fields, need, object, sha, token } from './protocol.mjs';

const bytes = (s) => new TextEncoder().encode(s).byteLength;
const string = (s, limit) => typeof s === 'string' && s.length > 0 && bytes(s) <= limit;
const name = (s) => typeof s === 'string' && /^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(s);
const callId = (s) => typeof s === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(s);
const equal = (x, y) => canonical(x) === canonical(y);
const checked = (condition) => need(condition, 'chat_tool_envelope_invalid', 400);

function pinRoute(route) {
  fields(route, 'provider', 'model', 'upstream', 'account', 'credentialEpoch');
  checked(token(route.account) && sha(route.credentialEpoch) && string(route.model, 128));
  if (route.provider === 'openrouter') {
    checked(typeof route.upstream === 'string' && /^[a-z0-9_.-]{1,64}$/.test(route.upstream) &&
      /^[a-z0-9_.-]+\/[a-z0-9_.-]+:free$/.test(route.model));
    return 'https://openrouter.ai/api/v1/chat/completions';
  }
  checked(route.provider === 'groq' && route.upstream === null &&
    /^[a-z0-9_.-]+(?:\/[a-z0-9_.-]+)?$/.test(route.model));
  return 'https://api.groq.com/openai/v1/chat/completions';
}

function catalog(tools) {
  checked(Array.isArray(tools) && tools.length > 0 && tools.length <= 8);
  const names = new Set();
  return tools.map((t) => {
    fields(t, 'name', 'description');
    checked(name(t.name) && !names.has(t.name) && string(t.description, 512));
    names.add(t.name);
    // Deliberately small, portable schema for trusted local tools; callers do
    // not supply arbitrary schemas, hosted tools or provider flags.
    return { type: 'function', function: { name: t.name, description: t.description,
      parameters: { type: 'object', properties: { input: { type: 'string' } },
        required: ['input'], additionalProperties: false } } };
  });
}

function base({ route, attempt, originalId, deadline, maxOutputTokens }) {
  const url = pinRoute(route);
  checked(token(attempt) && sha(originalId) && Number.isSafeInteger(deadline) && deadline > 0 &&
    Number.isSafeInteger(maxOutputTokens) && maxOutputTokens > 0 && maxOutputTokens <= 4096);
  const body = { model: route.model, stream: false, max_completion_tokens: maxOutputTokens };
  if (route.provider === 'openrouter') {
    body.provider = { only: [route.upstream], allow_fallbacks: false, max_price: { prompt: 0, completion: 0 } };
  }
  return { url, body };
}

async function seal(input, leg, url, body, preceding = null) {
  const payload = canonical(body);
  checked(bytes(payload) <= 32768);
  const binding = { schema: 'uif.prepared-chat-tool-exchange/1', leg, originalId: input.originalId,
    route: input.route, attempt: input.attempt, deadline: input.deadline, url,
    method: 'POST', payload, preceding };
  return Object.freeze({ url, payload, originalId: input.originalId, payloadSHA256: await digest(binding) });
}

export function prepareChatToolCall(input) {
  fields(input, 'route', 'attempt', 'originalId', 'deadline', 'maxOutputTokens', 'messages', 'tools');
  const { url, body } = base(input);
  checked(Array.isArray(input.messages) && input.messages.length > 0 && input.messages.length <= 32);
  body.messages = input.messages.map((m) => {
    fields(m, 'role', 'content');
    checked(['system', 'user', 'assistant'].includes(m.role) && string(m.content, 4096));
    return { role: m.role, content: m.content };
  });
  body.tools = catalog(input.tools);
  return seal(input, 'call', url, body);
}

export function normalizeChatToolCalls(response, model, allowedNames) {
  checked(object(response) && response.model === model && Array.isArray(response.choices) &&
    response.choices.length === 1 && object(response.choices[0]) &&
    response.choices[0].finish_reason === 'tool_calls');
  const m = response.choices[0].message;
  checked(object(m) && m.role === 'assistant' && (m.content == null || m.content === '') &&
    Array.isArray(m.tool_calls) && m.tool_calls.length > 0 && m.tool_calls.length <= 8);
  const ids = new Set();
  return m.tool_calls.map((c) => {
    fields(c, 'id', 'type', 'function');
    checked(callId(c.id) && !ids.has(c.id) && c.type === 'function');
    ids.add(c.id);
    fields(c.function, 'name', 'arguments');
    checked(allowedNames.includes(c.function.name) && string(c.function.arguments, 4096));
    // Lex precisely two JSON string lexemes separated by a colon. Parsing
    // the whole object would silently discard duplicate (even escaped) keys.
    const pair = /^[\x20\x09\x0a\x0d]*\{[\x20\x09\x0a\x0d]*("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")[\x20\x09\x0a\x0d]*:[\x20\x09\x0a\x0d]*("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")[\x20\x09\x0a\x0d]*\}[\x20\x09\x0a\x0d]*(?![\s\S])/.exec(c.function.arguments);
    checked(pair && JSON.parse(pair[1]) === 'input');
    const input = JSON.parse(pair[2]);
    checked(bytes(input) <= 2048);
    return { id: c.id, type: 'function',
      function: { name: c.function.name, arguments: canonical({ input }) } };
  });
}

export async function prepareChatToolResult(input, firstInput, retainedResponse, results) {
  fields(input, 'route', 'attempt', 'originalId', 'deadline', 'maxOutputTokens');
  const { url, body } = base(input);
  // Snapshot route/attempt/deadline and original ID before any asynchronous digest.
  input = { ...input, route: { ...input.route } };
  checked(firstInput.originalId !== input.originalId && input.attempt === firstInput.attempt &&
    equal(input.route, firstInput.route));
  const calls = normalizeChatToolCalls(retainedResponse, input.route.model, firstInput.tools.map((t) => t.name));
  checked(Array.isArray(results) && results.length === calls.length);
  const messages = results.map((r, i) => {
    fields(r, 'id', 'content');
    checked(r.id === calls[i].id && string(r.content, 4096));
    return { role: 'tool', tool_call_id: r.id, content: r.content };
  });
  const first = await prepareChatToolCall(firstInput);
  body.messages = [...JSON.parse(first.payload).messages,
    { role: 'assistant', content: null, tool_calls: calls }, ...messages];
  // Final result leg does not advertise any tools or enable a third leg.
  return seal(input, 'result', url, body, { originalId: first.originalId,
    payloadSHA256: first.payloadSHA256, callsSHA256: await digest(calls) });
}

// Use at the pre-reservation boundary. Does not validate the fence/lease or
// authorize an egress. Authority.physical keeps those responsibilities.
export function matchChatPhysicalOperation(operation, prepared) {
  checked(object(operation) && operation.original_id === prepared.originalId &&
    operation.payload_sha256 === prepared.payloadSHA256);
}
