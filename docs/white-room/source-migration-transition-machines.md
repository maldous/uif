# White Room evidence, recovery and source-migration machines

This is one bounded source-documentation contribution to
[UIF-249](https://linear.app/maldous/issue/UIF-249) and
[UIF-260](https://linear.app/maldous/issue/UIF-260), not an exhaustive UIF
inventory or an execution receipt. It describes current code, including its
limits, without changing a machine, configuration, claim, checkpoint or service.
The [existing identification account](../architecture/state-machines/README.md)
and its source-coverage ledger retain their original editions and custody.

## Bound source and coverage

All source and test assertions below refer to **maldous/uif** commit
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. The four allocated modules and seven
matching test files received a complete bounded semantic read. Their 2,101
physical lines are an accounting aid, not proof of behavioral completeness.
Only UIF repository bytes were inspected for this contribution. Profile names
referring to other projects describe existing UIF configuration code; no sibling
source, real configuration, installed native source or external data was acquired.

| Source file | Lines | Stable machines / disposition |
| --- | ---: | --- |
| [check.py](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/check.py) | 157 | `UIF-WR-COMPARE`: all helpers, evaluator and CLI |
| [remote_evidence.py](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/remote_evidence.py) | 267 | `UIF-WR-REMOTE`: scope/identity/response validation, read sequence and error projection |
| [transition.py](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/transition.py) | 183 | `UIF-WR-CHECKPOINT`, `UIF-WR-RECOVERY`: registrations, compatibility and composition |
| [migrate_cix_source.py](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/migrate_cix_source.py) | 395 | `UIF-WR-SOURCE-MIGRATION`: fixed profiles, exact-span rendering, guards, persistence and CLI |

The seven test sources are mapped below. `inventory.py`, Windmill transport,
the complete registry/store/coordinator lifecycle, concrete connector/saver
adapters, installation and live host behavior remain outside this allocation.
The selected registry callsite and fixture interactions are context, not a new
full review of their modules. The empty package initializer adds no transition.
No source tests or documented operational commands were executed in this pass.

## `UIF-WR-COMPARE`: supplied-evidence decision product

Owner: UIF comparison foundation. This is a **stateless evaluation**, not a
persisted lease or daemon. Inputs are declaration/evidence version 1, selected
project/profile and evaluation time. Outputs are independent check rows with
states `compliant`, `drifted`, `failed`, `blocked`, `unknown` or
`not-applicable` in the admitted observation vocabulary; a required dependency
reported as `not-applicable` becomes a `blocked` row.

| Event / guard | Derived result | Effects / next step |
| --- | --- | --- |
| Invalid version, project/profile, repository mapping, duplicate identity, freshness bound or unknown observation ID | Exception rather than a valid comparison | CLI catches its listed input/file errors, emits error class and `preflight_passed: false`, exit 2 |
| Wrong declaration digest or target; incomplete discovery | Drifted or unknown row | Continue collecting other applicable rows; omission does not waive requirements |
| Required observation missing, invalid/future/expired time, missing source or unqualified null expectation | Unknown row | Caller must acquire/qualify that actual evidence |
| Fresh noncompliant state or mismatching canonical actual/expected digest | Original noncompliant state or drifted row | No repair or retry |
| Complete recovery context, exact accepted/selected source and owner/epoch; live lease, recent remote reconstruction, preserved isolated work and fenced predecessor; no supersession or pending operations | Recovery guards satisfied | These are supplied assertions, not facts authenticated here |
| Explicit checkpoint absence or compatible source/issue/owner/epoch-bound resume; fresh remote publication checkpoint with matching identity | Corresponding guards satisfied | No checkpoint read, save or replay is performed by this evaluator |
| Nonempty row list and every row compliant | `preflight_passed: true`, CLI exit 0 | **Both** `execution_authorized` and `runtime_readiness_verified` remain false |
| Any noncompliant row | `preflight_passed: false`, CLI exit 1 | No admission, cancellation or rollback |

Evidence: [evaluate, lines 28–136](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/check.py#L28-L136).
Requirement freshness is an integer 1–86,400 seconds; recovery reconstruction and
publication observations are at most 300 seconds old. The evaluator hashes
canonical sorted JSON, preserving JSON type distinctions. It does not inspect a
real workspace, discover undeclared dependencies, authenticate an observation,
settle an operation, renew a lease or independently fence an old owner. There is
no durable state, transaction, asynchronous cancellation, backoff or automatic
retry here; those responsibilities belong to the consuming authority.

## `UIF-WR-REMOTE`: bounded remote reconstruction

Owner: UIF reader contract plus the integrating owner's authenticated `Reader`.
State is per-call phase, retained response hashes and final
`inconclusive`/`observed` result, not a persistent acceptance pointer.

1. Validate `Target` **before any reader call**. Without explicit `ProjectScope`,
   only UIF/develop/cloud is supported. An explicit reviewed scope binds
   repository/ref/project/profile and canonical Linear identities, including the
   selected issue UUID. It is owner configuration, not authority from issue prose.
2. Read branch, selected immutable commit, merged acceptance PR and ancestry.
   Require the selected merge to be on the integration history and a complete
   compare endpoint ending at the observed head (or identical base/empty list).
3. Normalize the issue, complete direct `blockedBy` enumeration and at most 16
   direct dependency issues. Require expected selected issue team/project; explicit
   scopes additionally check workspace and canonical issue UUID metadata. Do not
   recursively traverse dependency graphs.
4. Reread branch, PR, issue and direct dependencies. Changed normalized facts,
   nonmonotonic observation times or an expired 300-second window refuse the
   reconstruction. These repeat reads are **not an atomic snapshot**.
5. A coherent result becomes `observed` with source/work/dependency facts and two
   access observations. Denied/timeout/unavailable reads, malformed/moving/large
   responses or other connector failures yield `inconclusive`, no compliant
   observations and no partially accepted source/work/dependency projection.
   Already collected receipt hashes remain for private reconciliation.

Evidence: [target and issue validation, lines 60–159](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/remote_evidence.py#L60-L159)
and [read protocol, lines 161–267](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/remote_evidence.py#L161-L267).
Canonical decoded responses have a 256 KiB budget; the host reader must enforce
transport deadlines and bounds **before** decoding. The adapter implements no
network client, fallback route, pagination, credential handling, mutation or retry.
It cannot authenticate an injected Python object. Invalid caller targets and
initial clock validation can raise before the result-producing try block;
interrupt/cancellation exceptions also propagate, not an observed success.

Raw issue prose, unrecognized response fields/URLs and backend error bodies are
not projected. Receipt targets/hashes and normalized metadata still require
approved private custody. Source integration is not issue/product acceptance.
Ownership, checkpoint and pending operations remain unknown; discovery,
authorization, runtime readiness and saved-JSON authentication remain false.
The reader can observe terminal issues; **eligibility filtering happens in the
recovery composition**, not in this fact reader. See the existing
[remote reader contract](remote-evidence.md) for connector mappings.

## `UIF-WR-CHECKPOINT`: registered latest-tuple compatibility

Owner: integrating authority's registered saver and `CheckpointRead`; no new
checkpoint producer. Representation is thread, namespace, optional selected
checkpoint ID, wire version and exact `uif_recovery` metadata. A nonempty thread
is required; an empty-string namespace is allowed by the implementation.

| Event / state | Guard | Result / failure |
| --- | --- | --- |
| Verify `none` | Latest registered thread/namespace lookup returns no tuple and no selected checkpoint ID | Verified absence; a newer tuple invalidates an old absence claim |
| Verify `resume` | Latest tuple exists; selected ID and metadata are present | Continue compatibility validation, otherwise hold |
| Successor uses origin checkpoint | Explicit origin owner and integer generation precede target generation | Original or target owner/generation may bind; a different predecessor holds |
| Bind task/source | Exact Linear UUIDs, repository/base/head/tree and allowed owner/generation | Mismatch holds; historical metadata is not rewritten |
| Bind format/stages | Four 64-hex pin fields, positive integer wire version, nonempty distinct completed/pending labels and original-operations list | Invalid registration holds |
| Verify actual latest tuple | Exact config/ID/wire version, canonical metadata digest equality, `pending_writes == []` | Return resume summary or hold for incompatible/pending tuple |

Evidence: [verify, lines 36–100](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/transition.py#L36-L100).
The saver is read with **no old checkpoint ID selector**, so a newer tuple cannot
be hidden by reading a stale selected ID. Metadata hashes/wire values are compared
to owner registration; this is not proof the declared graph/code was installed or
every original operation settled. `original_operations` is checked as a list,
not independently reconciled or element-schema-validated here. There is no saver
write, checkpoint migration, stage execution or replay. A later producer saving
a successor tuple is a separate event, not a side effect of verification.

## `UIF-WR-RECOVERY`: receipt composition at an ownership boundary

Owner: the existing authority invoking `WhiteRoomVerifier`, not task JSON.
The call progresses through typed inputs → target/declaration binding → coherent
remote reconstruction → eligible issue/dependencies → recovery-context binding →
latest checkpoint → fresh access observations → evaluator → `RecoveryReceipt`.
Any missing/inconsistent prerequisite holds; no partial recovery is committed.

The target must bind the expected Linear identities, repository, accepted base
and lane (`local` maps to `foo`, otherwise `cloud`). Issue status type must be
`backlog`, `unstarted` or `started`; every enumerated dependency must be
`completed`. Candidate branch/head/tree, current owner/generation and source base
must match the recovery context. A verified origin checkpoint is normalized only
in a **deep copy** for the successor's comparator input. The saver/original
metadata remain untouched. Only GitHub/Linear access observations acquired by
this call replace the supplied access rows; other dependencies are not invented.

Evidence: [composition, lines 117–183](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/transition.py#L117-L183).
Success returns report digest, matching evidence reference and checked time,
not an execution grant. Named `RecoveryHold` reasons remain bounded; other
backend exceptions become `recovery_evidence_unavailable` without private bodies.
The existing registry's [selected `_recover` callsite](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/src/uif/quota_handoff.py#L271-L285)
builds the expected context and checks receipt type, binding and freshness.
Actual ownership locking, effects, fencing, deployment and durable off-Foo
authority are outside this verifier. Re-evaluation is not idempotent settlement
of an external operation, and no automatic retry or receiver takeover occurs here.

## `UIF-WR-SOURCE-MIGRATION`: future-clone configuration replacement

Owner: UIF source/custody repair through the actual acknowledged sole
configuration writer. This machine **can write a selected configuration** when
explicitly applied; documenting it neither invokes nor authorizes that effect.
Its state is original file/preimage, selected fixed profile, private backup,
prepared replacement and the point at which `os.replace` has occurred.

| Phase / event | Guard | Transition and durable effects |
| --- | --- | --- |
| Select/render | Fixed allowlisted profile; bounded strict JSON (no duplicate keys/nonfinite constants/trailing data, depth at most 64); one exact paused project, repository/origin/base and original hook hash | Render only selected scalar spans; verify all unselected JSON semantics unchanged, preserve other text spans |
| Acquire original | Absolute nonsymlinked path; no-follow/nonblocking open; cooperative exclusive nonblocking lock; bounded regular single-link file; exact SHA256 preimage | Retain original bytes/identity; any mismatch refuses rather than adopting drift |
| First state check | Fresh selected-project native observation within 5 seconds; paused and zero running/settling/tracked tasks | `dry-run` returns bounded hashes/limits without backup or replacement |
| Prepare application | Actual file owner, no group/world write; private owner-bound backup directory and exact original backup | Durably store/verify original; fsync backup directory and parent entry before replacing config |
| Prepare replacement | Temporary sibling, original mode/UID/GID, write/flush/fsync | No selected-config effect yet; preparation failure retains original, though a backup may remain |
| Final boundary | Same file device/inode/bytes; fresh profile state; repeated identity/byte check after state read | Atomic `os.replace`, then parent fsync and exact readback |
| Verified replacement | Installed bytes equal rendered output | `applied` with original/readback hashes; no runtime-adoption claim |
| Failure after replacement | Replacement may already be durable/visible | `UncertainMigrationError`; reconcile **original operation** and installed bytes, no automatic rollback or blind retry |

Evidence: [profiles/rendering, lines 32–201](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/migrate_cix_source.py#L32-L201),
[state/pause/path guards, lines 204–282](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/migrate_cix_source.py#L204-L282)
and [migration/CLI, lines 285–395](https://github.com/maldous/uif/blob/43fa5e645e1cdb91ec75136a7509b8c88c085ab9/tools/white_room/migrate_cix_source.py#L285-L395).
Maximum source configuration is 2 MiB. The default state reader is a fixed local
native-state HTTP read with a five-second timeout, not an arbitrary endpoint
selector. This pass did not invoke it. CLI exits are 0 for dry-run/applied, 1 for
uncertain effect, and 2 for its listed refused input/state/file conditions.

The `cix` profile changes its base `main` → `develop` plus explicit future clone
branch. CQ/ANQR/OffMenu profiles change only their pinned future clone hook and
preserve delivery base. CIX/CQ/OffMenu require zero retries; only the ANQR
hook-only profile permits a nonnegative **integer** retry count and requires the
exact installed generic pause/dispatch source digest at both state checks.
It does not execute that source or consume/reset retries. The installed bytes
and the claimed dispatch behavior were **not** reverified in this documentation
pass; the hash guard is not a fresh semantic proof. See
[CIX boundary](cix-source-selection.md) and [explicit profiles](future-clone-profiles.md).

The file lock follows the original inode and relies on cooperating sole-writer
custody; it is not distributed exclusion after rename. Immediate rechecks do not
fence arbitrary noncooperating writers. Reapplying the old preimage after success
refuses; an existing backup must match exactly and is never overwritten with a
different original. The tool removes a prepared temporary file on failure, not
the preserved original backup. It executes no hook, Git clone, claim transfer,
service restart, admission unpause or publication. File replacement is not
effective service reload, future-workspace qualification or original intent
retargeting. Private configuration/hook bodies and backups stay outside source.

## Existing synthetic test-source oracles, not executed results

All links are to files in the bound source edition. These are reviewed assertion
intent and fixture behavior, **not fresh passes or coverage percentages**.

| Matching test source | Lines / cases | Machine and meaningful oracles |
| --- | --- | --- |
| [test_check.py](../../tests/white_room/test_check.py) | 137 / 29 | Compare: profile-specific dependencies, incomplete discovery, source/owner/fence/supersession, uncertain effects, checkpoint/publication binding, type/freshness/identity errors. Its inventory test is contextual; inventory implementation is outside this pass. |
| [test_remote_evidence.py](../../tests/white_room/test_remote_evidence.py) | 139 / 22 | Exact bounded read order, movement and partial dependencies, target/refusal before reads, no retry/fallback, secret-like extra-field/error projection, stale clocks and incomplete stock declaration |
| [test_transition.py](../../tests/white_room/test_transition.py) | 173 / 12 | Typed provider, identity/lane/eligibility, latest absence/resume, pending writes, compatible metadata, predecessor binding with unchanged original |
| [test_registry_transition.py](../../tests/white_room/test_registry_transition.py) | 153 / 3 | Synthetic composition: origin survives acknowledgement/first effect; successor save is explicit fixture producer event; obsolete claim/failed recovery/pending writes hold without changing original owner |
| [test_migrate_cix_source.py](../../tests/white_room/test_migrate_cix_source.py) | 270 / 18 | Exact spans/Unicode, source/state/preimage/path/types, private backup/mode, preparation races, parent durability ordering, pre-effect failure and post-replace uncertainty |
| [test_future_source_profiles.py](../../tests/white_room/test_future_source_profiles.py) | 104 / 5 | Fixed hook-only profiles, preserved publication base/unselected bytes, no arbitrary profile or source pin, selected-project checks and original backup |
| [test_paused_retry_source.py](../../tests/white_room/test_paused_retry_source.py) | 123 / 6 | Synthetic pin substitution, preserved retries, strict other profiles, active/pause/type rejection, absent/drifted/symlink/FIFO source, final source recheck |

These 95 declared test methods include parameterized loops; this is not a count
of runtime executions. Synthetic hook/source pins are deliberately substituted
in fixtures. Fake readers/savers and fixture registry effects are not live
connectors, installed source, distributed authority or actual reboot recovery.

## Issue impacts, unknowns and revalidation

Native Linear readback during preparation found UIF-249, UIF-140 and UIF-247
`Blocked`; UIF-260 and UIF-130 `Backlog`. This is a dated scope snapshot, not a
status transition, execution claim, dependency edge or a second intake queue.

| Existing issue | Affected machines / contribution | Still not established by this document |
| --- | --- | --- |
| [UIF-249](https://linear.app/maldous/issue/UIF-249) | Five explicit stable IDs, allocated-file accounting and transition/failure/test-source maps | Exhaustive UIF/module/installed-edition inventory, complete issue impacts and independent whole-inventory verification |
| [UIF-260](https://linear.app/maldous/issue/UIF-260) | Source-grounded White Room reference linked to retained contracts | Whole documentation acceptance, automatic skill installation/discovery or global capability readiness |
| [UIF-140](https://linear.app/maldous/issue/UIF-140) | Future-source migration guards, original preimage/backup and uncertain-effect disposition | New host application, source custody release, old-workspace reconciliation or runtime adoption |
| [UIF-247](https://linear.app/maldous/issue/UIF-247) | Distinction between comparator evidence and owner-bound recovery receipt | Qualified concrete readers/savers, complete task declarations, installed dispatch/publisher hooks or off-Foo authority |
| [UIF-130](https://linear.app/maldous/issue/UIF-130) | Source-level recovery/failure boundaries for later handover evidence | Planned/unplanned live interruption, actual receiver fencing, installed continuity or recovery of an original operation |

Revalidate affected assertions when code/test blobs, profile/source pins,
declarations, saver/reader mappings or consuming registry semantics change.
Preserve earlier accepted receipts at their original source/operation; do not
rerun them for documentation bookkeeping. Missing installed/private evidence is
an explicit gap, not a reason to reconstruct a body from prose or bypass denial.
The future integrator may add a shared index/coverage reference after owner
review; this isolated contribution intentionally changes **only this new file**.
