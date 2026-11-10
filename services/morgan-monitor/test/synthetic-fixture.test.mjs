import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { syntheticLegacySnapshot, SYNTHETIC_TIME } from './fixtures/synthetic-legacy-status.mjs';
import { isPublicProjection } from '../public/approved-app.js';

test('legacy fixture is reproducible solely from invented schema inputs and an in-memory receiver', async () => {
  const actual = await syntheticLegacySnapshot();
  const saved = JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json', import.meta.url), 'utf8'));
  assert.deepEqual(actual, saved);
  assert.equal(isPublicProjection(saved), true);
  assert.equal(saved.generatedAt, new Date(SYNTHETIC_TIME + 300000).toISOString());
  assert.equal(saved.slots.length, 70);
  assert.deepEqual(saved.sources.map(source => source.status), ['stale', 'fresh', 'unavailable']);
  assert.equal(saved.summary.executionCoverageComplete, false);
  assert(!Object.hasOwn(saved, 'projectAssociations'));
  assert(!Object.hasOwn(saved, 'projectActivities'));
  assert.deepEqual(saved.slots.filter(slot => slot.assignment === 'assigned').map(slot => slot.id), ['cloud-C', 'cloud-E']);
  assert(saved.slots.every(slot => slot.displayState === null));
  for (const text of ['synthetic_legacy_roster', 'SYNTHETIC_ROSTER_TOKEN', 'invented-roster-1', 'Bearer'])
    assert(!JSON.stringify(saved).includes(text));
});
