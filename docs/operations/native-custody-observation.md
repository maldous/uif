# Bounded native-process custody observation

This UIF120 candidate prepares additions to the existing native Core, not a new
controller, authority store, election, publisher or restore protocol. Its sole
integration and installation owner remains UIF. Source preparation, synthetic
contracts, exact native-source comparison, source integration, installation,
live adoption and a real observation are separate outcomes.

## Exact source selection

The preparer accepts only the complete pinned regular files, each at most 1 MiB:

- orchestrator.py SHA256
  7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea.
- web.py SHA256
  c0e18143f3e16cf2c7584b030bf53fd7a60a5802456383e92f0cc2c2e5786c6a.

It inserts one observation method in Orchestrator, one existing-project selector
in MultiOrchestrator, and one read-only GET adapter in create_app. Existing
source bytes are preserved exactly when the inserted spans are removed.
Unexpected source, duplicate anchors or a second application refuses. The final
input file component is opened no-follow/nonblocking and must be regular;
this is not a directory-containment or adversarial filesystem guarantee.

Run the metadata-only preparer with explicit selected source paths:

    python3 tools/delivery/native_custody_observation.py \
      --orchestrator-source "$PINNED_ORCHESTRATOR" --web-source "$PINNED_WEB"

The optional --patch prints a generic-source unified diff to stdout. The CLI
writes no files, imports no native module, invokes no endpoint and installs
nothing. It does not acquire/release a claim, dispatch, restore a retry, restart
a service or modify any issue, original record or configuration.

## Proposed existing-Core read adapter

    GET /api/v1/projects/{project_name}/native-custody/{issue_id}

The selector chooses only an existing project; no project is created. The native
issue id is a bounded ASCII alphanumeric/hyphen/underscore key, not a filesystem
path or remote query. Invalid selectors return 400, an unknown project 404.
A valid id with no local entries can return 200 with false presence fields;
this does NOT verify that the Linear issue exists or that no owner/claim exists.

The JSON-safe response contains an observation timestamp/process PID, bounded
project/id selection, presence booleans for existing running/task/claimed-set/
gate/repair/retry entries, and bounded state/run/retry-attempt metadata.
Task registration does not prove the task is alive. No task, report, repair,
claim or retry object is serialized. No original body, context/source hash,
session, token, native retry context or native_claim value is exported.
Unrecognized or oversized selected scalar metadata becomes null, not repr,
string coercion or a raw object dump.

All external-owner, current-issuer, lease, epoch, fence and original-settlement
values remain explicitly unknown. Every authorization/effect flag is false,
including issue-existence verification, publication, dispatch/restore,
acquire/release and retry-preservation qualification. A PID or timestamp is not
an epoch, effect fence, lease or stable owner identity. Claimed-set membership,
including false membership, never grants authority or releases original custody.

This is a best-effort process-local observation, not an atomic cross-project
snapshot or authorization decision. Existing web GET handlers remain unchanged;
the candidate adds no authentication mechanism. Installation must separately
reconcile the intended access/exposure and source/runtime custody.

## Adoption and original-effect limits

Current startup creates empty in-memory claimed/retry/task maps. Existing
durable settlement recovery creates a new settling attempt/claim/task and can
perform effects; it is not proof of preservation of an existing retry's object,
native context or original current-claim identity. This candidate supplies no
export/checkpoint/restore mechanism and does not make a current old-process
restart safe. Original retry49 and sealed/denied operations remain untouched.

The separately prepared seven-owner-label correction changes the same pinned
orchestrator source. This preparer deliberately refuses changed pins; do not
silently apply it after that correction or claim combined adoption. A composed
source needs its exact new preimage, bounded review and consuming qualification
under the existing installer. Controller/workflow file pins in input-hold
bindings are distinct from the declarative native-stage custody contract.
Nothing here restores old gate compatibility or neutralizes ordinary comments.

The focused tests execute only credential-free synthetic class/web fixtures,
not installed Core, issue bodies, an HTTP server or existing product tests.
Passing them is not live custody recovery, issuer verification or full UIF120,
native publication, restart, continuity or BAU acceptance.
