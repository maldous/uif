# Allowance freshness qualification

Reviewed 2026-11-09. This is source/protocol evidence, not a live production
installation, provider-outage drill, credential inspection, or build attestation.

## Version and immutable source

The installed managed package manifest and CLI identify version **0.167.0**.
The collector separately pins the installed executable SHA-256, Linux Unix-socket
owner/mode, actual peer executable/PID/start identity, and expected home. Its
runtime does not follow a moving release tag or accept an updated binary silently.

The verified official release is
[0.167.0](https://github.com/openai/codex/releases/tag/rust-v0.167.0), published
2026-11-06. Its annotated tag object is
`cb41599ccbe371272892f156286a236aa097bada`; the tag resolves to commit
`086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`.

## Per-request backend read and error propagation

The reviewed release path contains no local successful-quota cache fallback:

1. [App-server handler, lines 1171–1275](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/request_processors/account_processor.rs#L1171-L1275)
   creates a backend client from active auth for this call, awaits
   `get_rate_limits_with_reset_credits()` at line 1202, and propagates a failed
   usage read through `map_err(...)?` at line 1213. An empty response is also an
   error. The returned `ordinary_usage_allowed` is exposed only after the current
   account and user match the backend response (lines 1251–1266).
   Reviewed file blob: `80f88efb7f433f7759abdf7cd312b2644faa2ef9`.
2. [Backend usage request, lines 23–79](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/backend-client/src/client/rate_limit_resets.rs#L23-L79)
   calls `get_rate_limit_status(...).await?` for each read, creates a GET request
   and calls `exec_request(...).await?`. Ordinary eligibility comes from that
   response's `rate_limit.allowed`, not a locally remembered permission.
   Reviewed file blob: `03e7a3048110e444ab9fa14cfe9299491c105ff8`.
3. [HTTP execution, lines 319–337](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/backend-client/src/client.rs#L319-L337)
   performs `req.send().await?`; transport/read errors propagate and any
   non-success HTTP response fails. It does not return an earlier usage result
   after an offline or authorization failure.
   Reviewed file blob: `c20b2694e26de5b6960ef34c5443786b0a443ca3`.

The passive collector does not set `supportsLunaReserve`, opt into an experiment,
request reset-credit detail lookup, or issue auth refresh/login/reset calls.
It correlates the returned account ID to `account/read` workspace routing and
checks that account metadata and process identity remain unchanged across the
read. Only successful, qualified metadata gets a new completed-read timestamp.
RPC errors, unknown fields, mismatches, missing permission or timeout yield
unavailable; they never renew a prior allowance proof as a new valid result.

## Verification limits

Generated schema from the installed version and bounded existing-daemon metadata
reads were qualified. Synthetic tests cover RPC failure, account switch, framing,
timeout, field drift, eligibility ambiguity and omission of unsafe public fields.
No live daemon was made offline, logged out, restarted, intercepted, or reconfigured
to test failure behavior. No source-to-binary reproducible build or release
signature verification was performed. The release signature artifact retrieval
was unavailable through the read connector. Therefore the source conclusion is
version-qualified, with the existing installed vendor binary pinned; it is not a
cryptographic build-provenance claim. Any version/hash change needs a new review.
