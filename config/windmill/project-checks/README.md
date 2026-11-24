# Private shared-checks source and toolchain custody

This directory belongs in the existing **private** `maldous/uif` repository.
It preserves the exact reviewed shared Foo helper and a separately proposed
fixed-path migration. It is not a service installer, provider export, credential
store, runtime readiness claim or permission to run an operation. No `.usf`
directory, sibling product checkout or transient chat workspace is required to
read and reconstruct these repository-managed source bytes.

## Source editions

- `source/project_checks.py` is the byte-exact approved existing source,
  SHA-256 `3568e9a7e3ccf23f33b64b1bde37fc70959b1352a3e350c79b063d34324da0d4`.
  It is preserved, not imported by the custody tests or deployed by a clone.
- `custody.json` records the original source/review/deployment identities and
  explicit restore gaps. Raw logs, original receipts, credentials and operation
  data remain in their existing authorized custody, not in this source bundle.
- `toolchains.json` binds the exact vendor Go archive and a fixed managed
  environment resource. Its official public vendor locator and hash were
  corroborated against [Go release metadata](https://go.dev/dl/?mode=json).
  No archive bytes were acquired for this source change.
- `migration/project_checks.py` is an **undeployed** desired successor.
  `migration.json` describes the only two inverse changes: the fixed Go archive
  pathname inside the engine and its corresponding catalogue base-engine digest.
  All other catalogue/source bytes and controls are preserved. Original
  operations and the old source-bound join are never rebound or rerun.

The preserved legacy `go_cq162_candidate` target also has a known external
heartbeat candidate file, recorded by path and digest in `custody.json`.
Its active use remains unknown. Those bytes are **not reconstructed or copied**
by this bundle, and preservation of the target does not clear that file for
cleanup or qualify a fresh reconstruction. Do not recover a denied payload or
retire a target as a side effect of this custody change.

The old singleton CQ Source/SDK join remains historical and ineligible when the
current source no longer equals its exact binding. Preserving its code does not
clear current admission, promote a failed coverage gate or authorize another
artifact acquisition.

## Reproducible archive custody, not installation

The future operator-invoked `tools/windmill/stage_toolchain.py` accepts only
`--archive PATH`, an already authorized existing copy of the pinned public
archive. The destination is fixed in source to
`/opt/morgan-toolchains/archives/go1.27.4.linux-amd64.tar.gz`.

The utility requires separately provisioned, root-owned, non-writable fixed
ancestors. It opens every directory without following symlinks. It refuses
aliases, changed descriptors, writable or wrong archive bytes, oversized input,
unprotected destinations and existing different bytes. It does not create or
repair directory permissions. Only its exclusively created unpredictable
temporary name may be removed. Final publication is an exclusive link with
file/directory synchronization. The source archive and every existing file stay
unchanged. A verified exact existing destination is read-only reconciliation.
The authorized existing input must belong to root or the environment's fixed
`user` account, matching the current helper's source custodian. This permits a
separately authorized copy of the protected dated archive; it never changes its
owner or permissions. The account/directory resources are environment-owned,
not created by this source bundle.

There is no download, extraction, tool execution, network call, credential
access, service change or helper deployment in this utility. It does not install
Go. A failure after publication may leave the exact final artifact present:
reconcile that same fixed destination, and preserve original uncertainty, before
any further host action. Never delete an existing destination to force a retry.

## Qualification and guarded migration order

1. Independently review the exact bundle, staging implementation and synthetic
   oracles. Run the affected repository tests only on the authorized executor at
   the exact published source; source review and AST/hash checks are not passes.
2. Obtain the applicable current host-operation scope. Preserve the dated
   original archive and directory. Acquire the exact public vendor archive or
   read a separately authorized preserved copy, verify the pin and stage it only
   under the reviewed operator procedure. There is no automatic host action on
   clone, import or test discovery.
3. Read back actual destination bytes/protection and the current shared script,
   draft/settings/source identity through the supported route. Reconcile
   existing original jobs and any material divergence. This bundle's old
   deployment evidence is not a fresh full source/schema/settings read.
4. After separately reviewed desired-source/transport bounds and actual scope,
   use the normal guarded shared-script CAS, preserving current settings and
   all targets. Stop on denial or drift. No alternative route is supplied here.
5. Verify deployed identity and a genuinely affected, separately admitted
   qualification. Retain the exact prior source and operator recovery record.
   Only then may the owner assess the old dated archive for cleanup, after
   reconciling all dependent claims and originals. This source change deletes
   nothing.

The current genuine resolved `wmill` dependency lock and full deployed schema,
settings and permissions are **not attested** by this bundle. Fresh provider
recreation and tested restore remain incomplete until those inputs and an
authorized destination are qualified. The historical November 9 transport export
elsewhere in this repository is not substituted for this different helper.

All synthetic staging tests use only owned temporary files, mocked root metadata
or finite mocked metadata calls. They do not import the preserved helper, execute
Go, touch `/opt`, read native state or provision a host. Their source is not a
runtime staging or recovery result.
