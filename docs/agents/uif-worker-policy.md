# UIF shared worker policy

Policy ID: uif-worker-policy. Version: 2.0.0.
Maintenance source: maldous/uif, docs/agents/uif-worker-policy.md.

## Authority and distribution

This policy governs the selected UIF execution environment and its delegated
work. Current user directions, platform requirements, access restrictions and
project-specific contracts remain applicable. Preserve narrower project holds,
review, tests, data rights and publication controls. Reuse authorization for
routine continuation of the same scope; escalate actual decisions or missing
authority rather than inserting approval for each command. The requested end
state is unattended, autonomous continuous BAU across local quota exhaustion,
Foo/MCP outages and recovery, independent of this conversation. Implement,
qualify and continue that authorized outcome through the agreed controls;
source preparation is not a separate completion or permission checkpoint.

UIF owns this policy and its [manifest](uif-worker-policy.json),
[environment profiles](environment-profiles.json), [capability inventory](skills-and-plugins.md)
and [memory rules](memory-routing.md). Load a reviewed pinned bundle through the
selected environment's startup instructions. Follow the [startup template](startup-template.md)
for delegation. A product clone does not need a sibling UIF checkout, network
fetch, UIF installation or maintenance-account binding to build or run.

Product AGENTS.md and nested instructions remain self-contained authorities for
product development. Environment policy governs execution of the selected lane;
it must not replace product contracts or become a product runtime dependency.
ANQR and CircularQuay have already removed maintenance-policy copies. Preserve
those accepted boundaries; reconcile CIX's existing removal candidate with its
owner. Retire obsolete active copies through reviewed owner changes, preserving
Git history and dated adoption records. Do not restore a removed copy merely to
satisfy an old consistency checker.

Publishing or checking this bundle does not install it into another environment,
activate services, acknowledge adoption or establish runtime enforcement. Record
source acceptance, environment installation, owner acknowledgment and observed
execution separately in the existing Linear coordination record.

## Fixed roles and executor boundaries

Retain five dedicated project roles: UIF, ANQR, OffMenu, CircularQuay and CIX.
Each has one coordinating owner and its own authorized cloud Git checkout,
CPU and storage, independent of Foo. CIX product and private operations are
separate repository surfaces of one role. The sixth general Cloud role has no
Git configuration or repository binding. Do not repurpose a role or create
additional durable cloud roles to obtain parallelism. Bounded assistant
subagents inside an existing role are distinct from durable cloud sessions.

Use the actual repository and current accepted source from the profile. Normally
use current develop, with the project's documented bootstrap exception when
needed. Preserve divergent, dirty, untracked and local-only work; create isolated
task branches/worktrees and keep the established sole integration writer. Return
an owner's available working checkout to current develop after integration only
when this preserves unrelated work and follows the project's flow.

Keep these execution surfaces distinct:

- **Cloud:** reasoning and native Git/build/test work in the role's isolated
  environment. Admission, quota, dependencies and actual activity are verified
  independently; a cloud label does not imply unlimited or separate quota.
- **Foo Codex:** admitted local model sessions with verified allowance, host and
  project prerequisites, task custody and resource admission. A running host or
  MCP connection is not evidence that local inference is available.
- **Foo MCP as user:** bounded host commands under the verified checkout owner,
  with project/worktree/source bindings and terminal operation receipts.
- **Foo MCP as root:** privileged transport for specifically authorized host
  operations. Repository Git, installs, builds and tests still run as the verified
  checkout owner. Root access grants neither unrelated effects nor session control.

Discover actual tool names, schemas, workspace, target host and granted scope.
Do not invent plugin IDs, MCP endpoints, arguments or Foo session-control tools.
The current generic Foo Workspace route and the fixed legacy root wrapper have
separate bindings; a workspace migration does not retarget a wrapper. Follow
[workspace guidance](windmill-workspace.md), revalidated against current metadata.
Missing capability pauses its dependent action while independent work continues.

## One intake and one execution owner

Native Linear is the sole live intake and work-coordination surface for this
operating model. Resolve workspace, project and immutable issue UUID; display
keys and imported source IDs are provenance. Reuse the existing issue for an
outcome rather than making issues for commands, messages, retries or subprocesses.
Historical ledgers, README snapshots and old source selectors are recovery
provenance, never an alternative dispatch queue. Supersede the 1.2.4
original-ledger-only intake extension for new admission. Preserve uncertain
legacy operations and their identities for reconciliation; migration does not
replay them or fabricate a Linear Done transition.

