/** O2 symmetric wide fold: exact pointy-top lattice; capacity is not liveness. */
export const DIRECTIONS = [
  [0, 1],
  [-1, 1],
  [-1, 0],
  [0, -1],
  [1, -1],
  [1, 0],
];
export const CLOUD_RING = [
  [0, -1],
  [1, -1],
  [-1, 0],
  [1, 0],
  [-1, 1],
  [0, 1],
];
export const O2_ROWS = [12, 11, 12, 13, 12, 11, 12];
export const keyOf = (q, r) => `${q},${r}`;
export const distance = (q, r) =>
  Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r));
export function axialToPixel(q, r, size = 34) {
  return { x: Math.sqrt(3) * size * (q + r / 2), y: 1.5 * size * r };
}
export function vertices(x, y, size = 34) {
  return Array.from({ length: 6 }, (_, i) => ({
    x: x + size * Math.cos(((30 + 60 * i) * Math.PI) / 180),
    y: y + size * Math.sin(((30 + 60 * i) * Math.PI) / 180),
  }));
}
export function hexagon(radius) {
  const out = [];
  for (let q = -radius; q <= radius; q++)
    for (
      let r = Math.max(-radius, -q - radius);
      r <= Math.min(radius, -q + radius);
      r++
    )
      out.push({ q, r });
  return out;
}
export function capacityLayout() {
  const cells = [];
  O2_ROWS.forEach((n, row) => {
    for (let col = 0; col < n; col++) {
      const r = row - 3,
        q = col - (n - 1) / 2 - r / 2;
      cells.push({ q, r, row, col });
    }
  });
  const byKey = new Map(cells.map((c) => [keyOf(c.q, c.r), c]));
  const assign = (q, r, group, id, extra = {}) =>
    Object.assign(byKey.get(keyOf(q, r)), { group, id, ...extra });
  assign(0, 0, "morgan", "morgan");
  CLOUD_RING.forEach(([q, r], i) =>
    assign(q, r, "cloud", `cloud-${String.fromCharCode(65 + i)}`, {
      alias: String.fromCharCode(65 + i),
    }),
  );
  cells
    .filter((c) => c.row === 6)
    .forEach((c, i) =>
      Object.assign(c, {
        group: "history",
        id: `history-${i + 1}`,
        topicIndex: i,
        historyLabel: String(i + 1),
      }),
    );
  cells
    .filter((c) => !c.group)
    .forEach((c, i) =>
      Object.assign(c, {
        group: "foo",
        id: `foo-${i + 1}`,
        alias: String(i + 1),
      }),
    );
  return cells;
}
export function historyFor(snapshot) {
  return ["status", "voice", "chat"]
    .flatMap((channel, channelOrder) =>
      (snapshot.conversationTopics?.[channel] || []).map((topic, index) => ({
        ...topic,
        channel,
        channelOrder,
        index,
      })),
    )
    .sort(
      (a, b) =>
        (Date.parse(b.observedAt) || 0) - (Date.parse(a.observedAt) || 0) ||
        a.channelOrder - b.channelOrder ||
        a.index - b.index,
    )
    .slice(0, 12);
}
function hasFreshAllowance(snapshot, state, canRunAgents) {
  const a = snapshot.fooAllowance,
    observed = Date.parse(a?.observedAt),
    now = Date.parse(snapshot.generatedAt),
    ttl = a?.staleAfterSeconds;
  return (
    a?.verified === true &&
    a.availability === "fresh" &&
    a.state === state &&
    a.canRunAgents === canRunAgents &&
    Number.isFinite(observed) &&
    Number.isFinite(now) &&
    Number.isFinite(ttl) &&
    ttl > 0 &&
    observed <= now &&
    now - observed < ttl * 1000
  );
}
export const shouldCompact = (snapshot) =>
  hasFreshAllowance(snapshot, "exhausted", false);
export const shouldRestoreFull = (snapshot) =>
  hasFreshAllowance(snapshot, "valid", true);
export function compactLayout() {
  return capacityLayout()
    .filter((c) => ["morgan", "cloud"].includes(c.group))
    .concat(
      [56, 57, 58].map((label, i) => ({
        q: i - 2,
        r: 2,
        group: "history",
        id: `compact-history-${label}`,
        topicIndex: i,
        historyLabel: String(label),
      })),
    );
}
export function layoutFor(snapshot, { layout = "o2", selected = null } = {}) {
  if (layout === "compact" || shouldCompact(snapshot)) return compactLayout();
  const all = capacityLayout();
  if (layout !== "focus") return all;
  const slots = new Map((snapshot.slots || []).map((s) => [s.id, s])),
    history = historyFor(snapshot);
  return all.filter(
    (c) =>
      c.group === "cloud" ||
      c.group === "morgan" ||
      c.id === selected ||
      (c.group === "foo" && slots.get(c.id)?.assignment === "assigned") ||
      (c.group === "history" && history.length > c.topicIndex),
  );
}
export function boundaries(cells, size = 34) {
  const byKey = new Map(cells.map((c) => [keyOf(c.q, c.r), c])),
    seen = new Set(),
    inner = [],
    outer = [];
  for (const cell of cells) {
    const { x, y } = axialToPixel(cell.q, cell.r, size),
      v = vertices(x, y, size);
    DIRECTIONS.forEach(([dq, dr], edge) => {
      const neighbour = byKey.get(keyOf(cell.q + dq, cell.r + dr));
      if (
        neighbour &&
        (neighbour.boundaryGroup || neighbour.group) ===
          (cell.boundaryGroup || cell.group)
      )
        return;
      const a = v[edge],
        b = v[(edge + 1) % 6],
        key = [
          `${a.x.toFixed(5)},${a.y.toFixed(5)}`,
          `${b.x.toFixed(5)},${b.y.toFixed(5)}`,
        ]
          .sort()
          .join("|");
      if (seen.has(key)) return;
      seen.add(key);
      (neighbour ? inner : outer).push({ a, b });
    });
  }
  return { inner, outer };
}
export function bounds(cells, size = 34) {
  const points = cells.flatMap((c) => {
    const p = axialToPixel(c.q, c.r, size);
    return vertices(p.x, p.y, size);
  });
  return {
    minX: Math.min(...points.map((p) => p.x)),
    maxX: Math.max(...points.map((p) => p.x)),
    minY: Math.min(...points.map((p) => p.y)),
    maxY: Math.max(...points.map((p) => p.y)),
  };
}
