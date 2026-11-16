# Native recovery preparation and supplied observation protocols

This bounded UIF-249/UIF-260 account names five decision flows in existing UIF
source. It adds no implementation, endpoint, runtime installation or authority.
Every inspected source byte is from **maldous/uif only**, commit
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. Native-source digest constants are
requirements enforced by these tools, not evidence that this pass acquired or
verified installed native bodies. No tool, embedded snippet or source test was
imported or executed in this documentation pass.

## Bound source and existing references

The four complete source modules (644 lines) and four complete test files
(694 lines, 48 named methods) were read. Counts are accounting, not measured
coverage or new passes. Existing operational references remain canonical for
usage/history: [composition](../../operations/native-recovery-composition.md),
[custody observation](../../operations/native-custody-observation.md),
[owner labels](../../operations/informational-owner-labels.md), and
[drift comparison](../../operations/foo-stokowski-drift.md). This document adds
state/transition/authority accounting rather than replacing those runbooks.

| Input path | Git blob | Lines / test methods |
| --- | --- | ---: |
| `tools/delivery/owner_label_context.py` | `819a67bfc04172922411c8644a19fafdd3d9789a` | 103 / N/A |
| `tools/delivery/native_custody_observation.py` | `da5e99197948eb4734506f3fc41c692c247deb26` | 239 / N/A |
| `tools/delivery/native_recovery_composition.py` | `7b00ade8229866e16a9bc4e216a7f2ee8ab27de4` | 143 / N/A |
| `tools/delivery/check_foo_stokowski_drift.py` | `86b857977eb3dab9be08ee339539f5a9f85441a0` | 159 / N/A |
| `tests/delivery/test_owner_label_context.py` | `010f3909ee50c0cc762acf059f3e1aaecf8f2f6f` | 87 / 7 |
| `tests/delivery/test_native_custody_observation.py` | `1efe5b8657ffca8b344d325f19b4e45c35358876` | 278 / 17 |
| `tests/delivery/test_native_recovery_composition.py` | `6360a9ea289ff284b175e232badfcd2d5c0d39ba` | 160 / 12 |
| `tests/delivery/test_foo_stokowski_drift.py` | `4da9940c16b71e339e1d4c70febb7e599ef7cad2` | 169 / 12 |

## `UIF-LABEL-SOURCE`: original bytes to a prepared context correction

Owner: the existing UIF native-source/installation custodian. In
[owner_label_context.py](../../../tools/delivery/owner_label_context.py),
`render_native_source` is a pure bytes-to-bytes transformer. Input must be bytes,
at most 1 MiB, with the fixed original source SHA-256 and exactly one literal
`machine_labels` expression. AST checks require exactly one synchronous
`_management_context` function and its one matching direct assignment, whose
value is a set comprehension. The only replacement extends that expression with
seven exact, case-sensitive informational labels: UIF, Cloud, ANQR, OffMenu,
CircularQuay, CIX and Morgan, each prefixed `Owner: `.

| State / event | Transition and guard | Effect / failure boundary |
| --- | --- | --- |
| Receive original | Exact type/size/hash/unique span and AST shape | Mismatch raises `CorrectionError`, not a best-effort patch |
| Prepare replacement | Replace the selected expression and parse resulting syntax | Return prepared bytes plus before/after hashes and false installation/adoption/gate/claim flags |
| Optional CLI emission | `--emit-candidate` selects a separately created path | Exclusive create, final-component no-follow, requested mode 0600; write/flush/file fsync, never overwrite |
| Reapply or adopt | A transformed input no longer matches the original pin | Refuse; no installed-source switch, gate rewrite, restart or claim transfer |

The CLI's explicit source reader admits only a bounded regular file, opened
no-follow/nonblocking at its final component. Parent-directory containment,
source access permission and actual sole-writer custody are not supplied by the
pin. The optional emitted candidate is the **one file-writing effect** of this
preparer. There is no directory fsync, rollback, resumable journal or installed
source replacement. If emission fails after creation, a partial/new candidate
may remain even though the CLI returns refused/exit 2. Reconcile that selected
output before retry; do not reinterpret `installed_source_changed=False` as
proof no candidate file was created. Success prints prepared metadata, exit 0.
Argument parsing and output errors are not a universal structured-error wrapper.

The intended label-context change does not neutralize ordinary comments or
arbitrary labels, reconstruct old requirements, qualify old gates, settle native
records or preserve retry objects. Syntax and the fixed transformation are not
a fresh review of the complete original native lifecycle.

