# Authenticated Windmill webhook for guarded Stokowski idle

This is an alternative to the existing signed workflow, not a reinterpretation
of a signature. The fixed, private Windmill endpoint's existing execute ACL
is approval. The endpoint has no arguments. Its internal `WM_JOB_ID` is audit
context, not approval or the release deduplication key. Expose no anonymous
trigger or caller-selected Bash. Keep ordinary root-maintenance privilege
separate from the operation's fixed arguments; this does not claim to protect
against an already trusted administrator intentionally selecting generic root.
The alternative uses the existing stopped/idle guards and may not run
inference, alter claims, or publish.

## Before a first install

Use the existing authenticated Foo root maintenance transport and run
only the exact reviewed maintenance invocation. Verify the isolated
interpreter and before execution verify the immutable maintenance
entry source and its two reviewed module bytes in root-controlled,
non-writable, regular single-link custody. Do not import or execute the
UID1000 detached artifact. The root-controlled preparation directory is
`/var/lib/uif-stokowski/idle-bootstrap-input/`:

- `guarded_service_upgrade.py`: exact independently accepted controller bytes.
- `install_guarded_idle_bootstrap.py`: exact independently accepted installer bytes.
- `bootstrap-pins.json`: the fixed reviewed deployment configuration (not a
  webhook request), with the original operation ID and four digests below.
- `bundle.tar`: exact independently accepted idle closure,
  including its fixed launcher, idle config and pinned interpreter.
- `quiescence.json`: existing fresh zero-inflight, children-gone, no
  unresolved-operation witness for the exact parked unit. No credentials.

Execute the exact reviewed `config/windmill/source/launch_guarded_idle_bootstrap.bash`
body in the existing authenticated root maintenance transport, **without
arguments**. Its isolated Python runs from the reviewed launcher body, checks
both fixed leaf paths and ancestor custody, hashes their bytes before import,
compiles the same verified bytes, then calls
`install_guarded_idle_bootstrap.install(BootstrapApproval(...))`. Protected
configuration fields are `operation_id`, `controller_sha256`, `bundle_sha256`,
`launcher_sha256`, and `config_sha256`. The launcher verifies that its own
compiled controller pin matches the protected configuration. Never execute
the root helper from the UID1000 detached stage, pass webhook arguments into
it, or accept arbitrary file paths.

Read-only preflight: observe `systemctl --no-pager cat stokowski-foo.service`
and its exact stopped properties via the current guard's `stopped_unit_sha`
before writing; compare the known parked unit preimage, absence of the
parked and idle markers, and check only exact prepared hashes. These checks
do not write a selector or establish that a start occurred. First protected
write flow: install the fixed loader and lock, preserve parked selector,
seal stage, install fixed drop-in, and verify exact retained bytes. The
installer never reloads or starts. The normally required maintenance reload
and effective unit hash readback follow existing custody procedures. Preserve
the old unit and drop-in. An independent review of the exact effective unit
and stage supplies the existing `review.json`; do not mint it from the
installer output or reuse a stale witness.

## Private normal invocation

Mount the Windmill entry using `bind_webhook` and a fixed trusted carrier
that invokes the protected Foo composition, never the generic command
argument. The trusted Foo composition binds
`guarded_service_upgrade.bind_private_webhook_idle(FixedWebhookIdleStart(...))`:
stable original operation, reviewed source head, review hash, bundle hash,
and selected-stage hash are pinned in protected configuration. It issues a
distinct Windmill apply receipt, selects the exact idle stage under the
existing selector lock, releases the lock before systemd activation, and
lets the existing systemd prelude consume the protected marker for that
unique invocation. It never makes legacy signed records. Verify original
apply/start/consume receipts, active unit's exact PID/invocation and idle
health status. A new Windmill delivery reads or reconciles the original
effect instead of replaying it. Unknown results remain reconciliation only.
