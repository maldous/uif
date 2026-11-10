# Sampled session runtime and reviewed project binding

## Scope and status

This candidate adds a new `session_runtime` capability and proof. It does not deploy, register a source, create credentials, install a schedule, poll a private thread, or alter any production data. `source-config.example.json` remains unchanged. The separate runtime configuration/event examples are examples, not registration instructions or actual captured readings.

A successful direct runtime read describes a **sampled session runtime**, not an OS process, a historical turn result, complete host discovery, a quota observation, or proof that the whole project is healthy. Current supported evidence is the native `codex_app.read_thread` / `wait_threads` runtime status union. Official reference: https://learn.chatgpt.com/docs/app-server.

The private adapter owns the verified session-to-alias mapping. Private thread IDs, host IDs, cursors, provider versions, titles, metadata, history/turns, error messages and tool output never enter this receiver's events, config examples or public projection. A source's current runtime is independent of its `latestTurn`, `turns`, and `updatedAt`: never infer runtime from those values or require metadata timestamps to advance.

## Exact reviewed source configuration

The receiver still accepts `{ "sources": [...] }` with at most 16 sources. For a runtime source these fields are required and no other fields are allowed:

- `id`: existing safe source identifier pattern, unique across config.
- `domain`: `cloud` or `foo`.
- `aliases`: unique public alias array. Cloud is A–F; Foo is the strings 1–64. Each domain/alias has exactly one source owner across all capabilities.
- `capabilities`: exactly `["session_runtime"]`. Mixing runtime with process, lifecycle, registry, curation, service or allowance capabilities is rejected.
- `runtimeGeneration`: integer 1–1,000,000. No `rosterGeneration` is accepted on runtime sources.
- `projectBindings`: array, possibly empty, with at most one entry for each owned alias.
- `staleAfterSeconds`: exactly 120.
- `outageAfterSeconds`: exactly 600.
- `tokenEnv`: the existing safe environment-variable name, unique across sources. This is a reference only, never a credential in configuration.

Each project binding has exactly `{alias, project, kind}` plus `index` only for primary/overflow:

- `alias` must belong to that same source and may occur once.
- `project` is `Cloud`, `UIF`, `ANQR`, `CIX`, `OM` or `CQ`.
- `kind` is `durable`, `primary` or `overflow`.
- `index` is required for primary/overflow, integer 1–6, and forbidden for durable.
- A target project/kind/index cell may be claimed by only one configured binding globally.

The reviewed Morgan root mapping is public Cloud alias A → durable Cloud. Cloud is Morgan's non-project work in this design. Other aliases are not inferred to be projects. A later generation-4 runtime registration should replace the old alias-owning historical source rather than coexist with duplicate alias ownership. The private identity mapping remains adapter-side. Historical immutable snapshots do not change.

Any configuration change, including a binding change, changes the existing configuration hash and fences retained observations immediately. A reviewed mapping change should also advance `runtimeGeneration`; generations are not self-issued by an event. No automatic registration is performed.

## Exact adapter event

Runtime events retain schema version 1 and have exactly:

`schemaVersion`, `sourceId`, `eventId`, `sequence`, `runtimeGeneration`, `observedAt`, `health`, `coverage`, `sessions`.

Existing transport rules apply: HTTPS, source-scoped authentication before body parsing, JSON-only bounded bodies, monotonic sequence/source clock, one-second rate guard, durable receipts and idempotent exact retries. `eventId` is an opaque safe 1–64 character `[A-Za-z0-9_-]` token; it must not encode a private identifier. `sequence` is a positive safe integer. `runtimeGeneration` must exactly match reviewed config.

`coverage` is always `{complete:false, observed:N, total:null}` where N exactly equals the number of supplied session samples. The configured roster is not host discovery. Runtime can never set complete execution coverage or a known total capacity.

For an `ok` event every session has exactly:

`alias`, `assignment`, `observedAt`, `state`, `proof`, `runtime`, `topicKeys`, `blockerKeys`.

- `alias` is an owned, unique public alias.
- `assignment` is exactly `assigned`; an unreadable/unknown runtime does not prove an empty or unassigned capacity position.
- `proof` is exactly `session_runtime`.
- `runtime` uses the exact provider union below, with no additional properties.
- `state` and `blockerKeys` must exactly match the derivation below; the receiver validates rather than trusting the adapter's assertion.
- `topicKeys` remains the existing reviewed vocabulary, at most three unique keys. Use `[]` when not separately reviewed. Runtime does not infer topics. Raw strings and `environmentKey` are rejected for runtime samples.
- Neither session nor top-level ingest can supply project associations, bindings, generation changes, private IDs or free text.

### Exact runtime union and derivation

- `{type:"active", activeFlags:[]}` → `state:"running"`, `blockerKeys:[]`.
- `{type:"active", activeFlags:["waitingOnApproval"]}` → `state:"blocked"`, `blockerKeys:["awaiting_approval"]`.
- `{type:"active", activeFlags:["waitingOnUserInput"]}` → `state:"blocked"`, `blockerKeys:["awaiting_input"]`.
- Active with both flags → blocked, blockers in canonical order `awaiting_approval`, `awaiting_input`.
- `{type:"idle"}` → `state:"idle"`, `blockerKeys:[]`.
- `{type:"systemError"}` or `{type:"notLoaded"}` → `state:"unknown"`, `blockerKeys:[]`.

Only active may carry `activeFlags`, and active must carry them. Unknown/duplicate flags, additional runtime keys, error bodies and status-like aliases are rejected. The public runtime flags are normalized to approval then input. Adapter events should use that same order. `stopped`, `completed` and `failed` are never inferred from these statuses. Prior turn history is excluded entirely.

### Timestamp and failure contract