## `UIF-CUSTODY-PREPARE`: exact original pair to observation-source additions

Source: [native_custody_observation.py](../../../tools/delivery/native_custody_observation.py),
lines 121–239 and its fixed snippet constants. The pure preparer receives original
orchestrator/web bytes, each at most 1 MiB and matching its fixed SHA-256. It
inserts exactly two methods and one route, then verifies removing those additions
restores both original byte strings and that both outputs parse as Python.

| Input / transition | Guard and output |
| --- | --- |
| Select orchestrator classes | Unique top-level `Orchestrator` and `MultiOrchestrator`; no existing observation method; unique `get_state_snapshot` anchor |
| Insert methods | Place before the anchor's earliest decorator/function line; preserve unselected original bytes |
| Select web route | Unique synchronous `create_app`, unique async `api_issue`, exactly the original issue GET decorator/span; no existing observation handler |
| Validate prepared pair | Exact inverse and syntax; return bytes and digest metadata with observation/installation/authority flags false |
| Changed/ambiguous/already prepared source | Refuse, rather than replacing pins or applying after another transformation |

The CLI reads selected bounded regular source files and prints metadata by
default; `--patch` instead prints a unified source diff. It creates no files,
loads no installed native module, invokes no endpoint and performs no adoption.
Patch mode deliberately emits source text, not a redacted receipt; preserve the
source's applicable disclosure rights. Final-component no-follow is not an
adversarial parent-directory guarantee. The core's byte inverse is preservation
proof for that preparation, not rollback of a live process.

## `UIF-CUSTODY-PROJECT`: process-local presence to a non-authorizing response

This is behavior of the **prepared snippets**, not an endpoint observed live by
this pass. The per-project method accepts 1–128-character ASCII issue selectors
made of alphanumerics/hyphens/underscores, with at least one alphanumeric. It does
not resolve a Linear issue or require UUID/Linear identifier syntax. It reads the
existing `_intake_snapshot_custody` tuple and retry map without creating a task.
The complete underlying native snapshot implementation was not newly acquired.

| Event | Response / transition | Not established |
| --- | --- | --- |
| Existing project selected | Multi-project method delegates to its existing orchestrator; project name needs only a string of length 1–128 | No project creation, global ownership lookup or new claim |
| Known/unknown local entries | New response containers contain timestamp/PID, bounded project/id, presence fields and selected gate/stage/run/retry attempt | False presence is not verified issue absence; registered task is not proof of liveness |
| Unrecognized scalar metadata | Exact strings of at most 128 characters or nonboolean integers 0–2,147,483,647 are projected; other selected values become null | Strings are not enum-validated or content-redacted; bounded metadata may still be private |
| Invalid selector / missing project | Prepared web handler returns 400 / 404; a valid issue selector with no local entries can return 200 | HTTP success is not issuer, lease, fence, retry recovery or issue-existence acceptance |

Claimed-set membership is retained only if actually boolean; other presence
fields report object existence, not serialized objects. No task/report/repair/
claim/retry context object is dumped or string-coerced by these snippets. Every
authority flag remains false and external owner/current issuer/lease/epoch/fence/
original settlement remain null. Fresh containers prevent caller mutation of one
response from becoming authority in the next.

This is best-effort process-local observation, not an atomic cross-project
snapshot, durable checkpoint or custody transfer. No lock, authentication layer,
access-control grant, journal, retry loop or effect fence is introduced by the
snippets. Existing hosting/authentication and approved private response custody
remain consuming obligations; PID/timestamp and selected scalar bounds do not
replace them. There is no export/restore of an old process or original retry.

## `UIF-RECOVERY-UNION`: strict standalone outputs to one exact source union

Source: [native_recovery_composition.py](../../../tools/delivery/native_recovery_composition.py).
The composer loads the two UIF sibling preparer modules from fixed checkout
paths via source compilation, without bytecode-cache emission. It does execute
those **repository modules** when imported; it does not import installed native
input. The loader itself does not authenticate its own checkout or modules by
hash. The selected source/custodian must supply that boundary.

1. Pass the **same originals** to both strict preparers, not one preparer's
   transformed output to the other. Keep their original-input pins unchanged.
2. Require all three fixed standalone output hashes and 1 MiB bounds. Verify the
   label-only output equals its one exact original-span replacement.
