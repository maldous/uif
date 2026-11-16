# Explicit native recovery-source composition

This UIF120/247 source unit composes the already accepted
[seven-label correction](informational-owner-labels.md) and
[custody observation](native-custody-observation.md). UIF retains sole
integration, installation and runtime adoption. No native source or old process
is changed by preparing or publishing this repository tool.

## Selection and standalone identities

The base is accepted UIF commit
6197de8540d295cf30eaf3cbc2344be286c84c8d, tree
4ef9f1e074aebb49a3b720052081d8b832683635.

Both unchanged strict preparers receive the SAME original pair, never each
other's transformed bytes:

- orchestrator.py SHA256
  7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea.
- web.py SHA256
  c0e18143f3e16cf2c7584b030bf53fd7a60a5802456383e92f0cc2c2e5786c6a.

The composer additionally requires the independently qualified standalone
outputs, not a caller-selected digest:

- Label-only orchestrator:
  b1c9fbd852cef7f9c999848a0c782e8fdc8fc5b0a28bf4e213d4b43e3af6f41a.
- Custody-only orchestrator:
  45175b39df3d2b644df30771f0f56e44b4f27908342109d16a130027adcad7ea.
- Custody-only web:
  0e4d50d23db903541c59f5a2937f0f9fb74b5b420af357227102d75f30aca2c3.

These are distinct standalone pins, not the composed orchestrator digest.
The accepted repository primitives are loaded from source at fixed sibling paths,
without emitting bytecode caches; installed native modules are never imported or
executed. Updating a primitive/output pin
requires a new exact reviewed source unit, not a CLI override or runtime patch.

## Exact union and proofs

The core function takes bytes and returns two prepared byte strings plus
metadata, with no file, network, service, record or claim operation. It calls both
strict original-input preparers unchanged. It then adds exactly the one
seven-label expression replacement to the separately prepared custody bytes.

The union includes only that replacement, the two custody methods and the web
GET adapter. Every selected span must occur exactly once. It proves:

1. Removing the label replacement gives the exact custody-only orchestrator.
2. Removing both custody methods gives the exact label-only orchestrator.
3. Removing all additions in either checked order gives the original orchestrator.
4. Removing the web GET gives the original web bytes; label-only web is unchanged.
5. Prepared source stays bounded and parses as Python syntax.

Unexpected inputs, transformed inputs, standalone output drift, ambiguous/missing
spans, inverse mismatch, invalid syntax or oversized output refuse. The original
1 MiB input/type/hash/AST bounds remain in the standalone preparers; this tool
does not relax them. Each composed file is at most 1 MiB as well.

## Bounded CLI

    python3 tools/delivery/native_recovery_composition.py \
      --orchestrator-source "$PINNED_ORCHESTRATOR" --web-source "$PINNED_WEB"

Default stdout is hash/proof metadata only. Optional --patch emits a generic
source unified diff to stdout instead. No output file is created and there is no
install, restart, endpoint, record selector or authority parameter. Source reads
reuse the accepted regular-file, final-component no-follow/nonblocking bound;
this is not an adversarial parent-directory containment guarantee.

Metadata reports original, each standalone and composed hashes separately.
Source preparation/proof success is not current custody observation, safe
old-process adoption, native installation or runtime execution.

## Genuine unresolved adoption boundary

This composition does NOT preserve live retry49 objects or native claim identities,
export/restore a process, fence an effect, recover an issuer, settle original
209/215/250, replay denied206/208/77 exports, or qualify continuous BAU.
All installation/adoption/endpoint/authority/compatibility flags remain false;
external owner, issuer, lease, epoch, fence and original settlement remain unknown.

Seven informational labels are excluded, but ordinary coordination comments still
affect management context. Controller/workflow hashes in input-hold bindings still
change when native source changes. Exact byte inverses do not restore old gate,
review or input-hold compatibility. Runtime preservation and consuming adoption
need their real supported solution under UIF custody, not another source-review
claim. The small new composition contracts are not a replay of accepted7/17 tests,
a native import, an HTTP server or an inspection of private original bodies.
