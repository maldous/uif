# Native quota realization and owner handoff

This is the integration handoff for the existing policy-2.0 source branch. The
current UIF runtime owner retains sole live-write custody. Source acceptance does
not lift a no-pause, no-installation or no-restart instruction. Preserve active
workspaces, current claims, product reservations and original operations.

## Source changes now implemented

- `Registry.perform_effect_async` uses the same task fence, durable original
  intent and destination receipt as synchronous effects. Read-only reconstruction
  runs off the event loop; quota, stopped state, source and generation are checked
  again before an intent. Awaited effects run outside the global metadata lock.
- `AsyncNativeQuotaBoundary` provides awaited agent/gate dispatch, metadata,
  provider and retry seams. `AsyncLinearClient` explicitly binds native
  `post_comment`, `update_issue_state`, `upload_file`, `create_label` and
  `add_label` to the current issue/team and original stage operation. A successful
  client return does not replace an independent destination observation.
- The same Foo authority now retains cold request results, events and terminal
  tasks in a private SQLite archive. Recent hot records are bounded; permanent
  original-ID lookup and accepted-task tombstones survive pruning. Unknown
  operations and active ownership remain hot. Archive failure holds new admission
  while reserved space remains available for control and reconciliation.

The new async and retention tests are isolated source checks. No live registry,
native module, runner, launcher or workflow was changed by these changes.

## Bind the existing native lifecycle

1. Compose with the runtime owner's already-reviewed Codex stdin runner
   correction. Verify its full before/after and patch digests from the owner's
   existing approved packet. Do not replace the installed runner with the public
   upstream file: that file is not the installed preimage. Preserve the original
   runner review rather than reviewing it again as new work.
2. Resolve immutable workspace/project/issue UUIDs through the actual configured
   native project and current issue read. The public upstream issue object does
   not itself supply the workspace/project binding. Bind current source, stage,
   owner/generation and original operation to the real profile and white-room
   reader; never infer those rights from a project name or request JSON.
3. Await `dispatch_stage` from both `_tick` and `_handle_retry` before native
   dispatch metadata or child launch. Preserve the native state machine. The
   original caller has already reserved an in-memory slot: a proved refusal can
   release that exact reservation, while an uncertain real Pool reservation
   requires original-operation and stopped-child reconciliation.
4. Agent stages use the actual admitted Pool permit and fresh provider evidence.
   Gate metadata on an existing claim remains deterministic. A new unowned gate
   cannot bypass claim admission by calling itself deterministic. Scheduled
   workers wait for dispatch registration to settle before their own prologue;
   dispatch registration proves registration, not model execution. Never nest a
   second same-task effect inside the dispatch fence.
5. Bind the issue-scoped async Linear client and publisher callsites to actual
   original-operation readback. Qualify provider, Pool/no-start, stopped-child,
   completion and coordinator observers through their existing authenticated
   mechanisms. A callback stub, hash, old public display or ordinary worker exit
   alone supplies none of those observations. `_schedule_retry` must use fresh
   provider policy without repeatedly entering an exhausted model route.

The native hook candidate must be composed and compared against the owner's
actual installed preimages. The source seams are not a drop-in monkeypatch for
the synchronous native `_dispatch`, and they do not install themselves.

## Retention adoption and recovery

The first successful cold prune records state schema `uif-quota-authority/2`,
archive schema `uif-quota-cold-archive/1`, registration binding and a minimum
monotonic archive revision. A newer archive is allowed after interrupted hot
replacement; missing or older required archive fails closed. Cold commit and
durability precede pruning, and only previously committed records are archived.

Keep the hot JSON, cold SQLite database and its recovery files together in the
same authority custody and backup/restore contract. Reads do not create or
migrate the archive. `status` reports hot work and archive health; use
`task_status` with the exact Linear key for terminal history. No archive garbage
collection, deletion, new queue or work selection is introduced.

Long-lived active histories and physical storage remain finite. Monitor their
actual headroom; do not claim unlimited retention. Reconcile the actual old
executor/writer adoption before pruning: a format change alone does not fence
every possible historical library effect path. That is an owner/source fence,
not permission to pause CIX or terminate unrelated work.

## Policy and cloud-role adoption

Merge the reviewed UIF source through its existing owner first so selected
canonical policy references resolve, then adopt the existing project PRs through
their owners. Preserve standalone product contracts and the accepted source
baseline. Each selected environment/coordinator records the policy version/hash,
managed supervision skill and project instructions it actually read.

The managed supervision skill is available in the bundle. An environment can
select and acknowledge that copy through its existing startup entrypoint;
publication does not overwrite a separately installed cloud catalog skill.
Keep that distinction explicit instead of treating an unavailable catalog edit
as a reason to block a qualified selected-role adoption.

Historical CIX-pause wording is not current control-state evidence. Preserve the
current observed CIX state under the user's latest direction; no pause, restart
or activation follows from startup text or policy distribution.

## Shortest actual operating proof

After the existing owner reconciles the candidate, adoption and action-time
holds, use a genuinely eligible issue through the existing native intake. UIF-255
is the existing consolidated documentary acceptance owner; do not duplicate it,
recreate the README list or infer pickup from its Todo state. Its source records
remain distinct from useful execution and final acceptance.

Observe actual pickup, the requested useful result, independent exact-result
review, destination publication/acceptance under original receipts, and automatic
pickup of the next eligible unit. Do not manufacture an issue or manually refill
to obtain this proof. Other independent projects keep their reservations.

Use already accepted white-room/source/checkpoint evidence at unchanged bindings.
Exercise changed async/retention boundaries only; an isolated quota-fault test
does not establish real provider exhaustion, and a process or pending slot is
not a running model. A real quota handoff additionally needs an active
authenticated Morgan coordinator, acknowledged exclusive claims, original-effect
reconciliation and useful successor work. Restored local quota admits independent
new units while existing cloud claims finish. Until these are observed, report
the exact pending action and owner rather than declaring unattended BAU complete.
