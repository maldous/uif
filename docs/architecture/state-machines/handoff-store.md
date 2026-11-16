# Private handoff-store persistence machines

This partial UIF-249 pass describes the complete 390-line
[`handoff_store.py`](../../../src/uif/handoff_store.py) at the primary edition
in the [coverage account](source-coverage-20261116.json). Its bytes are unchanged
at the documentation branch's base, UIF `caead2f9a515cb854f6e81007638abe450009d2c`.
It changes no implementation or authority. These are interacting filesystem
protocols inside one component, not three deployed services or a work queue.

## Owner, inputs and custody

`PrivateStore` is a UIF-owned, single-host persistence primitive consumed by
[`Registry`](../../../src/uif/quota_handoff.py). All cooperating clients must
reach the same private root and stable lock on the same host. The constructor
requires an existing absolute, resolved directory owned by the effective UID
with mode `0700`. Registration/state files and locks require the same UID,
regular single-link files and mode `0600`. Bounded state reads use no-follow,
nonblocking descriptors and compare descriptor/path identities before returning
the decoded object and raw-file SHA-256.

Canonical JSON serialization uses sorted keys, compact separators and refuses
nonfinite Python values. Decoding requires a bounded object, rejects duplicate
keys and the named nonfinite JSON constants, and translates selected parse
errors into `Hold`. It is not a complete application-schema validator; numeric,
actor, source, task and operation semantics belong to its caller.

The selected source contains no remote transport, independent election,
off-Foo replication, provider credential, scheduler or work-selection policy.
Its existence does not identify a currently installed store or prove that an
old native Core process uses it. In particular it is **not** a checkpoint/restore
adapter for the separately observed Core's in-memory retry/claim maps.

## UIF-STORE-HOT-TRANSACTION

| State or event | Guarded transition and observable result |
| --- | --- |
| Unprovisioned root | Explicit `initialize` creates a new root with `exist_ok=False`, then exclusive registration/state/lock files, file fsyncs and directory fsyncs. It never overwrites an existing root. An interrupted partial provisioning is not automatically repaired or rolled back. |
| Existing root, transaction requested | Validate custody, open the existing stable `authority.lock`, take a nonblocking **exclusive** advisory lock, then recheck lock/root identity. A busy lock refuses with `authority_busy`; even a readonly transaction uses this lock. |
| Locked snapshot | Read registration/state plus raw digests. When the state names a cold archive, check its registration binding and minimum committed archive revision before yielding `(registration, state, save)`. |
| Caller edits yielded in-memory state | No automatic persistence occurs at context exit. The caller must invoke `save`; readonly transactions refuse it. |
| Explicit save | Re-read registration and expected state digests, prepare retention, bound serialized state, write/fsync a private temporary file, replace `state.json`, fsync the root, then verify raw readback. Only after success update the closure's expected digest and committed snapshot. |
| Context exit or exception | Close the lock descriptor. The primitive does not translate every OS error, infer whether a partially observed effect committed, or retry under a new request/operation identity. |

The whole-record raw digest is an optimistic preimage check in addition to the
cooperating lock, not an authenticated signature or distributed compare-and-swap.
The replace is the visibility-changing point. A later directory-sync/readback
failure can therefore follow an effective replacement; an exception is not proof
that the old bytes remain. Recover the original operation before any effect
retry. No rollback, deletion or implicit new registration is supplied here.

## UIF-STORE-COLD-RETENTION

The hot JSON state is capped at 8 MiB. Retention targets 256 recent request
receipts and 256 events, but only **previously committed, still-identical**
records are eligible: these are not unconditional upper bounds on a newly
prepared hot snapshot. A task is eligible only in `complete` or `failed` state,
with a completion object and every current/historical operation in `verified`,
`failed` or `denied`. A pending/unknown operation is not silently terminalized.

