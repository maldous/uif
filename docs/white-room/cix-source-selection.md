# Bounded CIX future-source correction

UIF-140 owns this shared source-binding repair. It does not create a second
issue queue or replace CIX's product, review or publication controls.

Read-only live evidence on 16 November 2026 found the native CIX workflow's
repository/origin correctly selected `cix-project/cix`, but `delivery.base`
selected `main` and its creation hook cloned the default branch without an
explicit branch. Retained native issue workspaces therefore contained the old
main scaffold rather than the accepted develop product source. The actual
hook SHA256 was
`9c6ba100a43720ba16d6e586ed2ddf3d82bd14b3f8fb0d436227e0e89fe5597c`.
These observations do not invalidate or rewrite retained reviews of that old
source. Current accepted CIX develop must be independently observed by its owner.

## Exact migration boundary

`tools/white_room/migrate_cix_source.py` changes only two existing JSON scalar
spans in the one paused CIX project:

- `delivery.base`: `main` to `develop`;
- the pinned `after_create` hook: add `--branch develop` to its existing
  `clone --single-branch --` selection.

The complete source preimage and the original hook must match their actual
SHA256 pins. Repository, origin, pause and selected project must match. All
unselected bytes, including other projects, credentials, tracker/prompts and
timeouts, remain untouched. Raw configuration and credential-bearing hook text
are not output or copied into this repository.

The helper defaults to dry-run. It requires an absolute nonsymlinked regular
configuration file, bounded strict JSON without duplicate keys, and a fresh
native CIX paused/quiescent observation. Other projects need not be idle. For
application, run as the protected file's actual owner through the authorized
host transport and retain sole configuration-writer custody. A cooperative file
lock and immediate preimage/state rechecks detect drift; they are **not** a
distributed lease or a substitute for the actual writer's authority.

Use the full immutable reviewed source and original host operation identity.
The caller supplies the freshly observed preimage hash, never a guessed pin:

```sh
python3 tools/white_room/migrate_cix_source.py \
  --config "$ACTUAL_CONFIG" --expected-preimage-sha256 "$ACTUAL_SHA256"
```

Only the same authorized scope's qualified application adds `--apply`. It
preserves the original bytes in a private sibling `.uif-source-migrations`
directory and atomically replaces the file with mode/owner retained, then fsyncs
and verifies the readback. An incompatible preexisting backup is never replaced.
After a possible effect with a failed final check it reports `uncertain`, not a
rollback or success: reconcile the original host operation and source/readback
before any retry. Do not echo, upload or commit private backups/configuration.

## Adoption is separate from file replacement

This helper does not run the hook, Git, inference, service control, claim
transfer, queue retirement, publication or an issue-state transition. It does
not edit existing workspaces or sealed original source/publication bindings.
It never resumes admission, clears a gate or declares old scaffold work accepted.

Verify how the current installed service reads/reloads its workflow, using its
actual supported source/interface, before claiming effective adoption. Do not
restart a service or change any pause merely to apply this source correction.
Verify a fresh isolated clone selects the intended current develop commit as the
ordinary repository owner, without starting a native issue or repeating accepted
product suites. Record source review, helper dry-run/application, effective
configuration and future-source selection as distinct receipts in UIF-140.
Any old issue needing a new source disposition retains its original evidence
and requires that specific owner's reconciliation; this is not bulk redispatch.

## Synthetic qualification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/white_room -p test_migrate_cix_source.py -v
```

Tests use credential-free synthetic hooks and an injected state reader. They
cover exact two-span preservation, original pins, ambiguous/malformed JSON,
paused/current CIX state, owner/path/backup conditions, preparation races and
post-effect uncertainty. They do not prove installed runtime behavior or grant
host configuration authority. The real pinned native dry-run remains separate.
