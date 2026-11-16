# Policy conformance, startup installation and historical scope audit

This UIF-249/UIF-260 contribution documents four bounded decision flows in UIF,
not a new policy, instruction installation, work queue or runtime permission.
Source edition: **maldous/uif** `43fa5e645e1cdb91ec75136a7509b8c88c085ab9`,
tree `3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. Only this repository was read.
Role/repository names in declarations are compared as metadata; no product
checkout or cross-repository qualification is required by this account.

The three source modules (715 lines) and their three test files (598 lines,
58 named test methods) were read completely. Tests and production modules were
not imported or executed. Counts describe source coverage, not new passes or
whole-repository completeness. The current policy manifest and environment
profile were read as declaration context, not installed or host evidence.

| Input | Git blob | Lines / named test methods |
| --- | --- | ---: |
| `tools/agents/check_policy.py` | `6f1550442195b0cd7b4cc5ae705789a6099a5dd1` | 392 / N/A |
| `tools/agents/install_policy_bundle.py` | `401e80a01e3054aae95e74535adeae01d45d75e2` | 221 / N/A |
| `tools/agents/audit_linear_migration.py` | `07cee344efdca270da82dbb5380c915e86117f47` | 102 / N/A |
| `tests/agents/test_policy.py` | `370aebb33c8e7dcd5a5aae8fec343804972c0d47` | 337 / 39 |
| `tests/agents/test_install_policy_bundle.py` | `4987036b2f636982601235e6c1770b369ffbd6f8` | 189 / 13 |
| `tests/agents/test_linear_migration_audit.py` | `c75c09400cc0ac59acc9ce1f9efd292d8eec77f1` | 72 / 6 |
| `docs/agents/uif-worker-policy.json` | `7c7977a8c892274e14cf0bad564ae4af062884ca` | Declaration context |
| `docs/agents/environment-profiles.json` | `6db2c2aab380a62fd6496217a8fe7e6376be13ce` | Declaration context |

## `UIF-POLICY-CONFORMANCE`: declared source to diagnostic list

Owner: the environment bundle maintainer and caller selecting its trusted UIF
source. [check_policy.py](../../../tools/agents/check_policy.py) compares local
files; it does not authenticate a Git checkout, discover tools, install anything
or read the named product repositories. The API returns an error list; empty
means selected **source conformance**, not operational readiness.

| Decision | Guard / result | Authority boundary |
| --- | --- | --- |
| Load selected bundle | Resolve root; bounded relative reference reads, at most 1 MiB each; reject traversal/nonportable paths and symlinked relative components | This is not a race-proof filesystem sandbox; symlink checks and open are separate |
| Bind policy | Fixed manifest fields/version 2.0.1; optional expected bundle repository; SHA-256 of actual policy; exact caller-supplied expected policy bytes and header | Hash equality does not authenticate the caller's selected source |
| Bind startup/reference texts | Root AGENTS contains required policy/manifest pointers; selected text documents are UTF-8, LF-only and final-newline terminated | Manifest/profile JSON is parsed separately, not subjected to that same text-line rule |
| Validate profiles | Required role/executor sets, declared project matrix, repository/baseline/overlay/memory and capability constraints | These are source declarations, not observed grants, host limits or installed backends |
| Bind optional implementation record | Status/bounded path/hash/subject/source revision/time/receipt-reference shape | No remote receipt fetch, authenticity or current-runtime freshness proof |
| Selected role/repository | Selected repository belongs to its declared role; repository selection requires a role | Metadata selection does not load that repository |

The profile validator preserves six logical roles, of which five are product
matrix members; Cloud is the assigned infrastructure adapter, not a sixth
product gate. Repository bindings must not overlap roles. Nonstandard product
baselines need an explicit exception; Cloud retains its exact repository/main
and non-product boundary. Private memory namespaces/shared metadata/no-private-
data declarations and required/optional capability names are checked, not
provisioned. Capability names are found as literal backtick-delimited inventory
entries, not discovered through connectors.

Intake is declared native-Linear-only with canonical UUID field names, no ledger
queue, and an off-Foo claim authority. The local-session ceiling is exactly 64,
shared with MCP resource accounting; this does not measure available capacity.
Deterministic MCP profiles cannot declare reasoning/session control, root
transport retains checkout-owner execution, and cloud cannot declare a shared
Foo filesystem. These constraints do not implement the actual adapters.

The outage declaration requires positive integer configurable grace, at least
two qualified probes and healthy-probe reset. The current profile defaults to
900 seconds, but the validator accepts an explicitly configured positive grace,
not only 900. Existing-authorized inference continuity may not invent a new
provider. No probes, grace timer, scheduling or failover are run by validation.

`_implementation` admits `unimplemented`, `unqualified` and `qualified`. A
qualified claim requires a local hash-bound evidence record; any supplied record
must bind the selected subject/status, full 40-hex source commit, timezone-aware
observation and 1–128 distinct bounded opaque receipt references. Even a very old
observation can satisfy this **source** check: no age or live-receipt verification
is performed. The current declarations explicitly permit off-Foo authority to
remain unimplemented and Morgan lifecycle unqualified without fabricating a
runtime pass. Nonfinite JSON constants and duplicate keys refuse, but this is
not a complete arbitrary-JSON semantic validator; fixed fields/substructures are
checked and some additional fields are not rejected.

The CLI selects `--bundle` or legacy `repository=checkout`, not both, and reads
its own source policy as the expected bytes. It can additionally check routing
references only with `--bundle`. A successful invocation prints the explicit
source-only PASS; errors exit nonzero. Argument/output errors are not a universal
structured JSON protocol. No work, install, network, claim, memory or publication
effect follows from an empty error list.

## `UIF-POLICY-REFERENCE`: routing metadata without a policy copy

`check_reference` validates a bounded JSON file from the selected reference
parent and the bundle's profile declaration. Fixed schema/version/distribution/
maintenance fields must match. Its role must exist, its repository belong to
that role, any caller-selected role agree, and an optional repository-surface
field equal the repository. Historical paths must be portable relative names;
**historical files are not opened as current policy**.

A reference with a `sha256` field refuses: it cannot assert an installed policy
digest. This API alone does not run the complete bundle conformance check; the
CLI checks its selected bundle and references separately. Success establishes
routing shape only, not a copy requirement, installed capability, permission or
live source selection. There is no persisted state, transaction, retry loop or
callback effect in either conformance/reference function.

## `UIF-STARTUP-INSTALL`: preimage-bound instruction pointer replacement

Owner: the actual startup-file owner under separately authorized instruction
adoption. [install_policy_bundle.py](../../../tools/agents/install_policy_bundle.py)
is dry-run by default. It deliberately imports the static sibling validator
without bytecode emission; that executes trusted repository code, not a native
service. This documentation does not invoke it or edit any instructions.

| Phase | Guard / transition | Persistent effect |
| --- | --- | --- |
| Validate source | Absolute nonsymlinked path without parent traversal/control/backtick ambiguity; bundle must equal helper's selected checkout root, full commit equal actual HEAD, Git worktree clean, policy conformance valid | None |
| Select startup | Absolute nonsymlinked path outside bundle; singly linked regular file owned by actual UID, at most 1 MiB, valid UTF-8; acquire cooperative nonblocking exclusive flock and match exact preimage SHA-256 | None; input was read before lock acquisition |
| Render dry-run | Prepend version/digest/commit/bundle routing and retention notices; preserve old text, adding a final newline if absent | Return proposed hash and backup path; no backup/replacement |
| Preserve original | Exclusive new preimage-named backup, requested mode 0600, write/flush/file fsync and parent-directory fsync; existing backup must be owned/single-link/regular and byte-identical | New backup can remain, including an incomplete one after failure; never overwrite different bytes |
| Prepare replacement | Temporary sibling, original permission mode, write/flush/fsync | Temporary file only |
| Final guards | Validate bundle again, reopen startup and compare original device/inode/bytes | Refuse drift; retain original backup and clean prepared temporary on normal cleanup path |
| Replace/read back | Atomic `os.replace`, startup-parent fsync, reopen and compare bytes plus mode | Return `installed` and readback hash only after these checks |

The instruction prefix preserves older text as retained history; it is not a
managed-section parser or automatic deduplicator. A repeat with the old preimage
refuses after successful replacement. A new explicit preimage can produce a new
prepend, not an implicit idempotent upgrade. New backup creation asks for mode
0600; reuse of an existing matching backup does not separately enforce that mode.
Replacement preserves permission bits, not a documented guarantee of every
original group/ACL/xattr. No package, model, memory backend, service or execution
policy is configured by this file replacement.

The Git helper disables optional locks and configured fsmonitor, uses argv/no
shell and 30-second subprocess timeouts. It is not an all-configuration/Git-
environment sandbox or remote-source acceptance verifier. The installer does not
fetch, prove a remote origin/branch/review or establish a distributed lease.
Caller-selected reviewed source and nonadversarial checkout/sole-writer custody
remain prerequisites. The lock is attached to the original inode, so it is not a
general writer fence after rename; path checks also cannot fence arbitrary races.

Crucially, there is **no post-replace uncertainty latch**. A parent-fsync or
readback failure can occur after the new startup is already visible. The CLI's
listed adoption/file/subprocess errors yield `status=refused`, instruction-only,
exit 2; that label is not proof replacement never occurred. Reconcile the exact
startup bytes, original backup and original attempt before any repeat or repair.
No automatic rollback deletes history. Success/exit 0 means instruction pointer
installation only, not owner acknowledgment, instruction consumption, runtime
readiness, unpause, restart or successful product delivery.

## `UIF-MIGRATION-AUDIT`: frozen text to primary-import accounting

Owner: the migration/reconciliation caller supplying complete issue readbacks.
[audit_linear_migration.py](../../../tools/agents/audit_linear_migration.py) is
an offline pure audit, not native Linear intake. Its input is a historical README
string and an explicit list of full `id`/`description` objects. It makes no
connector call and cannot authenticate or prove the completeness of that list.

| Stage / state | Guard or projection | Meaning / failure |
| --- | --- | --- |
| Split historical source | Require literal nonadditive heading; enumerate action bullets before it and reference bullets after it; action set nonempty/unique and reference set unique | The heading's word “11” is not a check that the actual reference count equals eleven |
| Accept issue readback | Dict, string ID/full description, unique issue ID; at most one primary import marker | Missing description, duplicate issue or repeated marker refuses |
| Select primary span | Only after primary import marker and before ancillary heading | Unmarked descriptions and archival whole-README copies cannot supply primary coverage |
| Normalize/count | Broad inline issue-label substitution and removal of bold markers, then exact action-prefix line recognition | Text accounting, not the authenticated native-scope projector or general HTML canonicalization |
| Summarize | Missing action IDs, unique actions, every occurrence and receiving-issue hashes/counts | Repeated wording, including twice in one issue, is reported but not extra unique work |
| No missing actions | `scope_accounting_complete=True`, CLI exit 0 | Multiple primary occurrences can still exist; no semantic acceptance or issue completion is inferred |
| Missing actions / invalid inputs | Incomplete result/exit 1, or CLI parser error/exit 2 | No issue mutation, new dispatch or repair |

Evidence hashes cover each supplied description string and the source README;
they are not `linear_scope.py`'s ID/identifier/title/description native digest.
Optional URLs/update times are copied metadata, not independently verified
locators or freshness evidence. There is no status/archival/dependency eligibility
filter or atomic issue snapshot. The CLI reads explicit UTF-8 files and ordinary
JSON; unlike the policy decoder, it adds no duplicate-JSON-key validator or
input-size limit. It prints source IDs and selected readback metadata, not issue
prose; private metadata still needs its applicable output custody.

The audit changes no issues, source, claims, accepted dispositions, services or
runtime. Frozen README IDs remain provenance. Full accounting cannot revive
superseded requirements, override a newer owner correction or authorize replay
of an uncertain operation.

## Source-test oracles and residual scope

- **Policy, 39 methods:** bundle/version/hash/UTF-8/path/role/reference constraints;
  Cloud versus five-product boundaries; memory/capability separation; source-only
  implementation declarations; outage/inference rules; pinned historical
  qualification record and no product-policy-copy requirement.
- **Installer, 13 methods:** dry-run/source-cache/index preservation, atomic
  bytes/mode/readback, stale commit/preimage/dirty source, symlink/owner/hardlink,
  backup collision/reuse, concurrent target/source changes and replace failure.
  These synthetic cases do not newly test a real post-replace failure or host
  recovery in this documentation pass.
- **Audit, 6 methods:** primary inline-label coverage, ancillary-copy exclusion,
  repeated-versus-unique counts, abbreviated/duplicate/ambiguous readbacks,
  historical boundary/ID refusal and unmarked descriptions.

No inspected assertion is a newly executed result. UIF-249/UIF-260 gain bounded
source-machine coverage, not exhaustive identification or global documentation
acceptance. UIF-247/120/130 retain actual installed pointers, owner consumption,
qualified executor/authority/scheduling and state-preserving recovery obligations.
Prior task-local installation receipts remain at their own source and attempt;
none was replayed here. Revalidate these assertions after source/test/declaration
changes. No shared coverage inventory, index, active instructions or accepted
PR77/78/79 content is modified by this new-file contribution.
