# Linear scope projection and the quota JSON entrypoints

This bounded UIF-249/UIF-260 source account covers two adapter decision flows,
not two deployed services or persistent state machines. It changes no source,
authority, request schema, native gate or runtime. All inputs are from the single
`maldous/uif` edition `43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. Source-test assertions below were read,
not executed. This is neither a whole-repository coverage claim nor a fresh
qualification receipt.

## Source boundary

The three entrypoint/projection files and the Linear-scope test file were read
in full. Store/registry references and the quota test excerpts are supporting
boundary reads, not a new complete semantic review of those modules.

| Path | Git blob | Scope in this account |
| --- | --- | --- |
| `src/uif/linear_scope.py` | `5bb30283117c89ac825d1eb23abccdcbfac562fe` | Full, 65 lines |
| `src/uif/quota_cli.py` | `cc79cee62d22c8b9eb5da0bab541151921d29a3c` | Full, 40 lines |
| `tools/delivery/quota_handoff.py` | `9f139c56beed943354b8402ac57f29ba15c568e9` | Full, 10 lines |
| `tests/agents/test_linear_scope.py` | `f558319402e45c03f99da6d86b1cb0ebf66076ea` | Full, 106 lines; ten test methods |
| `src/uif/handoff_store.py` | `7a850cb4b25a205960208c62f3ed719b1a22fbb9` | Request decoder and authority-path custody boundary |
| `src/uif/quota_handoff.py` | `b7a6f14858e9d8d0ab7f09e60717bda9b1151d7c` | Constructor, authentication and `invoke` boundary |
| `tests/delivery/test_quota_handoff.py` | `87bcf41d9d7c9d0cbb02ea55984de5f13ee6fece` | CLI identity/missing-authority tests at lines 255–274 |

## `UIF-SCOPE-PROJECTION`: presentation to an expected native digest

Source: [linear_scope.py](../../../src/uif/linear_scope.py), especially lines
17–65. Owner boundary: a caller supplies both the issue mapping and an
**independently authenticated** expected native SHA-256. This module authenticates
neither the expectation nor the issue's owner or permissions.

### States, events and guards

| Decision state / event | Guard and next result | Side effect |
| --- | --- | --- |
| Receive expected digest | Exactly 64 lowercase hexadecimal characters; otherwise `ScopeNormalizationError` | None |
| Hash presented mapping | Mapping contains `id`, `identifier`, `title`, `description`; first three values are strings; description is string or null | None |
| Direct digest matches | Return the original mapping object, without projection | None |
| Direct mismatch | A null description refuses; a string proceeds to the fixed renderer substitution | None |
| Project description | At least one exact supported renderer token must match | Allocate a shallow `dict` copy and new description only |
| Projected digest matches | Return the projected mapping | No input mutation |
| No token, invalid shape/Unicode, or final mismatch | Raise the typed error; no alternate historical scope is guessed | None |

`native_scope_sha256` hashes the UTF-8 encoding of
`json.dumps([id, identifier, title, description], ensure_ascii=False)` in that
fixed field order, with Python's **default separators**, not compact JSON. Null
and empty descriptions are distinct. CR/LF, tabs, Unicode and trailing whitespace
are significant. Extra mapping fields are not hashed or authenticated; their
presence is not an error. These are shape checks, not UUID or issue-ID validators.

The one renderer grammar has fixed attribute order, double quotes and spacing:
`<issue id="lowercase-hex UUID-shaped value" label="TEAM-1" href="https://linear.app/.../issue/TEAM-1..." />`.
The label starts uppercase, its issue number is positive, and the URL must repeat
that same label. The matched UUID-shaped attribute is dropped in projection; it
is not independently resolved or checked against the linked issue. The URL,
including a supported slug/query/fragment, is retained verbatim in
`[LABEL](URL)`. This is not general HTML canonicalization, URL authorization,
entity unescaping or Markdown parsing. It does not recognize code fences or
literal-example context. If the original digest already matches, even a literal
renderer example is left alone; on the mismatch path every matching token is
subject to the same substitution.

The input mapping is returned by identity on a direct match. On a projected
match, nested/extra values retain shallow aliases; the result is not a frozen
snapshot or a sanitized issue object. No input-size cap, durable state,
transaction, lock, remote lookup, retry receipt or independent observer is
implemented here. Repeated calls compare their supplied inputs anew. The caller
owns freshness, authenticated scope identity and effect admission.

### Source-test oracles and illegal conclusions

The ten test methods cover exact field order/Unicode/default separators; two
renderer links and other-byte preservation; direct-match literal examples;
non-issue HTML refusal; changed requirements/title/identifier/id; unsupported
renderer syntax; no unescaping of other markup; null versus empty; malformed
expected digests; and invalid mapping/value shapes. The copy test also asserts
that the original is unchanged and its `extra` value is still shared by identity.
These are source assertions, not measured results from this documentation pass.

A real issue title/requirement correction is not a presentation-only change.
It cannot be made equal to an obsolete digest by this projector. Reconcile the
new scope with its owning native operation; do not treat projection as permission
to preserve old admission or reconstruct historical requirements.

## `UIF-QUOTA-CLI`: OS identity to an existing authority invocation

Sources: [quota_cli.py](../../../src/uif/quota_cli.py),
[checkout entrypoint](../../../tools/delivery/quota_handoff.py), and
[registry invoke boundary](../../../src/uif/quota_handoff.py). The packaged
`uif-quota-handoff` entrypoint is declared in `pyproject.toml` as
`uif.quota_cli:main`. The source-checkout wrapper prepends the checkout's `src`
directory to `sys.path` and calls that same function. This wrapper does not verify
Git, source hashes or the installation; the selected execution profile must bind
those independently.

| Stage | Result / guard | Persistence and authority boundary |
| --- | --- | --- |
| Parse `--registry` and `--actor` | Both required; normal `argparse` help/usage behavior applies | Parsing occurs outside the CLI's `try` block |
| Read stdin | Read at most `MAX_REQUEST_BYTES + 1`, with limit 32 KiB | Decoder rejects an oversized input, duplicate keys, nonfinite JSON constants, non-object JSON and malformed input |
| Select authority | Construct `Registry(args.registry)` | Existing private authority custody is required; this is not `Registry.initialize` |
| Invoke | Principal is `uid:` plus the actual `os.geteuid()`; actor is the requested registered identity | JSON cannot replace the authenticated principal or supply a verifier |
| `Hold` within the guarded call | Print JSON `status=held`, `reason=str(error)`; return 2 | A refusal is not proof that a previously uncertain operation had no effect |
| `OSError` within the guarded call | Print JSON `status=held`, `reason=authority_unavailable`; return 3 | Does not create a replacement authority or select another host |
| Invocation returns normally | Serialize its result with sorted keys and `allow_nan=False`; return 0 | Exit 0 means adapter invocation succeeded, not that every result is a product-delivery pass |

The decoder uses `json.loads` on bytes; do not claim this CLI enforces a separate
UTF-8-only input protocol. Raw-input length and Registry's reserialized request
size are separate checks. There is no CLI output-size/redaction layer. Registry
`status` may expose its returned task/provider/coordinator metadata to a properly
registered actor; routing that output elsewhere still needs the applicable
privacy and recipient authority.

`Registry` checks the authority's directory/file custody and registration before
action. The supplied actor is not trusted merely because it appears in an argv
flag. Exact `status` and `task_status` requests use authenticated read-only
transactions; other requests enter Registry's own request-id, coordinator, task
fence, proof and commit protocol. Those are not implemented again by the CLI.
The CLI installs none of the optional reconstruction, operation, provider,
handoff, completion or coordinator verifiers. A caller cannot manufacture their
trusted callbacks from request JSON. This conservative adapter therefore does
not qualify the corresponding claim/effect/recovery path by exposing its command.

There is no general catch-all around execution or output. Argument errors are
not structured `held` results; other exception types may propagate. Success JSON
serialization/output happens **after** the guarded invocation, so an output
failure must not be interpreted as proof that a committing request never ran.
The CLI adds no journal or automatic retry. Preserve the original request/effect
identity and reconcile through the owning authority before repeating uncertainty.
No network, subprocess, installation, service start, alternate-host failover or
registry bootstrap is implemented by these entrypoint files themselves.

### Source-test oracles and residuals

`test_cli_identity_cannot_be_overridden_by_hostile_json` supplies forged principal
and authentication fields, expects exit 2 / `unauthenticated_actor`, and confirms
Morgan remains unset; it also asserts duplicate JSON keys refuse.
`test_missing_authority_is_not_replaced_or_created` expects exit 3 for a missing
registry and confirms the path was not created. Both tests are synthetic source
oracles, not live authority or host-transport qualification.

UIF-249/UIF-260 still need their other source areas, caller coverage and
independent completeness review. UIF-140/141/159 retain their actual source,
preflight, native custody and delivery obligations; this adapter account clears
none of them. Private installed adapters, transport authentication, live durable
claims, off-Foo authority and unattended lifecycle behavior are not established
by these Git sources or by this document. There is no sibling-repository input or
cross-project qualification dependency in this account.