The shared control authority must remain available independently of Foo and
its MCP route. It owns coordinator election/leases and execution claims, never
issue intake, backlog, product prioritization or acceptance. Foo-local files and
locks cannot be the sole authority for cloud continuation during a Foo outage.
No storage provider, platform or endpoint is selected by this policy. The current
source/test implementation has no qualified off-Foo authority adapter; record
that implementation gap rather than presenting a local test store as deployed
outage resilience.
Bind each claim to the immutable Linear identity, project, lane, owner,
monotonically increasing claim generation, exact source and original operation.
Keep a task claim generation distinct from the coordinator's ownership epoch.
The supported authority must compare expected current ownership atomically for
claim, renewal, transfer, acknowledgment and completion. A Linear status/comment,
policy hash, ledger row or local file lock alone is not a cross-lane claim.

Before dispatch, shared integration, Linear transitions or publication, validate
current ownership/generation and applicable source/operation bindings at the
actual effect boundary. Fence obsolete owners before replacements can produce
conflicting effects. Checkpoints and completed receipts survive owner changes;
a new owner does not create a new operation ID to escape an uncertain result.
Keep one writer per shared mutation target and each project's existing
independent review and integration controls.

During implementation, retain disjoint acknowledged allocations until the
shared mechanism is installed and qualified. Never describe prose, advisory
schema fields or an offline checker as enforcement. In normal BAU, a Foo/MCP
outage does not stop cloud intake, claims, execution, review, integration,
publication or refill that use the reachable independent authority and actually
available project dependencies. Hold only units/effects that require the missing
Foo capability or have unresolved original outcomes. An authority outage itself
holds new claims/transfers and effects needing current ownership validation;
preserve assigned work and automatically resume after authority recovery. Never
use an expired or unverifiable claim for late effects.

## Durable coordinator and project lifecycle

Morgan is a stable logical coordinator role, not this chat or an immortal model
process. A durably scheduled or event-triggered lifecycle controller, independent
of Foo and this conversation, must ensure the configured Morgan execution and
project roles are available. Extend the established control lifecycle; do not
create a second intake scheduler, competing project owner or parallel status
ledger. The source schedule/trigger, authenticated session-control adapter and
off-Foo authority need qualification before uninterrupted BAU can be claimed.

The configured lifecycle identity atomically appoints a replacement when the
current coordinator is unavailable under the agreed conditions. Use the shared
authority's time/lease evidence, expected current epoch, qualified liveness probes
and original appointment operation; fence the obsolete execution. A missing
heartbeat alone is not proof all old operations stopped. Reconstruct the same
role from current Linear/GitHub, latest compatible LangGraph state and original
receipts, then acknowledge the new coordinator epoch before effects. Keep task
generations and coordinator epochs separate; coordinator replacement never
redispatches every issue or renews a stale task claim.

Project owners independently select and deliver eligible native Linear issues
within their assigned scope, coordinate dependencies and preserve one integration
writer. The lifecycle replaces failed executions of those same roles through
supported controls without repurposing a project binding or increasing the six
durable roles. Reconcile previous attempts, checkpoints and uncertain effects;
continue the first unaccepted stage. Perform routine recovery and self-healing
within the existing authorized scope and permissions, with bounded backoff and
automatic re-observation; do not wait for another user prompt for each step.
Explicit user stop/pause remains durable and is never overridden by self-healing.

For Foo/MCP reachability failures, default automatic ownership-changing failover
to a configurable 15-minute sustained-failure grace with at least two qualified
probes. A successful qualified probe resets the grace; separated short glitches
cannot accumulate into a takeover. During grace, stop unsafe new admission to
the uncertain route, reconcile/retry the same original operation when safe and
continue independent work. An uncertain response or timeout never authorizes a
new duplicate operation. Confirmed hard quota exhaustion closes dependent local
model admission immediately; it is classified separately from reachability loss,
authentication failure, transient rate limits and task failure.

## Execution modes and token exhaustion

Required migration triggers include confirmed local allowance exhaustion and
qualified sustained Foo/MCP outage under the grace/probe policy above. This is
the normal operating requirement, not a claim of installed trigger adapters.
Distinguish hard exhaustion, authentication failure, transient rate limiting,
network failure and tool failure. Unverified local allowance closes new local
model admission; it does not prove exhaustion or authorize repeated probes.
Use supported non-secret allowance evidence with observation time. A predicted
reset time is not restored availability.

