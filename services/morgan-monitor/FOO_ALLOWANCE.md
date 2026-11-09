# Foo allowance observation contract

This is an optional, separately qualified source contract. No allowance reader,
source registration, credential or live feed is created by this implementation.
The default public allowance is unconfigured. Neither missing telemetry nor an
execution error is evidence that allowance is exhausted.

## Authority and ingestion

Exactly one registered source may hold the `foo_allowance` capability. It must
have `domain: "foo"`; an allowance-only source normally owns no session aliases.
Use a distinct, explicitly provisioned source credential. An event cannot grant
itself a capability. Qualification must establish that the source independently
observes both the allowance state and whether that allowance permits Foo agents
to run. Process state, historical turn errors, an absent source, and an expired
credential are insufficient evidence.

An authenticated ordinary ingestion event with `health: "ok"` may contain this
one optional exact object:

```json
{
  "fooAllowance": {
    "state": "exhausted",
    "canRunAgents": false,
    "observedAt": "2026-11-09T06:00:00.000Z"
  }
}
```

This is a synthetic contract example, not a current allowance observation.
Only three state/value pairs are accepted:

- `valid` and `true`
- `exhausted` and `false`
- `unavailable` and `null`

No amount, account identifier, raw quota response, reason, error, transcript,
URL or additional field is accepted. `observedAt` is the actual allowance
observation time, not a heartbeat time. It must be UTC ISO, no earlier than
2000-01-01 and no later than either the event observation or receiver clock.
Allowance evidence has no future-clock grace period. Older durable outbox events
can be accepted, but do not become fresh on delivery.

The existing per-source sequence, event-id, authentication and rate constraints
apply. SQL guards additionally reject:

- A decreasing allowance time: HTTP 409, `allowance_clock_regression`.
- Different allowance values at the same observation time: HTTP 409,
  `allowance_observation_conflict`.

Both guards are atomic with event acceptance. A rejection cannot advance source
success time. Exact retries are idempotent. Omitted allowance fields and repeated
observations do not renew the stored allowance timestamp or original receipt.
A later genuinely observed `unavailable` result invalidates a prior assertion.

## Public projection and layout safety

New public snapshots always include this positive allowlist:

```json
{
  "fooAllowance": {
    "state": "unavailable",
    "canRunAgents": null,
    "verified": false,
    "availability": "unconfigured",
    "observedAt": null,
    "ageSeconds": null,
    "staleAfterSeconds": null,
    "outageAfterSeconds": null
  }
}
```

For a registered source, the thresholds come from its reviewed configuration.
An assertion is verified only while both its source and the separately dated
allowance observation are fresh. Source failure, missing evidence, configuration
drift, future observations, expiry or explicit unavailability yield
`state: "unavailable"`, `canRunAgents: null`, and `verified: false`. Known dates
and stale/outage availability remain visible; missing information is not quota
exhaustion.

Automatic compact layout is eligible only for `verified: true`, `availability: "fresh"`,
`state: "exhausted"`, and `canRunAgents: false`, with a valid nonfuture timestamp
inside the published freshness threshold. A fresh verified `valid` observation
restores full O2. The frontend must independently expire the assertion if the
receiver becomes unreachable. The user's explicitly selected `compact` MCP
presentation is also supported, independently of allowance evidence. Selecting
that view does not assert exhaustion and does not change the allowance metadata.
`o2`, legacy `hexagon`, `focus`, and `compact` are accepted layout parameters.

Allowance state, proof availability, observation time and thresholds participate
in the immutable revision. Only request-dependent `ageSeconds` is excluded.
Old snapshots without the optional field remain byte-for-byte historical
artifacts; they are never upgraded with inferred allowance. HTTP and read-only
MCP return the same projection. Historical snapshot freshness is historical,
not proof of current allowance.

Migration `0004_foo_allowance.sql` preserves earlier observations and snapshots
and adds a durable allowance record per source. Apply it only through the
reviewed deployment procedure. Tests use local SQLite and synthetic evidence;
they do not establish a live Cloudflare deployment or a qualified quota source.
