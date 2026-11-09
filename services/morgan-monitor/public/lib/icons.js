/** Fixed, deterministic SVG pictograms. No model calls, remote images or free markup. */
export const ICONS = Object.freeze({
  work: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 9h8M8 13h5"/>',
  monitoring: '<path d="M2 12h4l3-7 5 14 3-7h5"/>',
  cast: '<rect x="3" y="3" width="18" height="14" rx="2"/><path d="M12 17v4M8 21h8M5 10a4 4 0 0 1 4 4M5 7a7 7 0 0 1 7 7"/>',
  server:
    '<rect x="4" y="3" width="16" height="7" rx="2"/><rect x="4" y="14" width="16" height="7" rx="2"/><path d="M8 6h.01M8 17h.01M12 6h5M12 17h5"/>',
  engineering: '<path d="M8 6l-6 6 6 6M16 6l6 6-6 6M14 3l-4 18"/>',
  review: '<path d="M5 3h10l4 4v14H5zM15 3v5h4M8 14l3 3 5-6"/>',
  research: '<circle cx="10" cy="10" r="7"/><path d="M15 15l7 7"/>',
  planning:
    '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  testing: '<path d="M9 2h6M10 2v7L4 20h16L14 9V2M7 14h10"/>',
  delivery: '<path d="M3 6l9-4 9 4v13l-9 4-9-4zM3 6l9 4 9-4M12 10v13"/>',
  transition:
    '<rect x="2" y="3" width="7" height="10" rx="1"/><rect x="15" y="11" width="7" height="10" rx="1"/><path d="M11 7h8l-3-3M19 7l-3 3M13 17H5l3 3M5 17l3-3"/>',
  voice:
    '<rect x="8" y="2" width="8" height="13" rx="4"/><path d="M4 10v2a8 8 0 0 0 16 0v-2M12 20v3M8 23h8"/>',
  chat: '<path d="M3 3h18v14H9l-6 5z"/>',
  graphics:
    '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M7 7h4l2 4-2 4H7l-2-4zM14 17l5-5 2 2-5 5-3 1z"/>',
});
const MAP = Object.freeze({
  monitoring: "monitoring",
  monitoring_coordination: "monitoring",
  infrastructure: "server",
  research: "research",
  planning: "planning",
  engineering: "engineering",
  review: "review",
  testing: "testing",
  delivery: "delivery",
  status_display: "cast",
  status_updates: "monitoring",
  offmenu_sync: "transition",
});
export function iconFor(keys = []) {
  for (const key of keys) if (MAP[key]) return MAP[key];
  return "work";
}
export function iconSvg(name, x, y, size) {
  const shape = ICONS[name] || ICONS.work;
  return `<g transform="translate(${x} ${y}) scale(${size / 24})" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shape}</g>`;
}
