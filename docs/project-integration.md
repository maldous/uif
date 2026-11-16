# Project integration and historical dispositions

The [policy 2.0.1](agents/uif-worker-policy.md) is environment-owned. Its
[profiles](agents/environment-profiles.json) retain UIF, ANQR, OffMenu,
CircularQuay and CIX project roles plus the assigned Cloud infrastructure role.
Cloud owns maldous/aldous-info-platform and provider adapters, not a sixth product
gate; original live-site/publisher custody remains separate from repository source. Product AGENTS.md remains self-contained; the overlay does not become
a product build/runtime dependency. UIF owns shared Stokowski, Windmill,
LangChain/LangGraph, GitHub and Linear integration corrections; products retain
their own source, acceptance and publication controls.

## Current role and product boundaries

Revalidate the selected branch and accepted source before work. These bindings
route execution; they do not assert agent provisioning or product readiness.

| Role/surface | Repository and baseline | Contracts retained |
| --- | --- | --- |
| UIF | maldous/uif; develop | Shared infrastructure, native Linear integration, source/deployment drift and recovery; a running service does not prove autonomous delivery. |
| ANQR | maldous/anqr-v3; develop | Current product AGENTS, Node/npm pins, lockfile, typecheck/tests/conformance, rights and source-history preservation. Source checks do not establish browser/API/MCP journeys or deployment. |
| OffMenu | maldous/offmenu; develop | Repository standard/ADRs, typed delivery and validation, independent review, sole integration writer, release-candidate rules and private .delivery custody. Preserve applicable data/UI holds. |
| CircularQuay | maldous/CircularQuay; feature/initial-implementation during PR1 bootstrap | Current product AGENTS, contribution/security/qualification contracts and actual Go/SDK/browser pins. Empty develop is not its current bootstrap source. Preserve exact provider/data grants and synthetic-versus-real evidence. |
| CIX product | cix-project/cix; develop | Product activation record and independent review, Rust/native/codec requirements and Gitflow. Governance checks are not a native build/runtime pass. |
| CIX operations | private cix-project/cix-operations; develop | Current custodian/Ready issue, separate reviewer, owned paths and resource budget. Preserve stopped-benchmark and publication holds; dated historical resource policies are not today's global defaults. |
| Cloud infrastructure | maldous/aldous-info-platform; main | Assigned source and infrastructure/provider adapters only. Preserve existing live-site/publisher custody; not a sixth product delivery gate or inferred deployment permission. Public-safe infrastructure configuration/assets may be kept in the public repository without mandatory homepage presentation; secrets and private provider details stay private. No visibility change. |

CIX surfaces share one logical role but retain separate repositories and disclosure
boundaries. Do not expose private operational source, receipts or data in public
product packaging. USF is a separately selected foundation surface when actually
needed; its GOAL.md remains semantic authority and no competing doctrine is added.

ANQR's accepted removal of maintenance-policy copies and CircularQuay's equivalent
product boundary remain valid. Reconcile CIX's existing removal candidate through
its owner. OffMenu/CIX operations may keep short environment-policy references
while preserving old copies as history; such references do not require a UIF
checkout for product builds. Source publication, environment installation and
owner acknowledgment are separate evidence.

## Select and recover the current per-unit profile

Native Linear supplies current issue scope, immutable workspace/project/issue
identity, dependencies, acceptance and execution restrictions. Historical source
IDs remain provenance; no original-ledger selector or second intake is enabled.
Resolve the owner/claim generation and coordinator epoch, exact repository/
base/head/tree, allowed paths, worktree and actual OS user, executor/resource
budget, toolchain/fixed checks, rights/privacy, reviewer/publisher and original
operation/recovery contract before effects.

