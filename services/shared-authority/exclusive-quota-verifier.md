# Binding-only exclusive quota verifier

`exclusive-quota-verifier.mjs` wraps the [independent proof decoder](exclusive-quota-proof.md).
It is candidate source, not deployment or permission to admit inference. It has
one service-binding request: `POST https://quota.internal/v1/observation/read`.
There is no provider client, authority write, evidence writer, or deploy manifest.

A separately reviewed installation must have **no routes or custom domains,
workers.dev disabled, and previews disabled**. The fixed internal URL is a
protocol check, not authentication for a public Worker. Grant only the
authenticated shared authority its private HTTP binding to this service.

The verifier's only configured dependencies are:

- `EXCLUSIVE_QUOTA_BINDING`, the exact four-field JSON trust envelope for the
  pinned Free route, topology digest and two independent producer public keys.
  Qualify the deployed verifier and store against that binding digest before
  setting the authority's existing `quota_verification_sha256`.
- `QUOTA_EVIDENCE`, a private capability for a separately qualified immutable
  original store. The only read is
  `GET https://quota-evidence.internal/v1/original/<sha256>`, with no forwarded
  caller headers or redirect following. The store returns the canonical
  signed bytes as exactly `application/octet-stream` (up to 16 KiB), or refuses.
  The adapter checks the digest and both signatures; an overwritten or wrong
  object does not become a new original. No producer or storage deployment is
  supplied here. A store remains unqualified until custody, append-only
  behavior, separate writer/read authority and rollback/revocation are proven.

Absent dependencies, absent/oversize bytes, a pin mismatch, tamper, a stale
window, or an unverified proof refuse the observation. Successful responses
are not cached and include only the existing authority result shape. The
authority still enforces the request maximum and its retained liabilities.

## Exact next account-owner action

In the exact OffMenu Free account, the administrator and existing sender
custodian should produce in-place, sanitized custody evidence that the one
admitted sending credential has only the reviewed private receiver as a
consumer, and close or hard-bound account-owned, user-token, global key,
dashboard and other AI paths through a full UTC opening. Verify every active
binding or platform, the fixed standard-billing Gateway, and any source
condition; inventory snapshots or last-use fields alone are not enforcement.
Start an independently retained ledger at a verified 00:00 UTC (11:00 AEDT)
opening, preserving unknown prior liabilities. If that proof can't cover this
opening, do not backfill an assumed zero. Use the next proven opening or
qualify an independently complete account meter and gap ceiling.
