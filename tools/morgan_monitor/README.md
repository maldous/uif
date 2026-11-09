# Private Morgan monitor collector

Stdlib Python 3.10+ code for a separately supervised, read-only metadata collector.
It does not install/start a service, deploy a receiver, modify the existing TV
service, create credentials, or discover an authenticated cloud feed.

## Evidence and meaning

The collector separates three facts:

1. **Source observation:** a bounded read/probe succeeded at an actual UTC time.
2. **Recorded state:** a source row had a lifecycle state and original timestamp.
3. **Execution:** neither historical state nor a live process proves useful work.

`inProgress`, `running` leases, a loaded app, and recent registry reads do not
prove an active agent. This implementation deliberately emits no execution proof.
A current systemd/PID identity can establish only the source's `serviceState`.
Six cloud letters and up to 64 Foo numbers are display slots, not observed session
counts or guaranteed admitted capacity. No session is assigned from row order,
recency, lease count, or process count. Missing evidence is never "Unassigned".

The reviewed adapters are:

| Source ID | Qualified input | Capability and coverage |
| --- | --- | --- |
| `codex_registry` | `state_5.sqlite`, migration 62, exact `threads` column names/types | Historical registry only. Bounded keyset scan with total/returned/completeness; no heartbeat, connection, owner or PID proof. |
| `codex_turns` | `thread_history_1.sqlite`, migration 8, exact `thread_turns` column names/types | Historical turn outcomes only. Old `inProgress` stays unknown; no transcript or error bodies. |
| `symphony_registry` | Qualified `leases` and `events` schemas | Lease lifecycle history, no heartbeat/PID/expiry. Never imports or instantiates a pool or calls `Pool.status()`. |
| `controller_service` | Explicit operator-configured systemd unit | Read-only `systemctl show` of four properties. Read health and running/stopped process state are distinct. |
| `process_identity` | Explicit PID and kernel start ticks | Matching process identity only. PID reuse and missing processes do not count as the previous process. No command-line/environment reads or process discovery. |
| `foo_allowance` | Explicitly pinned existing local app-server 0.167.0 Unix socket | Ordinary Codex allowance only, with current account/workspace matching and explicit backend eligibility. No agent count or useful-execution claim. |
| `cloud_feed` | None | Explicitly unconfigured. No account OAuth/session token copied, conversational tool scraped, or cloud execution inferred from Foo records. |

These SQLite schemas are internal implementation contracts, not a stable vendor
API. Version/column drift, new status values, malformed records, future timestamps,
source outages and timeouts fail closed with fixed diagnostic codes. No SELECT *,
transcript/title/error/body query, schema migration, vacuum, checkpoint or source
journal change is used. Connections use URI `mode=ro`, `query_only=ON`, a short busy
timeout, progress deadline, one read transaction and bounded keyset pages. They do
not use `immutable=1`, so committed WAL data remains visible. Total counts may hit
the deadline; that yields an unavailable source, not a guessed count.

## Commands and safe defaults

Run from the UIF checkout. Keep the real config and database outside Git. Copy the
example to a private approved location and set only independently reviewed source
paths, unit and identity bindings. Relative paths resolve beside that config.

```sh
python3 -m tools.morgan_monitor check-config --config /APPROVED/PRIVATE/config.json
python3 -m tools.morgan_monitor collect-once --config /APPROVED/PRIVATE/config.json
python3 -m tools.morgan_monitor run --config /APPROVED/PRIVATE/config.json
python3 -m tools.morgan_monitor outbox-status --state /APPROVED/PRIVATE/monitor.sqlite3
python3 -m tools.morgan_monitor snapshot --state /APPROVED/PRIVATE/monitor.sqlite3
python3 -m tools.morgan_monitor mcp --state /APPROVED/PRIVATE/monitor.sqlite3
```

`selectedSources` is an optional explicit list of source IDs from the table above.
Omitting it preserves the all-seven default. An allowance-only production config
uses `"selectedSources":["foo_allowance"]`; only that adapter is constructed, read,
emitted and eligible for delivery. This avoids generating unsendable placeholder
streams for receiver sources that have not been provisioned. Unknown, duplicate
or empty selections and bindings to unselected sources are rejected.

