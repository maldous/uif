import test from "node:test";
import assert from "node:assert/strict";
import {
  capacityLayout,
  hexagon,
  layoutFor,
  axialToPixel,
  vertices,
  distance,
  boundaries,
  DIRECTIONS,
  CLOUD_RING,
  keyOf,
  historyFor,
  shouldCompact,
  O2_ROWS,
} from "../public/lib/layout.js";
import {
  renderSvg,
  deriveMorganMode,
  escapeXml,
} from "../public/lib/render.js";
import { iconFor, iconSvg } from "../public/lib/icons.js";
const base = () => ({
  schemaVersion: 1,
  generatedAt: "2026-11-09T06:00:00.000Z",
  revision: "a".repeat(64),
  summary: { executionCoverageComplete: false },
  sources: [],
  slots: [],
  conversationTopics: { voice: [], chat: [] },
});
const slot = (id = "cloud-A", state = "running", extra = {}) => ({
  id,
  domain: id.split("-")[0],
  alias: id.split("-")[1],
  assignment: "assigned",
  availability: "fresh",
  displayState: state,
  recordedState: state,
  proof: "process",
  topicKeys: ["engineering"],
  topics: ["Engineering"],
  blockers: [],
  ...extra,
});
const complete = () => ({
  ...base(),
  summary: { executionCoverageComplete: true },
  sources: [
    { status: "fresh", aliases: ["A"], executionCoverageComplete: true },
  ],
  slots: [slot()],
});
const connected = (cells) => {
  const allowed = new Set(cells.map((c) => keyOf(c.q, c.r))),
    seen = new Set(),
    queue = [cells[0]];
  while (queue.length) {
    const c = queue.shift(),
      key = keyOf(c.q, c.r);
    if (seen.has(key)) continue;
    seen.add(key);
    for (const [dq, dr] of DIRECTIONS) {
      const q = c.q + dq,
        r = c.r + dr;
      if (allowed.has(keyOf(q, r)) && !seen.has(keyOf(q, r)))
        queue.push({ q, r });
    }
  }
  return seen.size === cells.length;
};
test("approved O2 has 83 unique contiguous cells with correct capacity", () => {
  const cells = capacityLayout();
  assert.equal(cells.length, 83);
  assert.equal(new Set(cells.map((c) => keyOf(c.q, c.r))).size, 83);
  assert.equal(new Set(cells.map((c) => c.id)).size, 83);
  assert.deepEqual(O2_ROWS, [12, 11, 12, 13, 12, 11, 12]);
  assert.deepEqual(
    O2_ROWS.map((_, row) => cells.filter((c) => c.row === row).length),
    O2_ROWS,
  );
  assert(connected(cells));
  for (const [group, count] of Object.entries({
    foo: 64,
    cloud: 6,
    morgan: 1,
    history: 12,
  })) {
    const groupCells = cells.filter((c) => c.group === group);
    assert.equal(groupCells.length, count);
    assert(connected(groupCells), group + " connected");
  }
});
test("durable aliases always occupy the six immediate center neighbours", () => {
  const cells = capacityLayout();
  for (let i = 0; i < 6; i++) {
    const c = cells.find((c) => c.id === `cloud-${"ABCDEF"[i]}`);
    assert.deepEqual([c.q, c.r], CLOUD_RING[i]);
    assert.equal(distance(c.q, c.r), 1);
  }
  assert.deepEqual(
    cells
      .filter((c) => c.group === "foo")
      .map((c) => Number(c.alias))
      .sort((a, b) => a - b),
    Array.from({ length: 64 }, (_, i) => i + 1),
  );
});
test("O2 shape is symmetric and all twelve history cells are in the bottom row", () => {
  const cells = capacityLayout(),
    keys = new Set(cells.map((c) => keyOf(c.q, c.r)));
  for (const c of cells) assert(keys.has(keyOf(-c.q - c.r, c.r)));
  const history = cells.filter((c) => c.group === "history");
  assert.equal(history.length, 12);
  assert(history.every((c) => c.row === 6));
  assert(
    history.every(
      (c, i) =>
        i === 0 ||
        axialToPixel(c.q, c.r).x >
          axialToPixel(history[i - 1].q, history[i - 1].r).x,
    ),
  );
});
test("all cell sides are congruent and adjacent cells share exact edges without gaps", () => {
  const size = 34,
    origin = vertices(0, 0, size);
  for (let i = 0; i < 6; i++) {
    const a = origin[i],
      b = origin[(i + 1) % 6];
    assert(Math.abs(Math.hypot(a.x - b.x, a.y - b.y) - size) < 1e-10);
    const p = axialToPixel(...DIRECTIONS[i], size),
      near = vertices(p.x, p.y, size);
    assert(near.some((v) => Math.hypot(v.x - a.x, v.y - a.y) < 1e-10));
    assert(near.some((v) => Math.hypot(v.x - b.x, v.y - b.y) < 1e-10));
  }
});
test("group boundary segments are unique and polygon perimeter is exact", () => {
  const cells = capacityLayout(),
    edges = boundaries(cells);
  assert.equal(edges.outer.length, 74);
  const keys = [...edges.inner, ...edges.outer].map(({ a, b }) =>
    [
      `${a.x.toFixed(5)},${a.y.toFixed(5)}`,
      `${b.x.toFixed(5)},${b.y.toFixed(5)}`,
    ]
      .sort()
      .join("|"),
  );
  assert.equal(new Set(keys).size, keys.length);
});
test("focus keeps all six durable agents, only assigned Foo and actual conversation subjects", () => {
  const s = base();
  s.slots = [
    slot("foo-17"),
    slot("foo-2", "running", { assignment: "unassigned" }),
    slot("foo-3", "running", { assignment: "unavailable" }),
  ];
  s.conversationTopics.voice = [
    { text: "Monitoring coordination" },
    { text: "Status display" },
  ];
  s.conversationTopics.chat = [{ text: "Status updates" }];
  const cells = layoutFor(s, { layout: "focus" });
  assert.equal(cells.length, 11);
  assert(cells.some((c) => c.id === "foo-17"));
  assert(!cells.some((c) => c.id === "foo-2"));
  assert.equal(cells.filter((c) => c.group === "cloud").length, 6);
  assert.equal(layoutFor(s, { layout: "focus", selected: "foo-2" }).length, 12);
});
test("empty data never fabricates live work, topics, unassigned records, or emotional urgency", () => {
  const s = base(),
    svg = renderSvg(s);
  assert.equal((svg.match(/<polygon /g) || []).length, 83);
  assert(!svg.includes('fill="#e2f3e7"'));
  assert(!svg.includes('fill="#ed6a7e"'));
  assert(!svg.includes(">Unassigned<"));
  assert(!svg.includes("Monitoring coordination"));
  assert(!svg.includes('class="flames"'));
  assert.equal(deriveMorganMode(s).mode, "neutral");
  assert(!/unknown|not measured|Active badge/.test(svg));
});
test("current statuses are colored only when fresh, explicit unassigned stays neutral", () => {
  const s = base();
  s.slots = [
    slot(),
    slot("cloud-B", "blocked"),
    slot("cloud-C", "running", { availability: "stale" }),
    slot("cloud-D", "running", { availability: "outage" }),
    slot("cloud-E", "unknown", {
      assignment: "unassigned",
      displayState: null,
    }),
  ];
  const svg = renderSvg(s);
  assert.equal((svg.match(/fill="#e2f3e7"/g) || []).length, 1);
  assert.equal((svg.match(/fill="#ed6a7e"/g) || []).length, 1);
  assert(!svg.includes(">Unassigned<"));
  assert.equal(deriveMorganMode(s).target, "cloud-B");
});
test("pride requires complete fresh execution coverage and every assigned worker green", () => {
  const s = complete();
  assert.equal(deriveMorganMode(s).mode, "proud");
  for (const change of [
    (v) => (v.summary.executionCoverageComplete = false),
    (v) => (v.sources[0].status = "outage"),
    (v) => (v.sources[0].executionCoverageComplete = false),
    (v) => (v.slots[0].availability = "stale"),
    (v) => (v.slots[0].displayState = "idle"),
    (v) => (v.slots = []),
    (v) => (v.sources = []),
  ]) {
    const n = complete();
    change(n);
    assert.notEqual(deriveMorganMode(n).mode, "proud");
  }
});
test("fire requires explicit stopped process evidence for every assigned slot and complete coverage", () => {
  const s = complete();
  s.slots = [slot("cloud-A", "stopped"), slot("foo-1", "stopped")];
  assert.equal(deriveMorganMode(s).mode, "all-stopped");
  for (const change of [
    (v) => (v.slots[1].displayState = "blocked"),
    (v) => (v.slots[1].displayState = "failed"),
    (v) => (v.slots[1].displayState = "idle"),
    (v) => (v.slots[1].proof = "registry"),
    (v) => (v.slots[1].availability = "stale"),
    (v) => (v.summary.executionCoverageComplete = false),
  ]) {
    const n = structuredClone(s);
    change(n);
    assert.notEqual(deriveMorganMode(n).mode, "all-stopped");
  }
  assert(renderSvg(s).includes('class="flames"'));
});
test("only fresh verified blockers cause geometric pointing and stale blockers cannot", () => {
  const s = base();
  s.slots = [slot("cloud-A", "failed")];
  assert.deepEqual(deriveMorganMode(s), { mode: "pointing", target: "cloud-A" });
  assert(renderSvg(s).includes('data-pointing-target="cloud-A"'));
  s.slots[0].availability = "stale";
  assert.equal(deriveMorganMode(s).mode, "neutral");
});
test("personal thinking requires timestamped explicit evidence; selection is only looking", () => {
  const s = base();
  s.morganActivity = {
    verified: true,
    observedAt: s.generatedAt,
    mode: "thinking",
  };
  assert.equal(deriveMorganMode(s).mode, "thinking");
  s.morganActivity.observedAt = "2026-11-09T05:00:00.000Z";
  assert.equal(deriveMorganMode(s).mode, "neutral");
  s.morganActivity.observedAt = "2026-11-09T07:00:00.000Z";
  assert.equal(deriveMorganMode(s).mode, "neutral");
  delete s.morganActivity;
  s.slots = [slot()];
  assert.equal(deriveMorganMode(s, { selected: "cloud-A" }).mode, "looking");
});
test("SVG serializes exact revision and escapes all text/attribute input", () => {
  const s = base();
  s.revision = 'x"/><script>alert(1)</script>';
  s.slots = [
    slot("cloud-A", "running", { topics: ['<img onerror="alert(1)">'] }),
  ];
  s.conversationTopics.voice = [{ text: "<script>alert(1)</script>" }];
  const svg = renderSvg(s);
  assert(!svg.includes("<script>"));
  assert(!svg.includes("<img"));
  assert(svg.includes("&lt;script&gt;"));
  assert.equal(escapeXml("<&\"'"), "&lt;&amp;&quot;&apos;");
  assert(renderSvg(base()).includes('data-revision="' + "a".repeat(64) + '"'));
});
test("render dimensions are bounded and accessible interactive mode is keyboard selectable", () => {
  const svg = renderSvg(base(), { width: 1e10, height: -2, interactive: true });
  assert(svg.includes('width="4096" height="240"'));
  assert.equal((svg.match(/role="button" tabindex="0"/g) || []).length, 83);
  assert(svg.includes("prefers-reduced-motion:reduce"));
});
test("icon mapping is deterministic and unrecognized keys safely use generic work", () => {
  assert.equal(iconFor(["engineering"]), iconFor(["engineering"]));
  assert.equal(iconFor(["unreviewed-secret"]), iconFor([]));
  assert(!iconSvg("<script>", 0, 0, 20).includes("<script>"));
});

