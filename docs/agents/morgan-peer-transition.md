# Morgan peer transition playbook

Use this when Morgan oversees a UIF peer transition or recovers a Foo release.
Stokowski detects and handles native runtime transitions through
reviewed code, its configured scheduler and the shared authority. Normal intake,
claiming, continuation, publication reconciliation and refill must not depend
on an agent reading these instructions, a Dot conversation, or manual polling.

## Use native Stokowski capabilities first

Before proposing custom glue, inspect the installed Stokowski version, effective
configuration, and its supported documentation or hook contracts. Verify the
mechanism on the intended runtime; mark it unverified if evidence is missing.
Do not infer that a capability is absent just because it has not been observed.

| Responsibility | Prefer the existing owner and mechanism |
| --- | --- |
| Startup, shutdown, and recovery | Native lifecycle, supervision, restart and recovery controls. |
| Intake, scheduling, retry, and refill | Native scheduler and documented reconciliation or refill hooks. |
| Provider availability and selection | Native provider hooks with the existing request, budget and scope checks. |
| Runtime reporting | Native health/status and original-operation readback; Morgan interprets evidence and reports gaps. |
| Cross-peer ownership | Existing shared authority and peer handoff contract; Morgan does not replace either runtime's scheduler. |

Only propose a scoped extension where evidence shows the supported native
mechanism cannot meet the requirement. This is an engineering preference, not
a new admission or deployment gate.

## Verify provider recovery in the running service

Start the commissioned runtime even when its authorized roster has no eligible
route. Check that its provider readers keep running, report a separate hold for
each unavailable route, and retry according to each route's reset or backoff.
A healthy eligible route can take new work while other routes remain held.
An uncertain original keeps its existing route and reservation.

Use the roster and exact receipts to distinguish a listed model, an installed
reader, a fresh account observation, and an authorized sender. Test recovery
with an already installed sender by withholding its required evidence, restoring
an authenticated observation, and verifying a new admissible dispatch in the
same runtime. Capture the process, claim, original operation and effect
receipts. Repeat with a stale observation and a held original.

`Stokowski commissioned idle: free_route_unavailable` means the normal mount
verified its authority and is waiting for an installed route. A configured
route with unavailable quota is handled by the running discovery lifecycle.
Record the effective installed configuration and the current authority receipt
when interpreting either state.

Cloudflare's daily reset and an empty adaptive analytics result can support the
usage history. For Workers AI admission, verify the account-wide bound, the
exact Free route and its sender in the active deployment. Record any alternate
route whose reader or sender is unmounted. [Provider discovery and selection](../operations/provider-pool-selection.md)
· [Workers AI readiness](../operations/stokowski-provider-readiness.md)

## Transition decisions

| Trigger | Evidence to use | Morgan action or hold |
| --- | --- | --- |
| Normal run or observation | Verify the actual runtime identity and shared authority, current Linear scope and live claim/epoch. Distinguish current reads from retained receipts. | Keep the currently assigned owner. Eligible work on a disjoint scope may proceed through its existing scheduler and admissions; availability alone never transfers an issue. |
| Foo or MCP degraded | Use the existing sustained-failure policy and qualified probes. Check whether the affected scope depends on Foo, and whether the independently reachable authority is current. | Hold unsafe dependent work; preserve state, retain the existing claim and continue independent work. Timeout, quota reset or a missing session is not a transfer. |
| Transfer to Morgan or back to Stokowski | Reconcile original effects. Check exact owner session, current lease and coordinator epoch, independently authenticated control attestation of exact successor, quiescence and reconstruction, plus the qualified successor's durable assignment/workspace. | Use the supported current-owner handoff only when those existing inputs are present. Verify returned owner, generation, lease ID and epoch before acting on that issue. An unavailable original owner or missing evidence stays held; keep unrelated work moving. |
| Lost acknowledgment, interruption or stale fence | Read the same original with its existing identity; compare retained facts and the current fence. A successful old receipt can coexist with a newer fence. | Never mint a second operation to recover a lost result. Keep ambiguous effects held; stale owners do not write. Do not infer successful recovery from source or fixture tests. |
| Completion, refill or return to health | Observe exact terminal outcome and authenticated completion through the existing effect and publisher boundaries; verify current owner, scope and admission for the next eligible Linear issue. | Let the appointed scheduler refill. Do not issue another claim for held work, take from the other healthy peer, or substitute a new publisher. Restore capacity for eligible scopes without moving an active claim back merely because its old peer returned. |
| Explicit owner pause or stop | Resolve the scope and current durable control state before any work; treat a shared stop as global where the authority defines it so. | Honor it across recovery and reboot. Read-only status and reconciliation do not lift it; unrelated work proceeds only outside the scope of the stop. |

