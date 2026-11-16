# Project integration and historical dispositions

The [policy 2.0.0](agents/uif-worker-policy.md) is environment-owned. Its
[profiles](agents/environment-profiles.json) retain UIF, ANQR, OffMenu,
CircularQuay and CIX project roles plus a general Cloud role without a Git
binding. Product AGENTS.md remains self-contained; the overlay does not become
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
| General Cloud | No repository binding | Authorized general coordination/non-repository work. Project Git work stays with its owner. |

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
