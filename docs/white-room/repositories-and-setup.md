# Repository, mount and capability setup verification

Apply this checklist on the **actual worker or coordinator** before its selected
operation, including startup, replacement and recovery. Read the current
[startup/recovery contract](startup-and-recovery.md), selected policy/profile and
[environment alignment evidence matrix](../../.agents/skills/uif-environment-alignment/references/evidence.md).
Use the existing issue and original receipt store for findings; this document is
not a new queue, report schema, claim authority or automatic setup program.

Verify required setup rather than assuming a saved environment, parent session,
shared directory or published skill has been mounted into this caller. Source
preparation, build/test, publication and ownership transitions have different
prerequisites. Preserve user pauses and narrower source/data restrictions; keep
unrelated eligible work independent.

## 1. Identify the actual environment and admission outcome

Record the logical project role, actual task/executor, selected environment,
working directory, relevant workspace roots and next operation. Distinguish a
dedicated configured project environment from the Dot's common cloud computer,
Foo, a shared scratch checkout and a reconstructed source snapshot. An available
computer is not proof this task is attached to its intended configuration.

Read the original task/environment admission result. Separate task creation,
context/compaction preparation, container startup, repository checkout, setup
completion and a successful command in the destination. If admission or compact
preparation fails with a usage limit, record that failure: no destination
readiness or running child follows from the submission. Do not repeat a failed
restart when the current direction forbids retries. For an uncertain outcome,
read the original task/operation before submitting a replacement.

Discover supported environment selection and configuration tools. Attaching an
existing computer, provisioning a new configured task and changing a saved
configuration are different capabilities. If same-task rebind is unavailable,
report that exact gap. Do not silently replace the project owner, create a
duplicate durable role, or describe a new shared checkout as an isolated mounted
environment. A separately authorized shared-workspace unit can proceed with
that limitation explicit.

## 2. Reconcile repositories and the working baseline

For every repository actually needed by the unit, verify:

* Canonical repository identity and expected mount path from the selected
  configuration, plus the **actual** resolved Git root and common directory.
  A directory name, `.git` placeholder, export or old tracking ref is insufficient.
* Saved configuration ref versus actual checked-out branch, full commit/tree,
  current authenticated remote ref and accepted PR/merge outcome. Record drift
  independently; a fresh connector read does not update the checkout or prove
  native Git authentication.
* The normal current `develop` baseline and documented project exceptions.
  Revalidate exceptions from current owning records, including CircularQuay's
  selected bootstrap source and Cloud infrastructure's `main` baseline. CIX
  public product and private operations remain separate repositories. Do not
  silently use saved `main` for a project requiring `develop`.
* Root and applicable nested instructions, repository-defined setup, language
  pins/lockfiles, build/test/release contracts and any real sibling repositories.
  Do not import a sibling runtime or assume five roles means five repositories.
* Existing branches/worktrees, owners, dirty/untracked/ignored material and
  local-only commits within permitted inspection. Preserve recoverable work and
  refs; use a clean isolated task branch/worktree when needed, without resetting,
  cleaning, overwriting or taking another writer's workspace.