Changing selection does not delete or acknowledge old outbox events. Unselected
pending records remain in private custody, are not retried, and still count toward
the storage cap. If old retained events already fill the cap, reconcile their
approved delivery/retention rather than resetting the database or silently losing
them. New allowance-only installations should use their explicitly approved empty
collector state, never a source database or an abandoned queue repurposed blindly.

`config.example.json` has **all source paths/identities unset, no bindings, receiver
disabled, no URL and no token environment names**. `check-config` does not create
state or make network calls. Running the example creates only its explicit
collector-owned state and seven unconfigured observations; it does not inspect the
real user sources or send them anywhere.

`run` is a deterministic polling process, not an LLM turn. SIGTERM/SIGINT stop its
own loop without controlling any observed service. Clock/config errors fail visibly. Outbox pressure is reported explicitly as
`collectionDeferred:true`; the loop keeps retrying delivery without refreshing
source observations until the entire collection pass fits again.
`morgan-monitor.service.example` is a user-level template only; it has placeholders
and no EnvironmentFile or secrets. No unit is installed, enabled or started here.

### Optional Foo allowance adapter

`fooAllowance` is disabled when its `socketPath` is null (the default). Enabling
requires a reviewed existing mode-0600 Unix control socket, matching current UID,
exact executable path and SHA-256, expected Codex home, and qualified server
version 0.167.0. Every connection verifies the actual `SO_PEERCRED` peer, executable
hash, process start identity, initialization version/home, and unchanged account
metadata before/after the allowance read. No process is launched. An update,
identity mismatch, permission difference or protocol drift becomes unavailable.

The only RPC requests are `initialize`, `account/read` with `refreshToken:false`,
and `account/rateLimits/read` with `excludeResetCreditDetails:true`, followed by
a second `account/read` for identity consistency. All known notifications for
the qualified version are suppressed using the generated schema list. Unexpected
notifications, error responses or server requests terminate the read with a fixed
code. There is no generic RPC method, subscription, transcript query, token file
read, login, quota reset, purchase, restart or Reserve experiment opt-in. The
WebSocket handshake uses only ephemeral protocol nonces, not credentials.

The usage response must match the selected workspace account. A core `codex`
bucket is required. Only explicit `ordinaryUsageAllowed:true` with no conflicting
denial can produce `valid/true`. Only explicit false, `rate_limit_reached`, a
current exhausted window, and explicit absence of both paid and unlimited credits
can produce `exhausted/false`. Everything else produces `unavailable/null`,
including null permission, missing identity, another provider, contradictory
metadata, a vanished limit, elapsed reset time or a percentage alone. This is
ordinary Codex eligibility for the pinned execution identity, not a statement
about alternative accounts/providers or proof that useful execution is occurring.

Proof time is the actual completed read time, not the beginning of a slower
collection pass. Public output contains only `state`, `canRunAgents`, and
`observedAt`. Account IDs, emails, workspace names, balances, quota percentages,
reset-credit details, paths and backend URLs are never stored or published.
Receiver registration must give exactly one Foo source the `foo_allowance`
capability and no inferred session aliases. Only fresh verified exhausted/false
metadata may authorize compact display. Stale, unavailable, contradictory or
missing evidence cannot do so; restoration requires new valid eligibility.

A bounded one-off read against the existing managed daemon was qualified during
implementation. This did not install a collector, enable an adapter in persistent
configuration, create auth, or ingest an event. Persistent production access and
service installation remain separate approval/setup steps; the checked-in example
contains no real socket, executable, home, hash, UID or credential values.

