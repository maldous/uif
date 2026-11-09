import test from "node:test";
import assert from "node:assert/strict";
import { DIRECTIONS, capacityLayout, compactLayout, axialToPixel, distance, boundaries } from "../public/lib/layout.js";
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
      const arm = svg.match(new RegExp(`class="morgan-arm scanning-arm" data-arm-side="([^"]+)" data-arm-angle="([^"]+)" data-arm-target="${cell.id}"`));
      assert(arm, "The articulated arm aims at the verified ray target");
      const p = axialToPixel(cell.q, cell.r), scale = 34 * 0.004725,
        shoulder = arm[1] === "left" ? -43 : 43,
        expected = Math.atan2((p.y + 34 * 0.08) / scale - 48, p.x / scale - shoulder) * 180 / Math.PI;
      assert(Math.abs(Number(arm[2]) - expected) < 1e-9);
      assert(svg.includes(`data-marker="blocked" data-marker-target="${cell.id}"`));
      assert(svg.includes('class="arm-forearm"'));
      assert(!svg.includes('class="attention-line"'));
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

test("idle cells show only their alias; active soft green and blocked red retain task icons without crosses", () => {
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
  assert(!svg.includes('class="blocked-cross"'));
  assert(blocked.includes('class="label task-title"'));
  assert(blocked.includes('class="task-icon"'));
  assert(!blocked.includes('class="environment-label"'));
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
    slot("foo-39", "blocked", { environmentKey: "foo_uif" }),
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
    assert(title && alias);
    assert(Number(title[2]) < -10);
    assert.equal(Number(alias[1]), 0);
    assert(Number(alias[2]) < -20);
    assert(Number(alias[2]) < Number(title[2]));
    assert(Number(alias[3]) < 6);
    if (environment === "Foo") {
      assert(env && Number(env[2]) > 20);
      assert(content.includes(">Foo · UIF</text>"));
    } else {
      assert.equal(env, null);
      assert(!content.includes(">Cloud</text>"));
    }
    assert(content.includes('class="task-icon"'));
  }
  for (const id of ["cloud-B", "cloud-C"]) {
    const content = cellContent(svg, id);
    assert.equal((content.match(/<text /g) || []).length, 1);
    assert(!content.includes('class="task-icon"'));
    assert(!content.includes('class="environment-label"'));
  }
});


test("every worker alias uses the same padded north interior in blank and occupied cells", () => {
  const s = withSlots([
    slot("cloud-A", "running"), slot("cloud-B", "idle"),
    slot("cloud-C", "blocked"), { ...slot("foo-39", "running"), environmentKey: "foo_uif" },
  ]);
  for (const layout of ["o2", "compact"]) {
    const svg = renderSvg(s, { layout });
    const aliases = [...svg.matchAll(/<text class="cell-id" x="([^"]+)" y="([^"]+)"[^>]*font-size="([^"]+)"/g)];
    assert.equal(aliases.length, layout === "compact" ? 6 : 70);
    for (const [, x, y, fontSize] of aliases) {
      assert.equal(Number(x), 0);
      assert(Math.abs(Number(y) + 23.12) < 1e-9);
      assert(Number(y) - Number(fontSize) > -34 + 4, "The alias has padding below the north vertex");
    }
    const occupied = cellContent(svg, "cloud-A");
    const alias = occupied.match(/class="cell-id"[^>]*y="([^"]+)"[^>]*font-size="([^"]+)"/);
    const title = occupied.match(/class="label task-title"[^>]*y="([^"]+)"[^>]*font-size="([^"]+)"/);
    assert(Number(title[1]) - Number(title[2]) > Number(alias[1]) + Number(alias[2]) * 0.3 + 1);
  }
});

test("hex shadows are visibly stronger while text keeps the lighter static-compatible filter", () => {
  const svg = renderSvg(base());
  const cellFilter = svg.match(/<filter id="board-cell-shadow"[\s\S]*?<\/filter>/)[0];
  assert(cellFilter.includes('stdDeviation="1.9"'));
  assert(cellFilter.includes('dy="2.7"'));
  assert(cellFilter.includes('.42 0"'));
  assert(svg.includes('.cell polygon{filter:url(#board-cell-shadow)}'));
  assert(svg.includes('.cell-content,.group-boundaries,.board-time{filter:url(#board-visual-shadow)}'));
});

