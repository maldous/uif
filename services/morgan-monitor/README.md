# Morgan public monitor

Independent receiver, public HTTP/MCP projection, and deterministic SVG/web UI.
This is an additive UIF implementation. See IMPLEMENTATION_STATUS.md for the dated
release checkpoint and distinguish source validation from live deployment.
Nothing here restarts observed workers or changes the current TV display.

## Components and trust boundary

```
Private Foo metadata -> separately supervised collector + durable SQLite outbox
                    -> HTTPS authenticated /v1/ingest (one identity per source)
                    -> outside-Foo Cloudflare Worker + D1
                    -> public /api/status, immutable snapshots, SVG, read-only MCP
                    -> morgan.aldous.info browser (deployment target)
```

The receiver's one-minute scheduled evaluation remains outside Foo and outside
agent sessions. Lost source contact expires without a collector or browser being
alive. It cannot detect a total Cloudflare outage from within Cloudflare; the
browser then withholds current-state colors and reports receiver reachability
failure. A separately operated external uptime probe is still a deployment step.

The private collector and its source qualifications are documented in
[`../../tools/morgan_monitor/README.md`](../../tools/morgan_monitor/README.md).
A local stdio MCP and fixed-path Windmill wrapper expose safe metadata. The
credential-free Windmill renderer resource and read-only Bun script have been
deployed; public image retrieval awaits the independent receiver deployment.
Connected interactive tools are not daemon credentials.

## Public privacy contract

The public projection is a positive allowlist. Ingestion rejects extra fields,
unregistered source IDs, wrong capabilities, aliases owned by other sources,
free text, URLs, arbitrary topic/blocker keys and malformed clocks. A claimed
`sourceId` is not authentication. Each registered source has a distinct bearer
secret and exact domain/alias/capability ownership. Registration is operator
configuration, not an API operation. Secrets are environment bindings, never
source files, URLs, public DTOs or diagnostic messages.

Public data contains only:

- Stable display aliases: cloud A–F and Foo 1–64.
- Explicit assignment, coarse verified/recorded state, proof kind and UTC times.
- Source reachability, observation freshness and coverage completeness.
- Topic and blockage text selected from reviewed `TOPICS` / `BLOCKERS` catalogs.
- Recently curated Voice/Chat subjects (ten per channel) and generic status
  subjects (twelve), newest first. Channel provenance is never invented.
- Separately qualified Foo allowance state, boolean eligibility and freshness.

No transcript access or links exist. Private thread IDs, session titles, local
paths, raw diagnostics, process arguments, environment values and credentials
are not accepted for publication. Public source names are receiver-owned generic
aliases. Changing a catalog requires code review: deterministic rendering never
summarizes or invents private content. Do not broaden catalog text to include
sensitive context. The current curated subjects are explicit choices, not an
inference from messages.

Both fresh and stored immutable projections are validated before publication.
Browser text/attributes are escaped; no arbitrary HTML or SVG paths come from a
source. CSP permits only same-origin scripts/connections and does not permit
inline JavaScript, frames, objects or external fonts/images. Anonymous browser
receipts are not trusted execution or rendering evidence.

## State is not inferred

- A healthy metadata read is not an active worker.
- A historical `inProgress` row does not get a new session timestamp on a poll.
- Only fresh process-capable session evidence can create current-state colors.
- Complete historical coverage does not imply complete execution coverage.
- Missing registration is unconfigured; missing evidence is unavailable, never
  silently `idle`, `unassigned`, healthy or stopped.
- Source errors preserve last-good records without renewing their success time.
- Exact retries do not renew observations, curated topics or expiry thresholds.
- Stale/outage records retain timestamps for inspection; current color and main
  cell task details are withheld. No `unknown` or `not measured` label is used in
  the board. Details distinguish absent evidence with ordinary source wording.

Complete execution coverage requires every configured capacity slot to have a
fresh explicit process observation, including explicit unassigned slots. It is
intentionally conservative: absent cloud telemetry cannot produce a happy/fire
mascot. Fire additionally requires every assigned worker explicitly stopped;
blocked, failed, missing, idle or stale workers do not qualify. A fresh verified
blocker makes Morgan look/point at its mathematically placed cell. Selection may
make him look at a fresh assigned cell, without claiming activity.

The provided collector has **no qualified session-to-process adapter** and no
cloud feed. Therefore its metadata alone can never turn the board green or
produce an all-stopped claim. Integration must supply a supported authenticated
lifecycle/heartbeat source with stable alias binding before claiming live worker
coverage. Cast's historical durable alias is A; no source mapping is seeded from
old array positions or the prototype roster.

## Geometry and rendering

`public/lib/layout.js` uses an exact axial hexagonal lattice. Morgan occupies the
center, with cloud A–F in the six immediate neighbouring cells in a stable order.
The selected full O2 wide-fold grid has exactly 83 cells, in row counts
`[12,11,12,13,12,11,12]`:

- 1 Morgan, 6 cloud, 64 Foo and 12 bottom history cells.
- Congruent pointy-top regular hexagons with shared edges and no internal gaps.
- The middle row was expanded symmetrically to preserve all 64 Foo positions.
- Bottom history runs newest-left to oldest-right; blank capacity is not history.

Compact mode has the core seven cells and three bottom history cells (internal
56–58, not visible worker numbers). It is the user's selected MCP presentation.
Fresh verified exhaustion also selects compact; only fresh verified valid
allowance automatically restores O2. An absent feed never restores capacity.

