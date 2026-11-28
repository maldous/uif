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
| Guarded-idle start | The fixed authenticated Windmill mount submitted the [original start](https://windmill.aldous.info/run/01a21437-ea55-94ca-37ae-8c784ee3aad2?workspace=foo); the [host original](https://windmill.aldous.info/run/01a21437-eab1-9f96-6377-3424489d2b81?workspace=foo) belongs to that same operation. The [same-original terminal readback](https://windmill.aldous.info/run/01a21a53-9d93-77ba-2226-19b78ef7da1e?workspace=foo) verified the historical one-shot start, not normal scheduling. Follow apply, start and consumption through the existing [guarded-idle workflow](guarded-idle-webhook.md). | Reconcile an uncertain delivery on its original. A later [failed invocation](https://windmill.aldous.info/run/01a21961-6ba7-dbdd-33f0-4e6176141b62?workspace=foo) and [readback](https://windmill.aldous.info/run/01a21964-6614-4fc3-cb59-43d65b2a686f?workspace=foo) show why a consumed or expired one-shot marker is not a restart path. Do not repeat the parked bootstrap on a selected stage. |
| Verifier maintenance | The [V2 terminal readback](https://windmill.aldous.info/run/01a2193c-5eff-7d43-cb2b-e7a3fd9e1932?workspace=foo) verified both immutable shelves via the fixed create-only maintenance helper. | Reuse the existing maintenance route for a distinct reviewed verifier release; do not treat its receipt as permission to select or start an application. |
| OffMenu application | The [historical installed receipt](https://windmill.aldous.info/run/01a20d73-afb5-a077-96cb-dc15cb247ef6?workspace=foo) verifies the original through the protected package preparation and upgrade flow. The [newer projection](https://windmill.aldous.info/run/01a217a4-8a35-3593-6940-734266fede54?workspace=foo) is not installed evidence. | Match the current selection to its own installed original and role digests through the existing owner-side observer. Do not rebuild or reinstall a confirmed current release solely to obtain a receipt. |

When a receipt is lost, the existing owner inspects that original and its
destination before deciding the next action. Keep this exact route and
readback with the release. If the route no longer accepts the actual
installed state, record the first incompatible step and identify the
reviewed recovery release; do not search for an alternative command that
can bypass the current selector. For a routine restart, use the normal
commissioning path below, not the one-shot bootstrap.

## Stage recovery beside a previously selected guarded-idle release

The parked bootstrap and the old fixed start action are not staging actions.
For an already selected stage, the recovery staging receiver takes **no
caller arguments**. It belongs only in the existing protected maintenance
release, with the reviewed controller and installer siblings that it
checks against fixed source digests. Its protected deployment configuration
identifies the stage-only original, exact current drop-in and selector
digests, and the five reviewed package member digests. The release owner
supplies those inputs through existing custody, never via generic job
arguments. Source presence does not mean this receiver is installed on Foo.

Read back the same original on an uncertain response. A verified
`staged_not_activated` result means the sibling package is complete and
durable; the selected release and effective unit are unchanged. Staging
does not supply the separate recovery host adapter, startup evidence, or
release authority. Do not reuse the old activation original to stage or
start the new release.

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
   [`/v1/control`](../../services/shared-authority/README.md#wire-contract).
   When normal activation is intended, use that action to bind the installed
   stage, native session and commissioning evidence at the resulting epoch.
   Compare the epoch with the installed authority configuration and take the
   prior activation ID from the authenticated control readback.
   Retain the original receipt. The commissioned selection stays eligible for
   routine restarts at that epoch. Each start obtains its own fresh native
   receipt and respects the durable stop state.
4. **Read back normal operation.** Verify the installed revision, process and
   authenticated startup result, then exercise unattended restart and the
   separately authorized work lifecycle. A running process without
   authenticated startup proof is not dispatch-ready.

If an owner reference, callable action, or original receipt cannot be verified,
record the exact missing item and keep unrelated authorized work moving. This
section links existing actions; it creates no role, approval step, or bypass.

When the installer returns its verified terminal result, the installation owner
hands the exact release, unit and stage plus the original install receipt and
effective unit readback to the existing control owner. The
[`render_normal_selection`](../../tools/delivery/commissioned_normal_service.py)
output is the on-host selector input. The selector records the absolute stage
path. The normal activation names the stage basename and native runtime session.
The control owner uses the full commissioning evidence to prepare the
[immutable normal activation original](../../src/uif/stokowski_cloudflare/startup_control_original.py).
If the action and its inputs are ready, advance the handoff in the same work cycle instead
of waiting for another status poll. If a separate owner holds the next action,
deliver the exact evidence through the existing release handoff and track the
same original to its terminal result. The installer itself never lifts a stop.

### Fix forward when a retained release cannot be reused

A missing or mismatched artifact ends the **reuse** path, not the restoration
work. Keep the installed state and its receipts intact. The release owner
switches to clean forward qualification from current accepted source,
documented native interfaces and independently pinned inputs. Record which
source or dependency must be recovered or rebuilt and verify the resulting
complete closure through the existing independent acceptance. Do not bless
observed bytes as their own provenance or substitute an upstream-only build
for missing native behavior. If no valid source or dependency can be
obtained through the existing release route, hold that package scope and
record the exact source or artifact whose recovery will reopen it. Continue
preparing the disjoint installer and control inputs; do not select a substitute.

Advance these existing lanes in parallel where they do not share a writer:

| Lane | Existing owner and next action | Evidence for handoff |
| --- | --- | --- |
| Runtime package | Release owner reconstructs the native and dependency closure, builds the exact package and obtains independent qualification. | Exact accepted source, dependency and artifact manifest, qualification receipt, and custody locator. |
| Protected installer | Installation owner verifies the fixed normal install interface, prepares exact unit inputs, records the current selection and original receipts, and preserves the existing credential reference. | Exact callable and reviewed inputs ready for the same-original install; after installation, terminal receipt and effective unit readback. |
| Authority control | Control owner verifies the existing authenticated caller, native session, epoch and original commissioning input without issuing a bind against an unverified installation. | Exact callable/identity and original request inputs; after installation, the retained binding acknowledgment and fresh native readback. |

If a lane cannot advance, name its accountable owner, the exact missing input,
the supported action the owner can take, and the evidence that reopens it.
If ownership is not verified, record that and the exact existing owner or
release route to resolve it; do not guess an operator or transfer ownership.
Keep working the other lanes. A monitor can report that evidence arrived; it
does not build, install, or bind anything. When it arrives, resume the held
step and continue through the existing end-to-end acceptance above. Do not
restart a held unit or replay an original to obtain a result. This is work
coordination, not a new approval or activation condition.

### Prepare normal stage bytes without repeating a failed transfer

An accepted launcher, wheel, or web dependency candidate is only a component.
The runtime package also needs the compatible native source, interpreter,
standard library and native extensions, dependencies, and protected
configuration references. The current normal launcher imports the native
composition, which needs `ctypes` and its working `_ctypes` extension. The
[stage materializer](../../tools/delivery/stage_commissioned_normal_runtime.py)
also uses `ctypes` for atomic no-replace publication. Do not substitute
`os.link` or treat a loose Python executable as the runtime closure.

If the retained component cannot be transferred through its supported
custody route, use the verified distributable and its pinned build inputs to
rebuild it. Do not repeat an unavailable transfer through another channel.
The release owner supplies the verified component bytes in their original
custody; the existing independent reviewer qualifies the complete package.
The installation owner prepares the current stage preimage and staged
readback without changing the current selector.

The [detached archive renderer](../../tools/delivery/render_detached_normal_stage.py)
produces deterministic input bytes and digest metadata for the normal stage
materializer. It does not issue an owner approval or install a selection.
Keep credential bytes out of the archive and preserve existing protected
references. When a verified package and its fixed owner action are both
available, continue that action in the same work cycle and carry its original
receipt to the existing control handoff above.

### Retain the inputs for the next recovery

Keep the complete accepted release and its canonical manifest at a stable
locator readable by the existing installer. Record each component's exact
source, artifact digest, and recipe or build receipt. A version-only dependency
lock, successful CI run with no retained artifact, or upstream-only wheel will
not reproduce a customized runtime. Retain the native patch source and locked
web artifacts alongside the package. Keep credential material out of artifacts
and runbooks; record only the existing protected reference and its authorized
consumer.

For the previously retained native core, the read-only
[`compare_native_shelf.py`](../../tools/delivery/compare_native_shelf.py)
comparison uses the existing detached native file-map format and an expected
manifest digest retained independently by the release owner. It observes only
the fixed native and web shelves in place and returns role counts for missing,
unexpected and changed members; no member paths or bytes leave custody. A
missing expected map is held before observation. A matching map proves only
member identity, not provenance, installation, or runtime acceptance.
Without the caller's existing issued install writer exclusion, treat this as a
bounded sequential observation, not an atomic release snapshot. This is
an optional evidence check for reuse, not a new installer or startup condition.

For detached lineage qualification, the same helper can compare one candidate
role map against the exact count and digest of a retained historical receipt.
Compare Python and web dependencies separately. This uses the original
compact sorted-pairs digest, not the candidate JSON file hash. A difference
shows only that those observations are not byte-identical; the aggregate
receipt cannot identify which files differ or establish production acceptance.

Keep the fixed startup caller, its deployed revision and effective identity
with the commissioning receipt. Windmill job authentication alone does not
supply the shared authority control identity. If an acknowledgment is lost,
read back the same original as control before deciding what remains. The
lookup uses the authority's actual installation digest, the control caller's
session from the original submission, and the original request ID, not the
native session or an artifact or selector digest. Do not create a new
operation or replay the bind to obtain a receipt. A matching acknowledgment
is not the native startup readback.

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
