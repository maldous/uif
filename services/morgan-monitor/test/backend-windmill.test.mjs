import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { main } from '../windmill/morgan_public_render.ts';
import { renderSvg } from '../public/lib/render.js';

const resource = { renderUrl: 'https://morgan.aldous.info/render.svg' };
const revision = 'a'.repeat(64);
const svg = `<svg xmlns="http://www.w3.org/2000/svg" data-revision="${revision}" data-layout="compact"><title>Morgan</title><g><text>Public</text></g></svg>`;
const response = (body = svg, extra = {}) => new Response(body, { headers: { 'Content-Type': 'image/svg+xml' }, ...extra });

test('Windmill uses fixed HTTPS SVG endpoint and documented rich-display shape', async t => {
  let called;
  t.mock.method(globalThis, 'fetch', async (url, options) => { called = { url, options }; return response(); });
  const result = await main(resource, revision);
  assert.equal(result.svg, svg); assert.equal(result.metadata.revision, revision);
  assert.equal(result.metadata.renderSha256, createHash('sha256').update(svg).digest('hex'));
  assert.equal(result.metadata.renderedLayout, 'compact');
  assert.equal(result.metadata.requestedLayout, 'compact'); assert(result.metadata.durationMs >= 0);
  const url = new URL(called.url);
  assert.equal(url.origin + url.pathname, resource.renderUrl); assert.equal(url.searchParams.get('revision'), revision);
  assert.equal(url.searchParams.get('layout'), 'compact'); assert.equal(called.options.redirect, 'error');
  assert.deepEqual(called.options.headers, { Accept: 'image/svg+xml' });
});

test('Windmill rejects arbitrary endpoints, secrets, query injection and oversized options before network', async t => {
  let count = 0; t.mock.method(globalThis, 'fetch', async () => { count++; return response(); });
  for (const input of [
    { renderUrl: 'http://127.0.0.1/' }, { renderUrl: 'https://evil.invalid/render.svg' },
    { renderUrl: `${resource.renderUrl}?url=http://127.0.0.1` },
    { ...resource, authorization: 'PRIVATE' }, { renderUrl: 'https://user:secret@morgan.aldous.info/render.svg' },
  ]) assert.equal((await main(input)).error, 'invalid_render_options');
  assert.equal((await main(resource, '../../private')).error, 'invalid_render_options');
  assert.equal((await main(resource, null, 'compact', 999999)).error, 'invalid_render_options');
  assert.equal((await main(resource, null, 'compact', 1600, 1000, 'PRIVATE')).error, 'invalid_render_options');
  assert.equal(count, 0);
});

test('Windmill never exposes upstream exceptions, redirects or private error bodies', async t => {
  for (const fake of [
    async () => { throw new Error('PRIVATE /path token'); },
    async () => response('PRIVATE', { status: 522 }),
    async () => response('PRIVATE', { status: 302, headers: { Location: 'http://127.0.0.1/' } }),
    async () => response('PRIVATE', { headers: { 'Content-Type': 'text/html' } }),
    async () => response(svg, { headers: { 'Content-Type': 'image/svg+xml', 'X-Morgan-Render-SHA256': '0'.repeat(64) } }),
  ]) {
    const mock = t.mock.method(globalThis, 'fetch', fake);
    const result = await main(resource); assert.equal(result.error, 'renderer_unavailable');
    assert(!JSON.stringify(result).includes('PRIVATE')); mock.mock.restore();
  }
});

test('Windmill rejects active SVG, revision mismatch, entities, remote styles and excessive body', async t => {
  for (const body of [
    svg.replace('</svg>', '<script>alert(1)</script></svg>'),
    svg.replace('<g>', '<g onload="alert(1)">'),
    svg.replace('</svg>', '<style>@import "https://evil.invalid";</style></svg>'),
    svg.replace('</svg>', '<style>text{fill:url(https://evil.invalid)}</style></svg>'),
    svg.replace('</svg>', '<image href="https://evil.invalid"/></svg>'),
    '<!DOCTYPE svg><svg></svg>', svg.replace(revision, 'b'.repeat(64)), 'x'.repeat(2 * 1024 * 1024 + 1),
  ]) {
    const mock = t.mock.method(globalThis, 'fetch', async () => response(body));
    assert.equal((await main(resource, revision)).error, 'renderer_unavailable'); mock.mock.restore();
  }
});

test('Windmill returns the exact current deterministic renderer SVG including reviewed shadows', async t => {
  const snapshot = { schemaVersion: 1, generatedAt: '2026-11-09T06:00:00.000Z', revision,
    summary: { executionCoverageComplete: false }, sources: [], slots: [],
    conversationTopics: { voice: [], chat: [], status: [
      { key: 'monitor_service_implementation', text: 'Monitor service implementation', observedAt: '2026-11-09T05:21:09.000Z' },
      { key: 'compact_display', text: 'Compact display', observedAt: '2026-11-09T05:21:09.000Z' },
      { key: 'layout_design', text: 'Layout design', observedAt: '2026-11-09T05:21:09.000Z' },
    ] } };
  for (const layout of ['compact', 'o2', 'hexagon', 'focus']) {
    const rendered = renderSvg(snapshot, { layout, width: 1600, height: 1000 });
    const digest = createHash('sha256').update(rendered).digest('hex');
    const mock = t.mock.method(globalThis, 'fetch', async () => response(rendered, { headers: {
      'Content-Type': 'image/svg+xml', 'X-Morgan-Render-SHA256': digest, 'X-Morgan-Snapshot-Revision': revision,
    } }));
    const output = await main(resource, revision, layout);
    assert.equal(output.svg, rendered); assert.equal(output.metadata.renderSha256, digest);
    mock.mock.restore();
  }
});
