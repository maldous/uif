# Reviewed release transport contract (inert source draft)

The sole supported initial profile is `offmenu-native-upgrade-v1`: repository
`maldous/offmenu`, unit `offmenu-stokowski-current.service`, and the fixed role
`/usr/local/lib/offmenu-native-upgrade`. The operation is not a shell command.
Request fields have an exact allowlist: action, profile, operation ID, release
digest, package digest and digest of the protected signed OffMenu request. No
caller-controlled path, executable, user, permission or authority field is used.

The maintained `foo_root_jobs.py` is the legacy Windmill wrapper. It forwards to
the host helper under a technical SSH identity; its caller-supplied job ID and
Windmill admin audit label do not authenticate the effective request principal.
Do not forward this operation as a legacy `run` command, add it to the deployed
schema, or treat a successful wrapper receipt as owner approval. Neither the
resident helper's source provenance nor a trusted current-job bridge is
established. The draft entrypoint therefore always denies even valid requests.
No signing, staging, enrollment, replacement, start or publication is provided.

To enable a later implementation, the platform owner must qualify an independently
authenticated current-job verifier at the receiving host boundary. It must bind
the current executing Windmill job to its effective principal, exact reviewed
runner release, OffMenu profile, original operation, release/package/request
digests and independent host-owner authorization, and fail closed on expired,
missing, stale or mismatched evidence before dispatch. Authorization must not be
issued by the caller or the forwarding wrapper. The receiver should keep
original-operation identity and reconcile lost acknowledgments, not mint a new
identity. The qualified OffMenu ingress retains its separate installer-key and
protected packet checks. No existing privilege scope is expanded here.
