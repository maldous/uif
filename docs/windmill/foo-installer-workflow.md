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

### Reuse the route, not the original authorization

Previous Foo work established three separate routes. Use their receipts to
identify the right owner, command family and readback. Do not rediscover a
workspace, run a different tool, or rerun a completed operation because a
different target is unavailable.

| Target | Proven route and readback | What to do next time |
| --- | --- | --- |
| Guarded-idle start | The fixed authenticated Windmill mount submitted the [original start](https://windmill.aldous.info/run/01a21437-ea55-94ca-37ae-8c784ee3aad2?workspace=foo); the [host original](https://windmill.aldous.info/run/01a21437-eab1-9f96-6377-3424489d2b81?workspace=foo) belongs to that same operation. Follow apply, start and consumption through the existing [guarded-idle workflow](guarded-idle-webhook.md). | Reconcile an uncertain delivery on its original. A later [failed invocation](https://windmill.aldous.info/run/01a21961-6ba7-dbdd-33f0-4e6176141b62?workspace=foo) and [readback](https://windmill.aldous.info/run/01a21964-6614-4fc3-cb59-43d65b2a686f?workspace=foo) show why a consumed or expired one-shot marker is not a restart path. Do not repeat the parked bootstrap on a selected stage. |
| Verifier maintenance | The [V2 terminal readback](https://windmill.aldous.info/run/01a2193c-5eff-7d43-cb2b-e7a3fd9e1932?workspace=foo) verified both immutable shelves via the fixed create-only maintenance helper. | Reuse the existing maintenance route for a distinct reviewed verifier release; do not treat its receipt as permission to select or start an application. |
| OffMenu application | The [historical installed receipt](https://windmill.aldous.info/run/01a20d73-afb5-a077-96cb-dc15cb247ef6?workspace=foo) verifies the original through the protected package preparation and upgrade flow. The [newer projection](https://windmill.aldous.info/run/01a217a4-8a35-3593-6940-734266fede54?workspace=foo) is not installed evidence. | Match the current selection to its own installed original and role digests through the existing owner-side observer. Do not rebuild or reinstall a confirmed current release solely to obtain a receipt. |

When a receipt is lost, the existing owner inspects that original and its
destination before deciding the next action. Keep this exact route and
readback with the release. If the route no longer accepts the actual
installed state, record the first incompatible step and identify the
reviewed recovery release; do not search for an alternative command that
can bypass the current selector. For a routine restart, use the normal
commissioning path below, not the one-shot bootstrap.

## Commission the normal Stokowski release

This is a different operation from a guarded-idle start, verifier maintenance,
or replacing the OffMenu application. Use the [normal operating contract](../operations/business-as-usual.md#commissioning-and-routine-restarts).
The [normal profile renderer](../../tools/delivery/commissioned_normal_service.py)
in [UIF #152](https://github.com/maldous/uif/pull/152) and [#153](https://github.com/maldous/uif/pull/153)
produces launcher and unit inputs only. It does not build the installed
runtime closure, authorize selection, or commission the service.

Use the existing release handoff, recording its current callable actions and receipts:

1. **Release owner and independent acceptance.** Identify the exact accepted
   normal Stokowski runtime closure, including the interpreter, UIF
   wheel, native core and web dependencies. If reusing an installed dependency
   shelf, compare its observed files with the original build and dependency
   manifest through the existing owner and reviewer. A historical launcher
   hash, current inventory, or verified wheel alone does not establish the
   complete release.
2. **Protected installer.** Identify the existing authorized installer action
   for the normal release. Keep its exact original and terminal receipt.
   Verify the installed stage and effective service profile, including any
   inherited hooks, credential input, and service identity. The generic Foo
   transport and the OffMenu verifier shelf do not qualify this action.
3. **Shared authority control.** Identify the existing authenticated
   `control` caller for
   [`/v1/startup/bind`](../../services/shared-authority/README.md#wire-contract).
   Bind the installed unit, stage, native session and epoch to the original
   commissioning evidence and validity. Record the original receipt. An
   Access identity in policy is not a verified callable control runtime.
   While the binding is current, unexpired and not stopped, subsequent starts
   obtain fresh native receipts without new manual authorization or reuse of
   the consumed guarded-idle marker.
4. **Read back normal operation.** Verify the installed revision, process and
   authenticated startup result, then exercise unattended restart and the
   separately authorized work lifecycle. A running process without
   authenticated startup proof is not dispatch-ready.

If an owner reference, callable action, or original receipt cannot be verified,
record the exact missing item and keep unrelated authorized work moving. This
section links existing actions; it creates no role, approval step, or bypass.

### Retain the inputs for the next recovery

Keep the complete accepted release and its canonical manifest at a stable
locator readable by the existing installer. Record each component's exact
source, artifact digest, and recipe or build receipt. A version-only dependency
lock, successful CI run with no retained artifact, or upstream-only wheel will
not reproduce a customized runtime. Retain the native patch source and locked
web artifacts alongside the package. Keep credential material out of artifacts
and runbooks; record only the existing protected reference and its authorized
consumer.

Keep the fixed startup caller, its deployed revision and effective identity
with the commissioning receipt. Windmill job authentication alone does not
supply the shared authority control identity. If an acknowledgment is lost,
read back the same original as control before deciding what remains. Do not
create a new operation or replay the bind to obtain a receipt.

For an unattended recovery, keep the last accepted release and current
commissioning reference available to the existing service tooling. A service
restart should use that recorded state, not ask an agent to rediscover it.
Record the first missing input or failed check separately from a provider
hold. Offline interface checks and receipt tests help diagnose recovery but
do not replace the live process and original-effect readbacks above.

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
