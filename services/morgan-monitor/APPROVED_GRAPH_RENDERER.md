# Approved graphical monitor renderer

The renderer is a direct JavaScript port of the reviewed railway-v36 geometry and effects. It imports geometry and approved local artwork, never synthetic status fixtures.

## Files

- `public/lib/approved-graph.js`: deterministic browser/server renderer and evidence model.
- `public/lib/approved-geometry.js`: exact geometry, stripped of obsolete counts, historical labels, fixture values and source metadata.
- `public/lib/approved-assets.js`: reviewed PNG brand/task artwork, Roboto TTF fonts and measured regular-font advances.
- `scripts/generate-approved-graph-assets.py`: reproducible one-time import from the reviewed reference directory. Not a runtime dependency.
- `scripts/approved-graph-provenance.json`: source SHA-256 hashes.
- `test/approved-graph.test.mjs`: focused evidence, geometry, capture, rendering and escaping tests.

## API

`renderApprovedGraph(snapshot, options)` returns complete SVG markup. `graphLayout(snapshot, options)` returns the matching geometry and inspection model.

Options:

- `mode`: `full` (default) or `compact`. `layout` is accepted as an alias.
- `theme`: `light` (default) or `dark`.
- `now`: epoch milliseconds or ISO timestamp. Defaults to the supplied snapshot's `generatedAt` for deterministic immutable rendering. An interactive browser should pass its current time to expire evidence.
- `width`, `height`: optional positive output dimensions. The fixed viewBox and top-Cloud orientation remain unchanged.
- `phase`: 0 or 1, selecting the synchronized shared railway starting phase.
- `reducedMotion`: steady alerts and no traveling highlights. System reduced-motion settings are also respected.
- `selected`: public topology cell ID to highlight.
- `interactive`: false to omit keyboard button roles; defaults true.

The model includes `width`, `height`, `graphHeight`, `cells`, `groups`, `projects`, `durables`, `summaries`, `overall`, `observations`, `capturedAt`, `projectionAt`, `manual`, `expired`, and `footerLines`.

Every cell has `id`, `kind`, `x`, `y`, `r`, `vertices`, `status`, and `slotId` (null without verified binding). Project cells include `project` and primary/overflow cells have `index` 1–6. Shared railway cells have `projects`. Cell targets use `data-cell-id`; SVG root exposes `data-layout`, `data-theme`, `data-revision`, and `data-geometry`.

`capturedAt` is the earliest applicable manual source observation when a manual source exists. It never advances merely because the API is polled or allowance data changes. `projectionAt` separately records receiver projection generation. Per-subject observation timestamps remain visible verbatim.

## Geometry and design

- Full: 91 cells, eight outlined groups. Six project groups each contain six primary workers, six overflow workers and one summary. The center contains six durable project nodes, six shared railway lights and one overall summary.
- Compact: the same 13 central cells, unchanged geometry and typography; no outer groups or history.
- Cloud is at the top, clockwise through UIF, ANQR, CIX, OM, CQ in every aspect ratio.
- The approved 14-unit geometry gaps, 2.4-unit group outlines, theme-specific shadows/highlights and 28-unit icons are preserved.
- Approved OM, ANQR and CQ brand artwork is embedded. Cloud, UIF and CIX retain the approved text treatment.

## Evidence contract

Current public IDs such as cloud-A, cloud-B, and Foo slot numbers are observations, not project assignments. Topic, ordinal, domain and environmentKey are never used to infer a project. Current Engineering and Research subjects remain neutral, dated, explicitly unmapped observations in the footer.

The receiver projects verified session `projectAssociations`, and the strict shared validator checks runtime ownership, generation, state, safe fields and original timestamps. A binding names `slotId`, `project`, `kind` (durable, primary, overflow), `index` for local workers, `verified`, `observedAt`, and `runtimeGeneration` for sampled runtime. Duplicate cell/slot assignments, expired/unverified associations, generation mismatches and invalid snapshots fail closed. No project association is inferred from ordinal, domain, subject or environment.