### Full-token mode: local-ready

Stokowski performs admitted Foo-local issue pickup, coding, review, publication,
recovery and refill through the established harness. Morgan supervises, resolves
cross-project allocation conflicts and handles authorized session orchestration;
Morgan does not manually duplicate routine harness delivery. Its durable
lifecycle remains available for recovery and cross-project coordination without
requiring this conversation. Fixed cloud roles continue eligible independently
owned work and deterministic MCP jobs.

The Foo Codex ceiling is **64 concurrent local sessions**, subject to actual
provider admission and the shared CPU, RAM, disk, I/O and process budget. It is
not 64 sessions plus an unlimited deterministic-job pool, and it is not an
instruction to fill every slot. MCP builds/tests and local sessions share host
resource accounting. Preserve project isolation, dependencies and disjoint writes;
measure useful throughput before increasing admission.

### Quota handoff: transfer-pending to cloud-active

On confirmed local exhaustion, enter transfer-pending and close new Foo model
admission through supported controls. Capture surviving work deterministically: source/branch/HEAD, dirty
work custody, claim generation, completed/pending/unknown operations and
checkpoints. A model-generated handoff summary must not be required after its
allowance has failed. A missing session is not evidence its child operations
stopped.

Reconcile running and uncertain effects under their original identities, then
transfer eligible issues exclusively to the corresponding cloud roles with
claim-generation fencing and receiver acknowledgment. Require the current
coordinator lease/epoch at the effect boundary; the durable lifecycle appoints
and reconstructs Morgan when necessary rather than depending on an existing
chat. Enter cloud-active after supported transfer acknowledgment. Resume only
the first unaccepted stage. Foo-reserved issues retain their actual restriction;
they do not silently become cloud-eligible. Keep independent deterministic work
running where its own executor, authority and resources remain available.

Morgan becomes the **active fallback coordinator** while local admission is
closed. Project roles continue their own issue delivery and dependency
coordination; Morgan resolves allocation conflicts and follows transition
receipts. This mode-specific responsibility is distinct from normal read-only
supervision. Record Morgan's current identity/epoch, heartbeat and successor
handoff in the existing coordination evidence; fence a replaced coordinator.

A valid coordinator epoch remains necessary; the lifecycle must restore it
automatically through the qualified appointment/reconstruction boundary. If all
reasoning capacity is unavailable, preserve work and continue independent
authorized deterministic operations. Observe the existing approved routes with
bounded backoff and resume reasoning automatically when current evidence passes
admission. Use existing authorized capacity only: no new provider, paid API
fallback, account or spending is implied. Avoid model calls for deterministic
liveness, allowance collection or scheduling decisions.

### Gradual return: local-recovery to local-ready

Enter local-recovery after supported fresh allowance evidence and applicable Foo
white-room checks; use one bounded canary within the existing entitlement before expanding local
admission. Authentication/network/tool failures retain their separate diagnosis.
Reconcile accepted cloud changes, existing claims and pending effects before
local work is selected; never revive an old session solely because quota reset.

Return automatically **per issue**, using the same supported admission and
ownership controls. Cloud-owned units remain with their owner until completion
or reconciled failure; admit independent eligible local work without waiting
for every cloud unit or stealing its claim. Increase admission gradually within
measured shared resources, up to the 64-session ceiling. A new exhaustion event
closes dependent local admission again without cancelling independent cloud work.
When local readiness is established, return to local-ready: Stokowski resumes
normal local orchestration and Morgan returns to supervision; existing cloud claims remain valid until
normally completed or transferred.

A user-stopped service is an independent durable control state; quota or host
recovery cannot override it. Otherwise the lifecycle automatically restores the
requested running state through supported controls after applicable checks.
These mode names describe the required contract, not proof that the off-Foo
adapter, schedule, session controls or native/publisher hooks are installed.

## White-room reconstruction at every transition

Reuse the existing [white-room procedure](../white-room/README.md),
[read-only comparison](../../tools/white_room/check.py) and
[remote evidence adapter](../../tools/white_room/remote_evidence.py), with its
[bounded UIF/cloud contract](../white-room/remote-evidence.md). The retained adapter's default target remains UIF/cloud. The source extension
accepts a reviewed ProjectScope with exact repository/ref/project/executor and
native Linear UUID bindings; it does not discover those bindings from task text.
[transition.WhiteRoomVerifier and CheckpointRead](../../tools/white_room/transition.py)
compose authenticated reader, declared comparison and current owner-scoped
persistence checks at the supported ownership boundary. These are source
integration contracts, not installed native/publisher hooks or deployed recovery.
The comparator checks supplied evidence and always reports execution_authorized
and runtime_readiness_verified false. No tool exit code grants admission,
performs a transfer or starts a service.

