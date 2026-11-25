# Foo idle installer admission

The trusted Foo host owner uses this ceremony for `stokowski-foo.service`
and stage `uif1313-idle-a22fbb2` only. It ends with the original
`confirmation.json` and `admission.json`. The [idle upgrade procedure](stokowski-provider-readiness.md)
covers the separate selection and activation decisions.

Before running the tool, the Foo host owner must identify the existing
authorized operator and verify their existing public signing key fingerprint
through the owner's authenticated administration surface. Enroll one
protected allowed-signers entry at
`/etc/uif-stokowski/activation_allowed_signers`, using the operator's exact
Unix account name as the sole principal. The Unix UID must be nonzero.
Use an existing key; neither the installer nor the root runner enrolls a
signer. Stop if the authenticated owner or the approved fingerprint cannot
be established.

The owner separately qualifies and installs the fixed idle bootstrap as
reviewed. Preserve the original parked unit and both absent markers.
Protect the reviewed loader and this reviewed issuer at
`/var/lib/uif-stokowski/idle-upgrade/guarded_service_upgrade.py` and
`/var/lib/uif-stokowski/idle-upgrade/issue_foo_idle_installer.py`. Provision
the protected selector lock, receipts directory, and original parked
selection described in the idle upgrade procedure. Seal the exact
reviewed stage and qualify the effective unit before any admission.
Neither command installs those files or authorizes the bootstrap.

Place the independently accepted `review.json`, exact `bundle.tar` and
fresh independent `quiescence.json` in the fixed protected directory
`/var/lib/uif-stokowski/idle-upgrade/uif1313-idle-a22fbb2/`. The review
binds the actual effective stopped unit after bootstrap. The independent
observation covers zero inflight work, stopped children, and no unresolved
operations.

Use an owner-selected 64-hex operation identifier. From the existing
approved Foo root transport, run:

```sh
/usr/bin/python3 -I -S -B /var/lib/uif-stokowski/idle-upgrade/issue_foo_idle_installer.py \
  --action prepare --operation-id "$operation_id"
```

The enrolled operator reviews the returned request and saves those bytes
as `installer-request.json` in their trusted signing environment. No
private key leaves that environment.

```sh
/usr/bin/ssh-keygen -Y sign -f "$existing_operator_key" \
  -n uif-foo-stokowski-idle-installer installer-request.json
```

Return the exact JSON and detached `installer-request.json.sig` to the
protected packet directory. Use the same transport and operation ID with
`--action issue` within the 60-second independent-witness window. The tool
locks the selector exclusively, verifies the signature, repeats the
installer checks, and publishes the two original files create-only. The
confirmation is the exact signed JSON. The admission binds it, the review
and quiescence by digest.

An identical, still-qualified request can finish a matching confirmation
left by an interruption or read back the original completed pair. Preserve
conflicting or expired material for the Foo owner to reconcile. The
ceremony leaves the parked selection and unit intact and never issues an
apply packet or activation marker.

Use the same installer operation ID for apply preparation, apply and the
separately signed start. The guarded workflow checks that ID against the
protected original confirmation before accepting any of those actions.
