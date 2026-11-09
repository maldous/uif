# Durable session status source qualification

## Result

The external page cannot directly call the authenticated conversational
`thread_management.read_thread` tool. A bounded supported read from this task
successfully returned recorded durable turn status for two accessible sessions.
Both reported `latestTurn.status: failed`. These are old accessible sessions,
not an inventory of today's A–F workers. No current production roster is seeded.

No supported independent machine endpoint/credential for that tool has been
identified. This is a missing source integration, not something a Cloudflare
rule change alone can solve. No credential was copied from a browser, token
store, conversation, or local app-server. The denied Cloudflare action was not retried or bypassed.

## What the available sources actually establish

- `thread_management.list_threads`: IDs of accessible durable tasks with
  pagination. Presence in that list is not activity, task identity, assignment,
  complete account coverage, or a current A–F display mapping.
- `thread_management.read_thread`: supported authenticated, one-shot read of
  `threadId` and `latestTurn` including its ID, status and error. The tool warns
  that recorded status may lag. It also returns messages; those must never be
  persisted in a public feed. The qualified result is recorded lifecycle, not
  CPU liveness, useful progress, free capacity, or local Foo allowance.
- Foo MCP `list_runs`: a successful read returned compact Windmill job metadata
  including job ID, path, created/started time, status and duration. This proves
  a supported conversational job-status read, not cloud durable status or a
  complete local Codex session roster. A finished root-runner query may itself
  be the newest job. Counting it as an agent would be misleading.
- Foo's existing collector contracts: local registry and turn-history metadata,
  service/process identities and the separately qualified allowance source.
  They do not discover or authenticate to a cloud durable status API. The
  existing `cloud_feed` remains unconfigured.

## Candidate implementation, not a production bridge

`tools/morgan_monitor/durable_capture.py` provides an explicit one-shot adapter.
A caller in an already authorized runtime supplies an async callback that makes
a fresh supported `read_thread(threadId=<binding>, limit=1)` call. This package
does not implement or install that runtime bridge, supply an endpoint or login,
schedule polling, start a service, configure the roster, or publish anything.
It must not be described as independent, continuously refreshed cloud telemetry.

The projection uses the existing `coordinator_lifecycle` receiver contract:

1. Explicit private thread-ID to A–F mapping, at most six distinct threads.
   Mapping order never chooses a display alias. Generation is mandatory.
2. A real read completion timestamp for each row and collection completion for
   the envelope. No timestamp from a message or mutable response body is used.
3. `inProgress` projects as unknown, because a fresh read does not establish
   that potentially lagging recorded state is active now; `completed` remains
   recorded completed; `failed` becomes unknown with the fixed failure marker;
   `interrupted` becomes unknown. A missing turn is assigned/unknown, not an
   unassigned worker. In-progress/completed variants are synthetic test fixtures;
   only failed was exercised in the current live source qualification.
4. Raw errors do not classify quota or blockers. No CPU/process, execution
   coverage, all-stopped, allowance, environment or checkout claim is emitted.
5. Unknown status/schema, wrong thread identity, source failure and timeout omit
   that row. All failures produce an error event. No bindings produces an
   unconfigured event without reading a source. Empty/missing slots stay
   unavailable in the receiver rather than becoming unassigned.
6. Only closed catalog topic keys and fixed failure keys leave the boundary.
   No private thread/turn ID, message, error body, URL, path, account data or
   credential is copied to the event. Roster data is not shipped with the code.
7. Existing receiver expiry applies. A refreshed observation says the API was
   read again; it does not assert that the recorded turn advanced since the
   previous read. The receiver continues to attribute this as manual lifecycle
   observation with `executionCoverageComplete: false`.

The read deadline uses the event loop's monotonic clock. Timed-out callbacks are
cancelled without waiting for cancellation to finish, and late responses can
never enter the event. A callback must not block the event loop; the authorized
runtime must also supervise callbacks that suppress cancellation because Python
cannot forcibly terminate them. This adapter is not a process supervisor.

