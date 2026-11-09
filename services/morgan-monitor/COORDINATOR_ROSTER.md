# Manually observed coordinator roster

This protocol is not yet enabled in production. It is additive to deployed release
`55338e8`, its registrations or its credentials. It does not create an automatic
cloud feed. No production source is registered or seeded by these files.

## Evidence, not an OS probe

`proof: "process"` retains its existing process-liveness meaning. A native
coordinator API reporting a running/completed agent is different evidence and
must use `proof: "coordinator_lifecycle"`. The same authenticated coordinator
may explicitly report a blocker received from a trusted assigned worker. A
blocked row requires a reviewed blocker key; it must not be inferred from
missing telemetry, task wording or a completed API state.

The allowed lifecycle states are `running`, `completed`, `blocked`, and
`unknown`. Current individual display states are permitted only while the
source and dated observation are fresh. Completed workers may render blank,
but the recorded API state remains completed, not an invented idle/stopped
process state. This proof never establishes OS process liveness, complete
execution coverage, an all-stopped condition, or useful progress between reads.

Public source attribution is fixed `observationMode: "manual"` and
`evidenceScope: "coordinator_lifecycle"`. It represents an authenticated,
explicit coordinator observation, not a claimed continuously polled service.
The source must be refreshed by a new actual observation; copying a timestamp
forward without observing the API/report again is not permitted.

## Source registration and generation

Only one source may have the new capability. It must belong to `domain: "cloud"`
and cannot also claim `process` capability. Its operator-reviewed configuration
must include a positive integer `rosterGeneration` from 1 through 1000000.
The number 1 is the public generation called R1; the protocol carries a number,
not the string `"R1"`, private agent identifiers, thread paths or historical IDs.

An illustrative registration, **not installed by this document**, is:

```json
{
  "id": "coordinator_observer",
  "domain": "cloud",
  "aliases": ["A", "B", "C", "D", "E", "F"],
  "capabilities": ["coordinator_lifecycle"],
  "rosterGeneration": 1,
  "staleAfterSeconds": 120,
  "outageAfterSeconds": 600,
  "tokenEnv": "COORDINATOR_LIFECYCLE_INGEST_TOKEN"
}
```

Registration/credentials still require their normal deployment authorization.
Every event from this source, including an error event, must include the exact
configured numeric `rosterGeneration`. A mismatched or missing generation is
rejected. The existing durable event sequence and idempotency guards continue
across a generation change; do not reset or reuse the source sequence.

Changing the configured generation changes its configuration hash, immediately
withholding old current claims until an event for the new generation arrives.
Old immutable snapshots remain historical artifacts of their own generation.
An operator must advance the generation, never reuse an old generation number,
when adopting a new roster/identity mapping. This prevents implying historical
A–F identity continuity. The service is an observer, not an ownership/admission
or worker-fencing mechanism.

## Event and public projection

The ordinary event envelope gains only `rosterGeneration: 1`. Session rows use
the existing exact shape: alias, assignment, observedAt, state, proof, topicKeys
and blockerKeys, plus optional environmentKey. A–F positions are never inferred
from input array order.

For example, an explicitly verified current A observation uses:

```json
{
  "alias": "A",
  "assignment": "assigned",
  "observedAt": "2026-11-09T06:00:00.000Z",
  "state": "running",
  "proof": "coordinator_lifecycle",
  "topicKeys": ["monitor_coordination"],
  "blockerKeys": []
}
```

The example timestamp is synthetic. Actual publication must use the actual
capture time, no later than the event observation or receiver clock. It must
never publish a private thread/agent ID, title, filesystem path, link, report
body or raw error. Missing slots are unavailable, not unassigned. Explicit
unassigned rows require state unknown and empty topic/blocker lists.

The newly approved R1 display mapping is A monitor coordination,
B public deployment, C browser verification, D display refinement, E monitor
service implementation, and F status-display publication. This is not a historical
identity claim and is not a seeded runtime record. An actual fresh capture is
required before any running/completed/blocked assertion is published.

Public source and owned slots carry numeric `rosterGeneration`. Source health,
expiry and per-slot observation dates remain visible. A complete six-row
snapshot means coverage of this registered display roster only; both source
and global `executionCoverageComplete` remain false for this proof. Source
failure or expiry removes current display assertions without inventing stopped
agents, freed capacity or quota restoration.

No SQL migration is needed: the additive fields are stored in the validated
event payload and participate in the existing configuration/content hashes.
Old sources and old immutable snapshots without these optional fields retain
their existing meaning. The reviewed task keys map to fixed local pictograms:
public deployment → delivery, browser verification → testing, display
refinement → graphics, monitor coordination → monitoring.

## Verified public-safe work environment

An assigned session may include exactly one optional `environmentKey`:

- `foo_uif`: fixed renderer label `Foo · UIF`.
- `foo_tv`: fixed renderer label `Foo · TV`.
- `cloud_scratch`: fixed renderer label `Cloud scratch`.

The authenticated observer must verify the actual work environment before
including this field. A cloud agent can work on Foo, so the agent's `domain`
does not imply its environment. Cloud scratch does not claim a verified
repository checkout. Absence means no environment claim; null, free-form
labels, paths, repository names, branches and arbitrary keys are rejected.
Unassigned/unavailable rows cannot carry an environment key.

The public slot carries only the reviewed key, not its private verification
details. It is dated by the session's existing `observedAt` and subject to the
same availability/expiry: a retained key on a stale row is historical metadata,
not a fresh work-location assertion. A new observation without the key removes
the previous claim. A registration change to generation 2 withholds all prior
generation claims, including environments, until fresh generation 2 evidence
arrives. Immutable older revisions remain unchanged.

`ledger_packet_preparation` is the reviewed public subject `Ledger preparation`
and maps to the fixed planning icon. This file does not install a roster,
register a source, create a credential or publish an observation.

The later explicit display generation2 reserves C for the C/D packet-preparation
worker and F for the F packet-preparation worker, with D blank. Those cloud-scratch
workers have no verified checkout. This is a separate generation; it does not
reuse generation1 identity claims. Lifecycle states require a new actual capture.

## Project-only display labels

The user's latest display choice omits host/scratch descriptions. The verified
`foo_uif` context renders only `UIF`. `cloud_scratch` and `foo_tv` carry no
meaningful verified Git/project name and therefore render no bottom label.
This presentation rule does not turn missing checkout evidence into a project
claim; the public contract remains a closed-key allowlist.