Assigned focus keeps the six durable cloud neighbours and shows only observed
assigned Foo slots and actual curated subjects. This is an alternate layout, not
a replacement of the full-capacity option. Icons are an authored fixed SVG
catalog chosen by reviewed topic keys with a generic work fallback. No AI or
third-party graphics service is needed. Zoom, pan, keyboard selection and an
inspector reveal only safe public fields. Reduced-motion preferences disable
animation. Saving SVG uses the same renderer as HTTP/MCP.

A projection revision hashes meaningful public state but excludes drifting
`generatedAt`/age counters. It changes on observations, state, expiry or topics.
The first snapshot for a revision is immutable. Web rendering retrieves that
exact snapshot before displaying it, so a separately fetched SVG can match the
same revision. Pair revision plus render SHA-256 (or exact embedded SVG), because
a later renderer deployment may change styling for an older saved snapshot.
Unknown/expired revision IDs return 404, never the latest image.
The browser polls at 5 seconds visible / 30 seconds hidden, with bounded failure
backoff. Fetch failure removes current colors and expressive claims immediately.

## API

- `POST /v1/ingest`: HTTPS, registered source bearer token, exact versioned event.
- `GET /api/status`: current allowlisted projection and immutable revision.
- `GET /api/snapshots/<64-character revision>`: immutable public snapshot.
- `GET /render.svg?revision=...&layout=compact`: shared deterministic renderer.
- `GET /health`: receiver plus aggregate coverage, no private source identifiers.
- `POST /mcp`: read-only Streamable HTTP request/response subset: initialize,
  ping, tools/list, tools/call (`get_status`, `render_status`). No mutation tools.

Renderer arguments: width 320–4096, height 240–2160, layout `compact`, `o2`, `focus` or legacy `hexagon`,
optional worker `selected`, optional exact revision. The public MCP endpoint is
unauthenticated because it exposes only the same public data; ingestion is not.
A consuming MCP client still needs compatibility qualification before use.

D1 triggers atomically fence sequence, event identity, source clock and per-source
rate. Keep one authoritative producer per source ID. Do not reset sequences or
replace a collector database to work around conflicts. Receipts retain the latest
100/source; source heads permanently fence older replay. Snapshots retain128.
The D1 instance and private collector state need separately qualified backup,
retention, cost and operational monitoring policies.

## Offline validation

Requires Node24+ and Python3.10+ stdlib; no npm dependencies.

```sh
node --test services/morgan-monitor/test/*.test.mjs
node --check services/morgan-monitor/worker.mjs
node --check services/morgan-monitor/public/app.js
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/morgan_monitor -p 'collector_*.py'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/white_room
```

Backend tests use real SQLite constraints/triggers through a small D1 shim. This
is not proof of deployed D1 behavior. Geometry tests check counts, connectivity,
shared edges, symmetry and durable-center placement. Rendering tests cover
escaping, source expiry, icon fallback, strict expressive-state gates, exact
revision identity and reduced-motion CSS. Collector tests cover schema drift,
read-only source access, historical clocks, durable retries and saturation.

A loopback-only development preview runs with no external adapters:

```sh
node services/morgan-monitor/test/preview.mjs
# Explicit synthetic data, conspicuously labelled and never production:
node services/morgan-monitor/test/preview.mjs --fixture
```

The preview binds only127.0.0.1:8789. It creates in-memory test state, not telemetry.
It must not be exposed or used as the public receiver. Browser QA must run on the
same host or a separately approved development path. A rendered static artifact
is not evidence that browser interaction or receiver animation works.

## Deployment stages and gates

1. Review and checkpoint this additive branch; preserve UIF custody/reconciliation
   files and existing project work. Confirm current `develop` before integration.
2. Resolve a supported cloud agent feed and session-to-process attribution. Define
   stable aliases, source ownership, public keys and exact coverage semantics.
   Unavailable integrations stay explicitly unconfigured.
3. Review Cloudflare account/billing and create a separate Worker/D1 instance with
   all five ordered migrations. `wrangler.example.jsonc` is a template, not an executed
   deployment. Provision scoped per-source secrets only with required approval.
4. Verify actual staging D1 semantics, HTTPS auth, negative privacy cases,
   scheduled expiry, replay after uncertain delivery, browser interactions and
   reduced motion. Qualify the MCP transport with its intended clients.
5. Bind only `morgan.aldous.info` to the verified receiver. Preserve aldous.info
   portfolio hosting, wildcard, cq and windmill routes. Do not expose Foo or reuse
   its current TV server/heartbeat routes. No new inbound Foo access is needed:
   the private collector only pushes outbound to the exact receiver URL.
6. Install a reviewed user service from the provided collector template only after
   source paths/permissions, environment auth, ordered outbox recovery, disk limits
   and boot persistence are approved and verified. No credentials go in a unit or
   checked-in config. Do not silently enable user lingering or alter host security.
7. Establish an independent external uptime check and operational owner. Test
   source disconnect and recovery without disrupting actual agent execution.
8. Preserve the existing TV until the replacement is proven. The user has requested operational TV updates and paired chat previews.
   Cast-adapter development remains stopped; do not expand that scope.

At initial hosting audit, morgan.aldous.info had no explicit route and its
wildcard destination returned522. Deployment progress is a separate dated
checkpoint. Do not present templates or offline tests as live outcomes.
