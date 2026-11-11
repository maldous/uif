# Shared Foo admission and reconciliation source

Status: executable **offline admission bookkeeping**, not an installed or
qualified execution service. This is the next source layer after
[project-execution.md](project-execution.md). That document's description of the
plan-only component remains its own scope. No Foo service, account, policy,
permission, legacy route, receipt or data location is changed by this layer.

`tools/windmill/shared_admission.py` supplies a Python standard-library SQLite
store. It serializes reservation changes across projects using `BEGIN IMMEDIATE`
transactions and `synchronous=FULL`. It has no shell, subprocess, root execution,
network, transport, installed confinement profile or production launch backend.
SQLite durability still depends on the filesystem and storage honoring its
guarantees; these tests do not qualify host or storage failure recovery.

## Authority and storage

Every project uses the fixed filename `shared-admission.sqlite3` under the trusted
registry's service `admission_root`. A request cannot supply another database or
select its project state directory as a pool. The planner's registry validation
requires this root to be separate from service code and every project's source,
worktree, state and data roots. This store contains immutable admission intents
and reservation/cleanup state. Project output, logs and execution receipts belong
to their own project stores and are not implemented here.

The first offline fixture creates an already-provisioned temporary directory,
then constructs `SharedAdmission(registry, policy, binding_verifier=...,
cleanup_verifier=..., initialize_offline=True)`. This explicit bootstrap option
refuses an existing database. Ordinary construction omits the flag and requires
the database to exist. Missing authority storage is an error, not evidence of an
empty pool. Root-directory and database symlink checks fail closed, but do not
provide protection against hostile filesystem races or replacement by an owner.

The database pins the exact registry and capacity-policy digests. Reopening with
a changed registry, policy or authority fails. Configuration and immutable intent
updates, as well as intent/configuration deletion, are blocked by database
triggers. There is no policy migration, reset, expiry, pruning or new-owner
takeover API. Replacing lost storage, establishing first-install custody and
changing policy while work remains require a future installed authority, not use
of the offline bootstrap flag to manufacture spare capacity. SQL triggers do not
protect a database against a party permitted to replace or modify its schema.

## Aggregate capacity policy

The closed `foo-shared-capacity/v1` policy has these fields:

- `schema`, `host_id`, `service_id`, `authority`, `admission_root`.
- `global_limits`: one host-wide pool shared by all projects.
- `project_limits`: exactly one limits object for each registered project ID.

Every limits object contains `operations`, `workers`, `overhead_processes`,
`processes`, `cpu_millis`, `memory_bytes`, `output_bytes` and `io_slots`. Values
must be exact JSON-safe integers, not booleans, strings or fractions. They are
positive, except output may be zero; total processes must be at least two.
Each project ceiling must be no larger than its corresponding global ceiling.
The policy's canonical SHA-256 must equal the service's `policy_sha256`.

Each reservation consumes one operation and the request's declared resources;
process demand is workers plus overhead processes. All outstanding reservations
are summed in the same transaction, first against the global limits and then
against that project's limits. Requested per-operation limits still pass through
the project-binding validator. Read/read logical paths may overlap; write/write
and write/read overlaps conflict. These are lexical, cooperative reservations,
not mount confinement, live utilization measurements, resource allocation or
kernel enforcement. A future installed policy must account for other host
consumers and headroom; this module neither discovers nor changes those budgets.

## API and lifecycle

All methods use the exact planner operation identity. No legacy jobs are imported
or converted. Every returned admission record has `accepted_delivery: false`.

- `admit(plan, observation)` regenerates the plan from its request, the pinned
  registry and supplied source observation, compares every canonical field,
  invokes the binding verifier, and atomically commits immutable intent with
  capacity/path reservations. It never executes anything.
- `get(operation_id)` returns the existing record, including its fence, revision,
  reservation state and immutable plan. It never expires or replays anything.
- `request_cancel(operation_id, request_sha256=..., fence=..., revision=...)`
  records cancellation intent and advances the revision. It does not send a
  signal, establish cancellation delivery, stop children or release capacity.
- `observe(operation_id, observation, request_sha256=..., fence=..., revision=...)`
  records `running`, `cancel-observed`, `exited` or `unknown`. Unknown becomes
  `uncertain`; all these states retain reservations. Invalid state transitions
  and outdated fences/revisions are refused.
- `reconcile_cleanup(evidence)` is the only reservation-release operation.
  Successful release retains the immutable identity and cleanup evidence forever.

