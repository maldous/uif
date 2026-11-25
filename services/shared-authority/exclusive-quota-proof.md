# Detached exclusive-account quota proof

This module checks a byte-addressed original and produces the existing quota
verifier response shape. It does not create a Worker, read or write provider
usage, supply credentials, or enable inference. It is not installed in
`worker.mjs`. Do not bind it to `QUOTA_VERIFIER` with test keys, mutable caller
input, an Adaptive Analytics result, or an operational assertion in place of
enforced closure.

The future private receiver must independently retain and retrieve the canonical
UTF-8 envelope by its SHA-256. The submitter supplies only that digest and the
exact pinned route. The deployment pins the route (account, model, Gateway and
credential epoch), the qualified topology digest, and two different Ed25519
public keys. The digest of those canonical fields is its binding identity. A
separate qualification must tie that identity to the deployed code, private
store and evidence producers. Changing or invalidating the topology, proof
issuer or credential epoch must close admission before an unmetered route may
operate. This module does not monitor such changes.

The two signers have independent custody and responsibilities:

- The account/egress-control custodian signs the opening UTC day and exact
  route, separately retained inventory and enforcement artifacts, carryover
  inventory, and conservative millineuron ceilings for outside-ledger usage and
  prior-day unresolved operations not already owned by the authority. The gap
  must cover any outside-ledger consumption from UTC midnight through proof
  expiry that the ledger did not count, including unrelated sends after the
  watermark. A claim of zero requires exclusive enforcement. Standard/free
  operation and no paid fallback must be proven in place.
- The independent durable-ledger producer signs the exact closure digest plus
  the retained ledger-chain digest, a contiguous watermark and expiry, an
  account upper bound and any outside-authority unresolved current liabilities
  not already covered. It must verify opening state and record reservations
  before sending, reconcile unknown effects without replay, and refuse a
  broken chain, incomplete inventory or unsupported provider route. It must
  never merely sign numbers submitted by the quota caller.

Each signature covers the canonical JSON of its own named object. The exact
canonical envelope itself is hashed and immutable. The consumer adds outside
carryover and unresolved current liability conservatively to the reported
ledger upper. The authority separately keeps its own retained liabilities
and adds the next request maximum; the projected total must stay strictly
below 7,500,000 millineurons. Same-day and freshness checks do not substitute
for continuous enforcement.

**Still required upstream:** the exact account custodian must prove the sole
admitted sending credential and close or hard-bound every other capability
(Workers/Pages/platform bindings, direct REST, gateways, user/API/global
credentials, administrator/dashboard/manual use, and other automation). Then
a separately qualified durable ledger must retain a complete window from a
verified UTC midnight, with prior-day unknowns carried forward. If that proof
does not span the current opening, wait for the next proven opening or qualify
complete account metering with a separately documented gap cap. Empty analytics
and credential last-use metadata cannot fill either gap. The signatures protect
artifact integrity and issuer separation; they do not themselves establish
those upstream facts.
