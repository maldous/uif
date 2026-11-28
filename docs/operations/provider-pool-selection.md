# Provider pool selection (UIF-1313)

The source-only selector does not run another scheduler or claim ledger. It is
an advisory step for the authenticated owner adapter: use the current accepted
scope to choose one explicit route, and ask the existing authority for a real
reservation before any provider, worktree, Git or Linear effect.

## Keep the model roster current

Use Stokowski's existing provider discovery, pool and runtime hooks. Catalogue
refresh discovers candidate combinations of provider, account, client protocol
and model. Record the stage capabilities each combination has demonstrated,
such as structured reports, tool calls, streaming and cancellation. A model can
appear in several combinations with different capabilities and limits. Probe
the full set of configured combinations over time, deduplicating metadata and
quota reads shared by the same account. Prioritize stale observations and use
provider reset times or `Retry-After` with bounded backoff. Keep healthy routes
available while another cools down. [Legacy ANQR availability and fallback evidence](stokowski-provider-readiness.md#reusable-offline-protocol-fixtures-from-anqr)

At dispatch, filter by the action's capabilities, current project permissions,
fresh availability and headroom before selecting a route. Credential lifetime
and model headroom are independent. If the roster has no eligible route, wait
for the next scheduled refresh and continue work that needs no inference. Each
refresh can make a recovered route eligible again. Qualification uses the
existing adapter and reservation path. Reconcile an uncertain send under its
original route before another model can handle the work.

## Inputs and decisions

- Build a `PoolRequest` from the *verified* current Linear workspace, project,
  issue UUID and repository; hash the exact source and the owner-approved
  ordinary software scope; bind the original operation and author/review role.
  A description or an agent-supplied classification is not an approval.
- Give only the routes explicitly permitted for **this** request in policy
  order. The registry rows name the exact provider, client build, model,
  non-secret account fingerprint, credential epoch, project route and role.
  The trusted adapter must provide its canonical full route fingerprint
  (`adapter_route_sha256`), including exact upstream or gateway and policy
  details absent from the generic display fields. A changed fingerprint
  cannot reuse old readiness evidence; the selector verifies the binding shape,
  and the owner adapter and receiver verify its provenance.

| Route family | Scope of this source | Production dispatch |
| --- | --- | --- |
| Included Codex | Advisory candidate; account and window proof stays with its adapter | None |
| Cloudflare Workers AI | Advisory candidate; account and reservation proof stays with its adapter | None |
| Groq / OpenCode | Advisory candidate; exact client, tool and quota proof stays with its adapter | None |
| OpenRouter / OpenCode | Advisory candidate with exact non-auto `vendor/model:free`; upstream, price and fallback proof stays with its adapter | None |
| Included OpenAI Responses | Separate opt-in advisory candidate; requires the exact typed included-grant adapter and no-overage proof | None |
| Metered or prepaid OpenAI API, Antigravity, all other families | Not allowlisted here | None |

The selector configures or enables none of these routes. The table describes
this source, not the state of a separate installed legacy runtime.
- Normalize only already authenticated evidence from the applicable
  provider-specific adapter into `RouteAvailability`. Merely resolving an
  executable, key reference, model name or plan label does not establish
  availability. The independently admitted adapter must check the real
  included quota/rate windows, request upper bound, pending liabilities, reset
  and exclusions; its evidence must bind the entire request and exact route.
  A receipt-shaped hash or a true boolean is not authentication.
- An explicit stop or unknown stop state prevents all selection. A missing,
  ambiguous, stale, throttled or out-of-scope route is held independently;
  other explicitly admitted routes for independent work remain eligible.
  A held original effect blocks any replacement of that operation. A
  continuation or retry can only retain its original full route binding.
  An independent reviewer needs a different verified account fingerprint.
- `can_reserve` is advisory headroom for this bounded request, never a lease,
  reusable credit, a sum of route capacities, or permission to dispatch.
  Multiple admitted models on the same account cannot each get a copy of its
  included capacity. The authority must account for the real shared account.

### Included OpenAI Responses (not Codex)

The owner must explicitly list a registered `openai-responses` / `responses`
route and set `IncludedResponsesRequest.opt_in` to true for the exact payload
hash, deadline and maximum request cost in microdollars. Neither a default
model nor a Codex subscription qualifies this separate route.
The trusted adapter must provide a matching receipt, the
`responses_request_binding(request, route)` for this exact call/scope/route,
and an `IncludedResponsesGrant` with the companion policy's actual
`ApiRoute` and `ApiCreditEvidence` plus the upper bound for unsettled usage.
The provider route is specifically `responses.create`. Its fingerprint is
`digest(asdict(ApiRoute))`; the account fingerprint is
`digest({"organization": ApiRoute.organization})`, so two projects in one
organization cannot count as different reviewer accounts. The adapter binds
the exact project separately in the route fingerprint.
The selector delegates credit lifetime, projected balance and intent
thresholds to the separate adapter. An absent adapter, stale or mismatched
proof, an alert-only budget, paid/prepaid funding or unproved hard
no-overage never qualifies. The owner must still authenticate the proof and
reserve this exact bounded call at the real atomic authority before egress.

## Owner integration boundary (not installed)

At the existing `AsyncNativeQuotaBoundary.dispatch_stage` adapter seam,
`pool_admit(profile, operation_id)` remains the only place that can return a
real `PoolPermit` (or a verified `PoolRefusal` without a reservation/child).
`PoolSelection` cannot substitute for either. Deterministic gates do not use
provider selection. Do not turn one provider's readiness into the legacy
singleton Registry's provider observation: it cannot express independently
held routes, exact account bindings or multiple windows.

### Typed shared-authority wiring proposal

At the ordinary Stokowski dispatch admission boundary, derive `PoolRequest`
from the authenticated current `Lease`, its `ClaimFence.scope` and
`SourcePin`, the admitted task scope, and the original call. Compare the
canonical repository ID from `Scope` in the authority; the selector's
repository name is only a routing label. A selected route is not a claim.

Bind the selected route/configuration fingerprint, exact account and
credential epoch, bounded provider-specific quota quote, source, operation
role and request body hash to `Operation(fence=lease.fence,
original_id=request.operation_id, payload_sha256=canonical_intent_sha256)`.
An owner's composed backend must compare the live claim and admission and
reserve that account's worst case **in the same transaction** that registers
the original intent. Keep the existing `PendingEffect` / `TerminalEffect`
binding: an original terminal result is read back, not sent again; a
genuine new pending result also needs its matching reservation receipt.

The proposed `RegistrySharedStore.begin` registers a pending original
effect, but is not an atomic provider-budget reservation. Calling it and
reserving a separate local ledger afterwards does not satisfy this requirement.
The future owner-supplied backend must couple the two. Its receiver may use the
existing `consume_once(identity, pending, accept=..., observe_receipt=...)`
fence only when it verifies the exact reservation, route, provider and request
at bounded synchronous sink acceptance under the authority lock. An
independently queued send does not inherit that fence. Observe the original
receipt separately. If receipt, quota, policy or route
identity is stale, refuse and retain the original operation for reconciliation.
Never dispatch a replacement route for a pending original.

Do not call the shared store's `begin` or receiver from inside the same
legacy `Registry.perform_effect(_async)` per-issue fence: that boundary is
non-reentrant. The owner must compose one authority-facing effect path,
not stack two intent journals behind `pool_admit`. Existing launchers stay
unchanged in this patch. Authenticated remote authority/receiver, provider
senders, atomic account reservation, hosting and installation are still
unqualified.

The deterministic tests include independently admitted tasks on two routes
while one is throttled, fresh capacity readback, source/scope changes and
binding drift, no paid/implicit fallback, and both legacy dispatch boundaries
rejecting an advisory decision where a real reservation is required. These
are synthetic controls, not live provider, fairness, quota or BAU proof.
