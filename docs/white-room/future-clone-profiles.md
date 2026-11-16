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
existing selected-project guard requires fresh paused running, settling,
tracked-task and retry counts of zero, both before preparation and immediately
before effect. A paused retry is not silently discarded or treated as a released
claim; if present, application refuses while independent source work continues.
Any narrower future-only retry-preserving condition needs actual installed
control-flow evidence and a separately reviewed change, not a counter reset.

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
