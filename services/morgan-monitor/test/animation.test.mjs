import test from "node:test";
import assert from "node:assert/strict";
import { DIRECTIONS, capacityLayout, compactLayout, axialToPixel, distance } from "../public/lib/layout.js";
import { deriveMorganMode, scanDirections, renderSvg } from "../public/lib/render.js";

const base = () => ({
  generatedAt: "2026-11-09T06:00:00.000Z",
  summary: { executionCoverageComplete: false },
  sources: [], slots: [], conversationTopics: { voice: [], chat: [] },
});
const slot = (id, displayState = "blocked", extra = {}) => ({
  id, assignment: "assigned", availability: "fresh", proof: "process",
  displayState, topicKeys: ["engineering"], topics: ["Engineering"], ...extra,
});
const onRay = (cell, [q, r]) => {
  const steps = distance(cell.q, cell.r);
  return steps > 0 && cell.q === q * steps && cell.r === r * steps;
};
const cloud = DIRECTIONS.map((d) => capacityLayout().find((c) => c.group === "cloud" && onRay(c, d)));
const outer = DIRECTIONS.map((d) => capacityLayout()
  .filter((c) => c.group === "foo" && onRay(c, d))
  .sort((a, b) => distance(a.q, a.r) - distance(b.q, b.r))[0]);
const withSlots = (slots) => ({ ...base(), slots });
const complete = (state) => ({
  ...base(), summary: { executionCoverageComplete: true },
  sources: [{ status: "fresh", aliases: ["A", "B"], executionCoverageComplete: true }],
  slots: [slot("cloud-A", state), slot("cloud-B", state)],
});
const cellContent = (svg, id) => {
  const start = svg.indexOf(`data-content-cell="${id}"`);
  assert(start >= 0);
  const next = svg.indexOf('data-content-cell="', start + 1);
  return svg.slice(start, next < 0 ? undefined : next);
};

