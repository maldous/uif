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

Start the authorized normal lifecycle before checking whether any route can
send. When quota or availability changes for an installed sender, fresh
verified evidence should reopen admission in the running service. Keep
metadata for a provider without a sender in the roster and commission the
sender through its existing owner path.

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

| Route family | Readiness inputs | Native sender in current source |
| --- | --- | --- |
| Included Codex | Same account, client session, window, and included allowance | Requires a mounted fenced sender and quota reader |
| Cloudflare Workers AI | Account-wide usage bound, exact Free route, and current reservations | Existing native authority receiver for a configured route |
| Groq / OpenCode | Exact upstream client, tool, and quota proof | Requires a mounted fenced sender and quota reader |
| OpenRouter / OpenCode | Exact non-auto `vendor/model:free`, upstream, price, and fallback proof | Requires a mounted fenced sender and quota reader |
| Included OpenAI Responses | Per-request opt-in, typed included grant, and no-overage proof | Requires a mounted fenced sender and quota reader |
| Antigravity and other families | Provider-specific account, client, capability, and funding qualification | Requires a qualified adapter |

Read the installed authority and current sender bindings to assess live
availability. Keep metered and prepaid routes outside the included-only pool.
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
are detached controls. The exact native launch fixture also verifies that the
same running discovery lifecycle closes admission without quota evidence and
reopens it after the configured authority restores authenticated quota. It
observes one provider fixture send and one verified Linear fixture report.
Use the pinned native stage for this check. [Detached native launch tests](../../tests/delivery/test_detached_normal_launch_worker.py)
