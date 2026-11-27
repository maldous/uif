# Morgan peer transition playbook

Use this when Morgan is appointed to oversee a UIF peer transition. It guides the
agent's decisions; it does not appoint Morgan, run a scheduler, or authorize a
state change. Stokowski detects and handles native runtime transitions through
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

## Transition decisions

| Trigger | Evidence to use | Morgan action or hold |
| --- | --- | --- |
| Normal run or observation | Verify the actual runtime identity and shared authority, current Linear scope and live claim/epoch. Distinguish current reads from retained receipts. | Keep the currently assigned owner. Eligible work on a disjoint scope may proceed through its existing scheduler and admissions; availability alone never transfers an issue. |
| Foo or MCP degraded | Use the existing sustained-failure policy and qualified probes. Check whether the affected scope depends on Foo, and whether the independently reachable authority is current. | Hold unsafe dependent work; preserve state, retain the existing claim and continue independent work. Timeout, quota reset or a missing session is not a transfer. |
| Transfer to Morgan or back to Stokowski | Reconcile original effects. Check exact owner session, current lease and coordinator epoch, independently authenticated control attestation of exact successor, quiescence and reconstruction, plus the qualified successor's durable assignment/workspace. | Use the supported current-owner handoff only when those existing inputs are present. Verify returned owner, generation, lease ID and epoch before acting on that issue. An unavailable original owner or missing evidence stays held; keep unrelated work moving. |
| Lost acknowledgment, interruption or stale fence | Read the same original with its existing identity; compare retained facts and the current fence. A successful old receipt can coexist with a newer fence. | Never mint a second operation to recover a lost result. Keep ambiguous effects held; stale owners do not write. Do not infer successful recovery from source or fixture tests. |
| Completion, refill or return to health | Observe exact terminal outcome and authenticated completion through the existing effect and publisher boundaries; verify current owner, scope and admission for the next eligible Linear issue. | Let the appointed scheduler refill. Do not issue another claim for held work, take from the other healthy peer, or substitute a new publisher. Restore capacity for eligible scopes without moving an active claim back merely because its old peer returned. |
| Explicit owner pause or stop | Resolve the scope and current durable control state before any work; treat a shared stop as global where the authority defines it so. | Honor it across recovery and reboot. Read-only status and reconciliation do not lift it; unrelated work proceeds only outside the scope of the stop. |

## Keep a blocked restoration moving

For a missing or mismatched release artifact, hand off directly to
[clean forward qualification](../windmill/foo-installer-workflow.md#fix-forward-when-a-retained-release-cannot-be-reused).
Keep package preparation, protected installation preparation and control
reconciliation moving under their existing owners. For each held lane,
record the exact missing input, owner action and evidence that will reopen it.
If the owner is unknown, mark ownership unverified and identify the existing
route that can confirm it. Do not implicitly hand off the work.
Status checks do not advance a lane. When the evidence arrives, resume the
existing operation and continue to end-to-end acceptance without replaying a
lost effect, restarting a held service, or transferring a claim.

Read the [BAU service contract](../operations/business-as-usual.md) for accepted
status terms and the [white-room recovery contract](../white-room/startup-and-recovery.md)
for original-operation recovery. Report the observed role, scope, exact original
receipt and remaining dependency without credentials. Report a missing mounted
scheduler or authenticated transition input as missing; do not claim installed
failover or unattended refill from this playbook.