test("metadata-only evidence never creates live colors or expressive claims", () => {
  const s = complete();
  s.slots = [slot("cloud-A", "running", { proof: "registry" })];
  assert.equal(deriveMorganMode(s).mode, "neutral");
  assert(!renderSvg(s).includes('fill="#e2f3e7"'));
  s.slots[0].displayState = "blocked";
  assert.equal(deriveMorganMode(s).mode, "neutral");
  assert(!renderSvg(s).includes('fill="#ed6a7e"'));
});

const exhausted = () => ({
  ...base(),
  fooAllowance: {
    verified: true,
    state: "exhausted",
    canRunAgents: false,
    availability: "fresh",
    observedAt: "2026-11-09T06:00:00.000Z",
    staleAfterSeconds: 120,
  },
});
test("compact requires explicit fresh verified allowance exhaustion and preserves seven core identities", () => {
  const s = exhausted();
  assert(shouldCompact(s));
  const cells = layoutFor(s);
  assert.equal(cells.length, 10);
  assert.equal(cells.filter((c) => c.group === "cloud").length, 6);
  assert.equal(cells.filter((c) => c.group === "foo").length, 0);
  const history = cells.filter((c) => c.group === "history");
  assert.deepEqual(
    history.map((c) => c.historyLabel),
    ["56", "57", "58"],
  );
  assert.deepEqual(
    history.map((c) => [c.q, c.r]),
    [
      [-2, 2],
      [-1, 2],
      [0, 2],
    ],
  );
  assert(connected(cells));
  for (const change of [
    (v) => delete v.fooAllowance,
    (v) => (v.fooAllowance.verified = false),
    (v) => (v.fooAllowance.state = "valid"),
    (v) => (v.fooAllowance.canRunAgents = null),
    (v) => (v.fooAllowance.availability = "stale"),
    (v) => (v.fooAllowance.observedAt = "2026-11-09T05:00:00.000Z"),
    (v) => (v.fooAllowance.observedAt = "2026-11-09T06:00:01.000Z"),
  ]) {
    const n = exhausted();
    change(n);
    assert(!shouldCompact(n));
    assert.equal(layoutFor(n).length, 83);
  }
  assert(renderSvg(s).includes('data-layout="compact"'));
});
test("public history merges channels newest-left, shifts older subjects right, and caps at twelve", () => {
  const s = base();
  s.conversationTopics.voice = Array.from({ length: 8 }, (_, i) => ({
    text: "Voice" + i,
    observedAt: new Date(
      Date.parse(s.generatedAt) - (i * 2 + 2) * 1000,
    ).toISOString(),
  }));
  s.conversationTopics.chat = Array.from({ length: 8 }, (_, i) => ({
    text: "Chat" + i,
    observedAt: new Date(
      Date.parse(s.generatedAt) - (i * 2 + 3) * 1000,
    ).toISOString(),
  }));
  const before = historyFor(s);
  assert.equal(before.length, 12);
  assert.deepEqual(
    before.slice(0, 4).map((t) => t.text),
    ["Voice0", "Chat0", "Voice1", "Chat1"],
  );
  s.conversationTopics.chat.unshift({ text: "New", observedAt: s.generatedAt });
  const after = historyFor(s);
  assert.equal(after[0].text, "New");
  assert.deepEqual(
    after.slice(1).map((t) => t.text),
    before.slice(0, 11).map((t) => t.text),
  );
});