For local-ready, cloud-active and replacement-coordinator recovery, reconstruct
current GitHub accepted source/PR ancestry, native Linear scope/dependencies,
authoritative ownership and original operation receipts. Inventory retained
work before choosing a separate clean destination checkout. Match actual
deployment/configuration against the destination's current declared contract;
record drift, missing dependencies and incomplete discovery explicitly. Apply
Foo's host/service/toolchain/storage checks only to its selected work and cloud's
lighter isolated checks to cloud work, adding Foo dependencies only where used.
Quota reset is never a white-room pass.

Reuse accepted source, checks and operation outcomes at their exact still-valid
bindings; do not replay completed work to relabel its lane. Reconcile retained
LangChain/LangGraph persistence where selected, including code/schema/source,
issue, stage, checkpoint disposition, owner/epoch and operation compatibility.
Preserve superseded work and provenance; unknown absence is not permission to
clean or restart. The destination must authenticate applicable evidence and
obtain current fenced admission separately from these read-only comparisons.

## Minimum useful routing and durable handoff

Choose the smallest suitable route. Prefer available authorized deterministic
MCP or native commands for reads, builds and checks; open a local model session
when reasoning or independent parallelism warrants it. MCP is execution, not a
reasoning provider and not a way to bypass denied access or exhausted inference.
Prefer GPT-6.1 Sol Medium or another appropriate economical model where the
supported interface permits selection. Record a concrete reason for higher
capability/effort and distinguish requested from observed settings.

Pass children the exact issue/task, role, repository/source, owned paths,
policy version/hash, project instructions, authorization/holds, executor/resource
budget, current claim and original pending operations, required checks and
acceptance. Parent filesystem paths are not transfers. Load only relevant
skills/references; do not copy full conversation histories or unrelated projects.

At recoverable boundaries publish permitted source/tests/docs to the correct
branch and verify remote readback. Preserve secrets, private runtime receipts,
raw datasets and customer material only in their authorized custody. A local
commit is not a durable remote checkpoint. Reconcile LangChain/LangGraph state
where actually used against current source/schema, claim, issue, stage and
operation receipts; do not replay stale or incompatible checkpoints.

Report verified events promptly. Record durable scope/owner changes, source,
checks/review, material blockers, uncertain effects and next action in the
existing Linear issue; retain detailed execution receipts in their existing
private store. No new status ledger or general cross-project memory is created.
Distinguish prepared, published, merged, installed, acknowledged and observed
execution. A running process is not useful autonomous delivery.

## Adoption and readiness

UIF owns shared-infrastructure corrections; product owners retain source,
acceptance and project-specific capabilities. UIF and each product remain
independent build/runtime surfaces; an optional shared execution lane has its
own prerequisites only when selected. Preserve data, historical evidence and
custody while resolving genuine dependency drift.

Complete the requested restoration and controlled start without introducing a
separate approval checkpoint for this same authorized outcome. Reconcile selected
historical ledger identities into native Linear and qualify actual intake,
independent authority/election, claims, review/publication, recovery and refill.
Record passed, failed, blocked, unknown and not-run checks. Source-policy acceptance
alone does not install a service, resolve coverage or establish BAU; continue the
necessary integration, supported activation and destination readback under the
current authorization and applicable platform requirements. Preserve specific
project/data holds and stop only their affected work. Completion requires observed
continuous useful delivery and recovery, not another plan or policy publication.

Validate sudden exhaustion during coding/submission/publication, duplicate
transition notices, lost acknowledgment, late stale-owner results, Morgan
replacement with the original chat absent, sustained Foo/MCP outage and recovery,
authority loss, all-inference-unavailable preservation/automatic resumption,
quota restoration with active cloud work, Foo-only issues, grace/probe resets and
shared-resource contention. Acceptance requires no duplicate
admission or effects, preserved recoverable work and useful execution, independent
review, publication and refill evidence. Live disruptive exercises require their
applicable authorization; focused tests do not prove deployed recovery.
