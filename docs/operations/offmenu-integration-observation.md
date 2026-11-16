# Existing OffMenu repository journal observation

This is a **source-only, non-authorizing** metadata adapter for UIF127. Its CLI
has no root, transaction, configuration, command or effect selector. It is not
installed, and source acceptance does not authorize real-store invocation.

The existing contract was read at OffMenu `51ad2b27a226f097dcaa9a27db5fade33d9ef4dd`,
`src/offmenu/delivery/integration.py` SHA256
`7f30e531e6a992d30c6a1a4bf35213c7ecedf5d620e579e1f99875d0cf428178`.
It has repository-owner locking, active/legacy pointers, immutable canonical
binding fingerprints and a selected transaction state. This adapter imports no
OffMenu/native Controller and reuses no initialization or mutating status path.
The existing protected publisher observer remains fixed to its separate
`/var/lib/offmenu-publisher/integration` root; it is not retargeted here.

## Selected existing records only

The sole production root is `/home/user/src/offmenu/.delivery/integration`.
Every directory component is opened read-only with no-follow directory flags.
An **existing regular single-link** `repository-owner.lock` is opened read-only
and held shared/nonblocking. Missing or exclusively held locks refuse. No file,
directory, claim, lock or pointer is created, rotated, repaired or removed.

`active-owner.json` is selected if it exists; only its absence permits fallback
to `repository-claim.json`. A malformed or symlink active pointer never falls
back. Its keys must be exactly `binding_sha256`, `directory`, `owner`. The selected
absolute directory must be strictly within the fixed root, lexically canonical,
with no traversal/symlink components. Only its existing `binding.json` and
`state.json` are read; no directory scans or unrelated transactions are accessed.

Binding fingerprints use the actual existing recipe:
`sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode())`.
The entire bounded binding is hashed, including unprojected metadata; recorded
owner, `maldous/offmenu` repository and pointer hash must match. Known source
version and 40-hex head/base/tree shape are checked, **not** remotely authenticated.
State must have its matching binding hash and a bounded status. Missing binding
or state fields refuse rather than fabricate `prepared`, free or available.
This deliberately conservative observation cannot reinterpret a partial legacy
record as a complete selected state or validate a full integration contract.

## Bounds, output and limits

Final files use no-follow/nonblocking opens, then regular-file/single-link
checks, bounded reads and descriptor identity/size/time rechecks. FIFOs refuse
without waiting. JSON must be a UTF-8 object with no duplicate keys or nonfinite
numbers. Pointer limit is 8 KiB; each binding/state limit is 256 KiB, with 8,192
value nodes and depth 32. Selected directory identity is at most eight components
and 512 relative characters; recorded owner is a bounded 128-character token.
The pointer is reread under the same lock. This is a **cooperative same-host
file-lock** observation, not an atomic distributed snapshot or protection against
an adversarial filesystem or noncooperating writer.

At most 8 KiB of JSON is emitted: completion observation time, observed/refused
outcome, known record-presence facts, raw pointer/binding/state file fingerprints,
canonical binding fingerprint, bounded recorded role and relative directory,
identity-consistency booleans and selected status/merge/publication/release/tag
effect scalars when present. Full binding/state/adoption/intent/source/session
bodies and exception text are never output. A recorded `verified` scalar is
reported as documentary data, **not** independently verified external effect.
All authority/effect flags remain false. External owner, current issuer, lease,
epoch, fence, authentication and settlement remain unknown, even on success.

This neither grants a claim, a merge/publication, nor observes original Core109/
209 custody. It does not preserve/recover native retry49, authorize restart,
resolve ordinary-comment hashes or restore historical review authority. UIF owns
subsequent independent review, qualification, denied-scope reconciliation and any
explicitly authorized actual-data observation or adoption.

The retained UIF242 denial concerns a localhost HTTP GET to
`127.0.0.1:4213/api/v1/state` returning `URLError [Errno 1] Operation not permitted`.
That is not a documented filesystem read of this repository root, **nor** blanket
permission to read it. This assignment performs no real-store probe. The private
`_observe_root` fixture seam is only for synthetic in-process tests, not a new
runtime path/configuration option. Do not invoke the CLI on real data until its
owner separately qualifies and authorizes that exact read.

Only the new synthetic contracts in
[`test_observe_offmenu_integration.py`](../../tests/delivery/test_observe_offmenu_integration.py)
are affected. They use disposable fixtures, including malformed/partial records,
symlink/traversal/FIFO and lock contention, and assert no writes or private-body
output. They do not establish real-store access, publisher authority, integration
settlement or production readiness. No accepted test suite needs replay here.