Session `observedAt` is the actual completion time of that direct runtime read. It is not a provider transition time: the provider does not expose one. The event `observedAt` is the completed batch's observation time and must be at least the newest session sample. Both are strict UTC ISO timestamps, not future-dated. Runtime has no future-clock grace. A transport retry sends the exact original event; it never stamps cached runtime with the retry/poll time. A later successful direct read may report the same status with a later actual sample time even if provider `updatedAt` and prior turn fields did not change.

A read failure uses `health:"error"`, `sessions:[]`, and `coverage:{complete:false,observed:0,total:null}`, with an honestly dated failure observation. `health:"unconfigured"` uses the same empty shape. Failure reports immediately suppress current claims and associations from the entire owning source while retaining the previous sample as dated history. They do not refresh last success. If only part of a batch is safely sampled, an ok partial event may include only those fresh samples; omitted aliases have no current claim. Never substitute cached status for a failed sample.

The receiver cannot cryptographically prove that an adapter performed a new read. The supported direct-read adapter contract is therefore essential. The receiver enforces claimed sample times, retries, current-source failure suppression, configuration generation and outside-source expiry independently. A frozen sample expires at 120 seconds and reaches outage at 600 seconds even if status requests continue.

## Public projection and rendering

New public runtime sources have `evidenceScope:"session_runtime"`, `observationMode:"sampled"`, `runtimeGeneration`, and always `executionCoverageComplete:false`. Runtime-owned slots carry that same generation; observed slots also carry the strict public `runtime` union. Source IDs, token references, event IDs, sequences, private mapping and raw provider data are not emitted.

`projectAssociations` appears only when a runtime source is configured. Each emitted entry is exactly `{slotId,project,kind,verified:true,observedAt,runtimeGeneration}`, plus `index` for primary/overflow. The receiver constructs it from reviewed source configuration, never ingest. It emits an entry only for an assigned, actually observed, fresh sample on a fresh source. Unknown runtime can retain a fresh verified association, but the cell remains neutral. An unobserved, omitted, stale, failed or unmapped slot has no association or current cell color.

Both browser admission and renderer use the shared strict runtime projection validator. They check allowlisted fields/vocabulary, source ownership in both directions, generations, sample/source clocks and age, 120/600 limits, exact partial counts, unknown aggregate health and unique associations. Browser transport failure or local expiry clears associations and state immediately. Invalid runtime projection yields neutral output, not partially rendered untrusted fields. No ordinal, topic, environment or domain inference creates a binding.

Running and idle sample states use the existing green cell treatment; waiting uses the existing blocked treatment. This never establishes complete process coverage, project percentages, overall health or green whole-project railways. Unknown, missing and expired runtime stays neutral. Separate Foo allowance still controls automatic compact-mode behavior exactly as before.

The approved page remains hex-only with one theme icon. HTML, CSS, assets and exact railway-v36 geometry are unchanged. Browser/server graph modules add only the runtime contract and its accessible scope description, with no visible page controls or explanatory panel.

## Immutable history and integrity

Existing historical coordinator-lifecycle observations and revisions retain their exact shape and content hash; no runtime fields are injected into them. Old snapshots still validate without `projectAssociations`. Replacing live source config does not rewrite an immutable snapshot or the source history. Existing bounded snapshot retention is unchanged.

Runtime, source generation and every association field participate in the revision hash. Only the original known clock fields (`generatedAt`, ages) remain excluded from content hashing. Those excluded values have strict types and runtime clock-age consistency checks. For new runtime snapshots, saved `generatedAt` must also match the stored first-capture `created_at`, preventing capture-time alteration without changing historical legacy semantics. Revision replay uses its original capture time for deterministic SVG; the browser independently uses current time for expiry. Public projection validates before saving or returning current runtime, not merely at later immutable replay.

No database migration is needed: reviewed event and snapshot JSON use the existing durable tables and atomic guards.

## Verification

Run `npm run build:approved-static`, `npm test`, and `npm run check` from this service directory. The test suite exercises exact runtime states/flags; timestamps, delayed and repeated delivery; failed feeds; reviewed configuration/generation and unique binding ownership; public privacy/allowlists; bidirectional snapshot ownership; hash and capture-time integrity; legacy snapshot replay; browser admission/expiry; SVG and runtime binding behavior; and unchanged minimal-page lifecycle/geometry. SQLite-backed tests model D1 SQL behavior locally; they are not a deployed D1 or end-to-end live-feed claim. Real live source capture, source registration, receiver deployment and post-deployment verification remain separate integrator work.

## Offline publication preparation

`scripts/prepare-runtime-observation.mjs` reads one stdin JSON object with exactly `{sourceConfig, event}`. It calls the receiver's actual exported `prepareRuntimeObservation`, which uses `loadConfig`, `validateEvent`, canonical serialization and SHA-256, not a separate implementation. It rejects future samples or any event/session sample older than 60 seconds at preparation time. It emits prepared `observations` column names and parameters, source config hash, payload hash, preparation time and an absolute `publishBefore` deadline. It performs no writes, network access, credential reads, registration or scheduling. Errors use fixed text without echoing input.

The integrator must use the exact intended registered config, serialize sequence ownership with all other publishers, verify the actual registration is current, publish before the stated deadline, and verify the durable outcome before retrying. Preparation is not registration, delivery, a replay-protection check, or permission to restamp an old sample. If the deadline has passed, obtain a new direct sample and prepare again. The helper's input is the full reviewed config (which may include the existing separate topic and allowance sources); its output does not contain token environment references or values.

For runtime only, running retains the approved moving green highlight while idle is steady green. Legacy rendering is unchanged. Mapped runtime state and original observation time are included in the accessible SVG description even with `interactive:false` and `footer:false`; no visible text or additional controls are added to the minimal page.