test("blink uses a single fully closing aperture without flattening eye strokes or leaving a loop seam", () => {
  const svg = renderSvg(base());
  assert.equal((svg.match(/<clipPath id="board-eye-aperture"/g) || []).length, 1);
  assert.equal((svg.match(/class="eye-aperture"/g) || []).length, 1);
  assert.equal((svg.match(/class="eyes" clip-path="url\(#board-eye-aperture\)"/g) || []).length, 6);
  assert(svg.includes('.eye-aperture{transform-box:fill-box;transform-origin:50% 50%;animation:blink 7s ease-in-out infinite}'));
  assert(svg.includes('@keyframes blink{0%,44%,48%,100%{transform:scaleY(1)}46%{transform:scaleY(0)}}'));
  assert(!svg.includes('.eyes{transform'));
  assert(!svg.includes('scaleY(.12)'));
  for (let phase = 0; phase < 6; phase++) {
    const keyframes = svg.match(new RegExp(`@keyframes scan-${phase}\\{((?:[^{}]|\\{[^{}]*\\})*)\\}`))[1];
    assert(keyframes.startsWith(`0%{visibility:${phase === 0 ? "visible" : "hidden"}}`));
    assert(keyframes.endsWith(`100%{visibility:${phase === 0 ? "visible" : "hidden"}}`));
  }
});


test("coordinator lifecycle colors individual generation-bound cells without global execution claims", () => {
  const s = withSlots([slot("cloud-A", "running"), slot("cloud-B", "blocked"), slot("cloud-C", "completed")]);
  for (const item of s.slots) { item.proof = "coordinator_lifecycle"; item.domain = "cloud"; item.rosterGeneration = 1; }
  s.summary.executionCoverageComplete = true;
  const svg = renderSvg(s, { layout: "compact" });
  assert(svg.includes('fill="#e2f3e7"'));
  assert(svg.includes('fill="#ed6a7e"'));
  assert(svg.includes('Roster 1'));
  assert(!cellContent(svg, "cloud-C").includes('class="task-icon"'));
  assert.equal(deriveMorganMode(s, { layout: "compact" }).mode, "pointing");
  s.slots = s.slots.filter(item => item.id !== "cloud-B");
  assert.equal(deriveMorganMode(s, { layout: "compact" }).mode, "neutral");
  for (const item of s.slots) item.displayState = "stopped";
  assert.equal(deriveMorganMode(s, { layout: "compact" }).mode, "neutral");
  const stoppedSvg = renderSvg(s, { layout: "compact" });
  assert(!stoppedSvg.includes('fill="#ed6a7e"'));
  s.slots[0].displayState = "running"; delete s.slots[0].rosterGeneration;
  assert(!renderSvg(s, { layout: "compact" }).includes('fill="#e2f3e7"'));
});


test("clear ticks require confirmed evidence for every visible worker on the ray", () => {
  const c = cloud[0], d = DIRECTIONS[0];
  const s = withSlots([slot(c.id, "running")]);
  assert.equal(scanDirections(s, { layout: "compact" })[0].clear, true);
  assert.equal(scanDirections(s)[0].clear, false, "Absent Foo capacity cannot prove clear");
  assert(renderSvg(s, { layout: "compact" }).includes(`data-marker="clear" data-marker-target="${c.id}"`));
  const rayCells = capacityLayout().filter((cell) => ["cloud", "foo"].includes(cell.group) && onRay(cell, d));
  s.slots = rayCells.map((cell, index) => slot(cell.id, index ? null : "running", index ? { assignment: "unassigned" } : {}));
  assert.equal(scanDirections(s)[0].clear, true);
  for (const mutation of [
    (v) => v.availability = "stale",
    (v) => v.proof = "registry",
    (v) => { v.assignment = "assigned"; v.displayState = "unknown"; },
    (v) => { v.assignment = "assigned"; v.displayState = "stopped"; },
    (v) => { v.assignment = "assigned"; v.displayState = "blocked"; },
    (v) => v.assignment = "unavailable",
  ]) {
    const changed = structuredClone(s); mutation(changed.slots.at(-1));
    assert.equal(scanDirections(changed)[0].clear, false);
  }
  s.slots.forEach((v) => { v.assignment = "unassigned"; v.displayState = null; });
  assert.equal(scanDirections(s)[0].clear, false, "Empty confirmed capacity does not receive a tick");
});

