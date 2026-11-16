#!/usr/bin/env node
/** Offline stdin JSON → reviewed observation parameters on stdout. No writes. */
import { prepareRuntimeObservation } from '../worker.mjs';
let text = '';
try {
  for await (const chunk of process.stdin) {
    text += chunk;
    if (Buffer.byteLength(text) > 128 * 1024) throw Error();
  }
  const input = JSON.parse(text);
  if (!input || Array.isArray(input) || typeof input !== 'object' || Object.keys(input).length !== 2 ||
      !Object.hasOwn(input, 'sourceConfig') || !Object.hasOwn(input, 'event')) throw Error();
  const prepared = await prepareRuntimeObservation(input.sourceConfig, input.event);
  process.stdout.write(JSON.stringify(prepared) + '\n');
} catch {
  process.stderr.write('Runtime observation preparation rejected. Check reviewed config, exact event, generation and sample time (maximum age 60 seconds).\n');
  process.exitCode = 1;
}
