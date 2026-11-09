import {
  layoutFor,
  axialToPixel,
  vertices,
  bounds,
  boundaries,
  historyFor,
  shouldCompact,
  DIRECTIONS,
  distance,
} from "./layout.js";
import { iconFor, iconSvg } from "./icons.js";
export const escapeXml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[c],
  );
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
// These keys prove a reviewed project identity, not merely an executor location.
const WORK_PROJECTS = Object.freeze({ foo_uif: "UIF" });
// Coordinator lifecycle is an explicit dated API/report observation, not an OS
// process probe. It may color individual cells, never global execution claims.
const currentClaim = (slot) => slot?.proof === "process" || (
  slot?.proof === "coordinator_lifecycle" && slot.domain === "cloud" &&
  Number.isSafeInteger(slot.rosterGeneration) && slot.rosterGeneration > 0 &&
  ["running", "completed", "blocked", "unknown"].includes(slot.displayState)
);
const short = (value, n = 42) => String(value ?? "").slice(0, n);
function lines(text, max = 13, count = 3) {
  const words = short(text, 100).split(/\s+/),
    out = [];
  let current = "";
  for (const word of words) {
    if (!word) continue;
    if ((current + " " + word).trim().length > max && current) {
      out.push(current);
      current = word;
    } else current = (current + " " + word).trim();
  }
  if (current) out.push(current);
  return out
    .slice(0, count)
    .map((line, i) =>
      i === count - 1 && out.length > count
        ? line.slice(0, max - 1) + "…"
        : line,
    );
}
function textLines(
  text,
  x,
  y,
  {
    size = 7,
    max = 13,
    count = 3,
    lineHeight = 9,
    anchor = "middle",
    className = "label",
  } = {},
) {
  return lines(text, max, count)
    .map(
      (line, i) =>
        `<text class="${className}" x="${x}" y="${y + i * lineHeight}" text-anchor="${anchor}" font-size="${size}">${escapeXml(line)}</text>`,
    )
    .join("");
}
export function executionComplete(snapshot) {
  const sources = snapshot.sources || [];
  return (
    snapshot.summary?.executionCoverageComplete === true &&
    sources.length > 0 &&
    sources.every((s) => s.status === "fresh") &&
    sources
      .filter((s) => s.aliases?.length > 0)
      .every((s) => s.executionCoverageComplete === true)
  );
}
/** One nearest verified blocker on each exact axial ray, in stable grid order. */
export function scanDirections(snapshot, { cells, layout = "o2", selected = null } = {}) {
  const visible = cells || layoutFor(snapshot, { layout, selected }),
    origin = visible.find((cell) => cell.group === "morgan") || { q: 0, r: 0 },
    slots = new Map((snapshot.slots || []).map((slot) => [slot.id, slot]));
  return DIRECTIONS.map(([dq, dr], direction) => {
    const rayCells = visible.filter((cell) => {
      const q = cell.q - origin.q, r = cell.r - origin.r, steps = distance(q, r);
      return ["cloud", "foo"].includes(cell.group) && Number.isInteger(steps) &&
        steps > 0 && q === dq * steps && r === dr * steps;
    }).sort((a, b) =>
      distance(a.q - origin.q, a.r - origin.r) -
        distance(b.q - origin.q, b.r - origin.r) ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
    const freshClaim = (slot) => slot?.availability === "fresh" && currentClaim(slot),
      nonblocking = (slot) => freshClaim(slot) && (
        (slot.assignment === "unassigned" &&
          (slot.proof === "process" || slot.displayState === "completed")) ||
        (slot.assignment === "assigned" && ["running", "idle", "completed"].includes(slot.displayState))
      ),
      candidates = rayCells.filter((cell) => {
        const slot = slots.get(cell.id);
        return freshClaim(slot) && slot.assignment === "assigned" &&
          ["blocked", "failed"].includes(slot.displayState);
      }),
      clearCell = rayCells.find((cell) => {
        const slot = slots.get(cell.id);
        return nonblocking(slot) && slot.assignment === "assigned" && ["running", "idle"].includes(slot.displayState);
      }) || null,
      clear = Boolean(clearCell && rayCells.every((cell) => nonblocking(slots.get(cell.id)))),
      empty = rayCells.length > 0 && rayCells.every((cell) => {
        const slot = slots.get(cell.id);
        return nonblocking(slot) && (slot.assignment === "unassigned" || slot.displayState === "completed");
      });
    const cell = candidates[0] || null;
    return {
      direction, dq, dr, cell,
      target: cell?.id || null,
      distance: cell ? distance(cell.q - origin.q, cell.r - origin.r) : null,
      clear, clearCell: clear ? clearCell : null,
      empty, emptyCell: empty ? rayCells[0] : null,
    };
  });
}
export function deriveMorganMode(snapshot, { selected = null, cells, layout = "o2" } = {}) {
  const assigned = (snapshot.slots || []).filter(
      (s) => s.assignment === "assigned",
    ),
    fresh = (s) => s.availability === "fresh" && s.proof === "process";
  const complete = executionComplete(snapshot),
    stopped =
      assigned.length > 0 &&
      complete &&
      assigned.every(
        (s) => fresh(s) && s.displayState === "stopped",
      );
  if (stopped) return { mode: "all-stopped", target: null };
  const visible = cells || layoutFor(snapshot, { layout, selected }),
    blockers = scanDirections(snapshot, { cells: visible }).filter((ray) => ray.target);
  if (blockers.length)
    return { mode: blockers.length > 3 ? "panicked" : "pointing", target: blockers[0].target };
  if (
    assigned.length > 0 &&
    complete &&
    assigned.every((s) => fresh(s) && s.displayState === "running")
  )
    return { mode: "proud", target: null };
  const own = snapshot.morganActivity;
  const at = Date.parse(own?.observedAt),
    now = Date.parse(snapshot.generatedAt);
  const canLookAt = (id) =>
    visible.some((cell) => cell.id === id) &&
    assigned.some((s) => s.id === id && fresh(s));
  if (
    own?.verified === true &&
    Number.isFinite(at) &&
    Number.isFinite(now) &&
    at <= now + 5000 &&
    now - at <= 120000
  ) {
    if (own.mode === "thinking") return { mode: "thinking", target: null };
    if (own.mode === "looking" && canLookAt(own.targetSlotId))
      return { mode: "looking", target: own.targetSlotId };
  }
  if (selected && canLookAt(selected))
    return { mode: "looking", target: selected };
  return { mode: "neutral", target: null };
}
function scanMarker(ray, size) {
  if (!ray.target && !ray.clear) return "";
  const blocked = Boolean(ray.target), target = ray.target || ray.clearCell.id;
  return `<g class="scan-marker ${blocked ? "blocked-marker" : "clear-marker"}" data-marker="${blocked ? "blocked" : "clear"}" data-marker-target="${escapeXml(target)}" transform="translate(0 ${-size * 0.77})" fill="none" stroke="${blocked ? "#b51f32" : "#24804c"}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${blocked ? '<path d="M0-3V1"/><circle cx="0" cy="4" r=".8" fill="#b51f32" stroke="none"/>' : '<path d="M-3 0l2.5 3L4-3"/>'}</g>`;
}
function solidArm(side, rotation, scanning, target) {
  const rest = Math.atan2(32, side === "left" ? -20 : 20) * 180 / Math.PI,
    angle = rotation + rest,
    shoulder = side === "left" ? -43 : 43;
  return `<g class="morgan-arm${scanning ? " scanning-arm" : ""}" data-arm-side="${side}" data-arm-angle="${angle}" data-arm-target="${escapeXml(scanning ? target || "" : "")}" transform="translate(${shoulder} 48) rotate(${angle})"><path class="arm-blob" d="M-7-5Q-10 0-7 5L39 8Q51 8 57 0Q51-8 39-8Z" fill="#88aaff" stroke="#102b9f" stroke-width="4" stroke-linejoin="round"/></g>`;
}
function mascotPose(state, target, size, direction = null) {
  let left = 0,
    right = 0,
    eyeX = 0,
    eyeY = 0,
    tilt = 0,
    pointingSide = null,
    mouth = "M-7 5H7",
    eyes = ["M-34-14h15", "M19-14h15"];
  const looking = target || (direction && { q: direction.dq, r: direction.dr });
  if (looking) {
    const p = axialToPixel(looking.q, looking.r, size),
      length = Math.hypot(p.x, p.y);
    eyeX = (p.x / length) * 5;
    eyeY = (p.y / length) * 3.5;
    tilt = (p.y / length) * 3;
  }
  if (state.mode === "panicked") {
    left = 145;
    right = -145;
    eyes = [
      "M-33-15a6 8 0 1 0 12 0a6 8 0 1 0-12 0",
      "M21-15a6 8 0 1 0 12 0a6 8 0 1 0-12 0",
    ];
    mouth = "M-6 5a6 8 0 1 0 12 0a6 8 0 1 0-12 0";
  }
  if (looking && (direction || (target && ["pointing", "panicked"].includes(state.mode)))) {
    const p = axialToPixel(looking.q, looking.r, size),
      scale = size * 0.004725,
      useLeft = p.x < 0,
      pivotX = useLeft ? -43 : 43,
      angle = Math.atan2((p.y + size * 0.08) / scale - 48, p.x / scale - pivotX) * 180 / Math.PI,
      rest = Math.atan2(32, useLeft ? -20 : 20) * 180 / Math.PI;
    pointingSide = useLeft ? "left" : "right";
    if (useLeft) left = angle - rest;
    else right = angle - rest;
    if (state.mode === "pointing") {
      eyes = ["M-35-12l16-7", "M19-19l16 7"];
      mouth = "M-14 11q14-18 28 0";
    }
  }
  if (state.mode === "friendly") {
    eyes = ["M-35-12q8-12 16 0", "M19-12q8-12 16 0"];
    mouth = "M-15 2q15 20 30 0";
  }
  if (state.mode === "resting") {
    eyes = ["M-35-14q8 5 16 0", "M19-14q8 5 16 0"];
    mouth = "M-5 4a5 7 0 1 0 10 0a5 7 0 1 0-10 0";
  }
  if (state.mode === "proud") {
    left = 105;
    right = -105;
    eyes = ["M-35-10q8-13 16 0", "M19-10q8-13 16 0"];
    mouth = "M-12 3q12 17 24 0";
  }
  if (state.mode === "thinking") {
    left = -160;
    tilt = -5;
    mouth = "M-3 6q6-2 9 1";
  }
  if (state.mode === "all-stopped") {
    left = 150;
    right = -150;
    eyes = [
      "M-33-15a6 7 0 1 0 12 0a6 7 0 1 0-12 0",
      "M21-15a6 7 0 1 0 12 0a6 7 0 1 0-12 0",
    ];
    mouth = "M-8 4a8 9 0 1 0 16 0a8 9 0 1 0-16 0";
  }
  const s = size * 0.004725,
    fire =
      state.mode === "all-stopped"
        ? '<g class="flames"><path d="M-44-85C-62-101-33-108-38-126c21 15 9 24 24 31 4-19 14-20 12-39 26 23 9 31 26 42 8-14 9-19 4-27 27 21 30 32 15 36Z" fill="#f56b24" stroke="#b51f32" stroke-width="3"/><path d="M-24-87c-10-12 5-20 4-29 15 12 3 18 17 25 3-13 14-17 13-24 15 19 10 25 5 30Z" fill="#ffd267"/></g>'
        : "";
  return `<g class="morgan-actor ${state.mode}" data-pose-mode="${state.mode}" data-pose-target="${escapeXml(target?.id || "")}" transform="translate(0 ${-size * 0.08}) scale(${s})"><title>Morgan: ${escapeXml(state.mode.replaceAll("-", " "))}</title><path d="M-37 74l-3 30c0 14 26 14 27 1l2-25M13 80l2 25c1 13 27 13 27-1l-4-30" fill="#739aff" stroke="#102b9f" stroke-width="5"/><path d="M-39 43h78l10 33c6 27-104 27-98 0z" fill="#789eff" stroke="#102b9f" stroke-width="5"/><path d="M-13 63l12 9-12 9M9 80h14" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"/><g transform="rotate(${tilt} 0 -12)"><g class="head-motion"><path d="M-67 41c-23-7-29-33-19-51-20-25-1-57 25-59 8-31 42-40 62-23 26-18 60-6 64 22 27 4 40 36 25 57 11 27-6 52-32 55-34 6-90 6-125-1z" fill="#8daaff" stroke="#102b9f" stroke-width="5"/><rect x="-57" y="-48" width="114" height="76" rx="23" fill="#092895"/><g fill="none" stroke="#76e2ff" stroke-width="5" stroke-linecap="round"><g transform="translate(${eyeX} ${eyeY})"><g class="eyes" clip-path="url(#board-eye-aperture)"><path d="${eyes[0]}"/><path d="${eyes[1]}"/></g></g><path class="reaction-mouth" d="${mouth}" stroke-width="3"/></g>${fire}</g></g>${solidArm("left", left, pointingSide === "left", target?.id)}${solidArm("right", right, pointingSide === "right", target?.id)}</g>`;
}
function mascot(snapshot, cells, size, selected) {
  const state = deriveMorganMode(snapshot, { selected, cells }),
    scans = scanDirections(snapshot, { cells }),
    first = Math.max(0, scans.findIndex((ray) => ray.target)),
    scanning = ["neutral", "pointing", "panicked"].includes(state.mode);
  let poses;
  if (scanning) {
    poses = scans.map((_, phase) => {
      const ray = scans[(phase + first) % scans.length],
        reaction = ray.target ? "blocked" : ray.clear ? "clear" : ray.empty ? "empty" : "neutral",
        mode = ray.target ? (state.mode === "panicked" ? "panicked" : "pointing") :
          ray.clear ? "friendly" : ray.empty ? "resting" : "neutral";
      return `<g class="scan-frame scan-frame-${phase}" data-scan-direction="${ray.direction}" data-scan-target="${escapeXml(ray.target || "")}" visibility="${phase === 0 ? "visible" : "hidden"}" data-direction-reaction="${reaction}">${mascotPose({ mode }, ray.cell || ray.clearCell || ray.emptyCell, size, ray)}${scanMarker(ray, size)}</g>`;
    }).join("");
  } else {
    poses = mascotPose(state, cells.find((cell) => cell.id === state.target), size);
  }
  return `<g class="morgan-scan" data-morgan-mode="${state.mode}" data-pointing-target="${escapeXml(state.target || "")}" data-scan-directions="${scanning ? 6 : 0}">${poses}</g>`;
}
// Every frozen SVG and reduced-motion view keeps phase zero: a real blocker,
// if present, otherwise a neutral ambient glance. CSS changes only the pose.
const scanAnimationCss = DIRECTIONS.map((_, phase) => {
  const from = phase * 100 / 6, to = (phase + 1) * 100 / 6;
  // Declare both loop endpoints; never rely on synthesized underlying values.
  const end = to < 100 ? `100%{visibility:${phase === 0 ? "visible" : "hidden"}}` : "";
  return `.scan-frame-${phase}{animation:scan-${phase} 18s steps(1,end) infinite}@keyframes scan-${phase}{${phase ? "0%{visibility:hidden}" : ""}${from}%{visibility:visible}${to}%{visibility:hidden}${end}}`;
}).join("");
const COLORS = {
  cloud: "#f5f9ff",
  foo: "#fff",
  history: "#edf5ff",
  morgan: "#fff",
};
export function renderSvg(snapshot = {}, options = {}) {
  const width = clamp(Number(options.width) || 1600, 320, 4096),
    height = clamp(Number(options.height) || 1000, 240, 4096),
    size = 34;
  const selected =
      typeof options.selected === "string" ? options.selected : null,
    layout = options.layout === "compact" || shouldCompact(snapshot)
      ? "compact"
      : options.layout === "focus"
        ? "focus"
        : "o2";
  const history = historyFor(snapshot);
  const cells = layoutFor(snapshot, { layout, selected }).map((cell) => ({
      ...cell,
      boundaryGroup:
        cell.group === "history"
          ? history[cell.topicIndex]?.channel || "history"
          : cell.group,
    })),
    box = bounds(cells, size),
    pad = 20,
    footer = 26,
    view = {
      x: box.minX - pad,
      y: box.minY - pad,
      width: box.maxX - box.minX + 2 * pad,
      height: box.maxY - box.minY + 2 * pad + footer,
    };
  const slots = new Map((snapshot.slots || []).map((s) => [s.id, s]));
  let fills = "",
    marks = "";
  for (const cell of cells) {
    const p = axialToPixel(cell.q, cell.r, size),
      slot = slots.get(cell.id),
      topic = cell.group === "history" ? history[cell.topicIndex] : null;
    const fresh = slot?.availability === "fresh",
      blocked =
        fresh &&
        currentClaim(slot) &&
        slot?.assignment === "assigned" &&
        ["blocked", "failed", "stopped"].includes(slot?.displayState),
      working =
        fresh &&
        currentClaim(slot) &&
        slot?.assignment === "assigned" &&
        slot?.displayState === "running";
    const fill = blocked ? "#ed6a7e" : working ? "#e2f3e7" : COLORS[cell.group];
    const polygon = vertices(p.x, p.y, size)
      .map((v) => `${v.x.toFixed(4)},${v.y.toFixed(4)}`)
      .join(" ");
    let label =
      cell.group === "morgan"
        ? "Morgan"
        : cell.group === "cloud"
          ? `Cloud agent ${cell.alias}`
          : cell.group === "foo"
            ? `Foo slot ${cell.alias}`
            : topic
              ? `${topic.channel === "voice" ? "Voice" : topic.channel === "chat" ? "Chat" : "Status"} subject: ${topic.text}`
              : `History ${cell.historyLabel}: topic capacity`;
    if (slot?.availability && slot.availability !== "fresh")
      label += `; ${slot.availability === "unconfigured" ? "source not configured" : slot.availability}`;
    if (slot?.displayState && fresh) label += `; ${slot.displayState}`;
    const attrs = options.interactive
      ? ` role="button" tabindex="0" aria-label="${escapeXml(label)}"`
      : "";
    fills += `<g class="cell ${cell.group}${selected === cell.id ? " selected" : ""}" data-cell-id="${escapeXml(cell.id)}" data-group="${cell.group}"${attrs}><title>${escapeXml(label)}</title><polygon points="${polygon}" fill="${fill}" stroke="#bfd0e6" stroke-width=".8"/></g>`;
    let content = "";
    if (cell.group === "morgan")
      content = mascot(snapshot, cells, size, selected);
    else if (cell.group === "cloud" || cell.group === "foo") {
      // Every worker alias sits at the padded north interior, above its task.
      content += `<text class="cell-id" x="0" y="${-size * 0.68}" text-anchor="middle" font-size="${size * 0.17}">${escapeXml(cell.alias)}</text>`;
      const hasWork = slot?.assignment === "assigned" && fresh &&
        currentClaim(slot) &&
        ["running", "blocked", "failed", "stopped"].includes(slot.displayState);
      if (hasWork) {
        content += textLines((slot.topics || [])[0] || "", 0, -size * 0.42, {
          size: size * 0.16,
          max: 14,
          count: 2,
          lineHeight: size * 0.19,
          className: "label task-title",
        });
        content += `<g class="task-icon">${iconSvg(
          iconFor(slot.topicKeys || []),
          -size * 0.22,
          -size * 0.10,
          size * 0.44,
        )}</g>`;
        const environment = Object.hasOwn(WORK_PROJECTS, slot.environmentKey) ? WORK_PROJECTS[slot.environmentKey] : null;
        if (environment)
          content += `<text class="environment-label" x="0" y="${size * 0.66}" text-anchor="middle" font-size="${size * 0.17}">${escapeXml(environment)}</text>`;
      }
    } else if (topic) {
      content += textLines(topic.text, 0, -size * 0.22, {
        size: size * 0.21,
        max: 13,
        count: 3,
        lineHeight: size * 0.25,
      });
    }
    marks += `<g class="cell-content${blocked ? " blocked" : working ? " working" : ""}" data-content-cell="${escapeXml(cell.id)}" transform="translate(${p.x} ${p.y})" pointer-events="none">${content}</g>`;
  }
  const edges = boundaries(cells, size),
    cloudEdges = boundaries(cells.filter((cell) => cell.group === "cloud"), size).outer,
    paths = (items) =>
      items
        .map(
          (e) =>
            `M${e.a.x.toFixed(4)} ${e.a.y.toFixed(4)}L${e.b.x.toFixed(4)} ${e.b.y.toFixed(4)}`,
        )
        .join("");
  const date = Date.parse(snapshot.generatedAt),
    time = Number.isFinite(date)
      ? new Date(date)
          .toISOString()
          .replace("T", " ")
          .replace(/\.\d+Z$/, " UTC")
      : "No observation time";
  const rosterGenerations = [...new Set((snapshot.slots || [])
    .filter((slot) => slot.proof === "coordinator_lifecycle" && Number.isSafeInteger(slot.rosterGeneration) && slot.rosterGeneration > 0)
    .map((slot) => slot.rosterGeneration))];
  const rosterLabel = rosterGenerations.length === 1 ? ` · Roster ${rosterGenerations[0]}` : "";
  const revision =
    typeof snapshot.revision === "string"
      ? snapshot.revision.slice(0, 12)
      : "unpublished";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${view.x} ${view.y} ${view.width} ${view.height}" class="status-board" data-layout="${layout}" data-revision="${escapeXml(typeof snapshot.revision === "string" ? snapshot.revision : "")}" role="${options.interactive ? "group" : "img"}" aria-labelledby="board-title board-desc"><title id="board-title">Morgan public platform status</title><desc id="board-desc">Six durable cloud agent slots immediately surround Morgan. The full O2 layout reserves sixty-four Foo slots and twelve bottom history cells, newest at the left. Compact mode is selected explicitly or by fresh verified Foo allowance exhaustion. Conversation subjects are curated public summaries. Colors assert only fresh verified observations; blank capacity is not running work.</desc><defs><filter id="board-visual-shadow" x="-30%" y="-30%" width="160%" height="160%" color-interpolation-filters="sRGB"><feGaussianBlur in="SourceAlpha" stdDeviation=".8"/><feOffset dx="0" dy="1.1"/><feColorMatrix type="matrix" values="0 0 0 0 .094 0 0 0 0 .204 0 0 0 0 .318 0 0 0 .24 0"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter><filter id="board-cell-shadow" x="-45%" y="-45%" width="190%" height="190%" color-interpolation-filters="sRGB"><feGaussianBlur in="SourceAlpha" stdDeviation="1.9"/><feOffset dx="0" dy="2.7"/><feColorMatrix type="matrix" values="0 0 0 0 .094 0 0 0 0 .204 0 0 0 0 .318 0 0 0 .42 0"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter><clipPath id="board-eye-aperture" clipPathUnits="userSpaceOnUse"><rect class="eye-aperture" x="-48" y="-29" width="96" height="30"/></clipPath></defs><style>.status-board{font-family:Arial,Helvetica,sans-serif;color:#0751b9;background:#fff}.label,.cell-id{fill:#123d78}.cell-id{font-weight:700}.environment-label{fill:#577398}.cell:focus polygon,.cell.selected polygon{stroke:#092895;stroke-width:2.8}.cell-content{color:#0751b9}.cell-content,.group-boundaries,.board-time{filter:url(#board-visual-shadow)}.cell polygon{filter:url(#board-cell-shadow)}.blocked .cell-id,.blocked .label,.blocked .environment-label{fill:#4f1228}.head-motion{transform-box:fill-box;transform-origin:50% 65%;animation:breathe 5s ease-in-out infinite}.eye-aperture{transform-box:fill-box;transform-origin:50% 50%;animation:blink 7s ease-in-out infinite}.proud .head-motion{animation:proud 2.7s ease-in-out infinite}.looking .head-motion{animation:attentive 3.4s ease-in-out infinite}.pointing .head-motion{animation:worried 1.9s ease-in-out infinite}.friendly .head-motion{animation:friendly-nod 2.4s ease-in-out infinite}.resting .head-motion{animation:resting-breathe 3.8s ease-in-out infinite}.friendly .reaction-mouth,.resting .reaction-mouth{transform-box:fill-box;transform-origin:50% 50%}.friendly .reaction-mouth{animation:smile 2.4s ease-in-out infinite}.resting .reaction-mouth{animation:yawn 3.8s ease-in-out infinite}.thinking .head-motion{animation:attentive 2.7s ease-in-out infinite}.panicked .head-motion{animation:panic .65s ease-in-out infinite}.blocked-marker{animation:marker-alert 1.5s steps(1,end) infinite}.flames{transform-box:fill-box;transform-origin:50% 100%;animation:flame 1.4s ease-in-out infinite}@keyframes worried{0%,100%{transform:rotate(-2deg)}50%{transform:rotate(2deg) translateY(-1.5px)}}@keyframes friendly-nod{50%{transform:translateY(-2.5px) rotate(1.5deg)}}@keyframes resting-breathe{50%{transform:translateY(1.5px)}}@keyframes smile{50%{transform:scale(1.12,1.15)}}@keyframes yawn{0%,100%{transform:scaleY(.7)}50%{transform:scaleY(1.15)}}@keyframes marker-alert{0%,55%,100%{opacity:1}70%,90%{opacity:0}}@keyframes breathe{50%{transform:translateY(-1.3px)}}@keyframes blink{0%,44%,48%,100%{transform:scaleY(1)}46%{transform:scaleY(0)}}@keyframes proud{50%{transform:translateY(-3px) rotate(1.5deg)}}@keyframes attentive{0%,100%{transform:rotate(-1deg)}50%{transform:rotate(1.2deg)}}@keyframes flame{50%{transform:scale(1.03,.90)}}@keyframes panic{25%{transform:rotate(-3deg) translateY(-2px)}75%{transform:rotate(3deg) translateY(-1px)}}${scanAnimationCss}@media(prefers-reduced-motion:reduce){.status-board *{animation:none!important;transition:none!important}}</style><rect x="${view.x}" y="${view.y}" width="${view.width}" height="${view.height}" fill="#fff"/>${fills}<g class="group-boundaries" fill="none" stroke="#183451" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"><path d="${paths(edges.inner)}" stroke-width="2.1"/><path d="${paths(edges.outer)}" stroke-width="3.1"/><path class="cloud-boundary" d="${paths(cloudEdges)}" stroke-width="3.1"/></g>${marks}<text class="board-time" x="0" y="${view.y + view.height - 6}" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-size="8" fill="#516b8a">${escapeXml(time)}${escapeXml(rosterLabel)} · ${escapeXml(revision)}</text></svg>`;
}
