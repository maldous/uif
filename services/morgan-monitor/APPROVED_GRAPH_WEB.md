# Approved graph web integration

The public root serves the railway-v36 graph through reviewed static routes. The observability correction preserves that geometry, adds solid adjacent-durable railway lamps, visibly scoped allocated health, and separately typed native-work markers.
This port changes only the presentation layer and render options. It preserves
status projection, authenticated ingestion, durable storage, allowance collection,
source configuration, existing assets, and immutable revision metadata.

## Files and deployment

- `public/approved-index.html`, `approved-styles.css`, and `approved-app.js` are the new browser shell.
- `public/lib/approved-graph.js`, `approved-geometry.js`, and `approved-assets.js` are the shared browser/server renderer.
- `approved-static-source.mjs` is generated from those exact files with `npm run build:approved-static`. Regenerate after any source edit. Tests compare every served route byte for byte with its source.
- `worker.mjs` serves only the generated exact route allowlist and delegates unrelated paths to the existing assets binding. Deployment should retain that binding and existing assets.
- No source config, observation, database, credential, or deployed service was changed by this integration.

## Minimal page and honest observations

The visible page contains only the approved hexagonal graph and a single,
icon-only light/dark toggle fixed at the top-right. There are no headers,
footers, banners, selectors, inspector, menus, zoom/export controls, or cell
hit targets. The native button supports pointer, Enter, and Space activation.
The page graph is non-interactive. It fills the available viewport without the
SVG metadata-footer region; the approved cell geometry is unchanged.

The initial theme follows the system unless a light/dark query is supplied.
The single icon switches directly between light and dark. Explicit existing
`mode=full|compact` or `layout=full|compact` deep links remain honored; old full
aliases also remain supported. With no explicit mode the page uses compact on
viewports at most 600px wide or 480px high, and the automatic allowance-selected
view otherwise. Automatic allowance mode compacts only on verified exhaustion
and restores full on verified validity. Explicit full always remains full.

The public API is polled every five seconds. A newer request aborts the previous
request and rejects its late result. Request failure, malformed data, or expired
observations withhold current-state colors and verified allowance claims. The
expiry check continues independently of polling. Visibility and back/forward
cache restoration expire evidence immediately and start a new check.

Capture and source-state information remains in the accessible SVG description,
including the original dated public observations and the explicit distinction
between manual snapshots and continuous coverage. It is not displayed as a
visible footer or banner. Capture time is never replaced with feed-check time.
Unmapped project positions remain neutral. A native-work pill, when present, is separate from its durable session border and never changes session health or lamps. The center percentage is scoped as healthy/known allocated observations, with visible observed counts and a partial-discovery marker. No Foo aliases or cloud letters are
implicitly assigned to project positions. The graph reserves the approved
project topology, not a count of live workers.

## Render API

`/render.svg` and MCP `render_status` share the same renderer and immutable revision.
Supported layouts are `full`, `compact`, and `auto`; old `o2`, `hexagon`, and `focus`
values remain accepted as full-view aliases. Themes are `light` and `dark`.
The API rejects unknown or duplicate query parameters and invalid option values.
Current width/height limits, revision validation, no-store behavior, read-only
methods, revision and SHA-256 consistency metadata remain intact.

A stored revision is rendered at its recorded projection time for deterministic
SVG/MCP equality. The browser uses the current clock to expire evidence. Standalone SVG
footers state captured observation time and whether evidence is manual or stale.
The minimal web page uses the renderer-only `footer:false` option and preserves
this information in its accessible description. Public HTTP/MCP defaults and
option schemas are unchanged.

The existing CSP is retained with the narrow addition `font-src 'self' data:` for
the reviewed embedded Roboto font. Scripts and connections remain same-origin.
Embedded graphics/fonts do not load from arbitrary third-party URLs.

## Checks

- `npm test`: full existing backend, legacy renderer, new approved renderer, web,
  lifecycle, and Windmill compatibility suite.
- `npm run check`: syntax checks for worker and old/new browser modules.
- `test/approved-web.test.mjs`: static route byte parity, headers, methods, full and
  compact rendering, themes, strict options, MCP parity, current-claim expiry,
  automatic view behavior, and rendering the synthetic legacy API projection.
- `test/approved-lifecycle.test.mjs`: five-second polling, aborted and late requests,
  a single native theme control, both themes, responsive/deep-linked modes,
  independent expiry, pagehide, and back/forward-cache recovery.
- `npm run preview:approved`: loopback-only preview at port 4177 using the clearly labeled
  synthetic legacy regression fixture. It never represents live status.

Offline DOM tests are not browser visual QA. Production-browser QA remains the
responsibility of the deployment parent after publication.
