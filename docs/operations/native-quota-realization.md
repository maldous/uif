# Native quota realization and owner handoff

Current goal: [policy 2.0.0](../agents/uif-worker-policy.md) requires unattended
continuous BAU across quota loss and sustained Foo/MCP outages, independent of
this chat. It requires off-Foo election/claim authority and durable scheduled
Morgan/project reconstruction. The source realization below describes the
retained Foo-local implementation; its off-Foo adapter/scheduling integration
remains unimplemented/unqualified and does not satisfy that complete goal.
Preserve accepted source and original receipts while completing the authorized
integration; no separate approval checkpoint is introduced for the same outcome.

This is the integration handoff for the policy-2.0 source branch. The current
UIF runtime custodian retains sole live-write custody and completes the composed
implementation, qualification and rollout under the user's current instruction.
That instruction supersedes the former administrative no-install/no-restart
rollout hold; do not add a fresh human approval or binding checkpoint for the
same outcome. Actual access/security, project-data rights and custody controls
remain unchanged. Preserve active jobs/workspaces, current claims, product
reservations and original operations while rolling forward. The observed native
runtime remains unpaused; independent existing delivery must not be halted merely
because the new outage-recovery integration is unfinished.

The custodian supplied the receiving preimage record in original operation
`01a1da29-c0f2-37da-d855-c5b167ddcf51`, SHA-256
`d456a6a05fc130db255ac8a4674c160f4e9a3b2082e4c2abaaa3d3093fdfc8ed`.
Keep its private payload in the existing Foo custody. The approved stdin fix is:

| Artifact | SHA-256 |
| --- | --- |
| Approved patch | `d7aca0ba89abb50f31907fc36bc03fe186f1f4675369fea5b5cf8499295753d7` |
| Runner preimage | `8c158a73197ec5c5d2661c3859f9ceb122095ffcde9f853632b6f982ca8a3406` |
| Runner afterimage | `2492210a8d43cae9b2e0ebae256f8d37298fc75c0f2c65c3fdbd66a925c6b351` |

Receiving those pins does not claim the correction was installed. Native hook
composition is with the existing custodian, separately from the alignment source
writer. Actual project/UUID/source resolution is automatic integration work,
not a per-issue human binding or approval requirement.

Morgan separately verified an existing enabled ten-minute off-Foo heartbeat
with thread observation/routing capabilities. Reuse it; create no duplicate
monitor. It supplies coordination wake-up, not atomic claim/control authority,
effect fencing or automatic election. The remaining off-Foo integration is a
durable compare-and-set authority enforcing coordinator epochs, task generations
and original-operation intents/outcomes at actual effect boundaries. Current
Foo `flock` transactions cannot simply become remote database locks held over
awaited effects. An uncertain operation remains held through its original
receipt; independent issues can continue. Cloudflare monitor/D1/BridgeQueue
inventory does not establish this implementation or its caller grants.

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

The custodian composes and verifies native hooks against the actual installed
preimages as implementation work under the current instruction, preserving jobs
and prior accepted runner review. This technical verification is not a new human
approval/binding gate. Source seams are not a drop-in monkeypatch for synchronous
native `_dispatch`; qualified composed rollout and destination readback remain
necessary before claiming installation.

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

The existing custodian completes the composed rollout and its focused
qualification while preserving independent active work, then uses a genuinely
eligible issue through native intake as the shortest actual operating proof.
No fresh human approval/binding gate is introduced. UIF-255 is the existing
consolidated documentary acceptance owner; do not duplicate it,
recreate the README list or infer pickup from its Todo state. Its source records
remain distinct from useful execution and final acceptance.

Observe actual pickup, the requested useful result, independent exact-result
review, destination publication/acceptance under original receipts, and automatic
pickup of the next eligible unit. Do not manufacture an issue or manually refill
to obtain this proof. Other independent projects keep their reservations.

Use already accepted white-room/source/checkpoint evidence at unchanged bindings.
Exercise changed async/retention boundaries only; an isolated quota-fault test
does not establish real provider exhaustion, and a process or pending slot is
not a running model. A real handoff needs a current authenticated coordinator
epoch, acknowledged exclusive claims, original-effect reconciliation and useful
successor work. The independent durable lifecycle must automatically appoint and
reconstruct the same logical Morgan role when its execution is absent; it cannot
require this chat already to be active. Sustained Foo/MCP outage continuation
requires the qualified off-Foo authority, not the retained local test store.
Restored local capacity admits independent new units while cloud claims finish.
Continue to the requested completed autonomous outcome; until observed, report
the exact pending action/owner and implementation gap without claiming BAU or
stopping unrelated existing work.
