# Shared Foo project execution: new-operation binding

Status: preparatory source contract, not a deployed execution service. The
version-one Python validator and its synthetic tests create plans only. They do
not resolve the current deployed OffMenu execution gate, grant admission, launch
commands, produce terminal receipts or establish product acceptance.

## Required end state

Foo provides one common bounded host execution service outside all project
checkouts. UIF, OffMenu and other qualified subprojects use that same service and
one host-wide admission authority, with separate project identities, source
worktrees, private state and data bindings. OffMenu's existing specialized routing
is not the final target. A wrapper that keeps all new OffMenu work on the legacy
backend does not complete this migration.

The service's root, runner hash, admission authority/root, capacity-policy hash
and per-operation limits belong to the trusted service registry. Each project
profile binds its repository, owner, non-root UID/GID, worktree and canonical
checkout roots, state root, permitted data roots, runtime executables, resource
limits and isolation-profile hash. A project cannot select a different admission
pool. Limits describe requested ceilings, not allocations or utilization targets.
The plan carries the common admission identity with `granted: false`.

Service code, admission state and every project's checkout/worktree/state/data
roots must be lexically disjoint in this version. A shared read-only dataset
capability is not implemented by claiming that its root belongs to two projects.
No path is moved, no account or permission is changed and no live profile is
installed by this package. Existing data and running services remain intact.

## Implemented interface

`tools/windmill/project_binding.py` uses the Python standard library without
filesystem, network, subprocess, credential or service operations:

- `validate_registry(registry)` validates `foo-project-registry/v1` and returns
  a detached copy. Unknown fields, overlapping roots, root execution identities,
  per-project admission overrides and mutable project-owned executables fail.
- `profile_digest(profile)` hashes the profile excluding its qualification
  record. Qualification must declare `deployment-qualified`, pin this exact
  profile and service, and reference a qualification-evidence SHA-256.
- `reviewed_binding_digest(envelope)` hashes every request field except the
  review itself. Independent review must approve that exact binding, including
  host/project/repository, source, owner, command, registry and resources.
- `plan_dispatch(envelope, registry, observation, existing_operation=None)`
  validates `foo-project-operation/v1` with `intent: new-operation`, the exact
  registry pin, qualified project profile, branch/head/tree/worktree/UID/GID,
  reviewed command argv/cwd/timeout and explicit resources/read/write roots.
  A separate `foo-project-source-observation/v1` must match that exact source
  and project identity and declare clean, canonical paths with an evidence pin.
  Any supplied existing operation is refused, regardless of its current state.
- `canonical_bytes`/`digest` define bounded canonical JSON: sorted string keys,
  compact separators, ASCII escaping, integer numbers within the exact JSON
  safe-integer range, no floats, no coercion and bounded size/depth/item count.

The result is `foo-project-dispatch-plan/v1`, `status: plan-only`, with
`execution_started: false` and `accepted_delivery: false`. Its deterministic
`foo-project-v1-<request SHA-256>` identity binds the full reviewed envelope.
Reordering JSON keys leaves the identity unchanged; source, command, profile,
policy, resources or review changes produce a different binding. Creating a
different plan is not permission to repeat an existing effect.

Registry, review and observation authenticity are the caller's trust boundary.
Matching hashes prove internal consistency, not who supplied the facts, actual
deployment, review independence, current ownership or permission. The fixtures
use synthetic `deployment-qualified` declarations solely to exercise parsing.
They are not registered production profiles or reusable deployment evidence.
Lexical path validation does not inspect symlinks, mounts, filesystem ownership
or confinement. The future installed boundary must independently verify those
facts before execution; no caller-supplied flag substitutes for that check.

## Original operations and provenance

The OffMenu assessment used source commit
`d4d9cc1c042607aec6c3f3f93b153e0395d445e3`. Its bounded runner accepts a runtime
root but keeps admission locks within that same root. Copying it into separate
project state directories would create independent host-capacity pools. Its
resource paths are cooperative declarations; the existing SSH jail supplies the
confinement boundary. Running that supervisor directly as root is not a safe
shared-service migration.

The coordinated UIF-229 recovery identified retained commit
`4632dd238e2bffefc60246bcb328264a6fb85f9e` and its `scripts/windmill` platform
documentation/source as provenance for the intentionally OffMenu-only bridge,
private state and receipt ownership. That source is not evidence of an already
generic backend. Incident workspace, script identity and receipt consumers also
remain bound to the original route. The retained privileged-shell access hold
and the [source-custody qualification record](qualification.md) remain unchanged.

The existing custody export, desired configuration, mock recreation and legacy
readers are unmodified. Historical and uncertain jobs keep their original
workspace/script/job IDs, source, review, receipt schema, storage and readers.
Use the supported original status/reconciliation path; this planner does not
convert legacy receipts, import jobs, resume workers or authorize replay. Preserve
any required original control operation under its existing authorization until
actual stopped-child/effect reconciliation. Do not substitute a new shared ID to
escape a hold. New-operation planning cannot certify a migrated old outcome.

## Next executable adapter seam

The next component is the installed common host entrypoint that consumes this
closed new-operation contract, not an arbitrary root command forwarder. Its
trusted registry and service code must live outside mutable project checkouts.
It must:

1. Resolve authenticated caller/project authority and revalidate the exact
   installed profile, source, independent review, canonical mounts and ownership.
2. Persist submit intent and request identity; reconcile an existing identity
   instead of starting a second effect. Acquire one host-wide capacity and
   logical-path reservation using the existing authoritative admission owner.
3. Invoke the qualified non-root project confinement profile, with only approved
   source/data mounts and a filtered environment. The reviewed argv is not a
   substitute for filesystem/process confinement.
4. Supervise deadlines, cancellation, descendant cleanup and resource limits;
   retain uncertain leases until termination is verified. Keep durable per-project
   receipts and logs separate from shared admission state.
5. Expose same-operation status/cancel/reconciliation and exact versioned receipt
   verification. Connect OffMenu's typed journal/dispatcher and receipt consumers
   for NEW operations while retaining original readers for earlier operations.

The host service, registry storage/authority, confinement implementation,
transactional admission store and live transport are not implemented here.
Actual installation, credential destination or security-setting changes retain
their applicable authorization and confirmation requirements. Qualification must
cover host-wide contention, cross-project refusal, crash/no-replay recovery,
cancel/stopped descendants and exact-source receipt consumption before retiring
the obsolete lane. Source publication and these offline tests do not satisfy
those runtime obligations or change OffMenu's independent review/integration rules.

## Offline checks

Run `python3 -B -m unittest discover -s tests/windmill -p test_project_binding.py -v`
for the new contract tests. The existing Windmill, white-room and agent-policy
suites remain separate baseline regressions. No test in this file accesses Foo,
launches the planned argv or fabricates a successful execution receipt.
