# UIF shared effect authority

`worker.mjs` maps every authenticated operation to one named SQLite Durable Object. That object owns the immutable Linear workspace/issue claim, original effect receipts and account reservations. OffMenu's existing intake selects work. An empty object starts stopped at epoch 1.

## Installation bindings

The deployment owner provides a service-authenticated Access application, a Worker route for that application, one `AUTHORITY` namespace, `ACCESS_ISSUER` (the exact HTTPS team origin), `ACCESS_AUD` (the app's AUD tag) and `AUTHORITY_CONFIG` (a JSON string). `wrangler.jsonc` disables the workers.dev hostname and preview URLs. It contains no public route or account credentials.

The configuration keys are `authority_id`, `quota_authority_id`, `deployment_sha256`, `policy_sha256`, immutable OffMenu `workspace_id`, `project_id`, `repository_id`, `lease_seconds` (1–300), `peers`, `route`, and optional `reviewer_route`, `reviewer_opening_liability`, `quota_verification_sha256` and `upload_hosts`. Each `peers` key is a distinct registered Access service-token client ID and maps to `{role, session_id}`. The four roles are `native`, `morgan`, `control` and `quota`, each with a distinct session. Ingress verifies the signed Access `app` JWT against the issuer's RS256 JWKS, including audience, time, empty service `sub` and exact `common_name`. Client credential headers are consumed by Access. Only its signed assertion reaches the private object. The caller cannot supply an authenticated role or select an object ID. [Cloudflare Access application tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)

`route` uses the existing Python `FreeRoute` shape. Use null when physical inference is unavailable. Each commissioned sender needs its independently scoped credential in the Worker's protected `WORKERS_AI_TOKEN` or `REVIEW_WORKERS_AI_TOKEN` binding. The optional review route uses the distinct UIF Free account and GLM model. Its `reviewer_opening_liability` must bind the original ID, receipt SHA-256 and at least 2,489,344 millineurons of prior liability. This is conservatively retained across days and object restarts. The same issue claim protects both accounts.

Quota installation requires an independently qualified whole-account reader. Its fixed private `QUOTA_VERIFIER` service binding receives `POST /v1/observation/read` with `{source_receipt_sha256, route}` and returns `{binding_sha256, source_receipt_sha256, projection, coverage, gap_evidence_sha256}` for the exact original. `coverage` must be `entire-account-all-routes-from-utc-midnight`. The implementation identified by `quota_verification_sha256` must authenticate account identity, Free plan, fixed model/Gateway, absence of paid fallback, a conservative unsampled bound through `covered_through`, and a separate bound for unrelated account spend through `valid_until`. Configure that binding only after verifying its sources. A submitted number or a recent analytics sample cannot establish those bounds. Missing verifier, original receipt, bound or credential holds the affected route. This package does not install an account telemetry reader or mint a quota observation.

`LINEAR_TOKEN` is the exact protected Linear Authorization header value for the installed issue-scoped sender. `upload_hosts` specifies the individually verified storage hostnames returned by the authenticated Linear API. The uploader accepts private Linear asset URLs under the configured workspace UUID. Unknown upload destinations or unqualified access/cache headers hold the original. The Git integration writer remains with the existing OffMenu owner. Native issue state updates require a destination-side compare-and-set primitive that protects concurrent human updates. Both unsupported mutation routes refuse before egress. [Linear GraphQL authentication](https://linear.app/developers/graphql) · [Linear file uploads](https://linear.app/developers/how-to-upload-a-file-to-linear)

## Wire contract

The installed configuration digest includes Access issuer/audience, service identities and the complete nonsecret authority configuration. A changed digest is effectively stopped. Authenticated control compares the prior and new digests plus epoch to `/v1/commission` with independently reviewed reconstruction evidence. This preserves every original operation and liability, fences active generations and leaves admissions paused. Startup, grants, quota observations and independent attestations require fresh bindings. Changing the trust anchor itself requires a separately qualified migration. `/v1/transition/original` can read retained original transition receipts by original installation, session and request ID.

`GET /v1/status` returns the authenticated peer identity, role, coordinator epoch, pause, lease duration and independent quota identity. Other calls use JSON POST. Timestamps are whole UTC seconds. Scope, source, fence, lease, operation and `SendIntent` mirror their Python dataclasses. SHA-256 fingerprints use the repository's sorted, compact Python JSON representation. Control requests that mutate state carry an immutable `request_id`; an exact replay returns its own original receipt.

| Route | Receipt or effect |
| --- | --- |
| `/v1/control` | Control peer compares `expected_coordinator_epoch`, applies `stopped`, and advances the epoch on every new explicit stop or on resumption from a stop. An exact retry returns its original receipt without changing the epoch. After the protected installer verifies the normal Stokowski release, the same `stopped: false` request can include `startup: {stage, session_id, evidence_sha256, coordinator_epoch, previous_request_id}`. Use the installed configuration's epoch and the prior activation ID from authenticated control status, or `null` for the first selection. The request atomically selects that installed stage for `stokowski-foo.service` and returns the exact binding with its receipt. |
| `/v1/startup/bind`, `/v1/startup/read` | Existing time-limited bindings keep their original semantics. The normal binding recorded by `/v1/control` remains usable for restarts at the same epoch and deployment. Native startup still requires its authenticated identity and receives a fresh nonce-bound receipt valid for at most five seconds. An explicit stop or changed installation invalidates startup. |
| `/v1/grant` | Control supplies `{request_id, previous_sha256, grant}`. The grant pins `scope`, `source`, `sessions`, `attempt_ids`, `reconstruction_sha256`, `valid_until`, `allow_comments`, `allow_uploads`, `allow_provider` and `review_of`. Optional `result_retention` explicitly approves original model output for this exact issue/source. It supplies `max_response_bytes` (up to 256 KiB), `max_retained_results` (up to 128 per issue), `readers` (original actor and any named recovery custodian) and `evidence_sha256` for the destination/data authorization. Omission disables inference that needs recoverable response storage. The previous hash is null for creation. Changing an extant grant revokes its active generation. |
| `/v1/claim`, `/v1/claim/observe` | `{request_id,expected_coordinator_epoch,scope,source,reconstruction_sha256,attempt_id}`. Fresh claim and immutable original lookup return the V3 typed lease receipt with server-derived authority identity. |
| `/v1/claim/read`, `/v1/renew` | Lookup `{project:"OffMenu",identifier,by_attempt}` returns the current `NativeClaim`. Renewal compares `{request_id,current}` exactly. |
| `/v1/attempt/attest`, `/v1/claim/attempt` | Control binds `{fence,previous,successor,terminal_receipt_sha256,request_id}` to existing stage completion. The holder compares `{current,expected_attempt_id,attempt_id,request_id}`. Consumed attempts cannot reopen. |
| `/v1/attempt/successor/read` | For a marked native OffMenu assessment, compare the retained control attestation body (`original`) and native attempt CAS body (`advance`) with both actors' original receipts and the current successor lease (`current`). For a newly attested edge, the same owner may omit `original`: the authority uses the first marked control original retained when the edge was created. Pre-existing edges require the original explicit form. Both forms return only `{matches: boolean}`. A match proves the stage CAS already committed. Native task registration and the first worker entry require separate evidence. |
| `/v1/review/bind`, `/v1/review/read` | The control peer binds the original verified author operation/receipt, current lease, candidate, independent checkpoint receipt and final `send_prompt_sha256`. The candidate's preparation fingerprint includes issue, scope, source, tree (nullable for a documentary result), both attempts and the prepared prompt hash. Read accepts `{current,author_attempt_id,review_attempt_id}`. `review_of` selects the distinct account. Every reviewer call checks the finalized first user message before physical egress. |
| `/v1/revoke`, `/v1/handoff` | Revocation targets an exact fence. A handoff requires its distinct registered successor, exact prior lease, current epoch, independent quiescence/reconstruction attestations and reconciled originals. Its new generation has a new lease ID. |
| Missing peer before work starts | For an unchanged generation-one assignment granted to both peers, control can include `unstarted_attempt_id` in an exact-fence revoke. Its subsequent handoff fact must bind `recovery: {attempt_id, revocation_id}` to that original, the named successor, the current epoch, and independently observed workspace quiescence and reconstruction. The successor submits that exact fact's fields through `/v1/handoff` and reads its own retained receipt through `/v1/transition/original` after a lost reply. Explicit holds and any recorded work exclude this path. The scheduler must supply actual outage and workspace evidence, and the receiving runtime must bind the protected `uif.shared-lease.recover-unstarted/1` assignment before execution. |
| `/v1/attest`, `/v1/completion/read`, `/v1/release` | Control attests an independently obtained exact handoff, completion or original terminal receipt. The holder can read `{current}` and submit `{request_id,completion}`. One terminal fact per fence is immutable. |
| `/v1/reclaim`, `/v1/reclaim/observe` | A genuinely new scope compares the exact released `previous` lease and `previous_completion_sha256`, next immutable scope/source, independently granted new reconstruction and `attempt_id`, and current epoch. Old receipts and generations survive. The fresh transition kind is `uif.shared-lease.reclaim/1`. |
| `/v1/usage/write`, `/v1/usage/read` | Quota peer supplies `{request_id,source_receipt_sha256,projection}` and the receiver resolves the independent original. A role with read access supplies `{route}`. The reply adds `source_receipt_sha256` and `admission_binding_sha256` to the typed quota receipt. |
| `/v1/send`, `/v1/provider/receipt` | `{intent,body_base64}` sends exactly one fixed-route, bounded Workers AI request. Originally verified private response bytes, Gateway ID and receipt SHA-256 are recoverable using `{operation}` by the current granted original actor or the control custodian. The physical request and readback return `uif-receipt-sha256`. |
| `/v1/provider/ack`, `/v1/provider/prune` | Once the original owner durably saves the bytes, it acknowledges the exact operation, `receipt_sha256` and `body_sha256`. A named recovery custodian can then prune those bytes. Terminal hashes and charge records survive. Unknown effects have no prune path. |
| `/v1/linear/comment`, `/v1/linear/upload`, `/v1/linear/receipt` | For a comment send `{operation,attempt_id,body}`. For a file send `{operation,attempt_id,filename,content_type,body_base64}`. The original binds the issue, action and exact content. Both upload allocation and PUT validate current ownership. Receipt lookup `{operation}` returns the original comment ID or private asset URL and `receipt_sha256`. |
| `/v1/original`, `/v1/reconcile` | Original lookup is read-only and retains unknown outcomes. Control may record an independently attested exact original receipt once; it cannot overwrite a terminal result or settle an in-flight call. |

### Native investigation entry after a marked assessment

The native session can check the existing independent control approval using
`/v1/attempt/attest/native` with `{current,previous,successor}`. Its boolean result
compares the live predecessor with the original control receipt. Only the same native
owner's `/v1/claim/attempt` can arm a new investigation entry. That transaction checks
the retained control approval and the deterministic native request ID
`SHA256(canonical({lease:current.fence.lease_id,attempt:successor}))`, consumes the
attempt and stores the exact CAS body. A missing original leaves the predecessor in place.

After confirming the consumed CAS, the native task uses `/v1/attempt/successor/enter`
with `{current,advance}`. Here `current` is the live lease and `advance` is the exact
original CAS body including its original expiry. The transaction winner alone receives
`{entered:true,permit}`. A duplicate returns `{entered:false}`. After an uncertain reply,
`/v1/attempt/successor/observe` returns `{entered:boolean}` for that same original.
No read exposes a permit. Only a qualified winner can pass `uif-dispatch-permit` for
new investigation effects. Existing stop, source, quota and original-effect checks still
apply. Previously consumed assessments without an entry record remain available for exact
CAS observation. They cannot acquire a new investigation permit.

### Native entry after an unstarted recovery

A new `recover-unstarted/1` handoff to the configured native recipient records first-entry
eligibility in the same transaction as the handoff receipt. The recipient first retains its
create-only local intent. Only its creating task may POST `/v1/recovery/enter` with
`{current, attempt_id, original:{installation_sha256,session_id,request_id,request_sha256}}`.
The authority compares the exact handoff original, live native fence, current lease and attempt.
The winner receives `{entered:true,permit}` after durable confirmation. A second entrant receives
`{entered:false}`. After an uncertain response, use `/v1/recovery/observe` with the current lease
for the same fence. The boolean reply never returns the permit. The native process retains the
permit only for the winning task.

The `uif-dispatch-permit` header accompanies each new Workers AI send, Linear comment or
upload, source write/checkpoint, initial attempt advance and active onward handoff for this
recovered attempt. The authority rechecks the permit at reservation and immediately before
egress, including the attachment PUT. Source mutations require a permit-aware source sink.
Detached provider calls without recipient capability propagation remain held. Renewals,
original receipt reads and terminal reconciliation use their existing authority. An exact
independently attested terminal completion can release the fence. A revoked fence can move
under its existing quiescence, reconstruction and reconciled-original requirements. Neither
operation issues first-entry permission. A later stage requires its existing independently
attested attempt transition.

Pre-upgrade native recovery originals remain observable. They cannot establish exclusive
first entry or authorize new effects on their recovered attempt. Ordinary cooperative
handoffs, released-and-reclaimed issues and Morgan-recipient recovery keep their own
transition semantics. Work outside the authority must use the winning native task and its
protected workspace binding.

## Effect ordering and recovery

The same SQLite transaction compares the live claim, source, generation, attempt, deadline and whole-account evidence before reserving the original operation. Strict admission requires `upper + gap_upper + retained liabilities + request maximum < 7,500,000` millineurons. Pending sends survive UTC rollover. Verified local usage keeps its full conservative estimate until covered by subsequent independently verified complete telemetry. Same-day upper bounds may only increase. An observed overrun durably freezes the affected account pending correction of the invalid reservation assumption.

After synchronizing storage the object checks the claim and budget again and calls the physical fixed-destination fetch in the same JavaScript turn. Stop or revoke during the durability wait prevents that call. A lost response leaves the initial intent and liability in storage. Only the independent custodian can reconcile it with attributable destination evidence. The object never sends an unknown original again. Generated model content is retained in private SQLite storage only on verified success; the externally visible original receipt contains a digest. [Durable Object storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)

Run `npm ci` and `npm test` in this directory with Node 24. The affected tests use SQLite and an actual local workerd process with intercepted network requests. Live Access, provider usage, Git publication, and service installation require separate original receipts.

### Bounded public source candidates

An issue holder can edit a candidate inside this authority without acquiring write access to a checkout. The control peer must first bind an explicit public-source receipt and the named members with `POST /v1/source/bind`. Before issuing that binding it must verify the selected repository/tree, every parent directory, each regular Git member (including mode) or confirmed absence, permission to retain the named bytes and candidate, and which individual paths may be edited. The authority verifies each supplied base file's SHA-256 and Git blob hash, rejects aliases and unsupported modes, and keeps the original control binding immutable. A repository grant alone cannot initialize an overlay. Initial author and review attempts retain their existing ownership and account controls.

The holder reads authorized names and current bytes via `/v1/source/list` and `/v1/source/read`. For `/v1/source/write`, the authority independently reads the verified original provider response and its indexed tool call, checks the exact unique replacement or new-file contents, and compares the caller's path, original-operation ID, whole-candidate revision and byte hashes. After all asynchronous hashing, the same SQLite transaction checks the live fence, stop flag, lease, original tool and preimage, stores the actual bytes and records the receipt. Rejoining the same tool via `/v1/source/receipt` recovers that original effect. UTF-8 members are limited to 256 KiB, the current attempt to 32 paths and 512 KiB, and immutable change history to 64 edits and 8 MiB. New files receive regular mode 100644.

The author freezes a nonempty delta with `/v1/source/checkpoint`. Its digest covers the original fence, pinned source, author attempt, public retention receipt, revision, and sorted changed members with exact before/after bytes and modes. An explicitly registered successor reviewer can read the frozen candidate. The separate review binding checks its exact digest and requires the existing independent-account, original author response and finalized prompt bindings. The existing OffMenu publisher receives the accepted candidate under its own authenticated custody and must verify the source/tree, current preimages and exact postimages when materializing it. The candidate has no Git commit, PR or publication receipt until that publisher supplies one. An unsealed source attempt retains original tool responses needed for recovery.

A source bind can explicitly name the authenticated author and/or control custodian in `recovery_readers`. The default list is empty. Under the same authority trust anchor, `/v1/source/original` accepts the historical fence, attempt, revision and optional expected candidate digest. It can return retained bytes after lease expiry, stop or recommission. The response distinguishes a frozen checkpoint from an earlier unsealed revision. A null revision returns the last revision and sealed digest only. This lookup cannot issue an edit, freeze, grant or publication. Control may revoke a named recovery reader with `/v1/source/recovery/revoke`.

### First dispatch for explicitly enrolled fresh OffMenu assessments

Only an existing authenticated control owner may request this narrower behavior, by adding
`fresh_assessment: {attempt_id, first_intent_sha256}` to an otherwise valid OffMenu grant before
the issue or attempt has any claim. The attempt must be the selected first automatic assess
(`cf-intake-<64 lowercase hex>-assess`). The control owner must independently bind the exact
approved first dispatch intent.

For this grant, the configured native Access session supplies `first_intent_sha256` on
`/v1/claim`. Its request ID is the native assignment's deterministic SHA-256 over
`{claim: scope, recovery: reconstruction_sha256, authority: authenticated_identity, epoch: expected_coordinator_epoch}`.
The claim transaction compares the enrolled digest, original ID, source, attempt, epoch and
current grant before issuing generation one. The receipt fingerprint covers the complete body.
The shared lease receipt retains its existing typed digest. To reconcile a missing reply,
submit the identical original to `/v1/claim/observe`. A historical plain original remains
observable with its original body. It cannot open a new dispatch under this enrollment.
The Foo journal custodian verifies and retains the local bytes before submission. The authority
verifies the configured native session and authorized original. All processes able to use that
service credential share the same native principal.

The existing configured native peer can POST `/v1/dispatch/enter` with exact
`{current, attempt_id, first_intent_sha256}`, where `current` is the active native lease. Only
the transaction winner receives `{entered:true, permit}`, after the existing store confirms
durability; duplicates return only `{entered:false}`. POST `/v1/dispatch/observe` takes the same
body and returns only `{entered:boolean}` without issuing anything. Neither status nor old
claim receipts can recover permission. A missing or uncertain acknowledgment holds the
attempt for supported observation and does not justify a resend or another dispatch.

For that enrolled attempt, only the existing `/v1/send` endpoint accepts the winning permit
via the `uif-dispatch-permit` header. The header is forwarded only to the private authority,
never to the destination. The existing physical reservation transaction seals that exact first
provider operation, including its original ID and full payload/route fingerprint. Any other
operation refuses, even with the same permit. The seal and current permit are checked again
after storage confirmation and immediately before the physical call. Keep the permit in the
winning caller's memory only. The journal keeps its digest; reads and original receipts never
return the permit. Existing current-fence, scope, result retention, quota and no-second-send
checks still apply. A follow-up provider turn would be a different operation and is not allowed
by this first-dispatch profile.

Existing Linear comment and upload mutations for that enrolled attempt remain held until a
separately scoped, exact canonical report effect binding is available. A provider permit is
not that binding. Linear receipt observations, later attempts, unflagged grants, other projects
and startup are unchanged.