| State or event | Guarded transition and retained identity |
| --- | --- |
| No archive and nothing eligible | Return a copied hot state without creating SQLite storage. |
| First eligible compaction | Under the caller's authority lock, construct and commit a complete temporary SQLite schema bound to the raw registration digest, fsync it, publish it by replacement and fsync the root. |
| Existing archive | Verify file custody, schema/registration and nonnegative revision. Open with zero SQLite wait timeout; readonly calls select `mode=ro`, others `mode=rw` and full synchronous setting. |
| Archive transaction | `BEGIN IMMEDIATE`; insert committed request results/events/task generations, require exact equality for pre-existing keys, advance archive revision and commit. Conflicting archived bytes refuse rather than overwrite history. |
| Cold commit succeeded | Remove those records from the copied hot state, set schema `uif-quota-authority/2`, and bind a minimum archive revision before attempting the hot-file save. A crash before hot replacement can leave matching duplicates, not a newly fabricated committed request result. |
| Archival I/O unavailable during save | For the selected OS/archive-unavailable failures, keep all records hot and mark `archive_degraded`. Other `Hold` conflicts propagate. A later successful compaction clears the marker. |
| State already requires missing/older archive | Refuse `required_archive_unavailable` or `archive_revision_rollback`; do not recreate history or accept a lower revision. |

The schema retains request fingerprints/results, revision-keyed events,
task-generation records and a latest-generation task head. Exact request lookup
returns its archived fingerprint/result; task lookup checks terminal shape,
generation and the complete workspace/project/issue identity. `Registry` then
enforces its authentication, coordinator epoch and request fingerprint before
idempotent reuse. A cold receipt cannot revive an obsolete coordinator.

`require_headroom` holds **new** admission when archival storage is degraded or
the hot state would consume the control reserve: at least 1 MiB, or a larger
active-task/uncertain-operation estimate. This is not a guarantee against a full
disk or all future control growth. The primitive has no archive-retention expiry,
size quota, backup/restore service or cross-host recovery implementation.

## UIF-STORE-TASK-FENCE

`task_fence(identity)` derives a lock filename from the canonical identity
digest, opens or creates that task's private regular lock, takes a nonblocking
exclusive advisory lock and checks the opened/path inode join. A held same-task
lock refuses `task_effect_busy`; the descriptor's lifetime delimits the fence.
Different identities use different locks, while metadata saves still serialize
briefly through the authority lock. Lock files are not removed at exit.

In the inspected `Registry.invoke` and synchronous/asynchronous effect callsites,
the task lock spans reconstruction or the bounded external callback; global
metadata transactions do not span those waits. The caller persists a pending
operation before the effect, preserves an original pending/unknown operation
after lost replies, and rechecks live state around proof use. Those callsite
spans are context for this store, **not** a complete semantic review of all
765 registry lines or a claim that arbitrary callbacks are confined.

Neither advisory lock authenticates an external destination, fences another
host/issuer, prevents a noncooperating process from replacing files, or transfers
an existing native claim. Callers retain original identities and trusted adapter
requirements. A lock held in a synthetic fixture is not current production
authority or evidence that native effect admission is safe.

## Existing source oracles and remaining scope

The complete 239-line
[`test_quota_retention.py`](../../../tests/delivery/test_quota_retention.py)
contains nine selected source oracles: request reuse/conflict after pruning,
legacy-large receipt compaction, cold-before-hot interruption, terminal lineage,
unknown-operation retention, headroom/control fallback, missing/stale archive,
readonly status, and interrupted first archive publication. They use disposable
fixtures and injected verifiers. This documentation pass **reads**, but does not
execute or extend, those tests. They are not proof of arbitrary crash windows,
every filesystem/platform, adversarial replacement or a live receiver.

`handoff_store.py` and those nine test bodies have a full bounded static read.
The selected registry callsite spans remain explicitly partial context; its
task/coordinator/provider/handoff state machines, all other source oracles,
CLI/native consumers, installed configuration and current ownership/receiving
joins remain separate open work. Impacts include existing UIF-120/126/130/247
delivery and continuity scopes, not a newly imposed blocker for unrelated
project work. Full UIF-249 remains incomplete.
