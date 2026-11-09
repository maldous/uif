import test from 'node:test';
import assert from 'node:assert/strict';
import { TOPICS } from '../worker.mjs';
import { iconFor } from '../public/lib/icons.js';

test('reviewed coordinator roster subjects have deterministic approved icons', () => {
  for (const [key, label, icon] of [
    ['public_deployment', 'Public deployment', 'delivery'],
    ['browser_verification', 'Browser verification', 'testing'],
    ['display_refinement', 'Display refinement', 'graphics'],
    ['monitor_coordination', 'Monitor coordination', 'monitoring'],
    ['monitor_service_implementation', 'Monitor service implementation', 'engineering'],
    ['ledger_packet_preparation', 'Ledger preparation', 'planning'],
  ]) {
    assert.equal(TOPICS[key], label); assert.equal(iconFor([key]), icon);
  }
});