3. Apply that one label replacement to the custody-only orchestrator. Require a
   unique selected span and a bounded combined output.
4. Remove labels, custody methods and web route in the checked inverse orders.
   They must reproduce the exact standalone outputs and both originals. Parse
   prepared Python syntax; return two source byte strings and proof/hash metadata.

Inputs/output-pin drift, ambiguous spans, inverse mismatch, syntax or size
failure refuse. Receipt flags distinguish `source_prepared=True` from all
installation/runtime/endpoint/custody/authority/old-gate/retry-compatibility
claims, which remain false or explicitly unknown. Both CLI modes are stdout
only: metadata by default, unified diff with `--patch`; no source-file emission,
installation or external operation is performed. Neither a repeatable pure
union nor an inverse proves safe live adoption, original settlement or gate
compatibility. No automatic retry/compensation or competing installer exists.

## `UIF-DRIFT-COMPARE`: supplied objects to match/drift/unknown

Source: [check_foo_stokowski_drift.py](../../../tools/delivery/check_foo_stokowski_drift.py).
This stateless comparator has 18 leaf fields. Declared and observed roots must
be objects with the exact configuration keys. Types are exact (boolean UID is
not integer UID); UID is nonnegative, epoch a decimal string, SHA-256 lowercase
full hex, selected paths absolute without parent traversal. These are string
shape checks, not filesystem or ownership verification.

The declaration must explicitly select the installed-console entrypoint and
neutral isolated Foo infrastructure scope. An observed field/subtree may be
null to mean unknown. Within a known scope, `product_owner: null` instead means
explicit absence of a product owner; a null whole scope marks all three scope
fields unknown. Observed nonempty product-owner text is admitted as a mismatch,
not normalized away. Epoch strings are compared as supplied, not numerically.

| Leaf results | Aggregate result / CLI exit | Meaning |
| --- | --- | --- |
| Any mismatch, with or without unknowns | `drift` / 1 | Preserve both mismatch and unknown rows |
| No mismatch, at least one unknown | `unknown` / 3 | No omitted field becomes a match |
| Every leaf matches | `match` / 0 | Agreement of supplied values only |
| Invalid report/schema/types/keys/JSON/file input in listed handlers | `refused` / 2 | No host repair or admission |

The CLI requires an explicit report path and UTF-8 JSON with its schema version,
`declared` and `observed`. Duplicate JSON keys and non-JSON constants refuse;
extra top-level evidence is not an executable contract. There is no report-file
size cap, freshness/authentication check, host discovery or embedded-path open.
Output deliberately includes supplied declared/observed values and refusal error
text; this is not a secret-redacting transport. Keep its report/output private
where the supplied metadata requires it. The checked-in dated example is
historical supplied evidence, not a fresh current-service observation. No old
checker or historical successful test is re-executed by this account.

## Source-test oracles, issue impact and remaining review

| Test source | Distinct assertion intent, not fresh results |
| --- | --- |
| Owner labels, 7 methods | Exact expression/label set and other-label distinctions; original pin/AST/idempotent refusal; safe final-component source reads; exclusive candidate emission |
| Custody, 17 methods | Byte inverses/false flags/anchor refusal; synthetic private-object avoidance; scalar/selector bounds; fresh response containers; preserved web handlers; CLI metadata-only/refusal behavior |
| Composition, 12 methods | Same-original inputs, standalone pins, all inverses, false authority, changed/composed input refusal, strict output bounds, no bytecode/file emission and metadata-versus-patch output |
| Drift, 12 methods | All 18 leaf mismatches, mixed unknowns, type/key/path/hash/declaration refusal, CLI exits and no embedded-path execution/mutation |

Tests substitute synthetic admission pins and execute only their own synthetic
class/web fixtures where needed. Their source assertions do not establish
installed Core behavior, live web authorization, original claim/lease/retry
preservation, source access rights or unattended recovery. Actual execution was
not performed here.

UIF-249/UIF-260 receive five explicit protocols and bounded source/test-source
coverage, not whole-issue completion. UIF-120/130/140/247 retain installed wiring,
current issuer, original operations and supported state-preserving recovery.
Older gate/input-hold incompatibility and ordinary-comment effects are not fixed
by this documentation or byte composition. No denied body, sibling repository,
private native state or service was read. Revalidate affected assertions when
these source/test blobs, fixed pins or consuming runtime contracts change. The
shared index/coverage inventory and reviewed PR77/78/79 remain unchanged.
