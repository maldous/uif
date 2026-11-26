# Business-as-usual service contract

**BAU is continuous unattended delivery, not a session that stays open.** Morgan
and Stokowski must keep working together indefinitely without the initiating
Dot, an open chat, manual monitoring, or someone stepping in to resolve normal
operational events. Use the [operating procedure](../operating-procedure.md),
[worker policy](../agents/uif-worker-policy.md), and
[White Room recovery contract](../white-room/startup-and-recovery.md) for
implementation, evidence and the applicable authorization boundaries.

## Normal operating behavior

- **Morgan** coordinates disjoint collaborative subtasks in parallel, within
  verified capacity and current project scope. Children have explicit
  ownership and acceptance checks. Durable scheduling, reconstruction and
  authenticated session control keep the logical coordinator and its project
  roles functioning when a session, Dot or chat ends.
- **Stokowski** keeps admitting and processing eligible work through verified
  no-incremental-cost model routes. It monitors or evaluates OpenAI, Codex,
  Cloudflare, Antigravity, OpenCode, OpenRouter and other permitted candidates as
  their availability changes. OpenCode is a client: its upstream account and
  model also need proof. Listing a candidate is not permission to configure,
  enable or spend on it. Follow the [pool selection contract](provider-pool-selection.md)
  and current route and account controls.
- **Shared coordination** uses native Linear as the sole intake and status
  system. One issue has one active writer at a time. Both runtimes use the
  same durable election and claim authority. Bind the original claim to the
  immutable Linear issue ID and its repository/workspace/project scope; pin
  the source for each admitted generation. Enforce the current owner, epoch
  and generation at every shared effect. Reconcile the same original
  operation and receipt before a retry or transfer; stale ownership cannot
  write. Acknowledgment alone is not a claim. Independent review, existing
  publisher custody and required project checks still apply.
- **Closed-loop delivery** means automatic intake, claim, execution, required
  checks and independent review, authorized publication and destination
  readback, durable terminal outcome, and automatic refill. Progress and the
  last accepted useful results survive disconnects and replacement. Record
  the current owner, pinned scope and source, compatible checkpoint,
  unresolved operations and next eligible step privately in their established
  systems; do not create a second queue.

## No paid fallback; automatic recovery

Before each model dispatch, verify the exact approved provider/client/account,
model and applicable time windows, current availability, conservative
remaining included capacity after pending liabilities, and that overage or
upstream paid fallback cannot occur. Reserve the bounded usage atomically at
the shared authority; one account does not get separate pools per route.
A free label, reassessment time or sample with no rows is not quota evidence.

On confirmation of exhaustion, or when cost, authentication, account scope,
capacity, or a prior effect is uncertain, automatically pause the affected
route or operation. Unaffected eligible work continues. If no inference route
qualifies, preserve work, continue independent authorized deterministic steps,
and automatically recover and resume after fresh evidence and required checks.
An alternate route is eligible only if that exact route independently passes
the existing admission and atomic reservation. A client must not silently
retry on an unknown original, change providers or models behind the fence, or
fall back to metered or prepaid spend. Never open an account or add a provider
grant to maintain uptime. Preserve every explicit stop or scope hold. A
sustained Foo/MCP outage must not stop eligible independent cloud work. If
the shared authority is unavailable, hold dependent effects rather than
splitting ownership or bypassing the authority.

Durable runtimes maintain their own approved credentials before expiry.
Renew the same independently scoped identities through the existing approved
secret stores, verify role and epoch from the same runtime, and hold only the
affected actions if renewal or verification fails. A renewal never changes
claim ownership or clears a stop. [UIF-1315](https://linear.app/maldous/issue/UIF-1315)
tracks unattended identity renewal; do not report it as verified from an
administrative refresh alone.

## Acceptance and present rollout scope

Demonstrate in the authorized target, with durable original receipts and readback:

1. With the initiating Dot/chat absent, Stokowski and Morgan work together;
   Morgan's disjoint collaborative subtasks execute concurrently without
   duplicate claims or conflicting writes.
2. An eligible issue proceeds unattended from native Linear admission to
   verified publication (or its authorized terminal outcome), and the service
   picks up the next eligible issue automatically. Persist and recover the last
   accepted result without replaying it.
3. An eligible free route is admitted with account-wide evidence; loss of
   capacity or unverifiable cost closes affected dispatch, never starts paid
   fallback, and triggers autonomous pause/recovery/resume. Verify there is no
   paid inference or overage under the exercised failure paths.
4. A sustained Foo/MCP outage and coordinator replacement preserve eligible
   independent work and old operation identities. Show fenced recovery,
   durable checkpoint readback and automatic return to useful operation.
   Verify that explicit stops and project holds survive recovery.

OffMenu is the first rollout project. Circular Quay remains on its existing
hold; this contract does not resume it or extend scope to other projects.
Documentation, passing offline tests, a merged PR, provider discovery, a
started process, or a single successful job do not establish unattended BAU.
This definition does not authorize activation, installation, new credentials,
provider enablement, expenditure, or changes to existing grants and pauses.

## Release handoff and status

OffMenu's procedure lives in the
[OffMenu Foo release and operating handoff](https://github.com/maldous/offmenu/blob/develop/docs/development/native-runtime-release-runbook.md).
Retain the reviewed exact release and source revision, project scope and original
operation through its existing authorized installation transport. Read back the
actual selected unit/stage, installed revision and role before describing the
release as running normally. Publication remains a separate verified destination
effect under the existing independent publisher; refill and recovery must
reconcile that same original. If a result is lost, observe the existing
original rather than creating another installation, claim or publication.

Use the narrowest status that the evidence supports:

| Status | What the evidence establishes |
| --- | --- |
| Prepared / reviewed | Exact source or detached artifact is ready; no host adoption or execution implied. |
| Staged | The named exact revision is installed alongside or in a protected stage, but has not been verified as the selected running revision. |
| Supervised idle | The service answered as the guarded, no-dispatch mode; no normal intake, provider calls or publication implied. |
| Normal runtime verified | The installed normal revision and authenticated role are verified in the authorized runtime; unattended delivery is not yet demonstrated. |
| BAU verified | The acceptance checks above are demonstrated by real original receipts and destination readback, including independent publication, refill and recovery. |
| Held / unverified | State the affected scope and missing or failed proof. Do not infer success from a timeout, queued job or missing receipt. |

For a reported transition, record only verified original IDs/receipts, exact
installed revision and scope, the observed state, and any next unresolved step.
Keep secrets, raw credentials, protected selector content and private outputs
out of repository documentation and shared reports.

The off-Foo authority and a Foo native readback have been observed, and a
guarded idle run has been verified. Those observations do not establish normal
OffMenu dispatch, a live durable Morgan coordinator binding, authority-side
route and quota readiness, or unattended publication and refill. Track live
changes and exact evidence in [UIF-1313](https://linear.app/maldous/issue/UIF-1313).