Apply the existing [white-room procedure](white-room/README.md) and read-only
[comparison](../tools/white_room/check.py) to the applicable Foo or cloud
requirements. The retained [remote reconstruction adapter](../tools/white_room/remote_evidence.py)
retains its UIF/cloud default; its source ProjectScope extension requires
reviewed target and native Linear UUID bindings. The source
[transition verifier](../tools/white_room/transition.py) composes this reader,
declared comparison and owner-scoped checkpoint checks at an ownership boundary.
Qualify actual connectors and native/publisher hooks separately; source is not
proof of deployed reconstruction or ownership. Compare the destination's declared configuration with
observed installation/state and preserve accepted source/operation evidence to
avoid replay. Reconcile selected persistence/checkpoint versions, source, stage,
owner and original effects before the first unaccepted step resumes.

### Compare native and MCP issue presentation

For a comparison with a caller-authenticated native scope digest, use
[`normalize_issue_scope(issue, expected_native_sha256)`](../src/uif/linear_scope.py).
Supply native-shaped `id` (immutable UUID), `identifier`, `title` and full
`description`; do not substitute an MCP display key for the UUID. Native scope is
SHA-256 of `json.dumps([id, identifier, title, description], ensure_ascii=False)`
encoded as UTF-8, with default JSON separators.

The pure helper returns an already-matching input unchanged, including literal
example tags. Otherwise it converts only the proven MCP renderer form
`<issue id="UUID" label="KEY-1" href="https://linear.app/workspace/issue/KEY-1/slug" />`
to `[KEY-1](FULLhref)`, preserving the full href and every other byte/field. It
returns a description-only copy only if the resulting digest exactly matches;
unknown markup or a real requirements change is not forced into an old scope.
A mismatch raises `ScopeNormalizationError`. No HTML unescaping, title guessing,
history deletion or unrelated HTML canonicalization occurs.

Authenticate the expected digest for the particular comparison. This helper is
not a new intake/admission gate and does not require a new receiving scope to
equal an older producer scope. It grants no authority, claim, full-scope
acceptance, checkpoint, review or publication outcome; existing controls remain.
It has no host/API/model operation or automatic native-runtime installation.

An off-Foo control authority must fence coordinator epochs and task claims/effects
for both lanes; its concrete platform is not selected and the current adapter is
unimplemented. Foo/MCP loss must not prevent eligible cloud issue intake, claims,
execution, review, integration, publication or refill through reachable authority
and actual project dependencies. Default to a configurable 15-minute sustained
Foo/MCP failure grace with multiple qualified probes and reset on a healthy probe
before changing ownership; preserve unresolved original operations. Authority
loss itself holds only dependent claims/effects. Quota reset is not environment
readiness. Foo-only prerequisites apply only to selected dependent work; unrelated
cloud and pure UIF work retain lighter gates. The durable Morgan lifecycle
reconstructs the same configured roles without this chat; all-inference exhaustion
preserves work and resumes automatically using existing authorized capacity only.
Use the [operating procedure](operating-procedure.md) and relevant
[capability inventory](agents/skills-and-plugins.md), not copied host paths or
historical worker counts. Wrong target, rights, source or ownership refuses the
affected operation; no project hold is erased to make profiles appear uniform.

## Historical project snapshot: 6 November 2026

The donor recorded these five sibling Linear projects in the UIF team. Logical
parenthood did not imply an API hierarchy. The source, path, toolchain and status
observations below are historical inputs, not current admission or execution
facts. Current owners reconcile their own profiles; this document does not move
issues, publish product source or select a configuration effect.

