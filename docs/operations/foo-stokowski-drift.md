# Foo Stokowski configuration drift

Bounded source contribution for the original README deliverable
`restart-foo-configuration-drift` (`uif-ledger-20261111-060`). The accompanying
[JSON report](foo-stokowski-drift.json) records supplied owner evidence and an
explicit comparison contract. This is source preparation, with full runtime
reconciliation unproved. It does not repeat `restart-foo-validation` or its checker.

Local source baseline: `f982b939ae917c0b5aa6e2e6db118d851e273aed`, branch
`feature/restart-foo-configuration-drift-host-20261113`. Remote currency was not
checked. Shared policy 1.2.4 SHA-256:
`4b88a8be6d6f4d8bbd721a1f22bd3d47250d5391be220b5ed77011249e226fa9`.
The declaration here is selected from this assignment's requirements and pins;
it is not an independently accepted or installed deployment manifest.

## Current supplied observations

Recorded 2026-11-13 from the assignment; the observation timestamp was not
supplied. No host, private session, manifest bytes or service was inspected.
The JSON preserves both supplied hash pins and their exact file paths. Those
hashes do not attest executable bytes, the full source tree or deployment health.

| Binding | Supplied evidence and limit |
| --- | --- |
| Logical authority | Owner `uif-platform-owner`, store `uif-reconciliation-20261112-01`, epoch `1`; retained independently of OS custody |
| OS custody | Existing account `user`, UID `1000`; transfer receipt `01a1d49f-4366-1699-c230-9ae7f63013b4`, 3,874 entries reported |
| Neutral service | `stokowski-foo.service`, release `/opt/uif-stokowski/host-platform-20261113-a1`; active/substate not supplied |
| Runtime and Pool | `/var/lib/uif-stokowski/runtime`, database `/var/lib/uif-stokowski/runtime/pool/state.sqlite`; no data move or data-integrity proof |
| Entrypoint correction | Installed console entry point importing `uif.ledger_coordinator.main`; `python -m` duplicates `CoordinatorBindings` class identity. Actual ExecStart and console executable path remain unknown |
| Project scope | Project-neutral Foo infrastructure is required. Actual isolation and other-project admission remain unqualified |

The custody transfer changes OS ownership to existing UID1000. It neither
transfers the logical framework authority to a product owner nor moves Pool
data, replays accepted a7 work or completes BAU.

## Earlier typed-controller assumptions

Retain legacy owner `offmenu-cloud-github-owner-01a1866b` and legacy service
`offmenu-stokowski-current.service` as historical identities. The latter is
reported `inactive/dead`. At the local baseline, `config/white-room.json` still
expects that legacy service `active/running`. That declaration conflicts with
the supplied legacy state and neutral replacement identity; it is preserved
unchanged rather than silently rewritten to match the observation.

Earlier typed-controller/product-owner bindings do not establish the current
shared infrastructure owner, effective ExecStart, health, registration consumption
or admission. No historical UID is inferred. The JSON separates this historical
reconciliation from its current-object comparison. No old validation checker is
called and no historical controller or store is recreated.

## Reusable offline checker

From the repository root:

```sh
python -B tools/delivery/check_foo_stokowski_drift.py docs/operations/foo-stokowski-drift.json
python -B -m unittest discover -s tests/delivery -p test_foo_stokowski_drift.py -v
```

The stdlib API `compare_configurations(declared, observed)` compares supplied
objects. The CLI requires an explicit JSON path with `schema_version`, `declared`
and `observed`; it has no default report or host-discovery mode. The JSON's
configuration shape is the input example. All configuration keys are mandatory;
null observed fields/subtrees represent unknowns. `product_owner: null` inside a
known scope explicitly means no product owner. Unknown complete scope remains
unknown for every scope field. Declarations require the corrected installed
console entry point and neutral isolated infrastructure scope.

Each result contains field paths, declared/observed values, status, counts,
structured mismatches and unknowns. The checker compares framework owner,
store/epoch, service/user/UID, entrypoint mode/import target, release path and
manifest hash pins, runtime/Pool paths and isolated project scope. It refuses
incomplete/unsupported configuration keys, wrong types, malformed hashes,
relative/traversing paths, duplicate JSON keys and malformed reports. Metadata
outside the two configuration objects is retained evidence, not executable input.
No embedded path is opened and no command from the report is executed.

Exit codes: `0` supplied values match, `1` drift, `2` refused input, `3` unknown
without a known mismatch. Drift takes precedence while retaining unknown fields.
A match means agreement of supplied values only. It grants no runtime admission.
The current supplied report yields **13 matches, zero mismatches and five
unknowns** (entrypoint's two fields and project's three fields), exit `3`.
Legacy declaration drift is separately recorded above, not hidden by these counts.

Tests use a synthetic complete matching observation plus concrete changes to
every compared field, mixed drift/unknowns and refusal cases. They test only this
checker using unittest directly. Actual result: **12 tests passed**, exit `0`.
The supplied-report checker run returned exit `3` with the counts above.
README bytes matched local HEAD and the changed path set was exactly the four
allowed files. Synthetic matches are not service evidence.

## Unproved outcomes, disposition and next action

Current service active/substate, effective ExecStart, registration consumption,
CPU policy, executable pin, full dependency closure, Pool integrity and data
continuity remain unproved. So do runtime isolation, claim fencing, concurrency,
recovery, other-project outcomes and full BAU. Supplied paths and hashes are not
an independent live-state attestation or exhaustive dependency inventory.

Retain GitHub writes/publication/PR/merge holds, unqualified other-project
admission, all fenced original unknowns and accepted a7 coding/validation/review
without replay. CIX execution and production stay paused. All old unknown
histories and denied-action restrictions persist. The unchanged README retains
213 original action IDs and 11 nonadditive references. No ledger, policies or
controller state is mutated, and no credentials, network, private sessions,
privileged actions, live installation or service repairs are used.

Disposition: bounded four-file source contribution, pending owner review; no
whole-row completion, publication or BAU claim. Next action belongs to the
existing UIF infrastructure owner: review these exact files and obtain separately
authorized effective configuration evidence before dependent runtime admission.
Keep the existing owner/store/epoch and original row; this report is evidence,
not another tracker or authority source.
