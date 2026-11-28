# Stokowski provider readiness

Evidence snapshot: 24 November 2026 UTC. Scope and acceptance: [UIF-1313](https://linear.app/maldous/issue/UIF-1313). A documented client, installed executable, account login, or passing fixture is not live spend or dispatch approval. Keep every unqualified route disabled and preserve the current stop/hold state.

## Current status: 26 November 2026 AEDT

The reviewed guarded-idle deployment ([UIF #130](https://github.com/maldous/uif/pull/130))
was observed running with `free_route_unavailable` and zero restarts. That is
an idle health snapshot, not inference, publication, or delivery acceptance.

Cloudflare is one candidate, not a prerequisite. Assess all permitted
zero-added-cost routes and select the first that qualifies under the existing
admission controls. The advisory pool selector is not installed dynamic
dispatch. No live inference route was qualified in this snapshot.

- Cloudflare Workers AI: Free plan verified; safe route and quota enforcement
  are not yet qualified for inference.
- Existing OpenRouter and Groq access paths each returned HTTP 401. This is
  not a general finding about either provider or its free tier.
- Native Antigravity is present but unqualified. Other free routes assessed
  remain unqualified. Local Codex calls and paid, prepaid, or fallback routes
  were outside this assessment.

Provider tiers, access and runtime health can change. Catalogue availability,
third-party funding or possible future credits are not current authority to run.

## Requested routes

| Route | Verified or supported | What must pass before any live use |
| --- | --- | --- |
| **Cloudflare Workers AI, OffMenu Free** | Optional zero-added-cost candidate; the observed guarded-idle service does not qualify live inference. The bound model is `@cf/zai-org/glm-4.7-flash`; previous readback on the named Gateway was Standard, not Unified Billing. [Free allocation](https://developers.cloudflare.com/workers-ai/platform/pricing/) is 10,000 neurons per UTC day; UIF's stricter limit is **projected below 7,500**, not a provider setting. [Adaptive GraphQL](https://developers.cloudflare.com/analytics/graphql-api/sampling/) is not an account-wide hard bound. | Read the approved account, exact gateway/model/no-fallback, cutoff window and receipt completeness without inference. Establish authenticated unsampled account-wide upper bound, or exclusively fenced and reconciled all-producer accounting. Include gaps, unresolved reservations and worst-case physical call. No personal Paid substitution or unapproved activation. |
| **Codex, included signed-in plan** | Distinct from API-key billing or a generic Chat endpoint. UIF-1313 specifies stop new issues at **15% remaining**, stop all calls at **10%**. No current quota or worker authorization is inferred from historical readiness. | Verify active client/config/model in a no-inference readback and fresh account limits across all applicable windows. Prove the sender reserves for every physical request; refuse unresolved refresh, retry or concurrent ownership. Keep API-key and alternative provider fallback excluded. |
| **OpenAI Responses, existing promotional account** | Approved separate qualification path, not Codex API-key mode. Existing no-inference model-list readback and offline wire checks do not prove Responses grant eligibility or billable-path exclusion. [OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits) are a backstop, not an exact grant meter. | Read actual grant balance and expiry from the approved account, prove model/project eligibility and no purchased credits or auto-recharge fallback. Apply the 15%/10% grant rules on trusted fresh balances and bounded next attempts. |
| **OpenCode → Groq Free** | Separate client/provider binding. OpenCode v2.3.2 was seen on Foo, not proof of active auth, included quota, configured model or safe dispatch. [Groq limits](https://console.groq.com/docs/rate-limits), [OpenCode CLI](https://opencode.ai/docs/cli/commands). | Confirm Free org/project and exact installed client/provider/model supports the required tool protocol. Prove org-level counters/reset, no paid route, no unmetered subagents and no internal retry outside the sender fence. |
| **OpenCode → OpenRouter `:free`** | Another independent binding; a free suffix is not proof the client sends the intended model and routing header/body. [Limits](https://openrouter.ai/docs/api_reference/limits), [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection). | Verify exact free model and permitted endpoint, disable alternate model/provider and paid balance draw; read back account quota and keep body/routing controls pinned. Fail closed if the route stops being Free or cannot be pinned. |

## Assessment lanes, disabled by default

| Route | Status and next no-spend check |
| --- | --- |
| **Google Antigravity CLI** | [Personal Free OAuth](https://antigravity.google/docs/cli/install/), [headless machine output](https://antigravity.google/docs/cli/headless/), and [explicit model selection](https://antigravity.google/docs/cli/reference/) are documented. [Interactive usage](https://antigravity.google/docs/cli/commands/usage) is not a verified atomic headless meter. Verify exact client and account, model availability, effective [`useG1Credits` off](https://antigravity.google/docs/cli-settings), observable quota windows and bounded internal retries/fallback. Do not run a prompt to test any of these prerequisites. |
| **Claude Code** | [Headless `claude -p`](https://code.claude.com/docs/en/headless) exists. `--bare` does not use subscription OAuth; documented retries can be quiet. Anthropic [plan/programmatic guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) distinguishes signed-in subscription use from linked Console API credits for eligible plans. No Free headless entitlement established. Require exact entitled account, included allowance, no extras or paid fallback, and bounded physical sends. |
| **GitHub Copilot CLI** | [CLI supports all plans](https://docs.github.com/en/copilot/get-started/cli-quickstart), but [Free and Student are auto-model-only](https://docs.github.com/en/copilot/get-started/plans) and do not satisfy the fixed-model rule. Do not treat this as retired GitHub Models or add a subscription. |
| **Other free catalogues** | [OpenCode Console](https://opencode.ai/docs/console/models/), [Kilo](https://kilo.ai/docs/gateway/usage-and-billing), and [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/pricing) are assessment-only. Recheck the exact model and account, supported client, trial expiry, data-use rules and included-only billing with public metadata before considering fixtures. A changing list, an available model or a promotional balance does not grant live use. |

## Interface crosswalk

These are evidence states, not a new allowlist. A public catalogue or a model
list proves discovery; an authenticated no-inference readback can establish
account access and only the limits it actually reports. Neither grants
dispatch authority. Existing route admission governs live use.

| Interface | Access and current evidence |
| --- | --- |
| **Native Codex App Server / Codex SDK** | Signed-in ChatGPT account; the UIF App Server observer reads `account/rateLimits/read` without inference. This is the included Codex entitlement, not an OpenAI API grant. Existing native access is not a current Stokowski route qualification. |
| **OpenAI Platform Responses / Agents API** | Separate API organization/project and scoped API credentials. The [ChatGPT API credits program](https://developers.openai.com/api/docs/guides/chatgpt-api-credits) distinguishes linked API credits from Codex API-key usage. UIF has a disabled Responses admission/settlement seam ([#137](https://github.com/maldous/uif/pull/137)); no live grant proof or mounted [hosted Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview) route established. |
| **OpenAI-compatible Chat Completions** | OpenCode is a client, not an entitlement. Existing OpenRouter and Groq bindings returned 401. Public free-model/tool support does not qualify an exact provider endpoint or rule out automatic retries, alternate models, or auxiliary sends by the installed client. |
| **Cloudflare Workers AI native API** | The Free plan readback and model catalogue are confirmed evidence of plan and discovery only. Neither proves the complete account-wide usage bound required by the existing route. |
| **Gemini Developer API / Vertex AI / Antigravity** | Gemini offers [native and OpenAI-compatible](https://ai.google.dev/gemini-api/docs/openai) APIs with a Gemini key and project quota; the existing reference is disabled. Antigravity direct CLI is present but unqualified. Its [Python SDK](https://antigravity.google/docs/install/#programmatic-authentication) documents Gemini key, Vertex/ADC or offline local auth, not consumer IDE OAuth as SDK auth; [direct CLI/CI is distinct from consumer wrapping](https://antigravity.google/docs/faq). No verified Vertex route here. |
| **Anthropic Messages / Claude Code** | Messages uses Anthropic API credentials and billing; signed-in headless Claude Code has separate entitlement. No current eligible binding or included-only allowance established for either. |
| **Other typed candidates / local runtimes** | Legacy metadata-only tooling checks catalogues for Hugging Face and Nous, account state for Fireworks, and balance for DeepSeek; none is an inference receipt. Cerebras, Cohere, Mistral, NVIDIA, SambaNova, Together and Z.ai are disabled references, not usable routes. Codebuff/Ollama drafts do not establish a qualified relay or local daemon. GitHub Models is [retired](https://docs.github.com/en/github-models), not the Copilot CLI. |

### Refresh the provider catalogue

Use the existing Foo Windmill connection to list named provider references and
their resource, variable, or script metadata. If the owner supplies a Foo Bashrc
binding, use its named reference through that owner-approved interface. Keep
credential resolution and provider requests with the authorized runtime. A
missing or inaccessible reference holds that direct route independently of
other bindings.

The existing catalogue projector reads the `data[].id` shape for [OpenAI](https://developers.openai.com/api/reference/resources/models/methods/list),
[Mistral Studio](https://docs.mistral.ai/api/endpoint/models), and
[OpenRouter](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties).
It also reads `models[].id` from the [xAI language catalogue](https://docs.x.ai/developers/rest-api-reference/inference/models#list-language-models).
The projector retains explicit model IDs only. Its observations establish
discovery and carry no pricing, quota, or dispatch permission.

On refresh, a newly listed model can enter assessment under the existing owner.
An unavailable, stale, or incomplete read cannot renew an earlier availability
observation. Recheck an exact configured route before dispatch and reconcile any
outstanding original under its original account and model. Treat direct API and
router routes for the same model as separate bindings. A direct account, client,
supported protocol, and usage window must qualify on their own. On OpenRouter,
require the exact `vendor/model:free` model and a current no-cost route with the
required tool support, approved data handling, bounded retries, and no alternate
models or paid fallback. The first live scope remains the authorized OffMenu
rollout under the existing shared reservation and effect fence.

### Codex roster status

The normal launcher exposes `uif_provider_roster()` during the existing
discovery lifecycle. It reports each installed sender's reader state and lists
Codex App Server as a configured candidate. The current normal installer has
no `TrustedMetadataPort`, so it reports `reader-unmounted`. The task-host
usage tool calls the same `account/rateLimits/read` RPC. App Server v0.172.0
may proactively refresh managed authentication during that RPC. Passive
`account/rateLimits/updated` provides a rate snapshot with no account binding
or current-status guarantee for an idle worker. [App Server account handler](https://github.com/openai/codex/blob/rust-v0.172.0/codex-rs/app-server/src/request_processors/account_processor.rs#L1205)

With a qualified same-account, same-session port that prevents refresh and
inference mounted, the same discovery loop
publishes current windows and typed credit diagnostics from the exact metered
bucket. Changed or stale evidence drops out of the current view. The credits
snapshot describes account state. Credit-backed dispatch requires its own
bounded and authorized spending controls. Included-allowance dispatch still
requires exact verified entitlement, capability, quota and sender fences.

### Reusable offline protocol fixtures from ANQR

[ANQR #217](https://github.com/maldous/anqr/issues/217) records the archived
LiteLLM alias returning Chat Completions but 404 on Responses; preserve that
distinction rather than treating an OpenAI-compatible model list as a protocol
test. [#1158](https://github.com/maldous/anqr/issues/1158) distinguishes the AI
SDK bridge from the historical Codebuff SSE relay. Reuse those patterns with
synthetic streaming, cancellation and tool-call round trips, not old URLs or
credentials. [#200](https://github.com/maldous/anqr/issues/200),
[#202](https://github.com/maldous/anqr/issues/202) and
[#212](https://github.com/maldous/anqr/issues/212) document catalogue/cooldown
separation, attempt caps and all-routes-exhausted behavior;
[#1074](https://github.com/maldous/anqr/issues/1074) and
[#759](https://github.com/maldous/anqr/issues/759) capture integration and
deterministic fixture patterns. These are historical source references, not
current bindings. [UIF-289](https://linear.app/maldous/issue/UIF-289) explicitly
separates the selected V3 path from revival of the archived harness.

## Admission proof

Use existing no-inference seams, not a new scheduler: pin sanitized client/model/route metadata and test only synthetic wire plus offline refusal paths (wrong identity, quota stale or missing, 402/429, amount at cutoff, fallback attempted and lost receipt). Independent shared account/issue fencing must own the actual send; a CLI wrapper, a local budget reservation or a green fixture alone cannot do that. Hold unknown originals and their reservations. Verify release/ownership separately; a reset never starts the service or replays work. Record original operation, child cleanup and exact installed versus offline scope without credential values.

## Supervised idle startup

The existing Foo operator must authorize and install the exact reviewed
launcher stage through the protected installation process for
`stokowski-foo.service`. The unit's existing activation condition and the
protected `ACTIVATION_AUTHORIZED` marker still apply. Idle selection comes
from the root-controlled `selector-v1/remote-authority.json` of that stage.
Its schema is `uif.stokowski-remote-idle/1` with only `unit`, `stage`, `origin`,
`access_credential`, `native_identity`, `coordinator_epoch` and `hold_reason`
alongside `schema`. The reason is `free_route_unavailable` or
`free_quota_unavailable`. Bind the same native identity, authority and policy
pins to the installed stage. Never put credential bytes in this record.

The guarded process authenticates a fresh stopped status at that exact
epoch and reports its hold in systemd status and the journal. It runs no
scheduler, HTTP server, issue admission, provider calls or publication, and
exits on the normal service stop signal. Idle cannot promote itself.
Normal startup still requires its existing exact route and authenticated
startup receipt. Authorize that mode separately through the existing
installation process once the route and quota qualify.

### Detached operator preflight

After source review, the trusted Foo operator must provision the reviewed
preflight tool and a root-controlled packet for the exact admitted stage.
The following command uses the proposed stage for PR #114. The profile fixes
the target to `stokowski-foo.service`. Run as the named non-root operator in
the admission, including when the approved Foo runner is used as transport.

```sh
/usr/bin/python3 -I -S -B /var/lib/uif-stokowski/idle-upgrade/guarded_service_upgrade.py \
  --profile uif-idle --stage uif1313-idle-a22fbb2
```

The operator's protected loader supplies the following under
`/var/lib/uif-stokowski/idle-upgrade/uif1313-idle-a22fbb2/`.

- `admission.json` (`uif.guarded-upgrade-admission/1`) binds the exact profile,
  unit, stage, operator name and UID, and SHA-256 of each witness below.
  Set `issued_at` and `expires_at` in UTC epoch seconds, at most five minutes apart.
- `review.json` (`uif.guarded-upgrade-review/1`) records the actual
  author-distinct acceptance of the exact head, stage, bundle, launcher,
  idle config and current parked unit. The parked-unit pin is the SHA-256
  of the exact `systemctl --no-pager cat stokowski-foo.service` bytes.
- `confirmation.json` is the original trusted host-owner confirmation.
  The admission binds its complete byte hash. The preflight does not
  originate this confirmation or interpret a caller statement as approval.
- `quiescence.json` (`uif.guarded-upgrade-quiescence/1`) binds the unit and
  parked-unit pin to a fresh operator observation of zero inflight work,
  stopped children and no unresolved operations.
- `bundle.tar` is an uncompressed, independently reviewed idle-stage bundle
  with `launch.py` and `selector-v1/remote-authority.json` at their fixed
  relative paths. Use regular files and directories without link entries,
  credentials or an activation marker.

The command only reads and emits an advisory JSON plan. Its output cannot
authorize a later write. Repeating it cannot replace an existing stage.
The allowlisted legacy OffMenu profile remains with its existing owner-only
loader.

### Detached apply transaction

`guarded_service_upgrade.apply("uif-idle", stage, admission=admission, host=host)`
is an in-process entry point for the separately qualified Foo loader. The
installed loader, not a CLI caller, must authenticate `ApplyAdmission` using
the host-owned approval and exact preflight bytes. The admission also pins
the actual lock custody, operation ID, prior selection and exact idle
selection. The preflight command remains read-only.

The required host adapter must hold the protected selector lock for
`stokowski-foo.service`, including proof that all permitted selector writers
and unit start paths honor that custody. Under that lock it must:

- Authenticate the admission, rerun the actual quiescence and preflight
  checks, and verify the sealed idle stage and current selector by hash.
- Durably create a write-once intent fenced by both operation ID and prior
  selection. Expose both fences and completion for exact retry readback.
- Compare and select only the verified idle stage, rechecking the passed
  deadline at the write boundary. Preserve the prior stage and the existing
  activation condition, with a separately accepted restore procedure.
- Read back the effective selected stage and record completion durably.
  An intent without an exact completion requires the host owner to reconcile
  the original effect before another attempt.

### Linux selector bootstrap and apply

The current parked unit launches a fixed stage directly. Its effective
`systemctl cat` pin is
`96c56b11f328c4d97df40e5d5ab705d4c85c6cd31c96cbacaca699ca05873502`.
The existing OffMenu and UIF quota locks cover other resources.

The qualified Foo operator must independently authorize the one-time
bootstrap before running apply. Preserve the parked unit and its sole drop-in
`50-uif1313-stopped-guard.conf`. Confirm the exact parked-unit pin, live
quiescence, both markers absent, and that all permitted stage-selector
writers and activation operators use the new custody boundary. Install the
reviewed `guarded_service_upgrade.py` at the protected path above and the
exact `60-uif1313-idle-selector.conf` in the existing unit's drop-in directory.
The new drop-in replaces the one parked path condition with the idle
stage's marker condition. The selected launcher independently checks that
marker again. Qualify this migration and any required daemon reload on the
host, then record the new effective unit SHA-256 and retain the original
definition and stage. Apply itself runs no daemon reload.

Provision the fixed, nonempty, root-owned `selector.lock` (mode 0644) and
root-only `receipts/` below `/var/lib/uif-stokowski/idle-upgrade`. Initialize
`selection.json` with the exact bytes
`{"mode":"parked","stage":"uif1313-guarded-1f45bd56"}` (no newline).
Pre-materialize the independently accepted stage at
`/opt/uif-stokowski/uif1313-idle-a22fbb2`, with regular root-owned files,
the exact reviewed modes, and `venv/bin/python3.12` as the sealed executable.
The loader verifies the actual tree against the full reviewed tar. It does
not extract input archives or create the stage for you.

The admitted non-root operator can run the documented preflight command
with `--action sealed-preflight` to derive the exact plan, selector and lock
pins. The trusted host admission controller supplies a protected
`linux-apply.json` in the same packet directory. Its
`uif.foo-idle-apply/1` record includes the `ApplyAdmission` fields, the
operator-admission file hash, and both the original and bootstrapped unit
pins. It must bind the independently verified host approval as already
recorded by the operator admission. Transport access cannot issue this file.

After exact review and host admission, the fixed apply command in the
approved Foo root runner is:

```sh
/usr/bin/python3 -I -S -B /var/lib/uif-stokowski/idle-upgrade/guarded_service_upgrade.py \
  --profile uif-idle --stage uif1313-idle-a22fbb2 --action apply
```

Use workspace `foo` and the existing `u/admin/foo_root_jobs` transport.
The adapter locks the already-provisioned selector, creates durable original
receipts, changes the selector atomically and reads it back. Its ExecStart
use holds the same lock in shared mode across exec and checks the selected
interpreter, launcher, config and activation marker. Apply performs no
service start, marker write, provider call or legacy action. If a receipt or
selector disagrees, preserve both and reconcile the original. The restore
operation and any activation remain separately qualified host actions.

### Post-apply verification for the Foo owner

The approved operator can use the existing root runner as transport for the
read-only receiver after the protected apply completes. Set
`operation_id` to the original apply operation from the host receipt.

```sh
/usr/bin/python3 -I -S -B /var/lib/uif-stokowski/idle-upgrade/guarded_service_upgrade.py \
  --profile uif-idle --stage uif1313-idle-a22fbb2 --action activation-preflight \
  --operation-id "$operation_id"
```

The verifier holds the same exclusive selector lock as apply. It checks the
original protected packet, operator binding, review and witness hashes, intent,
selection receipt and prior-selection fence. It also verifies the exact sealed
bundle against the selected bytes and rechecks the stopped effective systemd
unit and both absent markers. It does not change the selector, service, packet
or marker. A successful readback is a snapshot, with the lock released before
it returns. A future issuer must recheck the bindings under the same lock and
obtain fresh quiescence and an operation-specific decision from the actual
authenticated Foo owner before issuing a marker.

For independently signed confirmation verification, the existing Foo host
owner must enroll the already-authorized operator's existing OpenSSH public
signing key at `/etc/uif-stokowski/activation_allowed_signers` through the
owner-controlled Foo administration surface. The file must be protected and
contain one ordinary allowed-signers entry with the original admitted operator's
exact account name as its sole principal. Wildcards, multiple principals and
signer options are refused. The command does not enroll the key and cannot
derive an authorized signer from chat or runner identity.

The enrolled host owner signs a JSON file named `activation-confirmation.json`
with exactly `schema` (`uif.foo-idle-activation-confirmation/1`), `action`
(`authorize_idle_marker`), `operation_id`, the returned `preflight_sha256`,
and integer UTC epoch seconds `issued_at` and `expires_at`. The window is
at most 300 seconds. The owner executes the existing-key signing action in
their own trusted environment.

```sh
ssh-keygen -Y sign -f "$existing_operator_key" \
  -n uif-foo-stokowski-idle-activation activation-confirmation.json
```

The owner supplies only the JSON and detached signature to the protected
packet directory. Run the same receiver with
`--action verify-activation-confirmation` and the same original operation
ID. It checks the explicit signature against the single enrolled principal
under the selector lock and returns only hashes and the expiry. An absent
enrollment or invalid, stale or mismatched confirmation is refused. The
verification result does not issue admission, place a marker or start a
service. Host bootstrap and those effects still require their own reviewed
owner path.

### Signed issue and one guarded idle start

The qualified Foo owner must first enroll the existing operator key at the
protected allowed-signers path above and complete the independently approved
bootstrap. The operator must place the installer admission, its original
host confirmation, exact review, fresh quiescence and bundle in the fixed
packet directory. These are independent inputs and are never generated by
the root runner. The reviewed idle drop-in sets `Restart=no`. Verify that
setting in the effective idle unit during bootstrap. The parked unit's
restart policy remains in place until the reviewed idle bootstrap.

Set `operation_id` to the owner's unique 64-hex original operation ID.
In an authorized root transport session use the fixed command for each action:

```sh
foo_idle() {
  /usr/bin/python3 -I -S -B /var/lib/uif-stokowski/idle-upgrade/guarded_service_upgrade.py \
    --profile uif-idle --stage uif1313-idle-a22fbb2 \
    --action "$1" --operation-id "$operation_id"
}
foo_idle prepare-apply
```

The enrolled operator signs `apply-confirmation.json`. Its exact keys are
`schema` (`uif.foo-idle-apply-confirmation/1`), `action`
(`issue_linux_apply`), `operation_id`, the returned `preflight_sha256`,
and UTC integer `issued_at` and `expires_at` at most 300 seconds apart.

```sh
ssh-keygen -Y sign -f "$existing_operator_key" \
  -n uif-foo-stokowski-idle-apply apply-confirmation.json
```

Place the JSON and detached signature in the protected packet directory.
Run `foo_idle issue-apply` before the earliest installer, witness or signature
expiry. It revalidates the sealed bundle, review, parked selection, effective
unit and custody under the selector lock. It creates only `linux-apply.json`.
An identical eligible request reads back the same packet. A different or
expired request cannot replace it. Use the fixed `--action apply` command
above for selection, then verify its original completion.

For a separate start decision the trusted host supplies a freshly observed
`start-quiescence.json` in the same protected directory. It has the same
schema and fields as installer quiescence but describes the post-apply
stopped unit and current effects. Run `foo_idle prepare-start` for the exact
current digest. The enrolled operator then creates a new
`activation-confirmation.json` using the fields and signing command above.
For this action set `action` to `authorize_idle_marker_and_start` and
`preflight_sha256` to the `prepare-start` result. A marker-only signature
cannot authorize a start.

With the exact signed JSON and signature in the protected packet directory,
run `foo_idle start-idle` within the earlier of the signature and the
60-second start-witness windows. It commits a create-only start intent and
the expiring, selection-bound marker under the same lock, then releases the
lock so the fixed systemd ExecStart can take it exclusively. The idle
drop-in uses systemd's `!` credential override for that one entry point and
sets `NoNewPrivileges=yes`. It preserves the unit sandbox and the service
account. The prelude's capability bound contains exactly `CAP_SETGID`,
`CAP_SETUID` and `CAP_SETPCAP`. The systemd and kernel checks require that
exact bound and an empty ambient set. Bootstrap must qualify the effective
systemd unit, its cgroup v2
membership, and protected receipt writes under the existing sandbox.

ExecStart checks its parent, system-manager MainPID and invocation ID, and
kernel cgroup before it can consume anything. While holding the exclusive
lock, it revalidates the exact protected intent, original owner signature,
reviewed bundle and selection, and writes a create-only
`<operation_id>.start-consumed.json` under the existing protected receipts
directory. That receipt binds the one invocation and PID. It preserves the
intent and marker, downgrades to the shared runtime lock, drops the
capability bounding set, switches to the configured `user:user` account and
requires all five kernel capability sets to be empty with
`NoNewPrivileges` still enabled before exec. Invalid, expired or reused proof refuses
before exec.

A successful readback requires the live PID and the closed-admission idle
message from the exact consumed invocation. If the start is interrupted,
reconcile its original intent, consumption receipt, marker and unit state.
Consumption cannot be repeated even when no launch or success receipt can
be established.