| Project and recorded Linear UUID | Historical source/profile observation | Retained boundary |
| --- | --- | --- |
| UIF — ffb6c7d9-4eff-46eb-a805-99f0cc2edfa0 | maldous/uif foundation documentation profile; a Foo workspace was then recorded | The then-documentation-only scope is not a permanent limit on UIF ownership of shared implementation |
| OffMenu — ce60c1ab-a732-4a71-8471-14c9dc393886 | Published develop 3540b2d4d5f4e96a518a63c832ccd6c3db809686; product, control and b850 consumer sources were distinct roles | Preserve actual source/UI ownership; historical source roles do not select today's task source |
| CIX — 9015ca89-b012-4b29-98f9-dbc17c9b8eb6 | Owner-reported main 7904a130fdea8e121f44381cfa0bad383af749d3, tree 11920b7bd734fc8d49f7bc598565e8c791880c32; toolchain 1.98.1 and lz4/snappy dependencies were unresolved | Those were bounded owner observations and holds, not current build/runtime proof or automatic permission |
| ANQR — e780fce4-7412-4060-90bc-68b1fc000502 | Retained real-QR source and unfinished V3 candidates; accepted implementation was then unselected | Assess particular reusable source/contracts through the current owner; synthetic checks alone do not prove QR correctness |
| CQ — 98c8070a-2fb0-438c-9358-634064bf3cca | Recorded base 3ecffe7 and local candidate d70ec22c911ecf279f871cea720c109dc96aeff6, tree 0ca79f2d3e26b0aa5c59936a1dfbb48a7e7c4a4c; push/runtime/CI acceptance were not established | Preserve source/frontend ownership and original effect e7475f4; no replay or current configuration selection |

The donor's then-selected CIX transfer and 389-record inventory wording were
also time-bound. Preserve UUIDs, old-key mappings, history and affected consumer
references. Do not infer migration completion or bulk-rewrite history from that
snapshot. Resolve the actual current team/project/issue identity before any
administrative effect.

## Exact historical CQ unit pin

UIF-179 selected source 74e17dd62ac83d6504d6589e9d7a46b26ee77252,
tree b47d902663d55c400498d0eebe6fbc68096613be and input-manifest SHA-256
dab0bdb45c8cc30c525bbe0a915d818e813ff7c5ee0bc357c42831c5ccc72279.
That exact unit must not silently follow a newer integration commit. Its pin is
not a default source for every current CQ task.

UIF-178's recorded scope was read-only acquisition, investigation and evidence
return.
Full Go/SDK build, a working publisher and OffMenu completion were not
prerequisites for that no-build/no-publication unit. This is historical scope,
not a present claim that those products or capabilities are accepted.

## Legacy treatment and adoption

USF and old UIF/TID physical-infrastructure proposals retain original names,
identities, accepted evidence and unresolved effects. No blanket migration,
history relabeling or new baseline is authorized. Recognize accepted work and
identify current gaps only through the actual applicable contract and owner.

Shared lessons belong to existing UIF obligations with explicit applicability.
Source publication, merge, owner acknowledgment, checkout adoption and actual
execution are separate. Use permitted evidence for each claim; unavailable
historical receipts do not prevent qualification of available current source or
authorize replay. These documents introduce no new scheduler, publication
identity, product dependency, native admission or runtime transition.

## Source-edition reconciliation for UIF-192