New admissions start at `intent`. Only a newly committed invocation of the
offline test seam can advance its own intent to `effect-possible`. Observations
advance that state to running, cancellation-observed, exited or uncertain;
cancel-observed cannot regress directly to running, nor exited directly to
running. Unknown allows subsequent observation without granting replay. Any
unreleased state requires independently verified cleanup before `released`.
Cancellation intent is a separate durable flag and does not change these rules.

`operation_id` binds the complete reviewed request. A second submission of the
same exact plan returns its existing record, regardless of state. A changed plan
under that operation ID is rejected. A unique `(project_id, task_id)` constraint
also rejects a changed request under a new operation hash for the same task,
including after release. A genuinely new task needs its own identity; renaming a
task is not authorization to repeat an uncertain effect. Preventing callers from
inventing new logical tasks remains part of the authenticated admission boundary.

## Synthetic callback and no replay

`submit_offline(plan, observation, offline_adapter)` exists only to exercise the
next adapter seam with synthetic test callbacks. It is not a production execution
API. The callback returns only an observation string; a claimed success receipt
does not qualify cleanup or product acceptance.

The function durably commits intent and reservations before attempting the
callback. It then rechecks bindings, confirms its fence/revision and cancellation
state, commits `effect-possible`, and invokes the callback once. Repeating this
operation never invokes the callback again, even after a crash, callback failure,
cleanup or an earlier plain `admit`. A crash between intent and callback can
therefore leave work unstarted but reserved. This deliberately favors no replay
over automatically recovering execution. Ordinary callback errors mark uncertainty
without release; abrupt interruption leaves the last durable state intact.

The launch and commit are not one atomic external operation. A future production
adapter needs a qualified launch boundary enforcing the recorded fence and
current bindings, plus independent process/effect reconciliation. The local
callback and its revision checks do not solve that distributed execution problem.

## Independent verification and cleanup fencing

The required read-only `binding_verifier(plan, phase)` must return exactly `True`
for current authenticated registry, independent review, source, ownership and
isolation bindings. It is called at admission and, in the synthetic seam, again
before the possible effect. Checksums and a caller's dictionary do not establish
authenticity. Duplicate lookups return existing status without a new launch or
new admission grant.

The closed `foo-shared-cleanup/v1` evidence names `operation_id`,
`request_sha256`, `fence`, the current `revision`, a terminal outcome, and an
`evidence_sha256`. Terminal outcomes are `succeeded`, `failed`, `cancelled`,
`timed-out` or `not-started`. It must also assert all four exact booleans:
`launch_fenced`, `executor_stopped`, `descendants_stopped`, `effects_reconciled`.

Those assertions are necessary but insufficient. The required
`cleanup_verifier(record, evidence)` must independently return exactly `True`
for that exact current identity and revision before release. It must verify that
no delayed executor can launch, all relevant executors and descendants are stopped,
and effects are reconciled. An exited wrapper, absent heartbeat, cancellation
observation or callback's claim is not this proof. A hash merely identifies
evidence; it does not verify its truth or authority.

Cleanup validation and release occur within one transaction. Repeating identical
verified cleanup is idempotent; changed evidence and outdated fences are rejected.
Every retained released record is rechecked for the closed cleanup schema, exact
operation/request/fence identity, strict assertions and its pre-release revision
before it can be omitted from capacity accounting. Malformed persisted cleanup
fails closed; this structural read check does not reinvoke the external verifier.
Late callback results cannot overwrite released or advanced records. The test
verifiers are synthetic and cannot be used to justify a real release. Actual
caller authentication, launch fencing, descendant supervision and effect
reconciliation remain unimplemented deployment work.

## Offline verification

Run from the repository root:

`python3 -B -m unittest discover -s tests/windmill -p test_shared_admission.py -v`

The suite uses temporary databases, independent SQLite connections, concurrent
threads and synthetic callbacks. It covers cross-project/global and per-project
capacity, all resource dimensions, identity reuse, crash/reopen no replay,
uncertainty retention, cancellation versus cleanup, cleanup fencing, changed
configuration refusal, missing-store refusal, path conflicts and strict numeric
validation. Tests need permission to create their temporary local files. No test
contacts Foo, runs reviewed argv, changes a live service or fabricates an accepted
execution receipt. Existing planner and project regression suites remain separate
checks; offline success does not establish runtime migration or product acceptance.
