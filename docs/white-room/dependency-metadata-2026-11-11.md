# UIF dependency metadata qualification, 11 November 2026

This bounded register advances [UIF-247](https://linear.app/maldous/issue/UIF-247/unify-cross-project-agent-bootstrap-contracts)
work package 4 and the existing `restart-foo-dependency-discovery` ledger outcome.
It does not close either outcome, change the 213-record ledger count, select
other projects' sources, or admit a durable execution. It records source-backed
metadata and actual offline checks separately from unverified live dependencies.

## Accepted source and ownership reconciliation

- GitHub `maldous/uif` was verified PRIVATE. Remote `develop` was read as
  `e2019c61b2ac43e2546592fbbf8359b5e9c1f3b0` and fetched without moving the
  canonical checkout. The retained canonical `develop` checkout remained
  `adbca6df668dca605b915e99665020e81ef80f06`.
- This document was prepared in a fresh UID1000-owned task worktree on
  `feature/uif247-dependency-evidence-20261111`, based on that accepted remote
  revision. The shared Git store is the existing UIF repository. Only this new
  document is the assigned write surface; other worktrees and WIP are retained.
- Current Linear UIF-247 was read with no comments and remains Backlog. Its
  planning/qualification boundaries remain intact. This is a native assistant
  assignment, not a lease transfer, new controller, or durable-worker resumption.
- Open PR11 (remote reconstruction), PR21 (Morgan monitor), and PR22 (CQ binding)
  remain separate owners' candidate work. Their changes are not treated as
  accepted. In particular, PR22 does not silently qualify the null CQ mapping in
  accepted `config/white-room.json`.
- Local-only connector evidence `a8a39b6b8151a93d23c67915ae584300d10eb90a`
  remains in its existing UIF-211 worktree. Current UIF-211 still leaves exact
  installed connection identity and complete consumer continuity unresolved.
  Neither connector is selected for retirement by this record.
- The coordinator reports durable task `6af39664-d574-8191-a540-b49e6dce4034`
  quota-failed. It was not retried or resumed. This document does not assert a
  recovered Codex quota, terminated historical host job, expired lease, or absence
  of pending external operations. Authoritative live ownership/fencing and
  persistence remain unqualified below. No dependent dispatch is attempted.

The accepted local worker policy and manifest were read, with policy SHA-256
`8a905f5482d1e7f05868571a49756f36cde5f9cf8571e4049fcca95bc64fe9b5` verified.
The stable parent bootstrap integrity checker also passed. The older canonical
checkout lacks the accepted `docs/agents` distribution; this task uses the current
worktree instead of silently rewriting that checkout. No `.agents/skills`
directory exists in this selected source.

## Requirement-by-requirement qualification

Source: [accepted declaration](https://github.com/maldous/uif/blob/e2019c61b2ac43e2546592fbbf8359b5e9c1f3b0/config/white-room.json).
There are 18 requirements, 15 with null expectations. Seven apply to both declared
profiles; eleven are Foo-only (one of those only for OffMenu). All five project
entries retain discovery incomplete for both profiles. UIF and OffMenu have
repository mappings; CQ, CIX and ANQR remain null in this accepted declaration.
A metadata observation does not fill a null expectation or qualify another project.

| Requirement | Current bounded evidence | Remaining qualification |
| --- | --- | --- |
| github-access | UIF remote ref, fetch, private visibility and open PR reads succeeded | Null expected access contract; no broader write/grant or all-project access claim |
| linear-access | Current UIF-247 and UIF-211 reads succeeded | Null expected contract; no all-project access claim |
| project-toolchain | Source README and CI select Python 3.12; this Foo run used 3.12.3 and Git 2.43.0 | Runtime-specific and other-project toolchains remain unqualified |
| project-dependencies | UIF offline comparison and tests use Python stdlib; inventory uses Git | Windmill deployed dependency lock and other runtime dependencies remain unqualified |
| capacity | Native worker executed bounded sequential checks; filesystem showed about 594 GiB available and memory about 54 GiB available at inspection | Not durable admission, an aggregate resource reservation, or future capacity guarantee |
| ownership-interface | Current issue and candidate PR states reconciled for this isolated document | No authoritative CAS/lease/fencing interface qualification |
| persistence-compatibility | Synthetic checkpoint comparison tests passed | No live checkpoint schema, run/thread, pending-effect or migration qualification |
| foo-quota | Independent native execution completed | Codex durable quota not verified restored; no retry |
| shared-data-bindings | Not needed by these source-only checks | Actual data mounts/access modes not inspected or qualified |
| git-delivery-runners | Existing workflow declares offline commands | Live runner/webhook readiness not verified |
| project-service-bindings | No service needed for these checks | Live service bindings not verified |
| host-storage | Fresh user-owned worktree and isolated fixture writes succeeded | No all-mount health, durability or all-project storage claim |
| stokowski-binding | Existing declaration remains null | No current binding observation or service action |
| langgraph-installed-lock | Existing declaration remains null | No installed lock or persisted store read |
| windmill-root-interface | Accepted source declares exact workspace/path/tool/protocol and parameters | No fresh host receipt or effective installed-connection qualification in this pass |
| windmill-worker-health | Existing declaration remains null | No fresh worker/tag/capacity observation in this pass |
| stokowski-service | Accepted expectation says active/running | No fresh observation; historical inactive evidence cannot establish current state |
| windmill-bridge | Accepted expectation is OffMenu-only | Not used by this UIF offline task; no OffMenu continuity claim |

## Source dependency and deployment limits

The accepted [source provenance](../../config/windmill/source-provenance.json)
and [deployment bundle](../../config/windmill/deployment-bundle.json) agree on
source hash `ac933811fe4b250e025afb30d5be8d8a9f2afb587d18027e90fdeaa18f5e26ba`
and schema hash `42e52f00e5427384abd9fd253f0c832fc88a2ac24e0a0b2fe60dde5d993843b0`.
They declare `paramiko==5.1.0` and `wmill==1.850.0`; neither a resolved lock nor
Windmill version hash is supplied. The bundle's lock hash is null. These pinned
source dependencies are not packages installed for the stdlib-only qualification
run. No lock, deployed version, credential or host protocol source was invented.

`white_room.py plan` still reports explicit blockers: approved source/config
custody and tested restore; deployed source/schema and host protocol qualification;
native-job and all-consumer reconciliation; confirmed new credential destination
and persistent access; fresh-child identity, same-job status and terminal receipt.
It returns no runtime mutations and `live_recreation_verified: false`. Preserve
these limits and the current transport. This metadata review is not an alternate
route around any denied source or credential boundary.

## Actual checks and next accountable work

Observed on Foo as UID1000 on 2026-11-11, completed by 01:57:18 UTC:

- `python3 -m unittest discover -s tests/white_room -v`: 29 passed.
- `python3 -m unittest discover -s tests/windmill -v`: 24 passed.
- `python3 -m unittest discover -s tests/agents -v`: 9 passed.
- `python3 tools/windmill/white_room.py validate`: configuration valid;
  live recreation remains false.
- `python3 tools/windmill/white_room.py plan`: plan-only, no runtime mutations;
  existing blockers preserved.

The 62 passing tests are offline/synthetic qualification, not proof of real
reboot recovery, all-project readiness, runtime migration, consumer cutover,
distributed fencing or full dependency discovery. No service changes, schedule
changes, dispatch, replay, package installation or credential access occurred.
Generated Python bytecode stays local and is excluded from source publication.

Next work remains with the existing UIF delivery owner: select each actual
execution role, qualify its exact expected access/dependency contract and permitted
fresh observations, then reconcile authoritative ownership and original operations
before dependent admission. UIF-211 retains connector installed identity/consumer
work; UIF-219 retains cross-project immutable baseline/isolation selection.
No new gate or duplicated implementation is added by this record.