The sequence argument is a protocol seam, not an in-memory sequence allocator.
Production wiring must allocate it atomically in durable custody and retry the
identical stored event after uncertain delivery. Do not reinvoke the capture
just to refresh an outbox item's timestamp. The current Python collector's
source/transport allowlists intentionally do not admit this candidate source;
no runtime route has been added or smuggled through `cloud_feed`.

## Required path to a genuinely independent public snapshot

The target architecture remains:

supported private status API or event source → authenticated independent
collector → fixed public-safe projection → durable outbox → per-source
authenticated receiver → expiring public JSON snapshot → page.

The unresolved first link needs a supported service interface and auth grant
that the independent collector can use. Conversational tool availability does
not confer daemon access. Do not use UI scraping, session/credential extraction,
undocumented internal endpoints or a different network path to get around this.
An agent-scheduled/manual snapshot would be a different reliability model and
must not silently substitute for the requested independent source.

After the first link exists, qualification must establish live status shapes,
clock semantics, roster identity and pagination; then durable storage/replay,
source isolation, receiver registration, source expiry and end-to-end delivery
can be tested. Installation, credential provisioning and deployment have not
been performed. No design was populated with live data.

## Approval and blocker disposition

- These supported read-only checks and local candidate source/tests reused the
  user's existing authorization, including the explicit permission to read Foo
  local state directly. No new user approval or live design change was made.
- A new persistent source/publisher credential or access grant requires its
  normal action-time confirmation once its exact scope and recipient are known.
  A general repeated “yes” cannot provide an API that is not available.
- The existing Foo → `morgan.aldous.info/v1/ingest` Cloudflare 403/error 1010 and
  twice-denied Browser Integrity Check exception are separate unresolved
  delivery blockers, as recorded in UIF PR #21. This task did not retry either
  rule mutation, spoof a client or use an alternate ingestion route. The exact
  human-applied rule/review resolution belongs with the deployment owner.
- Final-design approval is still required before connecting live data to that
  design, per the user's instruction.

## Verification and provenance