The following 12 November section is retained verbatim from the documentary
change in [UIF PR50](https://github.com/maldous/uif/pull/50) at
`54a4ead9a2b7292111cb517080196805ecfcc5e1`. Its uses of “current” refer
to that selected source edition, not to a fresh runtime or provider audit.
Its final paragraph describes the separate historical ledger assessment in
that PR; this doc-only extraction does not import those files, recreate a
ledger, assert current row status, or make their resolution a delivery gate.
The later accepted inventory section below is a separately pinned evidence
edition, not evidence that every intervening product change was inspected.
Both preserve project ownership and apply only to their stated source scope.

## Current shared client-capability inventory: 12 November 2026

This section integrates the accepted OffMenu and CircularQuay documentation slices for the current shared client baseline. It preserves product ownership and does not activate a provider, deploy a service, grant an entitlement or select a new runtime profile.

| Shared boundary | OffMenu-owned contract | CircularQuay-owned contract |
| --- | --- | --- |
| Client configuration and authority | The browser consumes sanitised public `/api/services` configuration. Optional service choices are isolated in `offmenu-third-party-consent-v1`, separate from pantry/account/health stores. | Applications own SDK/MCP transport, authentication, resource authorization and session lifecycle. A client token is not a provider credential; local configuration and provider custody stay distinct. |
| Optional integrations | Diagnostics and analytics are disabled in the example; advertising IDs/approval and consent/feedback configuration are unset. Current client behavior requires configuration and matching opt-in before optional effects. | Skills, client plugins, SDK sessions, protected MCP servers and distribution packages are different artifacts and contracts. An example URL, manifest or build is not publication, activation or provider authority. |
| Confirmed effects and recovery | Support/document delivery requires the product's sign-in, explicit preview/confirmation and original idempotency/unknown-outcome rules. Existing app callers use `setupServices()`; no caller may attach arbitrary tracking properties. | Preserve protected per-operation policy, actual caller/provider capability, original operations and release/readback obligations. Do not infer broader provider or native capability from a portable client artifact. |
| Product independence | OffMenu retains its application, consent and service configuration ownership. | CQ retains its CLI/SDK/MCP and provider policy ownership. Optional UIF orchestration does not become a product dependency. |

Exact OffMenu source is [e16267d](https://github.com/maldous/offmenu/tree/e16267d26c75fb96047ea34159889988220c1677): [client contract](https://github.com/maldous/offmenu/blob/e16267d26c75fb96047ea34159889988220c1677/docs/third-party-client.md), [service inventory](https://github.com/maldous/offmenu/blob/e16267d26c75fb96047ea34159889988220c1677/docs/third-party-service-inventory.json), and [configuration example](https://github.com/maldous/offmenu/blob/e16267d26c75fb96047ea34159889988220c1677/config/third-party.example.json). The inventory retains its own dated observations; this integration is not a fresh provider audit. The current caller is `apps/web/src/app.js:24,302` to `services-ui.js:21`.

Exact CQ source is [192b413](https://github.com/maldous/CircularQuay/tree/192b413662afcf9f323d4afbcaddc6452d174bd4): [agent/client integrations](https://github.com/maldous/CircularQuay/blob/192b413662afcf9f323d4afbcaddc6452d174bd4/docs/agent-integrations.md), [SDK contracts](https://github.com/maldous/CircularQuay/blob/192b413662afcf9f323d4afbcaddc6452d174bd4/docs/sdks.md), [release boundaries](https://github.com/maldous/CircularQuay/blob/192b413662afcf9f323d4afbcaddc6452d174bd4/RELEASE.md), and [portable skill](https://github.com/maldous/CircularQuay/blob/192b413662afcf9f323d4afbcaddc6452d174bd4/integrations/skills/cq/SKILL.md). The existing owners supplied these current slices; ANQR/CIX have no additional selected contribution for this scoped join.

The accompanying ledger assessment records the finite OffMenu profile support, unavailable original routing-candidate relation and CQ continuation plan separately. GitHub server PR records provide supported correlation for a normal authorised PR operation; row 051 remains open because no current shared CQ write/owner-held intent is selected and old raw-push correlation remains unavailable. None of these documentary dispositions releases a live combined effect or relabels missing old receipts successful.

## Accepted client-capability baseline (UIF-192)

This section integrates the independently accepted, bounded source-metadata
inventory from [UIF-192](https://linear.app/maldous/issue/UIF-192).
It is a historical source baseline, not a claim that today's product editions,
service installations or entitlements have been reverified.

The accepted OffMenu edition is commit
`3540b2d4d5f4e96a518a63c832ccd6c3db809686`, tree
`62efa45bb564affe4f1f1b2388bbb3447a14482c`. The nine-file inventory SHA-256 is
`1f52e20702f1faab8c82ddf9fb3688ef25bfa8cc9e456284386267fba0c1e76d`;
independent review SHA-256 is
`852b899a535a5a8f304ba78b820ffc0af7143191e797e57e9dd22db8a96ab66d`.
The [accepted review record](https://linear.app/maldous/issue/UIF-192#comment-617ee572-e937-490d-a7af-26477584bc6a)
supersedes the earlier pending-review wording without rewriting its history.
Reuse that acceptance; no repeat inventory or test suite is required solely
to integrate these source-qualified lessons.

| Accepted source evidence | Shared lesson and product boundary |
| --- | --- |
| OffMenu service-config.mjs projects strict V1 public metadata using THIRD_PARTY_CONFIG, SERVICE_GLOBAL_SETTINGS and SENTRY_DSN configuration names. | Record schema/name and applicability separately from actual configured values. Do not copy tenant identifiers, credentials or private configuration into shared documentation. |
| service-observability.mjs implements a filtered server Sentry envelope, diagnostic-code allowlist, authenticated same-origin consent route, bounded body/rate handling and five-second abort/redirect refusal. | A source hook is not a full browser SDK, replay installation, provider entitlement or demonstrated runtime delivery. |
| services-model.js/services-ui.js and docs/third-party-client.md describe independent device choices, optional OffMenu categories defaulting off, fixed vendor loaders, Zaraz purpose consent and local/TCF AdSense requirements. | Optional collection remains off until its particular purpose, consent, rights and entitlement are qualified. Preserve the recorded withdrawal limitations; a shared baseline does not activate ads, analytics or replay. |
| serviceCSP and handler.mjs contain conditional vendor origins. | CSP permission is neither user consent nor evidence that a vendor loaded. |
| auth-runtime.mjs/netlify.toml contain source-level hosting/auth wiring; OFFMENU_AUTH_MODE selects the existing Netlify/Better Auth route. | Presentation labels that mark Cloudflare or Netlify Identity configured even with an empty environment do not prove deployment, authentication, plan, capacity or entitlement. |

### Applicability and existing follow-ups

The accepted inventory observed browser/server JavaScript. Its bounded path
scan found no Android directory or .kt/.gradle/.kts files at that edition;
native Android and actual mobile behavior remain unqualified, not universally
absent. Eight existing service/consent test files were identified, not run.
The terminal metadata receipt's hardcoded seven-assertion label was inaccurate:
the original script contained six assert statements, some covering multiple
conditions. Preserve that correction; it is not a test-suite count.

Circular Quay adopted the source-hook versus SDK/runtime and configured-label
versus entitlement distinctions through its existing coordinator. Its
[UIF-172](https://linear.app/maldous/issue/UIF-172) native telemetry contract
retains default-on metrics, explicit disable before initialization and optional
browser diagnostics. OffMenu's optional browser default-off choices do not
override that different native contract.
[UIF-176](https://linear.app/maldous/issue/UIF-176) and CQ's existing RELEASE
documentation retain hosting, provider entitlement, identity and provisioning.
No product flags or private configuration are selected here.

OffMenu [UIF-184](https://linear.app/maldous/issue/UIF-184) retains its
developer-interface response-envelope/reference gap. An under-specified OpenAPI
envelope was observed, but strict schema invalidation was not established:
missing required-field declarations and allowed extra fields matter.
Bind a declared schema and actual current response evidence separately.
Do not duplicate these product follow-ups in a shared queue.

For later reuse, the existing project owner records the selected current source
and maintained module, configuration schema/name, client surface, purpose,
entitlement/applicability, consent/privacy/redaction, lifecycle/recovery behavior
and qualified evidence. Keep compiled, configured, deployed and actually verified
states distinct. Unknown fields remain unknown; public provider documentation
and accepted historical source are not grants.

This documentary integration does not resolve all client-surface, entitlement,
consent or recovery obligations under UIF-192 and does not hold unrelated
delivery. Current product changes remain with their owners; service activation
and provider effects require their existing authorization. No new platform,
adapter, universal release gate or collection requirement is introduced.

Original [submission](https://windmill.aldous.info/run/01a1ae0d-ab59-a4c5-163d-56af76bf8362?workspace=offmenu)
and [terminal metadata receipt](https://windmill.aldous.info/run/01a1ae0d-e68d-6d21-af54-723203ea5f1e?workspace=offmenu)
retain their original scope. Publication of this section is not a new runtime
or provider verification.
