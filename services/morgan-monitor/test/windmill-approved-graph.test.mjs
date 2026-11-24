import test from 'node:test';
import assert from 'node:assert/strict';
import { main } from '../windmill/morgan_public_render.ts';

const resource = { renderUrl: 'https://morgan.aldous.info/render.svg' };
const revision = 'a'.repeat(64);
const body = extra => `<svg xmlns="http://www.w3.org/2000/svg" data-revision="${revision}" data-layout="full"><defs><filter id="shadow"><feFlood flood-color="white"/><feComposite in2="SourceAlpha" operator="in"/></filter><mask id="clip"><rect width="1" height="1" fill="white"/></mask></defs>${extra}</svg>`;
const response = svg => new Response(svg, { headers: { 'Content-Type': 'image/svg+xml' } });

test('Windmill accepts passive full graph vocabulary with PNG data and embedded TTF only', async t => {
  const svg = body('<style>@font-face{font-family:Roboto;src:url(data:font/ttf;base64,AAEAAA==) format("truetype");font-weight:400}</style><image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/><path fill="url(#shadow)"/>');
  let called;
  t.mock.method(globalThis, 'fetch', async url => { called = new URL(url); return response(svg); });
  const result = await main(resource, revision, 'full', 1920, 1080, null, 'dark');
  assert.equal(result.svg, svg);
  assert.equal(result.metadata.renderedLayout, 'full');
  assert.equal(called.searchParams.get('theme'), 'dark');
  assert.equal(called.searchParams.get('layout'), 'full');
});

test('Windmill still rejects remote, executable and malformed embedded references', async t => {
  for (const extra of [
    '<image href="data:image/svg+xml;base64,PHN2Zz4="/>',
    '<image href="data:text/html;base64,PHNjcmlwdD4="/>',
    '<image href="https://evil.invalid/x.png"/>',
    '<image href="data:image/png;base64,iVBORw==" onload="alert(1)"/>',
    '<image href="data:image/png;base64,iVBORw==" src="https://evil.invalid"/>',
    '<use href="data:image/png;base64,iVBORw=="/>',
    '<style>@font-face{src:url(https://evil.invalid/font.ttf)}</style>',
    '<style>@font-face{src:url(data:text/html;base64,AAEAAA==)}</style>',
    '<image href="data:image/png;base64,&quot;onload=alert(1)"/>',
    '<style>@\\69mport "https://evil.invalid/x.css";</style>',
    '<style>text{fill:u\\72l(https://evil.invalid/x.svg)}</style>',
    '<style>text{fill:u&#114;l(https://evil.invalid/x.svg)}</style>',
    '<g xml:base="https://evil.invalid/"><path fill="url(#x)"/></g>',
    '<style>text{background-image:image-set("https://evil.invalid/x.png" 1x)}</style>',
    '<style>text{background-image:image-set("relative.png" 1x)}</style>',
    '<g style="background-image:image-set(&quot;relative.png&quot; 1x)"/>',
  ]) {
    const mock = t.mock.method(globalThis, 'fetch', async () => response(body(extra)));
    assert.equal((await main(resource)).error, 'renderer_unavailable', extra);
    mock.mock.restore();
  }
});

test('Windmill rejects invalid theme before any network request', async t => {
  let count = 0;
  t.mock.method(globalThis, 'fetch', async () => { count++; return response(body('')); });
  assert.equal((await main(resource, null, 'full', 1600, 1000, null, 'remote')).error, 'invalid_render_options');
  assert.equal(count, 0);
});

test('Windmill selection accepts exactly approved graph positions and legacy public aliases', async t => {
  const called=[];
  t.mock.method(globalThis, 'fetch', async url => { called.push(new URL(url)); return response(body('')); });
  for(const selected of ['durable-Cloud','summary-CQ','Cloud-primary-1','UIF-overflow-6','overall','railway-6','cloud-A','foo-64']) {
    const output=await main(resource,revision,'full',1600,1000,selected);
    assert.ok(output.svg);assert.equal(called.at(-1).searchParams.get('selected'),selected);
  }
  const count=called.length;
  for(const selected of ['UIF-overflow-7','railway-0','foo-65','summary-private','https://evil.invalid','overall#script'])
    assert.equal((await main(resource,revision,'full',1600,1000,selected)).error,'invalid_render_options');
  assert.equal(called.length,count);
});
