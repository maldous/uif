# UIF agent instructions

## Scope and operating boundary

UIF covers shared physical and virtual infrastructure, cross-project Linear integration, and the full harness / full durable delivery methodology. Keep UIF-platform, OffMenu, CircularQuay, and other projects' repositories, issue ownership, runtime state, and data boundaries distinct.

These are required startup, recovery, and validation behaviours, not evidence that the mechanisms are already implemented or deployed. Record implementation gaps and blocked or unknown checks explicitly. Documentation publication does not start services, recover jobs, modify databases, or establish operational readiness. Respect current user authorization, access restrictions, and mandatory platform confirmations; never use an alternate route to bypass a denied action.

Read the current repository instructions and applicable project contracts before acting. Preserve the README reconciliation ledger and its custody restrictions. Historical paths, branches, receipts, and checkpoints are evidence to revalidate, not automatic instructions to resume work or remove data.

## Two separate execution domains

- **Foo local durable execution:** depends on Foo's actual OS, local repositories and worktrees, shared storage, services, runners, credentials, available Codex quota, and orchestration state. Startup requires the exhaustive Foo checks below within discovered and authorized scope.
- **Cloud durable execution:** each worker and coordinator has an isolated Git environment and its own dependencies. Reconstruct it from current remote GitHub and Linear. Do not assume it shares Foo's filesystem, databases, processes, checkout, or credentials, or another cloud worker's local files.
- During local Codex quota exhaustion, independently available and authorized cloud durable agents continue through their isolated GitHub/Linear environments. Their accepted work can supersede old Foo work. A local quota reset is only one prerequisite for Foo readiness, not permission to replay an old session.
- Planned shutdowns and unplanned outages can terminate cloud workers and coordinators as well as Foo processes. “Durable” means recoverable published state and reconciled ownership, not process immortality or guaranteed preservation of local files.

## White-room startup and recovery: all environments

Use this sequence on fresh startup, restart, quota restoration, reassignment, and recovery after either planned or unplanned interruption.

1. **Establish the current target and owner.** Resolve the service instance, executor, project, repository, current issue, and supported control interfaces from current authoritative records. Do not infer identity from a workspace name or old session.
2. **Inventory before replay or cleanup.** Use permitted access to identify surviving workers, queued operations, leases, worktrees, dirty/untracked files, local-only commits, and persisted orchestration records. Preserve identifiable recoverable work and its provenance in approved custody. Do not reset, clean, delete, overwrite, force-push, or kill unrelated work to manufacture a clean state.
3. **Reconstruct from remote GitHub and Linear.** Read current remote refs, accepted commits, PR/merge outcomes, current Linear status, scope, dependencies, ownership, and linked operation evidence. Git records published code; Linear records current work and coordination. Resolve discrepancies explicitly; neither an old local HEAD nor an old issue status alone proves current execution state.
4. **Establish a clean current working baseline.** Fetch and synchronize the current project `develop` according to its established Git flow without overwriting divergent or recoverable work. If `develop` is unavailable or conflicts with the project's documented flow, resolve and record the actual integration baseline rather than silently substituting a branch. Allocate a clean checkout/worktree on the current appropriate branch only after inspecting current Linear work. Retained old worktrees are recovery evidence, not default execution targets.
5. **Reconcile durable orchestration state.** Where LangChain/LangGraph persistence is used or required, compare checkpoint/run/thread identifiers, graph/schema/code versions, issue and branch/commit pins, completed and pending steps, operation receipts, retry/idempotency keys, and execution ownership with current GitHub/Linear and live runtime evidence. A checkpoint does not authorize stale replay. Explicitly resume, migrate, supersede, or retain it for recovery only when compatible with current accepted state.
6. **Resolve ownership before admission.** Check whether Foo or cloud work has already completed, replaced, or currently owns the task. Do not infer an expired lease solely from silence. Use the supported ownership/lease mechanism to reconcile expiry, reassignment, and stale owners; fence superseded execution before admitting a replacement. Prevent duplicate intake, conflicting edits, duplicate external effects, and late publication from an obsolete owner. If ownership or an earlier operation outcome is uncertain, investigate that operation before retrying it.
7. **Validate the environment-specific prerequisites.** Complete Foo's exhaustive checklist or the cloud checklist below, recording evidence and exact blocked/unknown checks. Allow independent authorized work to continue; do not make Foo-only dependencies a global gate for cloud work.
8. **Resume only current work through the harness.** Admit the current selected task through supported controls after its applicable checks and ownership reconciliation. Do not blindly reconnect old sessions, replay queued tool calls, or manually duplicate harness dispatch. After BAU restoration the harness owns delivery and recovery; supervision observes and reports unless separate engineering or repair work is authorized.

A white room is a clean execution context built from current accepted state. It is not deletion of historical evidence or a claim that all previous work disappeared.

## Foo startup: exhaustive dependency discovery and validation

Discover actual dependencies for the service and each enabled project. Check all discovered requirements relevant to the proposed execution, not merely whether a process is alive. Record unavailable, inaccessible, failed, and unknown items separately. Discovery and permitted read-only validation can be prepared during downtime; this is not authorization to launch, restart, reconfigure, or repair services.

