# Foo installer workflow

Use this runbook to resume an existing Foo release without confusing a staged
artifact, a maintenance receipt and an installed application. It documents the
existing boundaries; it adds no approval or runtime condition.

## Select and reconcile the exact operation

1. Read [current Windmill routing](../agents/windmill-workspace.md). Discover
   the deployed schema and authenticated operator in the current generic
   **`foo` workspace**. The historical `offmenu` workspace, a direct shell or a
   Foo-local coding session is not a fallback for a denied or unknown action.
2. Name the actual target (guarded idle, verifier shelf, application
   replacement or service start), the sole writer, the original operation
   and reviewed source head/tree. Bind the reviewed artifact/release digest,
   the destination and the actual approved invocation to that target. Do
   not reuse a dated reference as current authority.
3. If an exact original was submitted, reconcile it in its original
   workspace. Check the inner terminal disposition and target readback, not
   just wrapper success. An unresolved acknowledgement, partial write or
   mismatch is a reconciliation case, not a reason to run a new original.
   For a genuinely new, authorized create-only operation, verify that its
   original identifier and fixed destination are unused.

## Choose the existing release path

- **Guarded idle:** [UIF #130](https://github.com/maldous/uif/pull/130)
  documents the established private, authenticated, fixed Windmill carrier
  for guarded idle. Follow the [guarded-idle workflow](guarded-idle-webhook.md)
  and its scoped apply/start receipts. The generic Foo runner transports the
  reviewed maintenance operation; neither its login nor job ID is an approval
  for arbitrary input. Guarded idle is not normal scheduling or delivery.
- **Verifier shelf:** [UIF #129](https://github.com/maldous/uif/pull/129)
  is the prior verifier bootstrap and pinning example. Select the exact
  reviewed fixed maintenance profile: its protected descriptor identifies
  the source manifest, complete closure, fixed destination and release
  digest. Reconcile or confirm an unused original as above. For a new V2
  shelf, use the existing owner-only Foo maintenance transport to invoke
  only the reviewed digest-bound launcher and fixed create-only helper.
  Read back the destination custody, complete closure and installed release
  digest against that same descriptor and original. Retain V1 and prior
  receipts. Never run the ordinary-owner stage as root, overwrite a prior
  shelf or infer a selector or application action. On a lost
  acknowledgement, read back the same original; do not resubmit merely
  because the application is still absent.
- **Application:** Follow the
  [OffMenu native runtime release runbook](https://github.com/maldous/offmenu/blob/develop/docs/development/native-runtime-release-runbook.md)
  and its existing protected ingress. Resolve the actual role custodians,
  exact five-role package, recognized independent review and typed
  `PackageAcceptance` in the already authorized process. The reviewer is
  distinct from the author and package owner. The accepted package, original
  operation, preflight, quiescence, locks, rollback and release/request pins
  must remain joined through the established installer. Neither a working
  maintenance carrier, create-only verifier readback, staged archive nor
  source approval supplies the independent application acceptance.

## Forward the protected application release

1. Keep one accountable, authenticated release owner on the existing
   protected application route. Use the accepted source head/tree and the
   actual owner and publisher roles; a reviewer must be independent of both
   the owner and source author. Reuse that recognized review and existing
   admission, not another approval of the same scope.
2. Reserve one immutable application original to the reviewed source and
   authenticated evidence for each input's source, role, digest and approved
   custody. The verified owner supplies the exact preflight, current
   quiescence, existing lock custody and recovery preimages. A tool
   identity, staged file, source review or caller assertion cannot supply
   these facts.
3. Check the original and its fixed destination, then prepare the package
   once under that original, using the existing protected owner-local or
   split-role composition as applicable. Keep snapshots and packet bytes
   in their approved custody. Pass only the allowed digest receipt to
   operators outside that custody. Do not reuse a verifier provisioning
   receipt or its operation for an application.
4. Check the prepared receipt and bind its resulting package digest, the
   reviewed verifier release and the exact protected request to the same
   original through existing admission. Perform only that admitted
   application operation. If preparation or commit is interrupted, observe
   the same original and compare its retained intent, effects and canonical
   completion. Preserve evidence and fix forward through the established
   owner path; do not create a new original, redo the review, or repeat the
   effect to manufacture a successful receipt. A canonical stopped install
   and subsequent activated runtime are separately recorded outcomes.

## Verify safely after each actual effect

Keep staged, provisioned verifier, installed application, service active and
normal delivery as distinct outcomes. Before an authorized restart, reconcile
all prior effects, verify quiescence, lock custody and the effective selected
unit, and retain the prior selection as recovery evidence. Fix forward using
the original operation and established controls. Do not restart to resolve
an unknown receipt. In the existing start path, verify
the approved source/release, actual service identity and selected mode before
claiming health. Read back the same service invocation and its receipt after
start; an active PID alone proves neither the intended runtime nor BAU.

For normal operation, use the [BAU contract](../operations/business-as-usual.md):
verify durable pickup, bounded and authorized provider use, original results
through review/publication, refill, and safe recovery without duplicate
effects or paid fallback. Preserve any explicit project hold. Record exact
current evidence and unknowns separately; an idle-only or stopped result
cannot be promoted by wording.

## Dated verification example

[OffMenu #346](https://github.com/maldous/offmenu/pull/346) merged at
`d5314d18b2cb2b69011bd15d48d3baf7537bc78e`. Its detached V2 verifier
stage had 176 verified members, with 175 unchanged byte-for-byte and in mode.
The [original stage](https://windmill.aldous.info/run/01a21934-f7a3-4e28-38a2-f56ee70fe62a?workspace=foo)
and [terminal readback](https://windmill.aldous.info/run/01a21935-3b09-1367-6af1-a62813c5f56c?workspace=foo)
establish that staged closure only. They are not an installed V2 receipt,
application acceptance or a current release selection.

Later independent [installed shelf readback](https://windmill.aldous.info/run/01a2193c-5eff-7d43-cb2b-e7a3fd9e1932?workspace=foo)
verified both 176-member root-controlled shelves and the unchanged V1, with
the exact V2 release digest. A separate [loader readback](https://windmill.aldous.info/run/01a2193d-ecb1-caf1-0c48-294c69b39097?workspace=foo)
verified the expected V2 module origins and dependency resources without
application effects. [Installed module pin readback](https://windmill.aldous.info/run/01a21942-9923-5a74-5b09-f5f9f3f33aed?workspace=foo)
matched the selected application-preparation and upgrade modules. These are
dated installed **verifier** and compatibility facts, not an application
packet, acceptance, replacement receipt, or normal BAU verification.
