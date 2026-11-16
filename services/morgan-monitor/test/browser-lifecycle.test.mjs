// Offline DOM lifecycle model against actual app.js. This is not browser QA.
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { renderSvg, escapeXml } from "../public/lib/render.js";
import {
  layoutFor,
  axialToPixel,
  historyFor,
  shouldCompact,
  shouldRestoreFull,
} from "../public/lib/layout.js";
const source = readFileSync(
  new URL("../public/app.js", import.meta.url),
  "utf8",
).replace(/^import [\s\S]*?;\n/gm, "");
const settle = () => new Promise((resolve) => setImmediate(resolve));
const data = () => ({
  schemaVersion: 1,
  generatedAt: "2026-11-09T06:00:00.000Z",
  revision: "a".repeat(64),
  summary: { executionCoverageComplete: true },
  sources: [
    { status: "fresh", aliases: ["A"], executionCoverageComplete: true },
  ],
  slots: [
    {
      id: "cloud-A",
      alias: "A",
      domain: "cloud",
      assignment: "assigned",
      availability: "fresh",
      proof: "process",
      displayState: "running",
      topicKeys: ["engineering"],
      topics: ["Engineering"],
    },
  ],
  conversationTopics: { voice: [], chat: [] },
});
function harness(initialData = data()) {
  const documentEvents = {},
    windowEvents = {},
    elements = new Map(),
    timers = new Map();
  let nextTimer = 1,
    calls = 0,
    mode = "ok",
    releases = [];
  const element = () => ({
    innerHTML: "",
    textContent: "",
    classList: { add() {}, remove() {} },
    addEventListener() {},
    setAttribute() {},
    querySelector() {
      return {
        viewBox: { baseVal: { x: 0, y: 0, width: 100, height: 100 } },
        setAttribute() {},
        focus() {},
      };
    },
  });
  const context = {
    renderSvg,
    escapeXml,
    layoutFor,
    axialToPixel,
    historyFor,
    shouldCompact,
    shouldRestoreFull,
    URLSearchParams,
    AbortController,
    Date,
    Math,
    Map,
    Blob,
    URL,
    location: { search: "" },
    setTimeout(fn) {
      const id = nextTimer++;
      timers.set(id, fn);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    document: {
      hidden: false,
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      addEventListener(n, h) {
        documentEvents[n] = h;
      },
    },
    window: {
      addEventListener(n, h) {
        windowEvents[n] = h;
      },
    },
    fetch: async () => {
      calls++;
      if (mode === "error") throw Error("Test outage");
      if (mode === "hold")
        await new Promise((resolve) => releases.push(resolve));
      return { ok: true, json: async () => structuredClone(initialData) };
    },
  };
  vm.runInNewContext(source, context);
  return {
    documentEvents,
    windowEvents,
    elements,
    context,
    calls: () => calls,
    mode(value) {
      mode = value;
    },
    release() {
      for (const fn of releases.splice(0)) fn();
    },
    timers,
  };
}
test("BFCache restores polling and immediately demotes old fresh evidence", async () => {
  const h = harness();
  await settle();
  assert.equal(h.calls(), 2);
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="proud"/);
  h.windowEvents.pagehide({ persisted: true });
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="neutral"/);
  assert(!h.elements.get("board").innerHTML.includes('fill="#e2f3e7"'));
  h.mode("hold");
  h.windowEvents.pageshow({ persisted: true });
  assert.equal(h.calls(), 3);
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="neutral"/);
  h.mode("ok");
  h.release();
  await settle();
  assert.equal(h.calls(), 4);
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="proud"/);
});
test("failed feed removes current colors and source completeness then retries", async () => {
  const h = harness();
  await settle();
  h.mode("error");
  h.documentEvents.visibilitychange();
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="neutral"/);
  assert(!h.elements.get("board").innerHTML.includes('fill="#e2f3e7"'));
  assert.match(
    h.elements.get("connection").textContent,
    /could not be reached/,
  );
  h.mode("ok");
  h.documentEvents.visibilitychange();
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="proud"/);
});
test("a suspended in-flight response cannot repaint fresh claims after pagehide", async () => {
  const h = harness();
  await settle();
  h.mode("hold");
  h.documentEvents.visibilitychange();
  h.windowEvents.pagehide({ persisted: true });
  h.mode("ok");
  h.release();
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="neutral"/);
  h.windowEvents.pageshow({ persisted: true });
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-morgan-mode="proud"/);
});

test("underlying unknown values are omitted from public details", async () => {
  const fixture = data();
  fixture.sources[0].serviceState = "unknown";
  fixture.slots[0].availability = "stale";
  fixture.slots[0].recordedState = "unknown";
  fixture.slots[0].displayState = null;
  const h = harness(fixture);
  await settle();
  assert(!h.elements.get("detail-body").innerHTML.includes("unknown"));
  vm.runInNewContext('choose("cloud-A")', h.context);
  assert(!h.elements.get("detail-body").innerHTML.includes("unknown"));
});

test("selected MCP compact view persists through absent evidence and only restores on verified valid allowance", async () => {
  const fixture = data(),
    h = harness(fixture);
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-layout="compact"/);
  h.mode("error");
  h.documentEvents.visibilitychange();
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-layout="compact"/);
  fixture.revision = "b".repeat(64);
  fixture.fooAllowance = {
    verified: true,
    state: "valid",
    canRunAgents: true,
    availability: "fresh",
    observedAt: fixture.generatedAt,
    staleAfterSeconds: 120,
  };
  h.mode("ok");
  h.documentEvents.visibilitychange();
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-layout="o2"/);
  fixture.revision = "c".repeat(64);
  fixture.fooAllowance = {
    ...fixture.fooAllowance,
    state: "exhausted",
    canRunAgents: false,
  };
  h.documentEvents.visibilitychange();
  await settle();
  assert.match(h.elements.get("board").innerHTML, /data-layout="compact"/);
});