- **OS and host identity:** confirm the intended Foo host, execution account, OS/toolchain compatibility, required utilities, network/name resolution, clock, required mounts/paths, and authorized access. Do not read secrets or change security settings as a shortcut.
- **Storage and capacity:** verify disk space and inodes, writable workspace locations, required mount health, memory/CPU and process capacity, and the limits governing concurrent jobs. Preserve shared data and custody locations.
- **Repositories and worktrees:** verify repository/remotes, current `develop`, branch/commit pins, worktree registration, dirty/local-only work, ownership and compatibility aliases, and clean task-specific worktrees. Respect the README's item-specific custody requirements; startup does not authorize a new cleanup campaign.
- **Shared local data and services:** discover dependencies outside repositories, their consumers, owning projects, access modes, endpoints, versions/schema expectations, and health. `offmenu-data`, possible project/customer databases, mocks, UIF workspaces, and ANQR development resources are discovery examples, not assertions that a particular resource exists, is required, or is healthy. Verify actual bindings through permitted metadata and health surfaces; do not inspect database bodies or publish raw datasets merely to establish readiness.
- **Stokowski:** establish its actual role, supported control/status interface, deployed revision, health, ownership, and dependencies where it participates in execution. Perform restoration/start actions only when authorized; document missing interfaces or evidence rather than inventing commands.
- **Windmill and root MCP:** verify the applicable Windmill workspace, MCP connectivity, supported root-runner interface, granted scope, runner/worker health, operation receipts, and actual target host. A tool connection or submitted job is not proof that a host operation completed.
- **Local Codex prerequisites:** verify supported quota/availability evidence, account/session readiness without exposing credentials, runtime/tool versions, configuration, required skills/tools, and access. Exhausted quota blocks dependent local execution; restored quota alone does not satisfy the other checks.
- **MCP and external services:** verify required GitHub, Linear, and other project-specific MCP connections, permissions, and bounded read/health operations. Check actual required access, not merely a configured connector name.
- **Git application services and runners:** discover and verify the Git application integrations, delivery/CI runners, webhook or polling dependencies, repository scope, runtime versions, queue state, and publication/check interfaces used by the project.
- **Project dependencies:** verify lockfiles/toolchains, build/test prerequisites, required local services and data contracts, startup order, and relevant health checks for each admitted project.
- **Persisted orchestration and concurrency:** reconcile UIF, Stokowski, Windmill, and LangChain/LangGraph state where applicable, including queued/running jobs, checkpoints, operation outcomes, leases and owners. Confirm supported concurrency, dependency ordering, resource headroom, project isolation, and duplicate-admission protection before refill.

An exhaustive checklist covers discovered dependencies within authorized scope. Inaccessible dependencies remain explicit unknowns and cannot be reported as passed. Do not assert end-to-end readiness from a healthy process, quota balance, or successful focused test alone.

## Cloud startup: isolated, lighter gates

For every cloud worker and coordinator:

1. Establish its own authorized environment, repository identity, remote access, and current GitHub/Linear connectivity.
2. Fetch current remote state and create a clean current project Git-flow checkout and task branch. Re-read Linear scope, dependencies, owner, and accepted PR outcomes; do not inherit a stale local view from another worker.
3. Recover only published commits/artifacts and accessible approved persistence. Reconcile LangChain/LangGraph checkpoints, prior operation receipts, task ownership, reassignment, and supersession before admission.
4. Validate the toolchain, credentials/access, build/test dependencies, storage/capacity, and concurrency actually needed by that isolated task.
5. Require Foo-only services or shared data only when the task demonstrably depends on them and access is authorized. Otherwise keep independent cloud work running. Report a missing dependency rather than pretending Foo storage is mounted or copying protected data.
6. Admit work with current ownership and idempotency protections. Repeat this reconstruction for a replacement coordinator as well as a replacement worker.

Cloud gates are deliberately distinct from Foo's exhaustive host validation; they do not waive project-specific correctness or security requirements.

## Checkpointing, loss limits, and handoff

- At appropriate recoverable boundaries, commit and push permitted source, tests, and documentation to the correct recovery/work branch; record the exact repository, branch and commit plus relevant PR, Linear issue/owner, checkpoint and operation status. Verify remote readback before calling a checkpoint published.
- Keep credentials, secrets, raw datasets, and protected custody material out of source publication. Preserve those only through their separately authorized storage mechanisms.
- A local commit is not a remote checkpoint. Uncommitted or unpushed work may be lost when a process, host, or ephemeral environment disappears. Report these limits honestly; do not claim recovery or absence of work without evidence.
- Before a planned shutdown, use supported graceful quiesce/drain and checkpoint procedures where authorized. Record in-flight operations and ownership for the successor. After an unplanned interruption, establish actual operation outcomes before replaying side effects.
- On cloud-to-Foo or Foo-to-cloud handoff, publish a current state record with accepted commits, outstanding work, branch/issue ownership, completed and uncertain operations, persistence compatibility, and remaining dependency blockers. The receiving executor must independently reconcile it against current remote and live evidence.
- Retain recoverable superseded work with provenance for comparison and disposition. Supersession is not permission to delete or merge it blindly.

## Recovery validation and reporting

Validate the implemented procedure, under the applicable authorization, against:

- Planned Foo reboot/shutdown and unplanned Foo interruption.
- Foo quota exhaustion while cloud work continues, followed by quota restoration after cloud work has superseded the old Foo task.
- Planned and unplanned cloud worker termination and coordinator termination, with isolated reconstruction and ownership reassignment.
- Cloud-to-Foo and Foo-to-cloud handoff with current remote Git/Linear and persisted LangChain/LangGraph evidence.
- Dirty/local-only recoverable work, unavailable dependencies, uncertain external-operation outcomes, stale checkpoints, incompatible code/schema versions, and conflicting or obsolete owners.

Demonstrate preservation of recoverable work, use of current accepted state, absence of duplicate admission/stale replay, correct ownership transfer, and applicable dependency validation. Explicitly distinguish passed, failed, blocked, unknown, and not-run checks. Record observed remote commits and operation outcomes separately from intended actions.

Do not simulate or perform disruptive recovery tests against live services without authorization. Documentation, a proposed test plan, or focused unit tests are not proof of deployed recovery. Report remaining implementation and validation work without claiming it already exists.
