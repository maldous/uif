# Policy 2.0.0 rollout qualification — 14 November 2026

## Current operating goal and dated evidence

The agreed end state is unattended autonomous continuous BAU across local quota
loss, sustained Foo/MCP outages and recovery, independent of this conversation.
It requires off-Foo election/claim authority, durable Morgan/project lifecycle,
a configurable default 15-minute outage grace with multiple qualified probes and
healthy reset, and automatic resumption on existing authorized capacity only.
The concrete off-Foo platform/adapter and independent scheduling/session-control
integration remain unimplemented/unqualified; the source snapshots below do not
prove them. This current requirement supersedes the earlier active-chat/Foo-only
rollout boundary without changing what its original receipts actually observed.

Policy/source preparation and the observed existing service are separate facts.
The current policy requires Linear-only intake, independently available execution
claims, quota/outage handoff and per-issue recovery; the retained source
implementation still uses Foo-local claims. The bundle does not activate or
replace the live native runtime. Keep the [operating procedure](../operating-procedure.md),
[recovery reuse map](../white-room/recovery-reuse-20261114.md) and original issue
receipts together when assessing the later controlled start.

## Sanitized native-host readback

The coordinator obtained bounded read-only receipts on 14 November 2026 UTC.
Use each original receipt's timestamp as authoritative; this document does not
invent a more precise observation time.

| Observation | Receipt-backed value |
| --- | --- |
| Host/service | Foo, `stokowski-foo.service`, ActiveState `active`, SubState `running` |
| Execution account | `user` |
| Selected native core | `/opt/uif-stokowski/native-core-20261113-a1` |
| Launcher SHA-256 | `27cd0f2f3f9b51d3bdbd17cbe4d43a6e76e142f513fc1a40dfcd9a054667d5b7` |
| Workflow SHA-256 | `375c0c3d31b0a238bd22d14a1b526ef0e499c16894fcbb611c00bcbdbcbf2854` |

- Binding job `uif-policy-host-binding-20261114-01`:
  original receipt retained in the authorized Foo evidence surface.
- Source inspection job `uif-policy-native-source-20261114-01`:
  original receipt retained in the authorized Foo evidence surface.

These observations identify the inspected binding and file bytes. They do not
prove complete dependency closure, effective resource limits, local allowance,
useful issue delivery, claim fencing, policy adoption or autonomous BAU. No
service start/stop/restart, installed binding replacement, credential change or
runtime configuration change is performed by this policy/source update. Keep raw
source, credentials and protected receipt payloads in their existing custody.

The earlier [November 13 supplied drift report](foo-stokowski-drift.md) is retained
as historical evidence. Its typed-controller assumptions do not override the
inspected native binding, and this readback does not retrospectively qualify
its unresolved outcomes.

## Source preparation versus installed behavior

| Area | Current qualification boundary |
| --- | --- |
| Policy/distribution | Version 2.0.0 bundle, six role profiles, capability/memory/startup guidance and offline consistency checks are source artifacts. Each environment still needs its installed-source and owner acknowledgment evidence. |
| Intake reconciliation | The frozen migration audit accounts for historical action identities against receiving native Linear issues. It is coverage/provenance evidence, not claim authority or proof of useful execution. |
| White-room reuse | Accepted comparison and bounded reconstruction are retained. Explicit ProjectScope, WhiteRoomVerifier and CheckpointRead add source composition for current issue/source/owner and persistence bindings. |
| Quota handoff | Source contracts/tests cover mode transitions, owner generations, original-operation reconciliation and effect fences. Real native dispatch/exit/publication hooks and their exact installed source remain separate integration. |
| Capacity | Policy ceiling is 64 local Codex sessions under shared resource admission. The service readback does not establish that ceiling is installed, available or safe to fill. MCP work consumes the same host budget. |
| Journal retention | The continuation adds committed-record cold archival with permanent original-ID lookup, terminal history and reserved control headroom. Actual owner adoption, joint hot/cold backup and storage monitoring still require qualification; no live pruning, deletion or unlimited retention is claimed. |
| Morgan continuity | The earlier snapshot required already-active Morgan and did not install wake-up. The current required durable lifecycle independently appoints/reconstructs the same role; off-Foo authority and scheduling/session controls remain unqualified. A monitor/telemetry receipt is not dispatch or acknowledgment. |

The current source tests use isolated stores and synthetic readers/observers.
They establish their stated source contracts, not installed provider availability,
real stopped-child recovery or a live local-to-cloud-to-local exercise. Preserve
exact test/candidate evidence with the review; do not label local tests as host
installation or whole-programme acceptance.

## Remaining qualification before the controlled start

Bind the reviewed source to the actual installed native lifecycle and publisher
hooks without selecting a historical ledger coordinator. Qualify authenticated
provider, operation, stopped-child, handoff, coordinator and completion observers,
project/executor dependency declarations and current ownership authority. Reuse
accepted evidence at unchanged exact bindings and resume only unaccepted work.

Verify environment policy adoption and native Linear issue restrictions. Exercise
quota loss during coding/submission/publication, stale owners/duplicate notices,
lost acknowledgment, coordinator replacement, Foo-authority outage and gradual
local recovery with cloud work still active through the supported test boundary.
Live disruptive operations retain their actual authorization requirements.

Continue the requested integration, controlled start and qualification under the
current authorization through verified controls and original operation receipts;
do not add a separate permission checkpoint for this same outcome. Demonstrate
useful autonomous pickup, execution, independent review, publication, refill and
recovery without this chat. Until observed, report source and installed/runtime
gaps separately. This document changes no running binding or historical receipt.

## Foo instruction adoption

The reviewed pinned source `ee41b73da247a6d192e88d04ad0b0349643ce1be` was
staged in a separate user-owned versioned bundle. Original operation
`uif-policy-startup-adopt-20261114-01` returned succeeded/exit 0 after guarded
startup replacement and destination readback. The [instruction adoption receipt](../agents/foo-startup-adoption-20261114.json)
records source/policy/preimage/readback hashes. Original instructions are preserved
in a hash-keyed backup and verbatim under explicit policy precedence; narrower
holds and unrelated USF routing remain applicable. File ownership and mode were
preserved, and no extended attributes were present.

This adopts the startup instruction pointer only. It does not prove running
sessions reloaded instructions, merge project PRs, replace the existing cloud
user skill, install native quota hooks or activate any runtime behavior.

## Authorized source continuation

The existing runtime owner retains sole live-write custody; the async adapter,
retention and continuity source work remains disjoint from the custodian's native
hook composition. See [the native realization handoff](native-quota-realization.md)
for exact seams, approved runner pins, retention adoption and operating proof.
The user's later direction supersedes the former administrative rollout hold:
alignment completion is the prerequisite, without another owner-approval gate.
Preserve active jobs, actual access controls and original operations through the
composed rollout. Historical CIX-pause wording does not pause current work. The
startup wording correction is prepared in source and has not yet been applied
as a new host action.
