# Explicit future-clone source profiles

UIF-140 owns these additional profiles in the existing
[bounded source migrator](cix-source-selection.md). They do not transfer native
claims, modify existing workspaces, resume admission, or settle original effects.

The actual installed hook reads on 16 November 2026 found one default-branch Git
clone, with no later fetch/checkout/switch/reset selection, for each of these
three projects. The exact original hook digest, repository, origin, paused state,
and full configuration preimage remain required. The new profiles change only
the selected `after_create` JSON scalar by adding a literal `--branch`:

| Explicit profile | Repository | Future source branch | Preserved delivery base |
| --- | --- | --- | --- |
| `cq-feature-bootstrap` | `maldous/CircularQuay` | `feature/initial-implementation` | `main` |
| `anqr-develop` | `maldous/anqr-v3` | `develop` | `develop` |
| `offmenu-develop` | `maldous/offmenu` | `develop` | `develop` |

CQ's owner selected the accepted feature bootstrap, not publication to that
branch. ANQR's observed GitHub default was `main` while its declared delivery
base was `develop`. An omitted explicit branch alone is not proof of OffMenu's
current default branch; verify the actual remote before claiming a mismatch.
These choices do not retarget any original main-bound publication intent,
replace old source/review identities, waive CQ's default-publication hold, or
authorize replay. Old original settlements and private bodies remain preserved.

The default `cix` profile retains its previously reviewed two-scalar main to
develop correction. No new profile accepts caller-supplied repositories, hook
digests or branch names. Use the reviewed source with the explicit profile:

```sh
python3 tools/white_room/migrate_cix_source.py --profile anqr-develop \
  --config "$ACTUAL_CONFIG" --expected-preimage-sha256 "$ACTUAL_SHA256"
```

This remains a dry-run until the same qualified scope adds `--apply`. The
selected-project guard requires fresh paused running, settling and tracked-task
counts of zero, both before preparation and immediately before effect. CIX, CQ
and OffMenu also retain the strict zero-retry requirement. Only ANQR's hook-only
profile preserves a nonnegative integer retry count while additionally pinning
the installed generic orchestrator source at both checks. A retry is never
consumed, reset, cleared or treated as a released claim by this migrator.

The qualified source is `orchestrator.py` SHA256
`7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea`.
Its only two normal `_dispatch` callsites are tick1599 and retry4447; both
synchronously check shared-pool pause before registering a task. Snapshot
paused=true comes from that pool, and running/settling/non-done tasks are
reported separately. Workspace/after_create occurs later inside that worker.
Thus with this source pin, pool pause and zero pre-existing active/tracked
workers, a retained retry cannot enter the normal clone route during this
future-hook-only edit. Missing/changed/symlinked source refuses application.

This is not a claim that pause freezes everything: retry metadata reconciliation
and claim bookkeeping precede the pause check; an already-dispatched worker has
no post-await pause recheck. Other modules/manual entrypoints and distributed
fencing are not proved here. The operator must retain actual sole-writer scope;
this guard neither grants it nor changes old operation/source identities.

The existing owner/path, durable private backup, strict preimage, cooperative
lock, final-state recheck and post-effect uncertainty protections remain in
force. Preserve one configuration writer and sequential actual preimages when
applying disjoint project profiles to the same shared file. Concurrent source
branches and checks do not permit concurrent shared-file replacement.

Qualification is distinct from application: five new credential-free synthetic
profile contracts cover hook-only byte preservation, immutable publication base,
allowlisted pins, selected-project state, and backup/readback. The existing CIX
contracts cover its unchanged behavior and shared migration machinery. Record
actual branch-aware Foo checks, exact native dry-runs, application, future clone
qualification and any blocked prerequisite separately. No file receipt alone
proves runtime installation, source acceptance, publication or gate clearance.
Six additional synthetic contracts cover the narrow retry-preserving behavior,
strict defaults/other profiles, active/pause/type refusal, source absence/drift,
nonblocking no-follow reads and the final pre-effect source recheck. Their
credential-free fixture source is explicitly substituted in tests, not accepted
as a production proof. Installed-source review and real native application
remain separately recorded evidence.
