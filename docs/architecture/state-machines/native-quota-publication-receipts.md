# Native quota, publication metadata and legacy receipt protocols

Bounded source edition: UIF commit
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`.
The three complete source modules and four complete matching test files below
were read, **not executed**. This is a new-file-only contribution to
[UIF-249](https://linear.app/maldous/issue/UIF-249) and
[UIF-260](https://linear.app/maldous/issue/UIF-260), not a complete coverage pass,
installed-hook observation or instruction to replay an original operation.
The existing [index](README.md) and [coverage account](source-coverage-20261116.json)
are intentionally unchanged pending their owner's integration.

| Fully read input | Git blob | Lines / named test cases |
| --- | --- | ---: |
| [native_quota.py](../../../src/uif/native_quota.py) | `f563a644d6c9025a2745bf0257bc328d7ea0911e` | 419 / N/A |
| [publication_handoff.py](../../../src/uif/publication_handoff.py) | `ecf771147273f4f5bced825c26ca82a2ce2994a1` | 59 / N/A |
| [legacy_receipts.py](../../../src/uif/legacy_receipts.py) | `03f21ff0ea2ada029a208cd0872d3cd25d59de69` | 302 / N/A |
| [test_native_quota.py](../../../tests/delivery/test_native_quota.py) | `ae50078c0612e4d6e3b6bd2c76781f2d0d2d5127` | 80 / 5 |
| [test_async_native_quota.py](../../../tests/delivery/test_async_native_quota.py) | `eb261b120c85b3e17c330c11cfa7e59b5facb9db` | 351 / 14 |
| [test_publication_handoff.py](../../../tests/delivery/test_publication_handoff.py) | `ed833c14bdd3fceb432bc8649ec250e3a008eb3d` | 32 / 11 |
| [test_legacy_receipts.py](../../../tests/delivery/test_legacy_receipts.py) | `c1b33a7b5617dd999205dd0d5d9471e7719f4ef5` | 311 / 32 |

These are 780 source lines and 774 test-source lines, with 62 named test methods,
not 62 newly passed checks. Async test-source also exercises the imported
Registry effect machinery; its fixtures and that machinery are not claimed
completely re-reviewed by this selection. Other files, installed native editions,
private archives, configuration and provider operations remain outside it.

## Shared responsibility and representation

The native boundary is an **explicit wiring seam** around an existing lifecycle.
Constructing/importing it does not insert hooks into native dispatch, retry,
worker prologues, metadata writes or publication. Its owner must bind actual
profiles, resource-Pool admission, destination receipt readers and Registry
effect consumers. This module supplies no scheduler, poller, provider client,
replacement native runner or independent off-Foo authority.

`NativeProfile`, `PoolPermit`, `PoolRefusal`, `LinearWriteBinding` and
`LinearWriteObservation` are frozen dataclasses, not durable records by
construction; nested dictionaries/objects are not made deeply immutable.
The actual Registry/store owns original intents, effect states and task fencing.
The descriptions below distinguish this module's guards/calls from guarantees
that depend on that injected implementation and its installed consumers.

The two metadata builders are different again: publication handoffs use exact
dictionaries and return copies; legacy receipts consume supplied bytes/pins and
return a bounded dictionary. Neither reads a native operation, starts a process,
publishes Git/provider state or settles an issue.

## `UIF-NATIVE-DISPATCH`: original dispatch and resource admission

Source: `native_quota.py` lines 19–120 and 153–233.
State consists of the selected profile plus externally retained Registry task
generation/source/owner and original operation. No new task claim is acquired.
`_profile(issue)` requires a `NativeProfile`/`LinearKey`, matching issue ID,
positive integer generation and retry budgets, valid source/token shapes, and a
status-read task with the same generation, actor, source, active state and local
lane (lines 70–86). A stale profile fails with `native_claim_drift` before its
effect is requested. This status check alone is not a new atomic lease.

| Event / guard | Transition or delegated effect | Failure / recovery boundary |
| --- | --- | --- |
| Synchronous `dispatch` with a bounded non-coroutine callback | Request Registry `perform_effect(... reasoning=True)` using the original operation and a digest of dispatch kind/source. | Registry owns persistence, current effect admission and duplicate-operation handling. |
| Admitted sync effect | Call Pool; require a `PoolPermit` matching key/generation/operation, token-shaped lease and fresh observation; invoke native callback, refuse an awaitable result, then call independent `outcome_read`. | Sync permit validation does not itself check the permit's `receipt_sha256`; no cleanup or source acceptance is inferred from its presence. |
| Async agent stage | Await Pool; validate matching permit **including** full receipt hash, lease token and freshness; recheck current effect/source with `reasoning=True` before callback/outcome observation. | A quota/claim change during Pool await can prevent the child. It does not prove a reserved Pool lease was released. |
| Async deterministic gate | No Pool call; callback receives `None`; recheck effect with `reasoning=False`. | Gate work can remain eligible when model quota is unavailable, subject to Registry's other guards; this does not grant reasoning. |
| Qualified async `PoolRefusal` | Require matching key/generation/operation, full receipt hash and freshness; optionally release the known native ephemeral slot; return a denied receipt. | Trusted refusal asserts no reservation/child. This path never releases an arbitrary durable lease. |
| `Hold` before async effect entry | Optionally invoke the supplied exact native ephemeral-slot release, then re-raise. | The `entered` latch prevents that compensation after Pool/dispatch entry; ambiguous exceptions retain original intent/custody for reconciliation. |

Async dispatch delegates to `perform_effect_async` with a digest that also binds
`stage_type`; `_await` permits synchronous or awaitable adapter results. Its
`entered` flag is invocation-local, not a persisted running-worker state.
Callback results become operation receipts through the selected reader, not a
unit Done transition. Exceptions, cancellations, uncertain Pool outcomes or lost
replies require the Registry's original-operation reconciliation; no new ID,
automatic retry or fabricated no-child proof is created here.

`guarded_effect` supplies the same bound issue/generation/original-operation
envelope for later metadata/publication callbacks, in sync or async form. It
does not select their payloads or make every native callsite use the envelope.
Registry fencing is non-reentrant as stated by the seam: installed workers must
wait for dispatch registration and must not nest another fenced effect inside it.
Actual cross-process/off-Foo exclusion and installed hook coverage are not
established by these dataclasses or source tests.

## `UIF-NATIVE-RETRY`: bounded timer intent, not resumed work

Source: `native_quota.py` lines 122–150 and 235–283.
Provider observation passes exactly `classification`, `observed_at`, `sequence`
and `evidence_sha256` from the injected reader to Registry's
`provider_observation` request. A supplied dictionary is not independently
authenticated by this adapter; qualification remains with the reader/Registry.

Both retry methods first resolve the current profile, require positive integer
attempt/delay within its configured budgets and a callable scheduling adapter.
They return `scheduled=False` for service-stopped, missing/hard-quota/auth-failure/
unknown provider, or stale provider evidence. Other classifications are not
automatically suppressed by this local exclusion set. No timer is scheduled in
those direct-return paths.

The eligible timer uses original operation `retry-{attempt}` with a digest of
attempt/delay and the normal guarded-effect boundary. That identity is scoped by
the task/generation envelope; a new counter must not replace an uncertain intent.
The sync callback directly invokes `schedule_native` after its earlier provider
check. The async callback additionally rereads profile, stop/provider and freshness
**inside** the admitted effect. If then unavailable, it returns a deterministic
`denied` receipt digest of its refusal context without calling the scheduler
(lines 259–280). That second observation is an async behavior, not a claim that
the sync method performs the same callback-time check.

A verified scheduling receipt describes the timer operation only. Subsequent
dispatch must repeat its current claim, provider, White Room and Pool admission.
No sleeping, timer implementation, backoff loop, quota reset or worker restart
is implemented in these methods; the existing native adapter owns those effects.

## `UIF-NATIVE-LINEAR-WRITE`: observed original destination results

Source: `native_quota.py` lines 286–419.
`AsyncLinearClient` is an issue-bound explicit proxy, not the controller's general
poll/discovery client. Construction validates a canonical team UUID, adapters and
the current issue profile. It exposes only `post_comment`, `update_issue_state`,
`add_label`, team-bound `create_label`, and byte-only `upload_file` writes.
Issue-labelled writes require the exact issue ID; upload metadata includes the
bound issue, filename/type, byte count and SHA-256 rather than the bytes.

| Step | Guard / state | Observable outcome |
| --- | --- | --- |
| Bind proposed write | Digest its payload; obtain typed `LinearWriteBinding` with matching key/generation/method/digest and token-shaped original operation. | Wrong issue/team, stale claim or binding drift refuses before the raw write. |
| Invoke under async guard | Call the original client, then the independent observer with mode `invoke`. | Require typed observation, exactly equal binding and full receipt hash; callback success alone is insufficient. |
| Replay returning an existing Registry operation | If this invocation did not run its callback, call observer with mode `observe` and `returned=None`. | Do not replay the raw client; reconcile its original destination result. |
| Complete this proxy call | Require returned Registry status/hash exactly match the destination observation, then validate the method's value shape. | Receipt drift holds; a boolean or unbound observation is not accepted evidence. |

Allowed observation statuses are `verified`, `failed`, `denied`. For comment,
state and label writes the value must be an actual boolean; `True` requires
verified status. Other failed/denied methods require `None`; a verified created
label requires a canonical UUID, and verified upload requires an HTTPS-prefixed
string of at most 4,096 characters. These shape checks do not fetch or verify a
URL. A verified result is not a whole-task completion or permission grant.

`fetch_comments`, `fetch_issue_states_by_ids` and `fetch_team_labels` forward
the selected reads after their issue-scope checks; they do not traverse `_write`
or acquire a write-effect receipt. The state-IDs read requires exactly the list
`[this_issue_id]`. No arbitrary mutation/GraphQL passthrough is provided. Callback
and observer payloads remain private by contract, not by logging/redaction or
network controls implemented in this proxy.

## `UIF-PUBLICATION-HANDOFF`: metadata shape and route selection

Source: `publication_handoff.py` lines 6–44, 55–59.
This is a stateless validation/projection protocol: input is rejected, or a new
normalized metadata record is returned. `catalog()` returns deep-copied static
route declarations with `foo_publish_supported=False` and
`publication_executed=False`. Listed tool names are source declarations, not
caller discovery, authentication or demonstrated current availability.

`normalize` requires exact top-level/nested key sets and schema
`native-publication-handoff/1`. It binds one declared project/route, opaque
operation/destination/publisher references, immutable source identity/tree, an
artifact digest/size and 1–32 distinct evidence references. References follow a
bounded ASCII token syntax, not fetched locators or verified authority. Git
source pins are 40 lowercase hex characters; generated-snapshot pins are 64.
Artifact size is a nonboolean integer in 1–2,147,483,648 bytes, and compact sorted
JSON must fit 12,000 bytes. The returned handoff hash covers that canonical JSON;
returned input/tool lists are deep copies, not retained caller references.

The two Cloud-specific routes require project `Cloud` and return
`state=native_verification_required`. The generic `project_release` route has
no native tools and returns `unsupported_release_route`, even for otherwise
valid metadata. No command, deploy or credential action exists in `main`.
The declaration's route names never grant provider rights, current ownership,
review acceptance, verified artifact bytes or live-state equivalence.

## `UIF-PUBLICATION-RECEIPT`: original tuple binding, not attestation

Source: `publication_handoff.py` lines 46–53.
`bind_receipt` first normalizes the handoff, then requires the receipt's exact
field set. Handoff hash, original operation, destination reference and artifact
hash must match; native receipt/readback references must satisfy opaque-reference
syntax. Allowed outcome labels are `succeeded`, `failed`, `unknown`.

| Guarded input | Returned state / boundary |
| --- | --- |
| Matching supported route and allowed outcome | `reported_succeeded`, `reported_failed` or `reported_unknown`; copied receipt, `independently_verified=False`. |
| Generic unsupported release route reporting success | Refuse; metadata cannot manufacture a publisher success. |
| Unsupported route reporting failed/unknown | May return the corresponding reported label; still no execution/attestation. |
| Changed original tuple, malformed shape/reference or unsupported action | Raise rather than publish or retry anything. |

There is no persistent initial/pending/committed record here. Repeating the pure
binding call does not reconcile an external effect; the existing native owner
must read the original operation and actual destination postconditions. The
output expressly does not authorize receiving, acceptance, retries or merge.

## `UIF-LEGACY-RECEIPT-JOIN`: bounded source-free compatibility

Source: `legacy_receipts.py` lines 15–302.
The representation is supplied original/review archive bytes, their `ArchivePin`
record/file identities, the externally authenticated published review tuple,
optional exact record recipe and explicitly selected documentary hashes. Neither
a directory name nor a caller's `verified=True` field qualifies the input.
The builder reads no files or native store and fetches no hash/pointer target.

The ordered validation/projection stages are:

1. Verify each raw byte input's full file SHA-256 and size at most 2 MiB, then
   decode its context/report shape (lines 111–119). File-byte hash and native
   record identity are distinct pins, not interchangeable digests.
2. Require the published review binding's exact nine fields, source-free route,
   independent-review state, candidate equal to issue ID, positive integer run,
   bounded strings and scope/context hashes. Freeze it through the wire codec.
   Freeze bounded enriched routing, require its core projection equals that
   binding, and require both review binding and full report routing match exactly
   (lines 122–130, 198–209). Historical enriched metadata is retained, not current
   authority inferred from fields such as `current_task_registered`.
3. Refuse a modern `reviewed_disposition` input rather than synthesize one.
   Require approved independent review; original stage/verdict must be
   `assess/approve` or `investigate/complete`. Match runs, issue/scope contexts and
   distinct nonempty original/review session strings. An optional original
   binding is checked for its selected source-free/issue/run/scope fields, not
   forced to the review binding's complete schema (lines 210–232).
4. Validate **both** artifact metadata lists: at most 128 relative safe unique
   paths with full hashes and nonnegative integer sizes. Explicit `[]` is valid;
   missing/`None` is not empty evidence. This validates no artifact body or rights
   (lines 133–147, 233–234).
5. Require bounded hash tuples, unique selected pins and no selected pin in the
   explicit exclusions or four known shared-policy hashes. Every selected pin
   needs exact classification `selected-documentary-input`; freeze that map.
   A new unknown policy hash still needs honest caller classification, not an
   automatic assertion that every unknown hash is selected evidence (235–253).
6. Traverse only `report.data_sources`, `report.claims`, `report.verification` to
   collect lowercase standalone 64-hex tokens and escaped RFC6901 pointers.
   Limits include depth 32, 256-byte keys, 1,024-byte pointers, 512 token hits,
   256 unique hashes and 64 KiB aggregate reference bytes (150–183, 254).
7. If explicitly selected, require the **exact** `native_retained_record_id`
   function, validate both native record pins and ensure the checked objects are
   unchanged. Its recipe is sorted JSON with `ensure_ascii=False`, default
   separators and nonfinite JSON refused: not raw file bytes or compact `wire`
   encoding (46–58, 255–263).
8. Build the proof alternatives below, project the receipt, enforce its 128 KiB
   wire bound, add a receipt hash over the record **before** that hash field, then
   recheck final size (264–302).

Routing metadata is separately bounded before copying: 512 visited nodes,
depth 32 and a 64 KiB aggregate budget over admitted primitive/list/dict values
(80–108). These are source limits, not live resource/authority measurements.

| Proof found in selected documentary fields | Join classification |
| --- | --- |
| Original **file** hash appears in review references | `original-evidence-file-sha256`; no directory-record identity claimed. |
| Exact record recipe verified both inputs, original record ID appears in review | `verified-native-record-id`. |
| Explicitly classified selected hash appears in both original and review | `selected-documentary-input-sha256`, with both pointer sets; this is a selected subset, not the whole original archive join. |
| No qualified proof | Return `input-join-unresolved`, not an exception solely for absent proof. A directory-like record reference without verified recipe remains an explicit hint. |

Any nonempty qualified proof yields `bounded-input-join`, not acceptance.
The receipt preserves original stage/verdict, sessions, binding/routing and
selected artifact metadata. It declares authentication/current scope/custody
checks still required, payload verification false, and receiving authorization,
gate clearance, full-issue acceptance, Git publication and runtime installation
all false. It adds no modern disposition. There is no timer, mutation,
compensation, current claim lookup or external replay in this pure protocol;
repeatability describes deterministic input processing only.

## Test-source observations, interactions and remaining custody

The five synchronous seam oracles cover Pool-before-native dispatch and same-ID
reuse, hard quota/retry suppression, wrong permit retained unknown, stale profile
refusal and retry budgets. The fourteen async-file cases include six Registry
effect/cancellation/reconstruction oracles and eight native/client cases: scoped
write/replay shapes, wrong scopes/stale generation, pre-entry slot compensation,
quota changes while Pool or retry proof awaits, deterministic gates, qualified
no-start refusal and missing independent destination observation.

The eleven publication tests check no effect/generic Foo publish, rejected
credential/action/project/source/size shapes, unsupported success, receipt
binding and copying. The thirty-two legacy tests check exact file/record/subset
joins, explicit empty artifacts, stage/run/session/core/enriched-routing joins,
policy exclusions, canonical recipe, missing/duplicate JSON/pins, unsafe paths,
immutability and bounded traversal/output. These inspected synthetic oracles
are not current installed/native/provider observations or new test results.

| Existing issue / boundary | What this source selection contributes; what remains external |
| --- | --- |
| [UIF-120](https://linear.app/maldous/issue/UIF-120) | Native/proxy seams and receipt shapes identify the consuming boundary; actual state-preserving hook wiring, current issuer and original receiving remain with their owner. |
| [UIF-130](https://linear.app/maldous/issue/UIF-130) / [UIF-140](https://linear.app/maldous/issue/UIF-140) | Original operation/source/generation joins are explicit; live persistence, adapter authenticity, cleanup and cross-lane fencing are not demonstrated here. |
| [UIF-122](https://linear.app/maldous/issue/UIF-122) / [UIF-219](https://linear.app/maldous/issue/UIF-219) | Metadata and compatibility joins are bounded interfaces, not source transfer, accepted baseline equivalence or selection of a journal backend. |
| [UIF-247](https://linear.app/maldous/issue/UIF-247) | Loaded policy and seam source differ from installed/current controls. No restart, activation, configuration change or new authority follows. |
| [UIF-249](https://linear.app/maldous/issue/UIF-249) / [UIF-260](https://linear.app/maldous/issue/UIF-260) | Six bounded interacting protocols are documented; canonical coverage/index and whole-issue completeness require their separate integration/review. |

In particular, source-free receipt compatibility does not settle ANQR's approved
finite-result receiving, grant a missing native claim, authorize a denied body
transfer, change a source/publisher reservation or prove prior children stopped.
PR77/78 and all original native operations retain their existing custody.
