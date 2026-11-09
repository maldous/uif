# Implementation release checkpoint

Date: 2026-11-09, 05:48 UTC. Repository: private `maldous/uif`.
Branch: `feature/morgan-monitor`. Integration baseline: `develop`
`62bd808ba03528288bb5e395ba319fb54b32a99f`, matched connected GitHub read.
Native `git fetch origin develop` was denied by the environment GitHub network
policy. No bypass or retry was attempted. Connector publication remains separate.

## Implemented and tested

- Independent Cloudflare Worker/D1 receiver, per-source scoped authentication,
  durable monotonic ingestion and outside-Foo scheduled expiry.
- Positive public allowlist, immutable snapshots, deterministic SVG/HTTP/MCP.
- Private metadata collector and durable outbox; pinned read-only Codex allowance
  adapter; selective allowance-only operation without unrelated source reads.
- Approved 83-cell O2 geometry: 64 Foo, six durable cloud neighbours, central
  mascot and 12 bottom history cells. Explicit compact MCP view has ten cells.
- Newest-left curated status history, task icons, safe selection/zoom/pan, shadows,
  six-direction scan and strict actual-state expressive gates. Blank unbound cells.
- 76 Node tests; 86 collector tests including 24 cross-language contract cases;
  29 existing UIF white-room tests; syntax and whitespace validation.
- Supported Chrome QA verified selection, keyboard focus, zoom, pan, responsive
  sizing, reduced motion, actual animation and failure demotion/recovery. Browser
  SVG download was not established after its tool timed out.
- Operational compact TV updates are verified separately by receiver revision
  receipt and a matching-size browser screenshot. Static PNG alone is not TV proof.

## Live setup at this checkpoint

- Credential-free Windmill resource and read-only Bun rendering script deployed
  and read back. Exact fixed endpoint: https://morgan.aldous.info/render.svg.
- Last actual Windmill job reached a controlled renderer timeout while the public
  route was not yet deployed. Successful image end-to-end remains unverified.
- Separate D1 provisioned. Scoped credentials privately generated under explicit
  approval; collector config pinned and validated, but service not started.
- Cloudflare Worker/domain and collector installation await this frozen release.
  Existing portfolio/cq/windmill hosting and TV server remain separate.
- Billing read returned403; no alternate credentials, upgrade or paid add-on.
  Independent external uptime monitoring is not yet configured.

## Data qualification and limits

Current historical databases are metadata, not active worker proof. There is no
qualified process-to-session adapter or cloud-agent feed; all live A–F bindings
remain empty. Cast's historical alias is A. Never reuse old array positions or
assign native workers to durable aliases by recency.

The existing pinned app-server0.167.0 account/rateLimits/read path makes a fresh
backend request per call and propagates failures without cached-success fallback.
Exact release source evidence is in tools/morgan_monitor/allowance-source-evidence.md.
This is source/version qualification, not reproducible-build/signature proof.
The adapter publishes only coarse allowance/eligibility/timestamps, never account
identifiers or raw responses. Missing/null evidence stays unavailable.

New Worker/D1 semantics, public TLS, authenticated delivery, scheduled expiry,
collector restart recovery, successful Windmill image retrieval and remote GitHub
publication must be verified and recorded after deployment. No reboot or disruptive
agent outage test has been performed. Never describe this checkpoint as acceptance.