- Reference source: [UIF PR #21](https://github.com/maldous/uif/pull/21),
  `feature/morgan-monitor`, verified remote head
  `1f8e048e76701444637678edcb0f9481c3886167` on 2026-11-09.
- Candidate adapter and tests are isolated local additions. The receiver source
  copied into this candidate directory is unchanged reference code, not a
  separate proposed receiver edit.
- Seventeen synthetic tests passed, covering all mappings, identity checks,
  message/error exclusion, partial and complete read failure, timeouts including cancellation-suppressing and late blocking callbacks, clock
  rollback, cancellation, duplicate bindings, mutable binding isolation and
  fresh reads. Six emitted shapes also passed the actual reference receiver's
  `validateEvent` function under Node 24.
- Aggregate checks against the unmodified pinned PR baseline plus this adapter:
  98 Node tests passed and all Node syntax checks passed. Python discovery ran
  101 tests: 90 passed and 11 existing allowance Unix-socket fixture tests errored
  at socket creation with `PermissionError: [Errno 1] Operation not permitted`.
  One permitted sandbox-escalation attempt produced the same environment limit;
  it was not retried further. The new adapter's tests passed at that checkpoint. This is not
  a full Python-suite pass or a source/allowance operational failure. The wider
  repository's white-room suite was not materialized or run in this candidate.
- No independent daemon, live credential, ingestion, Cloudflare change, design
  population, TV operation, merge or deployment was tested or performed.

Run candidate checks from this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/morgan_monitor -p 'collector_*.py' -v
```

## End-state implementation plan (2026-11-09 follow-up)

### Independent collection and public delivery

Use the existing Foo collector process and durable outbox as the deployment
boundary, rather than executing an assistant turn on every page refresh. Give
each source its own health, observation time, explicit roster bindings and
receiver credential. The browser only reads the receiver's expiring public
snapshot. This remains true whether the underlying source is local Codex,
Windmill, or a future qualified cloud control-plane interface.

1. **Foo Codex runtime:** use the documented app-server `thread/loaded/list`
   and `thread/read` with `includeTurns: false`, through an existing same-owner
   Unix socket. Qualify the exact daemon executable/hash, UID, socket mode and
   schema before enabling it. Do not start/resume a thread, consume its event
   stream, read credentials, or use transcript/history files as activity proof.
   Poll explicitly bound threads and retain only the closed runtime status enum.
   `active` can include waiting for approval/user input; it is not automatically
   useful progress. `idle` and `notLoaded` must not mean task completed or all
   workers stopped. A daemon's loaded list covers that daemon only.
2. **Windmill:** use supported job metadata for the specifically registered agent
   scripts/flows and worker heartbeat metadata separately. Do not count generic
   root-runner queries or the Windmill worker pool as agent sessions. A scheduled
   job can use its permissioned execution identity and ephemeral job token, not
   a token copied from the conversational connector. An installed service instead
   needs a narrowly scoped read grant. Job logs/results remain private and are
   unnecessary for the public projection.
3. **Cloud durable sessions:** enable a supported metadata-only runtime API or
   event bridge for the actual current cloud session namespace and the intended
   roster. The conversational read tool is accessible but its recorded status
   can lag; it does not supply a daemon endpoint or an external credential.
   Current CLI cloud commands did not resolve the known accessible task. Do not
   create a generic OpenAI API key and claim that it grants this source.
4. **Projection and storage:** define reviewed source capabilities for live
   app-server/Windmill status instead of mislabeling them as process proof or
   the cloud coordinator feed. Atomically allocate source sequence, store the
   redacted event and retry identical bytes after uncertain delivery. Register
   each source with its own server-side scope and secret. No title, message,
   account identifier, thread/job ID, path, URL or raw error goes public.
5. **Receiver and design:** deploy the independently reviewed receiver/collector
   changes only after their checks and normal approvals. Test ingestion and
   expiry using an isolated synthetic source. Connect the final layout to live
   data only after the user approves that layout.

The remaining cloud-source choice is material. A platform-supported service
interface preserves the requested independent monitoring. Moving future work
to Foo-owned observable sessions, or using agent-scheduled snapshots, changes
placement or reliability and requires the user's decision; neither is silently
substituted for monitoring the existing durable sessions.

### Verified source checks

- Foo's system CLI is 0.162.1 and the existing managed daemon is 0.167.0. Both
  completed `cloud list --json --limit 20` successfully with an empty task list.
  Both rejected the known, otherwise accessible durable task ID as invalid/not
  found. Sanitized checks did not classify either as an authentication-required
  response. This establishes an unresolved namespace/access mismatch, not
  absence of durable work or a demonstrated need to log in again.
- Two existing managed-daemon sockets were verified as mode 0600, owned by
  UID 1000, with same-owner peers matching the installed 0.167.0 executable.
  The binary's generated schema explicitly provides metadata-only `thread/read`
  and bounded `thread/loaded/list`, with runtime states `active`, `idle`,
  `notLoaded` and `systemError`. Under the user's explicit authorization for
  direct Foo local-state reads, one daemon was unavailable within the bounded
  probe. The other initialized successfully, returned an empty loaded-thread
  list with no next page, and returned JSON-RPC error -32600 for the known cloud
  durable ID. No error body, title, preview or conversation content was retained.
  This proves metadata-protocol access to that daemon, not current local
  activity, complete Foo coverage or an independent cloud-session bridge.
- A supported Windmill read returned job metadata, and the worker read returned
  16 service workers with recent heartbeats. These counts are not an agent
  inventory, a complete execution census, or a cloud durable-session source.

### Exact Cloudflare human step

Read-only inspection found Browser Integrity Check enabled and no custom
configuration ruleset for the zone. The zone uses the Free plan, which supports
Configuration Rules. DNS is already proxied; no DNS change is called for.

In Cloudflare, open **aldous.info → Rules → Overview → Create rule →
Configuration Rule**. Choose a custom filter expression:

```text
http.host eq "morgan.aldous.info" and http.request.uri.path in {"/health" "/api/status" "/render.svg" "/v1/ingest"}
```

Set **Browser Integrity Check: Off** for this rule and deploy it. Keep the
zone-wide setting and other WAF protections enabled. Rollback is disabling this
one rule. This is a narrow exclusion of browser-oriented inspection, not removal
of ingest authentication. Because the automated mutation was denied twice, this
plan does not retry it or recommend an alternate host or client disguise.

After the human confirms the rule exists, test from the unchanged Foo client:
`/health` should reach the Worker, and an unauthenticated ingest should receive
the Worker's 401. Then test one authorized isolated synthetic event and verify
its acknowledged sequence. Recheck Security Events if access is still blocked;
do not weaken additional controls by assumption. Passing these tests establishes
transport only, not source coverage or permission to populate the final design.

### Acceptance checklist and remaining permissions

- Qualify current source enums, ownership, pagination, timeout and clock behavior
  with fixtures plus metadata-only live checks. A source failure, restart,
  unmapped thread, incomplete census or changed binary fails closed.
- Confirm the deployment tracks current reviewed PR content; the source follow-up
  must not overwrite the sibling transport work reported at `d1977ef`.
- Exercise source-auth isolation, malformed input rejection, duplicate replay,
  offline outbox recovery, stale expiry, roster-generation changes and no private
  fields. Verify collector and receiver restart behavior independently of agents.
- Request action-time confirmation only for the exact new persistent read grant
  or source/publisher credential when its scope and recipient are established.
  An existing task approval covers routine source coding/testing, not creation
  of broader persistent credentials.
- The human Cloudflare step, qualifying the real cloud-session service interface,
  any required new scoped credentials and final-layout approval remain explicit
  outstanding dependencies. None has been represented as completed.

### Supporting documentation

- [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [Codex CLI cloud commands](https://learn.chatgpt.com/docs/cli/reference)
- [Cloud environments and legacy cloud distinction](https://learn.chatgpt.com/docs/environments/cloud-environments)
- [Windmill job lifecycle and execution identity](https://www.windmill.dev/docs/core_concepts/jobs)
- [Windmill MCP access](https://www.windmill.dev/docs/core_concepts/mcp)
- [Create a Cloudflare Configuration Rule](https://developers.cloudflare.com/rules/configuration-rules/create-dashboard/)
- [Cloudflare Browser Integrity Check](https://developers.cloudflare.com/waf/tools/browser-integrity-check/)
- [Configuration Rules availability](https://developers.cloudflare.com/rules/configuration-rules/)

## Recovery checkpoint validation

The publication parent is PR #21 head
`d1977efefc9c7c158c6c2ac9ddc3ab5eb90cb36f`, verified directly on 2026-11-09.
Its five changed transport/test/documentation blobs were fetched at that commit
and their Git blob hashes were verified before combined testing. This candidate
adds only `durable_capture.py`, `collector_durable_capture.py` and this document;
none overlaps the preceding transport patch. Receiver and frontend code are
unchanged. No production integration, roster or credential is included.

Final candidate checks on that baseline: 17/17 adapter tests passed (including
the real receiver validator), 96/96 runnable Python tests passed, 98/98 Node tests
passed, and Node syntax checks passed. Full Python discovery ran 107 tests with
the same 11 existing Unix-socket fixture errors at socket creation under the
cloud sandbox. Those failures are not hidden or reported as an aggregate pass.
The timeout regression now rejects cancellation-suppressing and late blocking
callbacks; no late result can refresh evidence. Credential-pattern and trailing
whitespace checks passed. The wider white-room suite was not run.