## Reconcile a native investigation report

When the current Stokowski investigation produces a reviewable result, the
native runtime can emit its scoped `stokowski:report` comment on the same
Linear issue. The report binds the attempt, issue, run, scope and retained
provider-result digest. Cloudflare checks the exact retained response and
current fence before admitting the comment. The tracking announcement and
the investigation report each have their own original operation.

If the report reply is lost, use the retained report intent and read the
receipt for that original. An unknown result keeps the operation held while
readback continues. Keep the report and its receipt with the issue evidence
for the assigned independent reviewer. Advance the workflow and refill only
after their respective review and settlement receipts verify.

## Locate and continue the normal Foo release

1. Start from the last **installation** receipt and the current service
   selection recorded against the active release issue. For Stokowski, use
   [UIF-1313](https://linear.app/maldous/issue/UIF-1313). Trace that original to
   the retained archive, complete canonical
   manifest, artifact digest, source revision and evidence for those exact
   bytes. For a first normal install, use the exact source, tree, and private
   archive locator in the active release handoff. Reconcile its recorded digest
   and the owner's canonical selection original before use. The guarded-idle
   start receipt identifies the earlier trial. Use the
   approved in-place comparison to verify reusable components against the
   independently retained manifest. Keep the archive, manifest, verification
   and original receipt together at a stable locator the installer can read.
   [Foo release workflow](../windmill/foo-installer-workflow.md#retain-the-inputs-for-the-next-recovery)
2. Verify the installer at the destination. Identify the deployed action and
   workspace, the role that can call it, the protected resolver that provides
   the retained release and original operation, and the actual target unit.
   Check the deployed Windmill action or owner interface and its original
   status lookup. Check effective privileges in the executing session. Morgan
   can use the deployed Foo root-job interface for authorized host readback
   when the checkout session cannot elevate. Carry the installer result for
   the exact release and the effective systemd readback into the existing control action for normal
   activation. [Windmill routing](windmill-workspace.md)
   [Normal commissioning](../windmill/foo-installer-workflow.md#commission-the-normal-stokowski-release)
3. Continue the first missing step with its existing owner. Use verified
   retained components and rebuild only the missing parts through the existing
   release route. If the original manifest is missing, qualify the complete
   build from known source with deterministic checks and retain the result.
   Prepare the protected installer and control inputs in parallel. If an action
   is unavailable, name the exact missing input or callable, its owner and
   the readback that will reopen it. Continue as soon as that evidence arrives.
   [Forward qualification](../windmill/foo-installer-workflow.md#fix-forward-when-a-retained-release-cannot-be-reused)

Use the same original after an uncertain result. Once the verified normal
release is active, routine service restarts use its recorded selection and
fresh runtime identity. They preserve the durable stop state and require no
repeat release approval.

Read the [BAU service contract](../operations/business-as-usual.md) for accepted
status terms and the [white-room recovery contract](../white-room/startup-and-recovery.md)
for original-operation recovery. Report the observed role, scope, exact original
receipt and remaining dependency without credentials. Report a missing mounted
scheduler or authenticated transition input as missing; do not claim installed
failover or unattended refill from this playbook.
