# Legacy source-free receipt compatibility

UIF-120 owns the shared receiving repair. OffMenu's retained original source-free
results and independent reviews must not be replayed merely because a later
receiver assumes a newer data contract. Preserve the original archive bytes,
published tuple, stage, verdict, empty selections, scope and review boundary.

## Proven contract difference

The sixteen recovered OffMenu pairs all lack the modern review report's
`reviewed_disposition`. Three originals are `assess` / `approve`; thirteen are
`investigate` / `complete`. Ten have explicit empty artifact lists, six have
nonempty lists. Requiring every old original to be investigate/complete with a
nonempty selection is not compatible with these retained contracts. Do not add
a field to an immutable old report or pretend its author approved a new tuple.

The installed native Git `contribution_review` validates Git source units.
Keep that validator strict. Source-free documentary evidence requires a distinct
bounded route, not a forged Git candidate or full-issue acceptance. A newer
mutable blocked settlement does not erase an older immutable approved review.

## Pure compatibility builder

`uif.legacy_receipts.build_legacy_receipt` accepts exact original/review archive
bytes, their pinned file hashes/record identities and the original published
source-free review binding. The caller must authenticate that published tuple
through its actual native tracker/archive reader. An arbitrary JSON object, this
receipt builder, a policy hash or a local file is not that authentication.

The builder checks exact file bytes, strict JSON and types, issue/scope/run and
source-free binding/routing, the actual legacy stage/verdict pair, distinct
native session identities and explicit artifact metadata. Distinct sessions are
recorded as such, not falsely presented as separate human identities. Git and
modern reviewed-disposition inputs retain their separate validation routes.

It emits a versioned receipt with one of two non-authorizing statuses:

- `bounded-input-join`: at least one precisely typed input link is evidenced;
- `input-join-unresolved`: no such input link was supplied/proved. Preserve the
  existing independent review while investigating the missing original join;
  this does not order another content review or manufacture rejection.

The proof list preserves materially different strengths:

1. A review's exact reference to the original **evidence file SHA256** joins
   the original file bytes already checked against their pin.
2. A review's reference to a native **record directory identity** qualifies only
   if an injected, source-qualified native digest validator computes the exact
   identity for both archive objects. A directory name alone never proves a
   content digest. Caller booleans/strings cannot supply that validator.
3. Explicit selected **documentary input hashes** must occur in both original
   and review documentary fields. They prove only that selected input link,
   not an exact whole-original-report review. Shared policy hashes are excluded.
   The builder returns exact JSON pointers to hash references, not report bodies.

At recovery, six reviews directly pin original evidence file hashes. Other
directory references must retain the constructor-verification distinction until
the actual native digest recipe is qualified. The three assess originals have
no direct original-archive reference: UIF-221 and UIF-229 share selected packet
hashes; UIF-227 has no recovered selected-input hash beyond shared policy.
Do not silently promote those different cases to equivalent proof.

## Effect boundary remains with the native receiver

This module performs no filesystem/network access or mutation, imports no
publication implementation, creates no queue/claim/controller, and installs
nothing. The receipt never fabricates `reviewed_disposition`. Artifact and
documentary payloads are not loaded or claimed verified by parsing their hashes.
All receiving authorization, gate clearance, whole-issue acceptance and Git
publication flags remain false. Current scope/custody and published-tuple
authentication requirements remain explicit even when an input join is proved.

The existing receiving adapter still must authenticate current selection and
owner, reserve through the actual exclusion/fence, preserve the original
operation and record bounded receipt-only custody through its supported route.
This source module alone does not wire that route into installed Stokowski,
qualify off-Foo authority, clear an issue or authorize a stale effect. Do not
substitute it for those remaining integration obligations.

## Focused qualification

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/delivery -p test_legacy_receipts.py -v
```

These are new synthetic compatibility contracts, not repeated content review or
the older controller's seventeen tests. Actual retained-pair qualification must
consume originals in their private Foo custody, verify their pins first and
export only bounded receipt metadata. Keep raw reports, packets and session
bodies out of source/publication artifacts. Record source review, immutable
branch execution, real archive results and native integration separately in
the existing issues; no new administrative or all-project gate is introduced.
