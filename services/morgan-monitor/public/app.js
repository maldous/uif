import { renderSvg, escapeXml } from "./lib/render.js";
import {
  layoutFor,
  axialToPixel,
  historyFor,
  shouldCompact,
  shouldRestoreFull,
} from "./lib/layout.js";
const $ = (id) => document.getElementById(id),
  params = new URLSearchParams(location.search);
let polling = false,
  activeController = null,
  requestGeneration = 0;
let snapshot = {
    schemaVersion: 1,
    slots: [],
    sources: [],
    conversationTopics: { voice: [], chat: [] },
  },
  selected = null,
  layout =
    params.get("layout") === "focus"
      ? "focus"
      : ["o2", "hexagon"].includes(params.get("layout"))
        ? "o2"
        : "compact",
  renderedLayout = null,
  zoom = 1,
  pan = { x: 0, y: 0 },
  baseBox = null,
  drag = null,
  pollTimer = null,
  stopped = false,
  lastRevision = null,
  failures = 0;
const stateText = {
  running: "Working",
  blocked: "Blocked",
  failed: "Failed",
  stopped: "Stopped",
  completed: "Completed",
  idle: "Idle",
  fresh: "Fresh observation",
  stale: "Observation expired",
  outage: "Source contact lost",
  unconfigured: "Source not configured",
  unavailable: "Evidence unavailable",
  error: "Source error",
};
const time = (value) => {
  const t = Date.parse(value);
  return Number.isFinite(t)
    ? new Date(t).toLocaleString(undefined, {
        timeZone: "UTC",
        hour12: false,
      }) + " UTC"
    : "Not provided";
};
function safe(value) {
  return escapeXml(value);
}
function pair(name, value) {
  return `<dt>${safe(name)}</dt><dd>${safe(value)}</dd>`;
}
function overview() {
  let html =
    '<p>Six durable cloud slots always surround Morgan. O2 reserves exactly 64 Foo slots and 12 public history cells across the bottom. Newest subjects start at the left; older subjects shift right.</p><p class="hint">Capacity is not a live-worker count. Select a cell to inspect public evidence. Drag to pan, or use the zoom controls.</p><h2>Source coverage</h2>';
  if (!snapshot.sources?.length)
    html += "<p>No monitoring sources are configured.</p>";
  for (const source of snapshot.sources || []) {
    html += `<div class="source-row"><strong>${safe(source.domain === "cloud" ? "Cloud source" : "Foo source")}</strong><span class="source-state">${safe(stateText[source.status] || "Evidence unavailable")}</span><br>Last contact: ${safe(time(source.lastReceivedAt))}<br>${source.executionCoverageComplete ? "Complete execution coverage" : "Execution coverage incomplete"}${source.serviceState && source.serviceState !== "unknown" ? "<br>Recorded service/process: " + safe(source.serviceState) + "<br>Observed: " + safe(time(source.lastObservedAt)) : ""}</div>`;
  }
  html +=
    "<h2>Public snapshot</h2><dl>" +
    pair("Captured", time(snapshot.generatedAt)) +
    pair(
      "Revision",
      snapshot.revision ? snapshot.revision.slice(0, 16) : "Not published",
    ) +
    "</dl>";
  return html;
}
function inspect() {
  const cell = layoutFor(snapshot, { layout: "o2" }).find(
    (c) => c.id === selected,
  );
  $("clear-selection").hidden = !cell;
  if (!cell) {
    $("detail-title").textContent = "Platform overview";
    $("detail-body").innerHTML = overview();
    return;
  }
  if (cell.group === "morgan") {
    $("detail-title").textContent = "Morgan";
    $("detail-body").innerHTML =
      "<p>Morgan’s expression is driven by verified public state. A fresh blocker can be pointed out. Pride requires complete fresh green execution coverage; fire requires every assigned worker to be explicitly verified stopped.</p><p>Unavailable sources never stand in for a stopped or healthy worker.</p>";
    return;
  }
  if (cell.group === "history") {
    const topic = historyFor(snapshot)[cell.topicIndex];
    $("detail-title").textContent =
      (topic?.channel === "voice"
        ? "Voice"
        : topic?.channel === "chat"
          ? "Chat"
          : "History") + " subject";
    $("detail-body").innerHTML = topic
      ? `<p>${safe(topic.text)}</p><dl>${pair("Curated observation", time(topic.observedAt))}${pair("Order", `History ${cell.historyLabel}; newest at the left`)}</dl>`
      : "<p>Reserved topic capacity. No subject is populated here.</p>";
    return;
  }
  const slot = snapshot.slots?.find((s) => s.id === cell.id);
  $("detail-title").textContent =
    (cell.group === "cloud" ? "Cloud agent " : "Foo slot ") + cell.alias;
  let html =
    "<dl>" +
    pair(
      "Assignment",
      slot?.assignment === "assigned"
        ? "Assigned"
        : slot?.assignment === "unassigned"
          ? "Unassigned capacity"
          : "Assignment evidence unavailable",
    ) +
    pair(
      "Observation",
      stateText[slot?.availability] || "Evidence unavailable",
    );
  if (slot?.availability === "fresh" && slot.displayState)
    html += pair(
      "Verified state",
      stateText[slot.displayState] || slot.displayState,
    );
  if (slot?.observedAt) html += pair("Observed at", time(slot.observedAt));
  if (slot?.proof)
    html += pair(
      "Evidence type",
      {
        process: "Process liveness",
        coordinator_lifecycle: "Coordinator observation (manual)",
        registry: "Historical registry metadata",
        turn_history: "Historical turn metadata",
      }[slot.proof] || "Public metadata",
    );
  if (Number.isSafeInteger(slot?.rosterGeneration))
    html += pair("Display roster", `Generation ${slot.rosterGeneration}; not a historical identity claim`);
  html += "</dl>";
  if (slot?.topics?.length)
    html +=
      "<h2>Public subjects</h2><ul>" +
      slot.topics.map((t) => "<li>" + safe(t) + "</li>").join("") +
      "</ul>";
  if (slot?.blockers?.length)
    html +=
      '<h2 class="state-blocked">Blockage details</h2><ul>' +
      slot.blockers.map((t) => "<li>" + safe(t) + "</li>").join("") +
      "</ul>";
  if (
    slot?.recordedState &&
    slot.recordedState !== "unknown" &&
    slot.availability !== "fresh"
  )
    html +=
      '<p class="hint">Historical record: ' +
      safe(slot.recordedState) +
      ". This is not a current execution claim.</p>";
  $("detail-body").innerHTML = html;
}
function applyView() {
  const svg = $("board").querySelector("svg");
  if (!svg || !baseBox) return;
  const w = baseBox.width / zoom,
    h = baseBox.height / zoom;
  svg.setAttribute(
    "viewBox",
    `${baseBox.x + (baseBox.width - w) / 2 + pan.x} ${baseBox.y + (baseBox.height - h) / 2 + pan.y} ${w} ${h}`,
  );
  $("zoom-label").textContent = Math.round(zoom * 100) + "%";
}
function draw({ fit = false } = {}) {
  if (shouldCompact(snapshot)) layout = "compact";
  else if (layout === "compact" && shouldRestoreFull(snapshot)) layout = "o2";
  const old = baseBox,
    compact = layout === "compact" || shouldCompact(snapshot),
    mode = compact ? "compact" : layout;
  if (renderedLayout !== mode) fit = true;
  renderedLayout = mode;
  if (
    selected &&
    !layoutFor(snapshot, { layout, selected }).some(
      (cell) => cell.id === selected,
    )
  )
    selected = null;
  $("layout-full").disabled = compact;
  $("layout-focus").disabled = compact;
  $("layout-mode").textContent = compact
    ? shouldCompact(snapshot)
      ? "Compact: verified Foo allowance exhausted"
      : "Compact view: MCP mode"
    : "";
  $("board").innerHTML = renderSvg(snapshot, {
    width: 1600,
    height: 1000,
    layout,
    selected,
    interactive: true,
  });
  const v = $("board").querySelector("svg").viewBox.baseVal;
  baseBox = { x: v.x, y: v.y, width: v.width, height: v.height };
  if (fit || !old) {
    zoom = 1;
    pan = { x: 0, y: 0 };
  }
  applyView();
  inspect();
  $("revision").textContent = snapshot.revision
    ? "Revision " + snapshot.revision.slice(0, 12)
    : "";
  $("layout-full").setAttribute(
    "aria-pressed",
    String(!compact && layout === "o2"),
  );
  $("layout-focus").setAttribute(
    "aria-pressed",
    String(!compact && layout === "focus"),
  );
}
function choose(id, { focus = false } = {}) {
  selected = id;
  draw();
  if (focus)
    $("board")
      .querySelector('[data-cell-id="' + CSS.escape(id) + '"]')
      ?.focus();
}
function zoomBy(multiplier) {
  zoom = Math.max(1, Math.min(8, zoom * multiplier));
  if (zoom === 1) pan = { x: 0, y: 0 };
  applyView();
}
$("board").addEventListener("click", (event) => {
  if (drag?.moved) return;
  const node = event.target.closest?.("[data-cell-id]");
  if (node) choose(node.dataset.cellId);
});
$("board").addEventListener("keydown", (event) => {
  if (["Enter", " "].includes(event.key)) {
    const node = event.target.closest?.("[data-cell-id]");
    if (node) {
      event.preventDefault();
      choose(node.dataset.cellId, { focus: true });
    }
  }
});
$("stage").addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.14 : 1 / 1.14);
  },
  { passive: false },
);
$("stage").addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  drag = {
    x: event.clientX,
    y: event.clientY,
    pointerId: event.pointerId,
    pan: { ...pan },
    moved: false,
  };
});
$("stage").addEventListener("pointermove", (event) => {
  if (!drag || !baseBox) return;
  const dx = event.clientX - drag.x,
    dy = event.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) > 4) {
    drag.moved = true;
    $("stage").setPointerCapture(event.pointerId);
    $("stage").classList.add("dragging");
  }
  if (!drag.moved) return;
  const rect = $("stage").getBoundingClientRect(),
    ratio = Math.max(
      baseBox.width / (zoom * rect.width),
      baseBox.height / (zoom * rect.height),
    );
  pan = { x: drag.pan.x - dx * ratio, y: drag.pan.y - dy * ratio };
  applyView();
});
$("stage").addEventListener("pointerup", () => {
  $("stage").classList.remove("dragging");
  setTimeout(() => {
    drag = null;
  }, 0);
});
$("stage").addEventListener("pointercancel", () => {
  drag = null;
  $("stage").classList.remove("dragging");
});
$("zoom-in").onclick = () => zoomBy(1.4);
$("zoom-out").onclick = () => zoomBy(1 / 1.4);
$("fit").onclick = () => {
  zoom = 1;
  pan = { x: 0, y: 0 };
  applyView();
};
$("layout-full").onclick = () => {
  layout = "o2";
  draw({ fit: true });
};
$("layout-focus").onclick = () => {
  layout = "focus";
  draw({ fit: true });
};
$("clear-selection").onclick = () => {
  selected = null;
  draw();
};
$("download").onclick = () => {
  const blob = new Blob(
      [renderSvg(snapshot, { layout, selected, width: 1920, height: 1200 })],
      { type: "image/svg+xml" },
    ),
    url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download =
    "morgan-" + (snapshot.revision?.slice(0, 12) || "unpublished") + ".svg";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
function announceChanges(old, next) {
  if (!old?.revision) return;
  const previous = new Map(old.slots.map((s) => [s.id, s]));
  const changes = next.slots.filter((s) => {
    const before = previous.get(s.id);
    return (
      before &&
      (s.displayState !== before.displayState ||
        s.availability !== before.availability)
    );
  });
  if (changes.length)
    $("announcement").textContent =
      changes
        .slice(0, 3)
        .map(
          (s) =>
            (s.domain === "cloud" ? "Cloud " : "Foo ") +
            s.alias +
            ": " +
            (s.availability === "fresh"
              ? stateText[s.displayState] || "Fresh observation"
              : stateText[s.availability] || "Evidence unavailable"),
        )
        .join(". ") +
      (changes.length > 3 ? `. ${changes.length - 3} more changed.` : "");
}
function withholdCurrentClaims() {
  lastRevision = null;
  snapshot = {
    ...snapshot,
    revision: undefined,
    summary: { ...snapshot.summary, executionCoverageComplete: false },
    fooAllowance: {
      ...snapshot.fooAllowance,
      verified: false,
      availability: "unavailable",
      state: "unavailable",
      canRunAgents: null,
    },
    sources: (snapshot.sources || []).map((source) => ({
      ...source,
      status: source.status === "fresh" ? "unavailable" : source.status,
      executionCoverageComplete: false,
    })),
    slots: (snapshot.slots || []).map((slot) =>
      slot.availability === "fresh"
        ? {
            ...slot,
            availability: "unavailable",
            recordedState: slot.recordedState || slot.displayState,
            displayState: null,
          }
        : slot,
    ),
  };
  draw();
}
async function poll() {
  if (stopped || polling) return;
  polling = true;
  const generation = ++requestGeneration,
    controller = new AbortController();
  activeController = controller;
  const timeout = setTimeout(() => controller.abort(), 7000);
  try {
    const response = await fetch("/api/status", {
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    });
    if (!response.ok) throw Error("Feed unavailable");
    const current = await response.json();
    if (stopped || generation !== requestGeneration) return;
    if (
      current.schemaVersion !== 1 ||
      !Array.isArray(current.slots) ||
      !Array.isArray(current.sources)
    )
      throw Error("Invalid public projection");
    if (current.revision !== lastRevision) {
      let next = current;
      if (current.revision && /^[0-9a-f]{64}$/.test(current.revision)) {
        const frozen = await fetch("/api/snapshots/" + current.revision, {
          credentials: "omit",
          signal: controller.signal,
        });
        if (!frozen.ok) throw Error("Snapshot unavailable");
        next = await frozen.json();
      }
      if (stopped || generation !== requestGeneration) return;
      announceChanges(snapshot, next);
      snapshot = next;
      lastRevision = current.revision;
      draw();
    }
    failures = 0;
    $("connection").classList.remove("error");
    const unavailable = current.sources.filter(
      (s) => s.status !== "fresh",
    ).length;
    $("connection").textContent = !current.sources.length
      ? "No sources configured. The board shows capacity, not live workers."
      : unavailable
        ? `${unavailable} monitoring source${unavailable === 1 ? " is" : "s are"} unavailable or stale. Current claims are withheld.`
        : current.summary?.executionCoverageComplete
          ? "Fresh source observations with complete execution coverage."
          : "Sources reachable. Execution coverage remains incomplete.";
    $("feed-time").textContent =
      "Feed checked " +
      new Date().toLocaleTimeString(undefined, { hour12: false }) +
      " local";
  } catch {
    if (stopped || generation !== requestGeneration) return;
    failures++;
    withholdCurrentClaims();
    $("connection").textContent =
      "Status service could not be reached. Showing the last captured snapshot, not a current-state claim.";
    $("connection").classList.add("error");
  } finally {
    clearTimeout(timeout);
    if (generation === requestGeneration) {
      polling = false;
      activeController = null;
      if (!stopped)
        pollTimer = setTimeout(
          poll,
          Math.min(
            60000,
            (document.hidden ? 30000 : 5000) * 2 ** Math.min(failures, 3),
          ),
        );
    }
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !stopped) {
    clearTimeout(pollTimer);
    poll();
  }
});
window.addEventListener("pagehide", () => {
  stopped = true;
  requestGeneration++;
  activeController?.abort();
  activeController = null;
  polling = false;
  clearTimeout(pollTimer);
  withholdCurrentClaims();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    stopped = false;
    failures = 0;
    clearTimeout(pollTimer);
    withholdCurrentClaims();
    $("connection").textContent =
      "Rechecking current observations after returning to this page…";
    poll();
  }
});
draw({ fit: true });
poll();