Use the existing [inventory helper](README.md#non-destructive-sequence) within its
limits: it reports dirtiness as unknown and does not establish all worktree
custody. Avoid executing repository filters/hooks or exposing embedded remote
credentials just to collect metadata. A snapshot, shallow repository,
authenticated clone, local commit capability, native push and connected-app
publication are separate facts. Bind the available source/acquisition route and
limitations before editing; do not claim an unsupported route ran.

## 3. Verify shared mounts and resource boundaries

Discover only mounts/storage used by the selected operation. For each, record
the declaration and actual resolved path, owning environment/project, intended
read/write mode, permitted access and current consumers. Verify relevant
mount/volume identity and availability through supported metadata, not just a
matching pathname. A path received from another task is not a file transfer.

Determine whether a resource is genuinely shared between these executors or
local to one container/host. Do not assume Foo storage, another cloud task's
checkout, `/workspace/shared` or a retained artifact exists in a newly provisioned
container. Materialize authorized transfers through the supported route and
verify identity/readability on the consumer. Never copy protected data to
manufacture readiness.

Separate shared Git object storage from worktree ownership, and source from
mutable caches, dependency environments, test data, build outputs, ports and
private receipt/checkpoint stores. Record isolation or safe-sharing, sole writers,
capacity/admission and recovery responsibilities where required. Read-only
metadata is the first check; necessary disposable write/access probes must be
authorized and confined to their own scratch location, not live data or another
worker's files. Unknown access or unproven isolation stays explicit.

## 4. Verify plugins, skills and setup in this caller

Inventory required and optional capabilities for the **selected operation**, not
every installed plugin. For each applicable plugin/skill, bind its canonical
source/package/version or digest, project/environment scope, actual readable path
or tool catalog, supported schema and relevant authorization. Distinguish:

1. Source or saved configuration declares the capability.
2. Its files/package are installed in the destination.
3. This caller can discover/read the skill or see the supported tool names.
4. The selected engine/connector supports the needed authorized operation.
5. A real applicable invocation has a matching result/original receipt.

These are separate observations. Another task's successful call does not
establish this caller's access. Read relevant repository `.agents/skills`
instructions when catalog discovery omits them; readable files do not prove
automatic loading or engine execution. Use the current
[capability inventory](../agents/skills-and-plugins.md), including caller-local
Context7 distinctions. An irrelevant optional tool is not a build gate, and an
external documentation lookup is not private-repository correctness evidence.

Verify setup against selected source: interpreter/compiler/package-manager
versions, pinned dependencies, setup commands and completion receipts,
filesystem access, authentication scope, network/sandbox policy and shared
resource budget. Inspect setup instructions/scripts before executing them under
their actual grants. Configured commands and ambient binaries are not proof
setup completed or that the pinned isolated runner is available. For Foo checks,
bind deployed helper hash/catalog, exact branch/full commit, target/toolchain
and terminal inner receipt, not a draft helper or submitted job.

Missing files, caller visibility, authentication, unsupported operations,
version drift, quota, failed setup and denied access need different diagnoses.
Do not install, reconnect, change security/settings, create credentials, switch
providers or broaden grants just to make a row green. Follow actual authorization
and the existing custodian for necessary correction; never bypass a denial.
Re-observe mutable bindings after an actual environment/mount/tool change,
without repeating still-valid accepted checks.

## 5. Record the operation-scoped result

In the existing issue/handoff, record observation time, selected operation,
declaration/source identity, actual destination evidence, relevant original
receipt, result/limitation, affected step and responsible next action. Use the
existing typed outcomes as their current schema permits: compliant, drifted,
failed, blocked, unknown, unsupported, not-run or not-applicable. Explain
non-applicability; never waive a required dependency or fabricate a passing probe.

| Next operation | Setup evidence needed | What it does not establish |
| --- | --- | --- |
| Read-only/source preparation | Accessible current source, instructions, scope and preserved custody; explicit mount/acquisition limitations | Build, push, production access or transferred ownership |
| Build/test | Above plus exact executor/source, completed setup, pinned dependencies, supported target and resource admission | Publication, installation or product-wide compliance |
| Integration/publication | Above plus accepted candidate, independent review, sole writer/destination grant and original-effect reconciliation | A release from a source merge or an old claim becoming current |
| Replacement/mode transfer | Surviving operations, current ownership/fencing, compatible checkpoints, receiving acknowledgment and destination capabilities | Transfer from a pause, quota reset, missing session or green comparator |

The [comparison tool](README.md) still checks supplied evidence only and reports
`execution_authorized: false` and `runtime_readiness_verified: false`. This
checklist adds no automatic mount, provisioning, setup, lease or dispatch action.
Report dedicated-environment and capability gaps truthfully while continuing
independently authorized work through its actual available route.
