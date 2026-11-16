// Rebuild only synthetic regression data. This script never reads a live capture.
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { syntheticLegacySnapshot } from './fixtures/synthetic-legacy-status.mjs';
import { isPublicProjection } from '../public/approved-app.js';
import { graphLayout, renderApprovedGraph } from '../public/lib/approved-graph.js';

const snapshot = await syntheticLegacySnapshot();
assert.equal(isPublicProjection(snapshot), true);
const hashes = [];
for (const mode of ['full', 'compact']) for (const theme of ['light', 'dark']) {
  const model = graphLayout(snapshot, { mode, theme });
  assert.equal(model.cells.length, mode === 'full' ? 91 : 13);
  assert.equal(model.overall.percentage, null);
  assert(model.cells.every(cell => cell.status === 'neutral' && cell.slotId === null));
  for (const options of [{ mode, theme }, { mode, theme, footer: false, interactive: false }])
    hashes.push({ options, sha256: createHash('sha256').update(renderApprovedGraph(snapshot, options)).digest('hex') });
}
writeFileSync(new URL('./fixtures/synthetic-legacy-status.json', import.meta.url), JSON.stringify(snapshot) + '\n');
writeFileSync(new URL('./fixtures/synthetic-render-sha256.json', import.meta.url), JSON.stringify(hashes, null, 2) + '\n');
console.log('Rebuilt synthetic legacy projection and eight deterministic renderer hashes.');
