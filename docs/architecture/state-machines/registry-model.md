# Registry ownership, admission and original effects

This is a bounded source-documentation continuation for
[UIF-249](https://linear.app/maldous/issue/UIF-249), not installed authority,
current ownership or exhaustive project coverage. The complete 765-line
[registry module](../../../src/uif/quota_handoff.py) was read statically.
Its primary-edition blob `b7a6f14858e9d8d0ab7f09e60717bda9b1151d7c`
and SHA256 `840dec50c59258e341bef7919f2d1fab22e0c733b1da66c0fd29a83d2a556d7e`
are unchanged at this pass's develop base
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`. Line references below bind the
[original primary edition](https://github.com/maldous/uif/blob/5a9d08866066c149e06bb7cde713c77d33b7998b/src/uif/quota_handoff.py).
No registry, store, callback, test, native method or service was executed.

## Representation and responsibility

`Registry` wraps the single-host `PrivateStore`; its transaction, retention and
cooperating task-fence boundaries are described in the existing
[store map](handoff-store.md). It records claims, original operation intents,
attempt history, mode and receipts. It does not select Linear work, run a model,
launch sessions, elect a coordinator automatically or install an off-Foo endpoint.
The module explicitly requires installed native/publisher/MCP consumers to call
its guards. An unadapted executor is not fenced (lines 1–7).

Initialization validates canonical Linear UUIDs and a UID-bound registration of
actors, projects and authenticated principals. It creates revision 0 in
`local-recovery`, stopped, local limit 1, no provider/heartbeat/tasks, coordinator
generation 1 and no pending successor (192–249). Initialization is not discovery
of existing custody. The CLI's actual OS-principal derivation and all other
consumers remain separate source-review work, not facts established here.

Task generations and coordinator generations are different fields. Source
bindings contain repository, branch and full base/head/tree pins. Identity and
hash validation establishes shape, not authenticity of external evidence.
Trusted constructor-injected verifiers provide that missing integration boundary;
wire JSON cannot select one (46–55, 82–153, 179–190, 271–285).

## `UIF-REG-REQUEST`: authenticated two-phase mutation

`invoke` accepts a bounded request and authenticates principal-to-actor against
registration. Status reads are distinct from mutating actions: general `status`
returns current hot state to an authenticated actor; exact-key `task_status`
additionally checks workspace/project authority and may read cold history
(311–328). Neither read grants admission or a permit.

For mutations, the actor/request-ID pair indexes the retained result. Same ID
with the same complete request digest returns that result; changed payload is
`request_identity_conflict`. Hot and retained request receipts are consulted.
Morgan's current execution/generation/principal and pending-transfer checks occur
**before** this replay lookup, so an old heartbeat receipt cannot renew a replaced
coordinator. Non-Morgan requests cannot carry coordinator identity (360–385).

| Phase | Transition or guard | Source |
| --- | --- | --- |
| Snapshot | Acquire a per-task fence for the six task actions, authenticate/check replay under a short metadata transaction, copy registration/state | 329–338 |
| Qualify | Apply to the copy; call trusted readers outside the global lock and cache each exact context fingerprint/result | 156–176, 339–342 |
| Commit | Authenticate/check replay again; apply to current state, reusing only matching proof contexts; advance revision, store request result/event and save | 343–358 |
| Refuse | Missing reader, changed proof context or failed guard raises `Hold`; no successful request receipt is committed by this path | 163–175, 344–357 |

The task fence remains across proof reads, but unrelated task/heartbeat metadata
can progress. A context match is not a newly observed external event. Exceptions
from ordinary reader callbacks become bounded `*_unavailable`; an existing
`Hold` is retained. Callbacks are trusted code, not sandboxed by this class.

## `UIF-REG-COORDINATOR`: explicit cooperative succession

State is current `{generation, execution_id, principal, pending}` plus an optional
heartbeat. Only the configured Morgan actor may transfer or acknowledge. This
module has no lease-expiry timer, automatic election or missing-heartbeat takeover.

| Event | Guards and resulting state | Source |
| --- | --- | --- |
| `transfer_coordinator` | Current coordinator binding; token-valid distinct successor execution ID and a registered successor principal permitted to act as Morgan; typed reconciliation context accepted by the injected verifier. Set pending successor at generation +1 and clear heartbeat | 365–376, 389–408 |
| Other Morgan mutation while pending | Refuse `coordinator_transfer_pending`; do not reuse a prior receipt to escape the pending state | 365–384 |
| `acknowledge_coordinator` | Authenticate the pending successor's execution/generation/principal; reverify the same origin-to-successor context. Replace current with pending and clear pending | 368–374, 409–422 |
| `heartbeat` | Current binding; fresh, nonfuture observation strictly later than an existing heartbeat. Record current generation/execution and observation | 251–264, 423–431 |

Acknowledgment leaves heartbeat absent until a later valid heartbeat. Cloud claim
and task handoff require `_morgan`: no pending transfer, exact current
generation/execution and heartbeat fresh within 90 seconds. That is a source
guard, not evidence that an installed lifecycle invokes or enforces it.

## `UIF-REG-MODE`: quota, stop and gradual local recovery

The four mode strings are `local-ready`, `transfer-pending`, `cloud-active` and
`local-recovery`. The independent `stopped` boolean is not another quota class.

| Event | State change and limit | Source |
| --- | --- | --- |
| `set_stopped` | Morgan-only boolean update; mode, tasks and external child processes are not changed | 432–436 |
| Qualified provider observation | Native/Morgan actor; one of available/hard-quota/transient/auth-failure/unknown; fresh time, positive increasing sequence, nondecreasing observation time, matching verifier | 437–453 |
| Hard quota | Preserve already `cloud-active`; otherwise enter `transfer-pending`. Set local limit 0 | 454–458 |
| Newly available after absent/nonavailable sample | Enter `local-recovery`, limit 1. Existing cloud claims are not reassigned | 459–461 |
| Transient/auth-failure/unknown | Store classification without imitating hard quota or changing mode/limit; `_provider` rejects dependent local reasoning | 266–269, 462–465 |
| Qualified cloud claim or handoff, no local active/pending task remains | Enter `cloud-active` at the applicable branch | 493–496, 583–585 |
| Local completion during recovery | Accepted outcome increases limit through 1/4/8/16/32/64 up to registered ceiling; reach ceiling to enter `local-ready`. Failed outcome resets limit 1 | 614–618 |

New local claims and retries require nonstopped state, fresh available provider,
local-ready/recovery mode and active local count below both ramp and ceiling.
Cloud admission requires current Morgan evidence instead (466–538). Generic
active-effect checks require nonstopped/active owner/generation; provider/mode
checks apply there only when `reasoning=True` and lane is local (302–309).
Stopping or a new quota sample does not cancel an already-running external call;
installed callers must use the effect boundary/recheck appropriately.

## `UIF-REG-TASK`: claim, handoff, finish and qualified retry

Tasks are keyed by immutable Linear workspace/project/issue UUIDs. Hot absence
does not mean no claim: exact task lookup can rehydrate retained history, and
new claim checks both hot and archived records (292–300, 474–475).

| Transition | Necessary guards and preservation | Source |
| --- | --- | --- |
| Absent → active generation 1 (`claim`) | Registered project/lane role, nonstopped, capacity, lane prerequisites, original operation/source/evidence pins and fresh reconstruction receipt | 466–497 |
| Local active → handoff-pending (`capture_handoff`) | Existing owner, transfer-pending mode, fresh Morgan, child-gone assertion/digest, typed checkpoint/completed-step/source fields and trusted handoff verification. Append original unresolved operation IDs | 539–569 |
| Handoff-pending → cloud active generation +1 | Project role, nonstopped, fresh Morgan, every original operation terminal, successor reconstruction. Retain origin owner/generation for checkpoint compatibility | 570–586 |
| Active → complete/failed (`finish`) | Current active owner, reconstruction, terminal operations, boolean acceptance and verified completion receipt/context | 601–619 |
| Failed → active generation +1 (`retry_claim`) | Same expected generation; nonstopped, lane role/capacity, terminal old operations and qualified new original-attempt ID. Snapshot prior attempt/history; fresh reconstruction | 503–538 |

Same-source retry preserves genuine checkpoint origin; changed source starts with
no reused origin. This is not rewriting historical ownership. Completion is
distinct from a successful effect receipt. `complete` has no retry transition in
this interface; stale generations and duplicate new claims refuse rather than
implicitly taking over. Handoff capture records unresolved effects but does not
make them safe: acknowledgment cannot proceed until their terminal reconciliation.
Capture itself has no independent nonstopped guard; successor acknowledgment does.

## `UIF-REG-EFFECT`: durable intent and uncertainty

Both synchronous and asynchronous effects hold the same cooperating per-task
fence. Effect callables are injected trusted adapters, never selected by JSON.
External operations remain outside an atomic filesystem transaction.

| Step/event | State and failure behavior | Source |
| --- | --- | --- |
| Existing original ID | Require exact payload. Return a terminal recorded outcome; pending/unknown refuses replay. An ID in prior attempts also refuses reuse | 704–716 |
| Prepare/qualify | Snapshot active owner/generation, reconstruct outside global lock, then recheck active state and proof context | 630–638, 657–670, 718–729 |
| Admit | Require all other operations terminal and headroom; durably save `{pending, payload_sha256, generation}` before invoking callback | 730–737 |
| Callback returns | Require exactly terminal status plus receipt SHA256; record under matching owner, generation and unchanged original intent | 639–645, 671–679, 739–749, 760–765 |
| Callback invocation/await or result validation throws after admission | Attempt to retain `unknown`, re-raise original exception. If that persistence fails with Hold/OSError, original durable `pending` remains held | 639–644, 671–678, 751–758 |
| Recording a validated result fails | `_record_effect` is outside the callback/validation try block. This path does not automatically retain `unknown`; reconcile the original intent and actual persisted outcome before another action | 645, 679, 739–749 |
| Independent `resolve_operation` | Existing owner or Morgan, exact nonterminal original ID/payload context, terminal status/digest and matching trusted operation-verifier result | 587–600 |

The result validator checks shape, not external authenticity; the adapter must
actually observe the destination. Terminal outcomes are immutable to the resolve
action. There is no generic rollback/compensation or automatic replay worker.

The async path reconstructs through `asyncio.to_thread` before intent admission;
cancellation can leave that read-only reader finishing, but cannot use its late
result to create a new intent. After admission, callback cancellation preserves
uncertainty through the exception-retention path. Failure while recording a
validated callback result is different: inspect the original persisted record
and destination outcome, rather than asserting it was automatically marked unknown. The optional `recheck_effect` verifies current source and the exact
pending generation/intent after an awaited reservation; it creates no permit and
requires the caller still to hold the surrounding task fence (647–702).
This is not proof that every installed caller uses it.

## Evidence strength, interactions and current issue impacts

This pass read implementation, not the complete tests/CLI/adapter source. Named
existing tests in `test_quota_handoff.py`, `test_registry_transition.py` and
`test_native_quota.py` are follow-up entry points, not newly inspected full
oracles or executed qualification. The coverage account updates only the actual
registry read; prior source hashes and accepted store/continuity reads remain.

| Existing owning issue | Affected boundary and unresolved input |
| --- | --- |
| [UIF-249](https://linear.app/maldous/issue/UIF-249) | This fifth full source/test-file read extends the partial inventory; other modules, consumers, editions and full issue-impact closure remain open. |
| [UIF-120](https://linear.app/maldous/issue/UIF-120) / [UIF-130](https://linear.app/maldous/issue/UIF-130) | Actual authority/lifecycle/native integration and retained original operations require their own evidence. A source registry cannot restore an old process's maps/claims or establish restart safety. |
| [UIF-140](https://linear.app/maldous/issue/UIF-140) / [UIF-141](https://linear.app/maldous/issue/UIF-141) | Source roles, checkpoint origin, consumer bindings and original publication intent are not inferred from valid hashes. |
| [UIF-159](https://linear.app/maldous/issue/UIF-159) | Effect callback success is not task acceptance, refill or useful unattended delivery. |
| [UIF-247](https://linear.app/maldous/issue/UIF-247) | Fresh reconstruction/installed verifier and current source matter at admission; a readable policy or successful comparison is not a claim. |
| [UIF-127](https://linear.app/maldous/issue/UIF-127) / [UIF-218](https://linear.app/maldous/issue/UIF-218) | Publication needs actual destination receipts and retained sole-publisher custody; no original effect is retried by this map. |

Remaining unknowns include actual authenticated readers and runtime callsites,
off-Foo availability, external lease/fence enforcement, safe host interruption,
provider/Pool binding and complete cross-repository ownership. Those are not
silently waived by this bounded read. No claim, coordinator epoch, native pause,
runtime configuration, security setting or original receipt changed.
