# Native project activity, separate from sessions

This additive schema records actual native-worker lifecycle observations without
claiming that a persisted or local project session exists. It uses the existing
observation log, sequence guard, config hash, source head and immutable snapshots.
There are no migrations, credentials, collectors, schedules or production writes.
The unbound token reference in the example does not enable authenticated ingest.

## Reviewed source configuration

`coordinator-activity-config.example.json` shows a cloud coordinator source with
empty `aliases`, one capability (`coordinator_lifecycle`), a positive
`rosterGeneration`, 120-second stale and 600-second outage thresholds, and
`activityBindings: [{project, kind: "native_worker"}]`. Only the six public
project names are allowed; a project can be bound once. Native activity owns no
session aliases and does not change runtime generation or project bindings.
Legacy coordinator sources without `activityBindings` retain their old contract.

## Observation

`coordinator-activity-event.example.json` is an illustrative historical example,
not a current capture. A real event must retain the actual observation time.
Never replace an old observation time with publication time or infer state from
task text, earlier turn history, the existence of work, or a project name.

Activity-source events require `sessions: []`, session coverage
`{complete: false, observed: 0, total: null}`, the matching `rosterGeneration`, and
an `activities` array. Each activity has exactly `project`, `observedAt`, `state`,
`topicKeys`, `blockerKeys`. States are `running`, `completed`, `blocked`, `unknown`.
An interrupted or uncertain worker is `unknown`, never `idle`. A blocked state
requires at least one safe blocker key; other states require no blocker keys.
Topics/blockers use the existing allowlisted vocabulary, never arbitrary text.
Project IDs must have an explicit reviewed source binding. Extra fields, native
agent IDs, paths, private task text and future timestamps are rejected.

Each event is a complete replacement of that source's activity list. Omitted
projects disappear. A source failure must send an empty activity list and clears
all current activity assertions, even when last-good observations are retained.

## Public projection

Activity-enabled snapshots contain `projectActivities: []` even when there is no
current evidence. Each current entry has exactly:

- `project`, `kind: "native_worker"`, `verified: true`
- `observedAt`, `rosterGeneration`, `state`
- `topicKeys`, `topics`, `blockerKeys`, `blockers`

The safe reviewed `activityBindings` are also projected on the owning public
source to validate exact project ownership in the browser and saved artifacts.
There is no slot ID, private source ID, event ID, sequence or free-text metadata.
`projectAssociations` remains exclusively for session allocation. Native work
cannot affect session health percentages, coverage, denominator, or railway
lamps. The renderer may show a separately labeled native-work marker inside an
existing project hex, without changing its session-state border.

The server, shared public validator, browser admission and immutable retrieval
validate exact fields, unique ownership, generation, safe metadata, lifecycle,
and source/observation timing. Browser polling clears assertions on transport
failure and has independent expiry, including when an activity is older than its
source heartbeat. Source failure, expiry, removed binding and generation changes
all clear the current overlay. Historical immutable captures remain historical.

## Offline publication preparation

`prepareCoordinatorActivityObservation(sourceConfig, event, now)` from
`worker.mjs`, or `scripts/prepare-coordinator-activity-observation.mjs` with stdin
`{"sourceConfig": ..., "event": ...}`, performs no network, database, credential
or filesystem operations. The CLI only reads stdin and writes stdout/stderr.
It uses the receiver's actual config/event validator, canonical JSON and SHA-256.
The oldest actual event/activity observation must be at most 60 seconds old.
The result includes `preparedAt`, `publishBefore` (oldest observation +60 seconds),
config/payload hashes, and ten bound values in the exact existing D1 INSERT order:

`source_id, sequence, event_id, payload_sha256, observed_at, received_at, health,
config_hash, payload, allowance_observed_at`.

A separately authorized integrator must verify current source configuration and
sequence ownership, publish promptly before `publishBefore`, and use the
parameterized insert. Expired preparation needs a fresh genuine observation,
not new timestamps on cached state. The helper does not publish or grant access.