Protocol references: [app-server API](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md),
[usage contract](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/account.rs),
[Unix transport](https://github.com/openai/codex/blob/main/codex-rs/app-server-transport/src/transport/unix_socket.rs).
Generated schema from the installed qualified version, rather than a newer remote
main branch, pins the actual allowed fields and notification names. The exact
release's per-request backend read/error propagation is documented in
[allowance-source-evidence.md](allowance-source-evidence.md); no cached successful
eligibility fallback was found in that path. Ship `allowance_notifications.json`
with the Python package because it is part of the qualified protocol contract.

### Explicit optional slot bindings

Bindings are private operator-reviewed associations, not source-discovered labels:

```json
{
  "sourceId": "codex_turns",
  "recordKey": "SYNTHETIC-THREAD/SYNTHETIC-TURN",
  "alias": "1",
  "topicKeys": ["monitoring"],
  "blockerKeys": []
}
```

This example is synthetic. Real identifiers stay in the private configuration and
collector store. A turn key is its thread ID, `/`, turn ID. A thread/lease key is
its own ID. No binding is provided by default. Only numeric Foo aliases 1–64 may
be bound, each once, to a registry/history source. Receiver registration must own
exactly the intended aliases and capabilities. Systemd and PID probes do not get
session bindings because they lack a qualified session-to-process mapping.

Topic keys: `infrastructure`, `monitoring`, `research`, `planning`, `engineering`,
`review`, `testing`, `delivery`. Blocker keys: `source_unavailable`,
`coverage_partial`, `awaiting_input`, `dependency_unavailable`, `quota_unavailable`,
`execution_failed`. At most three keys of each kind are allowed. These keys are
reviewed annotations, not extracted from source text. Unknown keys, extra fields,
URLs, free text and duplicate bindings are rejected. A mapping without a current
bounded source record emits no slot; receiver default is unavailable.

## Durable custody and delivery

The dedicated SQLite state contains:

- Private projected record IDs/states/timestamps, never message/error bodies.
- Independent health, original latest record time, actual last successful read,
  and historical registry coverage. Success does not renew a row's timestamp or
  `last_changed_at` if the record is unchanged. A failed read preserves prior
  records and last-success time.
- An atomic per-source sequence plus public-safe outbox. `eventId` is the decimal
  sequence within a stable source ID. Event creation and evidence commit together.
- Retry attempts, next retry time, fixed error code and confirmed acknowledgments.

New state is created mode 0600 inside a newly created mode 0700 directory. An
existing database must already be private and be a collector database; a foreign
source database is never migrated into a collector database. Configuration also
rejects a state/source path collision. Do not publish or attach the private state.
Read-only MCP output is a separate positive metadata projection without record IDs.

Unacknowledged events are never evicted. Delivery runs before new collection. If `maxPending` (default 10,000) leaves
insufficient space for a complete collection pass, the pass is explicitly deferred
instead of discarding data or starving later sources. Delivery retries continue
on later polls; after capacity returns, collection resumes automatically. No
source timestamp or sequence is advanced during a deferred pass. Capacity must
be monitored. Acknowledged
history and projected records are retained, not automatically purged, so ongoing
storage capacity/backup/retention operations must be decided before deployment.
A successful write on Foo is durable across process restart, not proof against
host/disk loss. Provision a separately authorized backup if that is required.
Do not replace/reset the database to recover a sequence conflict; preserve it and
reconcile receiver history and source registration first. One authoritative
collector owns each source ID. Clock regression or same-second duplicate reads
cannot silently refresh telemetry or advance its health.

### HTTPS receiver contract

Delivery requires all of the following explicit external configuration:

- `enabled:true` and an exact HTTPS `https://host/v1/ingest` URL.
- The same URL in `allowedUrls`; no HTTP, userinfo, query, fragment or non-443 port.
- A source-specific `tokenEnvs` name for each delivered source. Existing approved
  bearer token material is supplied only through that environment variable. It is
  never created, copied from an account, printed, put in config or persisted.
- Matching server-side source ID/domain/capability/alias registration and token.

The receiver destination, authorization and registration remain **unconfigured**.
Runtime auth provisioning is a separate action, not something this source enables
by implication. TLS verification uses the platform default trust store. Redirects
are rejected; proxy inheritance is disabled. Request timeout is bounded (0.1–10s),
response size is capped and only an exact acknowledgment acknowledges an event.
Failures get bounded exponential retry (5–300s); the same stored payload/event ID
is retried. A durable two-second integer timestamp gate guarantees at least one
real second between accepted events for the same source, including after restart.
Between source polls, independent delivery ticks drain pending events faster than
new observations arrive; an outage backlog does not remain permanently delayed.
The next source-poll interval begins after a pass completes, so slow transport
cannot trigger an immediate duplicate observation in the same clock second.
Production acknowledgments/retries use their actual completion time, not an old
collection-cycle timestamp. Per-source ordering prevents later delivery skipping a pending event,
while a failing source does not block another source's delivery.

Every event is an exact versioned DTO:

```json
{
  "schemaVersion": 1,
  "sourceId": "codex_turns",
  "eventId": "1",
  "sequence": 1,
  "observedAt": "2026-11-09T00:00:00Z",
  "health": "ok",
  "coverage": {"complete": false, "observed": 0, "total": null},
  "sessions": []
}
```

An optional `serviceState` is permitted only for a `service_process` source.
An optional `fooAllowance:{state,canRunAgents,observedAt}` is permitted only for
`foo_allowance` with health `ok`; it is independent of session execution proof.
`health` is `ok`, `error` or `unconfigured`. Session objects contain only alias,
explicit assignment, original `observedAt`, coarse historical state, proof,
topicKeys and blockerKeys. Source event time is the actual independent probe time;
session observation time remains the original row timestamp. An old outbox event
is replayed unchanged after an outage, so receipt never turns old evidence fresh.
The receiver's independent expiry is required to detect Foo/collector loss while
Foo cannot report it. Source errors do not renew its last-good data.

Historical SQL coverage is stored privately as `registryCoverage`. It is **not**
public session coverage: all collector events set `complete:false`, and total is
unknown even if every historical row was scanned. A private MCP snapshot also
sets `executionCoverageComplete:false` and `cloudFeedConfigured:false`.

Expected responses: new event 202 or exact duplicate 200, body
`{accepted:true,duplicate:<boolean>,eventId:<same>,sequence:<same>}`. Any other
response is unacknowledged, including an uncertain connection failure after a
remote write. A retry lets the receiver prove its prior receipt. Credentials and
raw response errors never enter diagnostics or the outbox.

## MCP and Windmill access

The stdio MCP server supports initialize, ping, tools/list and one read-only tool,
`monitor_metadata`. It opens the collector database `mode=ro` and accepts no path,
query, ID, or mutation in tool arguments. The path is fixed at process startup.
Input is bounded; malformed requests yield fixed errors, not source details.
This is a private local metadata interface, not a network listener or replacement
for the receiver's public render/MCP surface.

`windmill_read.py:main()` is an optional private wrapper around the same read-only
snapshot. It takes no caller arguments and reads the explicitly provisioned
`MORGAN_MONITOR_STATE_PATH`. Deployment requires an approved worker with package
and filesystem access, fixed server-side configuration, and appropriate access
control. No script, worker, mount, access permission or credential is provisioned
by these files. A connected conversational Windmill tool is not proof of daemon
access or a persistent cloud-session feed.

## Verification and remaining stages

All fixture data is explicitly synthetic. Run offline checks from the checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/morgan_monitor -p 'collector_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/white_room -v
```

Tests cover exact schema/version drift, bounded keyset scans, stale in-progress
rows, read-only enforcement/WAL visibility, service versus registry meaning, PID
reuse, source outage, field/key allowlisting, unchanged-record timestamp retention,
state transaction rollback, restart sequence persistence, retry identity, malformed
acknowledgments, no default networking, HTTPS destination bounds, safe MCP output,
and CLI/config behavior. Synthetic Unix-socket tests also cover read-only RPCs,
frame bounds, fragmentation/pings, account switching, identity pins, notification
suppression, and conservative allowance classification. The optional Node cross-contract check validates 24
synthetic events against the actual receiver validator; it is explicitly skipped
if the receiver source or Node is unavailable.

Not established by these tests: live daemon source coverage, cloud telemetry,
auth provisioning, actual HTTPS delivery/uncertain-receipt replay, deployed D1/MCP,
service supervision, host restart/power loss, backup recovery, public DNS/TLS and
end-to-end public or TV rendering. No live TV service was changed. Do not label
this source package deployed, fully connected, or operationally qualified.