Individual colors require fresh source and slot evidence with accepted process, session-runtime or coordinator-lifecycle proof. Sampled runtime is an observation, not complete process discovery. Running, idle and completed are healthy; blocked and failed are blocker states. Stopped is a known nonhealthy state. Unknown and stale evidence remains neutral.

### Allocated health percentages

The displayed percentage is `round(100 × healthy / known)` among fresh, verified, allocated session observations mapped into the displayed topology scope. The center covers all mapped session positions, even in compact mode. Each outer summary covers its twelve local positions; its durable position is not included in that summary. Configured but unobserved/unassigned capacity never enters the denominator. If there are no known fresh allocated observations the result is `?`, not zero.

The center always includes `N/D observed`, showing healthy/known counts. Incomplete discovery adds a visible `partial` marker. A partial 100% therefore never asserts global health. Partial outer percentages are also marked `observed · partial`. Accessible descriptions disclose mapped unknown/stale counts, all unknown/stale assigned observations, and observations excluded because they lack current verified project association. They explicitly distinguish unknown allocation from configured capacity. Full discovery is never inferred from all currently observed sessions being healthy.

### Railway lamps and motion

Each of the six small shared lamps uses only the two closest **durable** hexagons named in its geometry. It never uses the thirteen-position project aggregate. Both durable tiles must have current healthy evidence for a solid green face. If either is unknown, the pair is neutral unless the other is a verified blocker.

Any blocked adjacent durable tile gives the lamp a solid red face during its alert phase and a neutral off phase. A global alternating phase over the even six-lamp ring keeps the two lamps around every blocked tile opposite, including two adjacent blocked tiles or all six blocked tiles. No off phase falsely turns green. The cycle is 1.6 seconds (0.625 Hz), well below 3 Hz. Reduced motion holds all relevant lamps solid red.

Healthy tile borders pulse over 2.4 seconds. Only verified `running` has a green traveling edge; idle and completed never travel. Blocked tiles retain a steady red border and add a traveling warning segment with a 1.6-second strobe that never extinguishes the red baseline. Explicit reduced motion and the system media query stop pulse/strobe animation and hide edge travel. Geometry, brand artwork, orientation and group outlines remain unchanged.

### Native work is separate

Validated `projectActivities` may add a small native-work pill inside the existing durable hexagon. The pill says `native active`, `worker done`, `native blocked`, or `native ?`. Its accessible description includes the original observation time and says it is one observed native worker, with partial native coverage, not project completion or all native work. Native work never changes the session border, shared lamps, session numerator/denominator, or session allocation. Unknown remains explicit; source failure and expiry clear the marker. See `PROJECT_ACTIVITY.md` for the separate typed source contract.

## Verification

- `test/approved-observability.test.mjs` exercises every adjacent pair and state combination, all 64 blocked-neighbor combinations, both layouts/themes/phases, reduced-motion markup, partial counts, unknown/unmapped sessions, source failure, original timestamps and separate native-work markers.
- `test/fixtures/synthetic-render-sha256.json` pins the corrected renderer on independently generated synthetic legacy data. The generator, provenance and regeneration test are documented in `test/fixtures/README.md`; no captured operational payload is included.
- `test/prepare-observability-qa.mjs` generates clearly titled synthetic test-only local pages under the sibling `graph-qa` directory. No preview/fixture routes or fixture imports exist in production.
- Local Inkscape rasterization checks steady-state geometry, full lamp fills and label fit. It does not validate browser animation or embedded-font rendering. Live browser QA is required separately and must use actual current public data for any user-facing state capture.

## Resource and safety requirements

Only data:image/png and data:font/ttf resources are emitted. CSP should allow local script/style and `img-src 'self' data:; font-src 'self' data:`. There are no remote asset requests, dynamic markup assets or filesystem reads at runtime. Public text is XML-escaped and limited to explicit public fields; IDs are validated against the public slot vocabulary. Fonts and artwork are immutable local imports.