test("coordinator clear and alert markers require current supported lifecycle claims", () => {
  const c = cloud[0];
  for (const state of ["running", "completed", "blocked", "unknown"]) {
    const s = withSlots([slot(c.id, state, { domain: "cloud", proof: "coordinator_lifecycle", rosterGeneration: 2 })]);
    const ray = scanDirections(s, { layout: "compact" })[0];
    assert.equal(ray.clear, state === "running");
    assert.equal(Boolean(ray.target), state === "blocked");
    for (const change of [(v) => v.availability = "stale", (v) => delete v.rosterGeneration, (v) => v.domain = "foo"]) {
      const changed = structuredClone(s); change(changed.slots[0]);
      const invalid = scanDirections(changed, { layout: "compact" })[0];
      assert.equal(invalid.clear, false);
      assert.equal(invalid.target, null);
    }
  }
});

test("A–F has a continuous outer-weight boundary including Morgan and the bottom status cells", () => {
  for (const layout of ["o2", "compact"]) {
    const svg = renderSvg(base(), { layout });
    const border = svg.match(/<path class="cloud-boundary" d="([^"]+)" stroke-width="([^"]+)"/);
    assert(border);
    assert.equal(Number(border[2]), 3.1);
    const cells = layout === "compact" ? compactLayout() : capacityLayout();
    const expected = boundaries(cells.filter((c) => c.group === "cloud")).outer;
    const path = expected.map((e) => `M${e.a.x.toFixed(4)} ${e.a.y.toFixed(4)}L${e.b.x.toFixed(4)} ${e.b.y.toFixed(4)}`).join("");
    assert.equal(border[1], path);
    assert.equal(expected.length, 24);
    assert.equal((svg.match(/<polygon /g) || []).length, layout === "compact" ? 10 : 83);
    assert(!svg.includes('class="attention-line"'));
    assert(!svg.includes('class="blocked-cross"'));
    assert(!svg.includes(">Cloud</text>"));
  }
});

test("arm articulation and alert blinking remain optional motion, with honest static markers", () => {
  const empty = renderSvg(base(), { layout: "compact" });
  assert(!empty.includes('data-marker="'));
  assert.equal((empty.match(/class="morgan-arm scanning-arm"/g) || []).length, 6);
  const blocked = renderSvg(withSlots([slot(cloud[0].id)]), { layout: "compact" });
  assert(blocked.includes('class="scan-marker blocked-marker"'));
  assert(blocked.includes('animation:arm-articulate 2.2s'));
  assert(blocked.includes('animation:marker-alert 1.5s'));
  assert(blocked.includes('@media(prefers-reduced-motion:reduce){.status-board *{animation:none!important;transition:none!important}}'));
  assert(!blocked.includes('class="flames"'));
});


test("work environment labels require an explicit reviewed key and never expose arbitrary strings", () => {
  const s = withSlots([{...slot("cloud-A", "running"), environmentKey:"foo_uif"}, {...slot("cloud-B", "running"), environmentKey:"cloud_scratch"}, {...slot("cloud-C", "running"), environmentKey:"/private/raw/path"}]);
  const svg = renderSvg(s, {layout:"compact"});
  assert(cellContent(svg,"cloud-A").includes(">Foo · UIF</text>"));
  assert(cellContent(svg,"cloud-B").includes(">Cloud scratch</text>"));
  assert(!cellContent(svg,"cloud-C").includes('class="environment-label"'));
  assert(!svg.includes("/private/raw/path"));
});