for (const layout of ["o2", "compact"]) {
  test(`${layout}: scans all six exact rays and points at their actual visible cells`, () => {
    for (const [direction, cell] of cloud.entries()) {
      const s = withSlots([slot(cell.id)]), rays = scanDirections(s, { layout });
      assert.equal(rays.length, 6);
      assert.deepEqual(rays.map((r) => [r.dq, r.dr]), DIRECTIONS);
      assert.deepEqual(rays.filter((r) => r.target).map((r) => r.direction), [direction]);
      assert.equal(rays[direction].target, cell.id);
      assert.deepEqual(deriveMorganMode(s, { layout }), { mode: "pointing", target: cell.id });
      const svg = renderSvg(s, { layout });
      assert.equal((svg.match(/data-scan-direction="/g) || []).length, 6);
      assert.match(svg, new RegExp(`scan-frame-0" data-scan-direction="${direction}" data-scan-target="${cell.id}" visibility="visible"`));
      const line = svg.match(new RegExp(`data-attention-target="${cell.id}"[^>]*><path d="M([^ ]+) ([^L]+)L([^ ]+) ([^"]+)"`));
      assert(line, "A visible ray has an attention line");
      const [x1, y1, x2, y2] = line.slice(1).map(Number), p = axialToPixel(cell.q, cell.r);
      for (const [x, y] of [[x1, y1], [x2, y2]]) {
        assert(Math.abs(x * p.y - y * p.x) < 1e-8, "Line is collinear with the target");
        assert(x * p.x + y * p.y > 0, "Line points toward, not away from, the target");
      }
      assert(Math.hypot(x2, y2) > Math.hypot(x1, y1));
      assert(Math.hypot(x2, y2) < Math.hypot(p.x, p.y));
    }
  });
  test(`${layout}: four distinct blocker directions panic without fire; one to three only point`, () => {
    for (let count = 1; count <= 6; count++) {
      const s = withSlots(cloud.slice(0, count).map((c, i) => slot(c.id, i % 2 ? "failed" : "blocked")));
      const mode = count > 3 ? "panicked" : "pointing";
      assert.equal(deriveMorganMode(s, { layout }).mode, mode);
      const svg = renderSvg(s, { layout });
      assert(svg.includes(`data-morgan-mode="${mode}"`));
      assert(!svg.includes('class="flames"'));
    }
  });
}

test("full layout picks nearest verified blocker per ray in stable direction order", () => {
  const s = withSlots([...outer, ...cloud].map((cell) => slot(cell.id)));
  assert.deepEqual(scanDirections(s).map((r) => r.target), cloud.map((c) => c.id));
  s.slots.reverse();
  assert.deepEqual(scanDirections(s).map((r) => r.target), cloud.map((c) => c.id));
  const cells = capacityLayout().reverse();
  assert.deepEqual(scanDirections(s, { cells }).map((r) => r.target), cloud.map((c) => c.id));
  s.slots.filter((s) => s.id.startsWith("cloud")).forEach((s) => s.availability = "stale");
  assert.deepEqual(scanDirections(s).map((r) => r.target), outer.map((c) => c.id));
});

test("multiple blockers along one ray count as one direction, not panic", () => {
  const cells = capacityLayout().filter((c) => ["cloud", "foo"].includes(c.group) && onRay(c, [1, 0]));
  assert(cells.length > 3);
  const s = withSlots(cells.map((c) => slot(c.id)));
  assert.equal(scanDirections(s).filter((r) => r.target).length, 1);
  assert.equal(deriveMorganMode(s).mode, "pointing");
});

test("off-ray, stale, missing, metadata-only, unassigned, and stopped cells never attract attention", () => {
  const offRay = capacityLayout().find((c) => c.group === "foo" && !DIRECTIONS.some((d) => onRay(c, d)));
  const variants = [
    slot(offRay.id), slot("not-a-visible-cell"),
    ...["stale", "outage", "unconfigured", "unavailable"].map((availability) => slot("cloud-A", "blocked", { availability })),
    ...["registry", "none", null].map((proof) => slot("cloud-A", "failed", { proof })),
    slot("cloud-A", "blocked", { assignment: "unassigned" }),
    slot("cloud-A", "blocked", { assignment: "unavailable" }),
    slot("cloud-A", "stopped"), slot("cloud-A", "idle"), slot("cloud-A", "running"),
  ];
  for (const invalid of variants) {
    const s = withSlots([invalid]);
    assert(scanDirections(s).every((r) => r.target === null), JSON.stringify(invalid));
    assert.equal(deriveMorganMode(s).mode, "neutral");
    assert(!renderSvg(s).includes('class="attention-line"'));
  }
});

test("compact and explicit visible layouts exclude hidden Foo from attention and selected looking", () => {
  const s = withSlots(outer.map((c) => slot(c.id)));
  assert.equal(deriveMorganMode(s).mode, "panicked");
  assert.equal(deriveMorganMode(s, { layout: "compact" }).mode, "neutral");
  assert.equal(deriveMorganMode(s, { cells: compactLayout(), selected: outer[0].id }).mode, "neutral");
  assert(!renderSvg(s, { layout: "compact" }).includes('class="attention-line"'));
  s.fooAllowance = { verified: true, availability: "fresh", state: "exhausted", canRunAgents: false, observedAt: s.generatedAt, staleAfterSeconds: 120 };
  assert.equal(deriveMorganMode(s).mode, "neutral");
});

test("neutral ambient scanning makes no activity or distress claim and reduced motion freezes a static pose", () => {
  const svg = renderSvg(base());
  assert(svg.includes('data-morgan-mode="neutral"'));
  assert.equal((svg.match(/data-scan-direction="/g) || []).length, 6);
  assert.equal((svg.match(/visibility="visible"/g) || []).length, 1);
  assert.equal((svg.match(/visibility="hidden"/g) || []).length, 5);
  assert(!svg.includes('class="attention-line"'));
  assert(!svg.includes('class="flames"'));
  for (let i = 0; i < 6; i++) assert(svg.includes(`@keyframes scan-${i}`));
  assert(svg.includes("@media(prefers-reduced-motion:reduce){.status-board *{animation:none!important;transition:none!important}}"));
  assert.equal(svg, renderSvg(base()), "Rendering the same snapshot is deterministic");
});

test("fire and pride retain complete fresh execution coverage gates regardless of visible layout", () => {
  for (const state of ["stopped", "running"]) {
    const expected = state === "stopped" ? "all-stopped" : "proud";
    assert.equal(deriveMorganMode(complete(state)).mode, expected);
    for (const change of [
      (s) => s.summary.executionCoverageComplete = false,
      (s) => s.sources = [],
      (s) => s.sources[0].status = "stale",
      (s) => s.sources[0].executionCoverageComplete = false,
      (s) => s.slots[1].availability = "stale",
      (s) => s.slots[1].proof = "registry",
      (s) => s.slots[1].displayState = "idle",
      (s) => s.slots[1].displayState = "blocked",
      (s) => s.slots[1].displayState = "failed",
      (s) => s.slots = [],
      (s) => s.slots.push(slot("foo-1", "idle")),
    ]) {
      const s = complete(state); change(s);
      for (const layout of ["o2", "compact"]) {
        assert.notEqual(deriveMorganMode(s, { layout }).mode, expected);
        assert(!renderSvg(s, { layout }).includes('class="flames"'));
      }
    }
  }
  assert(renderSvg(complete("stopped")).includes('class="flames"'));
  assert(!renderSvg(complete("running")).includes('class="flames"'));
});

test("idle cells show only their alias; active soft green and blocked red use a large separated cross", () => {
  const s = withSlots([
    slot("cloud-A", "idle"),
    slot("cloud-B", "running", { assignment: "unassigned" }),
    slot("cloud-C", "running"), slot("cloud-D", "blocked"),
  ]);
  const svg = renderSvg(s);
  for (const id of ["cloud-A", "cloud-B"]) {
    const content = cellContent(svg, id);
    assert(!content.includes("Engineering"));
    assert(!content.includes("<path"));
    assert.equal((content.match(/<text /g) || []).length, 1);
  }
  assert(!svg.includes(">Unassigned<"));
  assert.equal((svg.match(/fill="#e2f3e7"/g) || []).length, 1);
  assert.equal((svg.match(/fill="#ed6a7e"/g) || []).length, 1);
  const blocked = cellContent(svg, "cloud-D");
  assert(blocked.includes('class="blocked-cross"'));
  assert(blocked.includes('stroke-width="3.4000000000000004"'));
  assert(blocked.includes('class="label task-title"'));
  assert(blocked.includes('class="task-icon"'));
  assert(blocked.includes('class="environment-label"'));
  assert(blocked.includes("Engineering"));
});

test("static-compatible SVG shadows, smaller Morgan, and history text without visible headings", () => {
  const s = base();
  s.conversationTopics.chat = [{ text: "Latest subject", observedAt: s.generatedAt }];
  for (const layout of ["o2", "compact"]) {
    const svg = renderSvg(s, { layout });
    assert(svg.includes("<feGaussianBlur"));
    assert(svg.includes("<feOffset"));
    assert(svg.includes("<feColorMatrix"));
    assert(svg.includes('<feMergeNode in="SourceGraphic"/>'));
    assert(svg.includes('filter:url(#board-visual-shadow)'));
    const scale = Number(svg.match(/data-pose-target="[^"]*" transform="translate\([^)]*\) scale\(([^)]+)\)/)[1]);
    assert(Math.abs(scale - 0.16065) < 1e-12);
    assert(!svg.includes('<text class="topic-kind"'));
    const id = layout === "compact" ? "compact-history-56" : "history-1";
    const content = cellContent(svg, id);
    assert(content.includes(">Latest<") && content.includes(">subject<"));
    assert(!/>Status|>Voice|>Chat/.test(content));
    assert.equal((content.match(/<text /g) || []).length, 2); // two wrapped words, no heading
  }
});


test("verified work has top task text, centered icon, bottom environment, and separate small alias", () => {
  const s = withSlots([
    slot("cloud-A", "running", { project: "Never infer this project" }),
    slot("foo-39", "blocked"),
    slot("cloud-B", "running", { proof: "registry" }),
    slot("cloud-C", null),
  ]);
  const svg = renderSvg(s);
  assert(!svg.includes('class="morgan-name"'));
  assert(!svg.includes(">Morgan</text>"));
  assert(!svg.includes("Never infer this project"));
  for (const [id, environment] of [["cloud-A", "Cloud"], ["foo-39", "Foo"]]) {
    const content = cellContent(svg, id);
    const title = content.match(/class="label task-title" x="([^"]+)" y="([^"]+)"/);
    const alias = content.match(/class="cell-id" x="([^"]+)" y="([^"]+)"[^>]*font-size="([^"]+)"/);
    const env = content.match(/class="environment-label" x="([^"]+)" y="([^"]+)"/);
    assert(title && alias && env);
    assert(Number(title[2]) < -10);
    assert(Number(alias[1]) < -20);
    assert(Number(alias[3]) < 6);
    assert(Number(env[2]) > 20);
    assert(content.includes(`>${environment}</text>`));
    assert(content.includes('class="task-icon"'));
    assert(content.indexOf('class="task-title"') < 0); // compound class is intentional
  }
  for (const id of ["cloud-B", "cloud-C"]) {
    const content = cellContent(svg, id);
    assert.equal((content.match(/<text /g) || []).length, 1);
    assert(!content.includes('class="task-icon"'));
    assert(!content.includes('class="environment-label"'));
  }
});
