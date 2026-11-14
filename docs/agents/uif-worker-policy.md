# UIF shared worker policy

Policy ID: uif-worker-policy. Version: 2.0.0.
Maintenance source: maldous/uif, docs/agents/uif-worker-policy.md.

## Authority and distribution

This policy governs the selected UIF execution environment and its delegated
work. Current user directions, platform requirements, access restrictions and
project-specific contracts remain applicable. Preserve narrower project holds,
review, tests, data rights and publication controls. Reuse authorization for
routine continuation of the same scope; escalate actual decisions or missing
authority rather than inserting approval for each command.

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

The shared claim authority resides on Foo and tracks execution ownership only.
It must not duplicate issue intake, backlog, prioritization or acceptance.
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

Where this shared mechanism is not installed and qualified, state the gap and
keep cloud/Foo allocations explicitly disjoint under acknowledged owner
coordination. Never describe prose, advisory schema fields or an offline checker
as enforcement. If Foo authority is unreachable, pause new claims, transfers and
effects requiring ownership validation. Cloud owners may preserve their already
assigned work on permitted recovery branches; they may not use an expired or
unverifiable claim for late acceptance, shared integration or publication.

## Execution modes and token exhaustion

Quota exhaustion is the first supported migration trigger. Distinguish hard
allowance exhaustion from authentication failure, transient rate limiting,
network failure and tool failure. Unverified local allowance closes new local
model admission; it does not prove exhaustion or authorize repeated probes.
Use supported non-secret allowance evidence with observation time. A predicted
reset time is not restored availability.

### Full-token mode: local-ready

Stokowski performs admitted Foo-local issue pickup, coding, review, publication,
recovery and refill through the established harness. Morgan supervises, resolves
cross-project allocation conflicts and handles authorized session orchestration;
Morgan does not manually duplicate routine harness delivery. Fixed cloud roles
remain available for allocated independent work and deterministic MCP jobs.

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
claim-generation fencing and receiver acknowledgment. Require a fresh heartbeat
from the active Morgan coordinator before a new cloud claim or transfer; enter
cloud-active only after the supported transfer is acknowledged. Resume only the
first unaccepted stage. Foo-reserved issues retain their restriction; they do not
silently become cloud-eligible. Keep independent deterministic MCP work running
when its host, authority and resources remain available.

Morgan becomes the **active fallback coordinator** while local admission is
closed. Project roles continue their own issue delivery and dependency
coordination; Morgan resolves allocation conflicts and follows transition
receipts. This mode-specific responsibility is distinct from normal read-only
supervision. Record Morgan's current identity/epoch, heartbeat and successor
handoff in the existing coordination evidence; fence a replaced coordinator.

This version requires active Morgan for new cloud claims and transfers. Existing
cloud owners may preserve assigned work within current claims; they cannot
substitute their heartbeat for Morgan's coordinator role. It adds no unattended
wake-up, new recurring automation or promise that a chat survives. If Morgan is
unavailable, preserve state and report the missing active coordinator; do not
invent a scheduler or restart old watchdogs. If neither
reasoning route is available, continue only already-authorized deterministic
work and preserve recoverable state.

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

A stopped service is an independent control state; changing a mode does not start
or resume it. These mode names match the source contract, not proof that the
adapter or native/publisher hooks are installed.

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

Before the controlled full-token start, reconcile selected historical ledger
identities into native Linear and demonstrate the actual intake, claims,
review/publication and refill path. Record passed, failed, blocked, unknown and
not-run checks. Source-policy acceptance alone does not start a service, deploy
claim fencing, resolve ledger coverage or establish BAU. Use current supported
start/stop controls under the user's actual instruction; preserve existing
protected-action and project holds.

Validate sudden exhaustion during coding/submission/publication, duplicate
transition notices, lost acknowledgment, late stale-owner results, Morgan
replacement, Foo-authority loss, quota restoration with active cloud work,
Foo-only issues and shared-resource contention. Acceptance requires no duplicate
admission or effects, preserved recoverable work and useful execution, independent
review, publication and refill evidence. Live disruptive exercises require their
applicable authorization; focused tests do not prove deployed recovery.
