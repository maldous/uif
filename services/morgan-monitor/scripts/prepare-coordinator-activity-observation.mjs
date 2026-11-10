#!/usr/bin/env node
/** Offline stdin JSON → reviewed native activity parameters. No I/O beyond stdio. */
import { prepareCoordinatorActivityObservation } from '../worker.mjs';
let text = '';
try {
  for await (const chunk of process.stdin) {
    text += chunk;
    if (Buffer.byteLength(text) > 128 * 1024) throw Error();
  }
  const input = JSON.parse(text);
  if (!input || Array.isArray(input) || typeof input !== 'object' || Object.keys(input).length !== 2 ||
      !Object.hasOwn(input, 'sourceConfig') || !Object.hasOwn(input, 'event')) throw Error();
  process.stdout.write(JSON.stringify(await prepareCoordinatorActivityObservation(input.sourceConfig, input.event)) + '\n');
} catch {
  process.stderr.write('Native activity preparation rejected. Check reviewed binding, exact event, generation and actual observation time (maximum age 60 seconds).\n');
  process.exitCode = 1;
}
