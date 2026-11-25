# Stokowski provider readiness

Evidence snapshot: 24 November 2026 UTC. Scope and acceptance: [UIF-1313](https://linear.app/maldous/issue/UIF-1313). A documented client, installed executable, account login, or passing fixture is not live spend or dispatch approval. Keep every unqualified route disabled and preserve the current stop/hold state.

## Requested routes

| Route | Verified or supported | What must pass before any live use |
| --- | --- | --- |
| **Cloudflare Workers AI, OffMenu Free** | First known-route validation. Foo has the corrected parked selector; no live inference is qualified by it. The bound model is `@cf/zai-org/glm-4.7-flash`; previous readback on the named Gateway was Standard, not Unified Billing. [Free allocation](https://developers.cloudflare.com/workers-ai/platform/pricing/) is 10,000 neurons per UTC day; UIF's stricter limit is **projected below 7,500**, not a provider setting. [Adaptive GraphQL](https://developers.cloudflare.com/analytics/graphql-api/sampling/) is not an account-wide hard bound. | Read the approved account, exact gateway/model/no-fallback, cutoff window and receipt completeness without inference. Establish authenticated unsampled account-wide upper bound, or exclusively fenced and reconciled all-producer accounting. Include gaps, unresolved reservations and worst-case physical call. No personal Paid substitution or unapproved activation. |
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

No production adapter or custody lock is provisioned by this source.
The existing OffMenu lock binds its publisher and repository target, and the
UIF quota lock binds its quota records. Neither serializes this unit's stage
selection. When the qualified Foo operator has installed and accepted the
exact adapter, the existing `u/admin/foo_root_jobs` in workspace `foo` can
transport its one scoped invocation. The transport itself supplies neither
admission nor a selector lock. The detached transaction performs no service
start, marker write or provider call.
