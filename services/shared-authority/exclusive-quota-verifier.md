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
- `EXCLUSIVE_QUOTA_OPENING_SHA256`, the independently qualified digest of
  the canonical opening record. The opening binds the same account/route
  trust digest and the exact outside carryover upper bound. An absent pin
  holds before requesting evidence.
- `QUOTA_EVIDENCE`, a private capability for the immutable original store
  and its current checkpoint. The verifier reads both
  `GET https://quota-evidence.internal/v1/original/<sha256>` and
  `GET https://quota-evidence.internal/v1/checkpoint/current/<account_id>`.
  The current lookup is keyed by the configured account, independently of
  the caller's digest. Neither request forwards caller headers or follows
  redirects. Both return canonical `application/octet-stream` bytes
  (up to 16 KiB each), or refuse.
  The adapter checks the digest and both signatures; an overwritten or wrong
  object does not become a new original. No producer or storage deployment is
  supplied here. A store remains unqualified until custody, append-only
  behavior, separate writer/read authority and rollback/revocation are proven.

The store must return its durable current head, with the ledger signer's
signature, the exact original and ledger-chain digests, and the opening
bytes. The verifier checks the opening bytes against the configured pin and
the signed original, verifies the checkpoint signature, and checks
contiguous half-open windows from UTC midnight to the signed ledger
watermark. The sum of their usage and the unresolved bound must match the
signed original. Serving an old original alongside a different current
checkpoint refuses. The store still needs external qualification for
durable advancement, rollback protection, and access control.

Absent dependencies, absent/oversize bytes, a pin mismatch, tamper, a stale
window, or an unverified proof refuse the observation. Successful internal
responses carry private checkpoint pins and liabilities. One
three-second deadline covers the full lookup and verification, not just response
headers. A timed-out operation returns a refusal immediately but continues to
occupy one of four in-flight slots per isolate until the original call and any
cancellation settle. Saturation returns a refusal without adding a store call;
a permanently stuck upstream can intentionally leave the isolate closed. The
authority commits the checkpoint evidence atomically with the existing
usage receipt. It continues to use the original reservation ledger and
count unknown effects and carryovers. An uncheckpointed legacy usage
projection cannot authorize a send. Exact prior request receipts remain
readable without fetching a new checkpoint or granting new admission.
Opening amounts cannot change and unresolved external liabilities cannot
decrease through a usage refresh. A verified reconciliation operation for
releasing those liabilities would need separate qualification.

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
