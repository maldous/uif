# Bounded local Git inventory decision flow

This UIF-249/UIF-260 source account closes only the `inventory.py` allocation
excluded from the separate White Room machine reference. It changes no tool,
configuration, checkout or runtime. Source is **maldous/uif**
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. No inventory command, production
import or source test was executed for this document.

Bound inputs:

- Full [inventory.py](../../../tools/white_room/inventory.py), 78 lines, blob
  `66e15413b8322b13307152846fc6956d0525dca4`.
- Supporting inventory test at lines 107–135 of
  [test_check.py](../../../tests/white_room/test_check.py), blob
  `9a9e0b734fa44276178f953f4b3c34e2a6c3eee0`. The broader comparator cases were
  read previously; this account adds no new review/execution claim for them.

## `UIF-WR-INVENTORY`: selected checkout to bounded metadata or refusal

Owner: the caller selecting an authorized local checkout, not a work allocator
or remote source accepter. State is an invocation's selected root, sequential
Git reads, output buffers and resulting metadata; nothing is durably committed.
Relative/root-symlink inputs are resolved to an existing path, then the reported
Git top-level must equal that exact resolved root. A subdirectory is not silently
promoted to a different selection.

| Phase / guard | Decision / output | Boundary |
| --- | --- | --- |
| Prepare Git environment | Remove inherited `GIT_*`; disable lazy fetch, optional locks, terminal prompts, system/global Git config; disable fsmonitor, hooks and untracked cache for each command | This is scoped command configuration, not authentication or an OS sandbox |
| Execute fixed argv | No shell; pipe stdout, discard stderr; drain with selectors | Maximum 4 MiB and 15 seconds **per command**, not a single whole-invocation budget |
| Output inactivity until deadline, oversize or unexpected exit; invalid UTF-8 after completed read | Guarded read/wait failures kill and wait for that Git subprocess; UTF-8 decoding occurs after child completion, outside that guard | Successful empty EOF with an allowed exit can be valid (including configuration no-match/exit 1); no general descendant containment or automatic retry |
| Bind selected root | `rev-parse --show-toplevel` resolves to requested root | Does not verify remote origin, source acceptance or current claim |
| Check acquisition mode | Presence of a partial-clone/promisor configuration key refuses | Name-only query rejects the key's presence, without interpreting its value as a safe false |
| Enumerate declarations | NUL-separated tracked `HEAD` tree paths; select known declaration basenames, root `config/`, `.github/`, `.agents/`, and `.service` paths | No worktree file-body read or semantic interpretation of a manifest |
| Observe local identifiers | Separate HEAD, symbolic branch and local `refs/heads/` reads | Metadata may move between calls; no atomic source snapshot or final pin recheck |
| Return metadata | Version/time/resolved root/head/branch/ref and declaration-path lists | Dirty state remains null/unknown, remote verified and discovery complete remain false |

Known declaration basenames include AGENTS, Python/JavaScript/Rust/Go/Elixir
manifests/locks and Compose names. That finite selector is not exhaustive discovery
of every build dependency or installed capability. The result supplies no
per-path content hash, service state, provider allowance or publication evidence.
A local branch/ref is not proof its commit is pushed or accepted remotely.

The helper intentionally does **not** run `git status`, diff, refresh or clean
filters. It does not fetch/reset/clean a checkout, inspect untracked/ignored
bodies, check other worktrees or contact an external service. `dirty=None` is
not a cleanliness pass, and a branch that moves during sequential reads can
produce metadata from different instants. The consumer must separately bind its
actual source and reconcile work/custody before dependent effects.

A symbolic branch read requires a successful exit; detached HEAD is not converted
into a branch name and may refuse the invocation. The CLI catches its listed
input/file/subprocess errors, prints fixed `inventory_failed` with discovery
false and returns 2; success prints metadata/returns 0. Argument parsing/output
are not a universal error envelope. Paths and ref names are still potentially
private metadata; no body disclosure does not mean arbitrary external sharing
is authorized. The helper adds no redaction, claim, lease, receipt journal,
checkpoint migration, resource admission, dispatch or runtime repair.

## Existing synthetic oracle and residuals

`test_read_only_dirty_checkout_and_declaration_discovery` creates a fabricated
Git repository with an untracked private marker and changed manifest, configured
fsmonitor and clean-filter programs that would leave marker files if run. Its
assertions expect unknown dirty state, a declared manifest path, no private body
in output, neither program invoked and all starting file bytes unchanged. It
then adds a promisor key and expects refusal. These are inspected assertions,
not a fresh test result or hostile-repository containment qualification.

UIF-249/UIF-260 gain one bounded protocol, not exhaustive module/consumer
coverage or whole-issue completion. UIF-247/140/130 retain actual remote-source,
workspace/custody and recovery obligations. Source/declaration changes require
revalidation of affected assertions. No sibling repository, private native
source, real service or original uncertain operation was accessed or replayed;
shared index/coverage and accepted PR77/78/79/80 remain unchanged.
