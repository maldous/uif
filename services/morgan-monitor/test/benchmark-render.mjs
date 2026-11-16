// Offline CPU benchmark. Synthetic public DTO; no live-source or job-latency claim.
import { performance } from 'node:perf_hooks';
import { renderSvg } from '../public/lib/render.js';
import { main as windmillRender } from '../windmill/morgan_public_render.ts';

const snapshot = { schemaVersion: 1, generatedAt: '2026-11-09T06:00:00.000Z', revision: 'a'.repeat(64),
  summary: { executionCoverageComplete: false }, sources: [],
  slots: [...'ABCDEF'].map(alias => ({ id: `cloud-${alias}`, domain: 'cloud', alias, assignment: 'unavailable', availability: 'unconfigured', displayState: null }))
    .concat(Array.from({ length: 64 }, (_, index) => ({ id: `foo-${index + 1}`, domain: 'foo', alias: String(index + 1), assignment: 'unavailable', availability: 'unconfigured', displayState: null }))),
  conversationTopics: { voice: [], chat: [] },
  fooAllowance: { state: 'unavailable', canRunAgents: null, verified: false, availability: 'unconfigured', observedAt: null, ageSeconds: null, staleAfterSeconds: null, outageAfterSeconds: null },
};
const summary = values => {
  values.sort((a, b) => a - b);
  const round = n => Math.round(n * 1000) / 1000;
  return { samples: values.length, minMs: round(values[0]), p50Ms: round(values[Math.floor(values.length * .5)]),
    p95Ms: round(values[Math.floor(values.length * .95)]), maxMs: round(values.at(-1)) };
};
const result = { scope: 'offline synthetic Node CPU and mocked-HTTP wrapper only', node: process.version, layouts: {} };
for (const layout of ['compact', 'o2']) {
  for (let i = 0; i < 20; i++) renderSvg(snapshot, { layout, width: 1600, height: 1000 });
  const times = []; let svg;
  for (let i = 0; i < 300; i++) {
    const start = performance.now(); svg = renderSvg(snapshot, { layout, width: 1600, height: 1000 }); times.push(performance.now() - start);
  }
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(svg, { headers: { 'Content-Type': 'image/svg+xml' } });
  const wrapped = [];
  try {
    for (let i = 0; i < 100; i++) {
      const start = performance.now();
      const output = await windmillRender({ renderUrl: 'https://morgan.aldous.info/render.svg' }, snapshot.revision, layout);
      if (output.svg !== svg) throw new Error('Benchmark wrapper did not return exact SVG');
      wrapped.push(performance.now() - start);
    }
  } finally { globalThis.fetch = originalFetch; }
  result.layouts[layout] = { render: summary(times), mockedWrapper: summary(wrapped), svgBytes: Buffer.byteLength(svg) };
}
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
