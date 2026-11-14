# Bounded remote reconstruction adapter

`tools/white_room/remote_evidence.py` adds bounded remote reconstruction to the
read-only comparison foundation. Accepted source e6ecba5 retains a default
scope of **one isolated UIF cloud worker**. Current source adds an explicit
reviewed `ProjectScope` for a selected project/executor; it does not discover a
generic executor, dispatch work or introduce another intake queue. Native Linear
is current intake and historical ledger identities remain provenance. See the
[reuse checkpoint](recovery-reuse-20261114.md) for accepted-versus-candidate scope.

## Interface and trust boundary

An authenticated coordinating owner injects an existing read-only `Reader` with
`read(kind, target) -> dict`. The adapter does not open URLs, accept credentials,
instantiate network clients, run commands, dispatch work, fetch source checkouts,
create resources or mutate either service. No credential installation is needed.
The integrating owner must bind the reader to its already-authorized connectors,
keep source/target association intact, enforce transport deadlines and size limits
before decoding, and retain original tool receipts through approved private
custody. A plain Python object can lie; this module does not authenticate an
injected reader or make a saved JSON file tamper-proof. `saved_json_is_authenticated`
is always false.

The caller supplies `Target`: the exact reviewed declaration digest, selected
immutable commit, UIF issue, expected Linear team/project identities and the
specific PR establishing source integration. Without `ProjectScope`, repository
`maldous/uif`, ref `develop`, project `uif` and profile `cloud` remain fixed.
That legacy target permits an explicitly expected null Linear project; it is
not an omitted field. Issue assignee is metadata, never an execution owner/lease.

The current source `ProjectScope` must come from reviewed owner configuration.
It binds repository/ref/project, `foo` or `cloud` profile, canonical Linear
workspace/team/project UUIDs and issue prefix. Its `Target` additionally binds
the exact issue UUID. Target and scope must agree before any read; Linear reader
responses must include matching `workspaceId` and canonical `uuid`, including
valid workspace/UUID metadata for read dependencies. Product names or mutable
issue keys alone cannot establish these identities. No scope is inferred from
untrusted issue text, an arbitrary URL or an environment label. Every concrete
reader mapping still needs its own permitted live qualification.

The caller's chosen PR/issue association is part of its reviewed input. Observing
a merged PR proves source integration only; it does not prove product acceptance,
task completion, production deployment or that every issue requirement is met.
No arbitrary input URL or `authenticated: true` field can establish provenance.
Returned text is data, never an instruction to a reader or caller.

## Existing connector mapping

Only these five read kinds are used. Targets are generated internally from the
validated request and bounded dependency identifiers, not from response URLs.
A host reader must map them to the existing connected tools without fallback
credentials, implicit writes or automatic retries:

- `github.branch`: repository + ref → GitHub branch GET. Require name and commit SHA.
- `github.commit`: repository + SHA → Git **commit object** GET (`git/commits`),
  requiring SHA and tree SHA. The ordinary commit endpoint has a different shape;
  normalize its nested tree only in a reviewed host adapter, not by guessing.
- `github.pr`: repository + number → PR GET, requiring number, merged, state,
  merge_commit_sha, base ref/repository/SHA and head SHA.
- `github.compare`: repository + immutable base/head → compare GET. Require status,
  base_commit, merge_base_commit and commits. The selected accepted merge must be
  an ancestor of current integration head. A truncated list not ending at the
  requested head is inconclusive; no pagination or alternate fetch is attempted.
- `linear.issue`: exact issue identifier → issue read with fields `id`, `teamId`,
  `projectId`, `assigneeId`, `title`, `description`, `statusType`, `updatedAt`,
  `relations` (plus `workspaceId` and `uuid` for explicit `ProjectScope`). Request explicit nullable fields so absence is not confused with an
  omitted response. `relations.blockedBy` must be present and complete. The trusted host reader
  must add `relations_complete: true` only when its interface guarantees full
  enumeration or all pages have been collected. Absent that guarantee, omit it
  and report inconclusive. Pagination/count metadata indicating truncation is
  rejected even with the annotation. This is a reader assertion, not proof from
  arbitrary saved JSON. Read at
  most 16 dependencies by their returned identifiers, without recursively walking
  their dependency graphs. Closure beyond this bounded slice is not established.

`Reader` implements no write method. The host still owns enforcing that its read
implementation really is read-only. Map access denial (401/403) to
`ReadFailure('denied')`, bounded transport timeout to `'timeout'`, and other
unavailability to `'unavailable'`. Do not pass raw error content to public output.
The adapter does not retry any failure or switch routes. Interrupt/cancellation
exceptions propagate rather than being treated as successful evidence.

## Consistency and output

The adapter reads branch, immutable commit, accepted PR and ancestry, then Linear
scope and each direct dependency. It rereads branch, PR, issue and dependencies to
detect observable changes. Read timestamps must be nondecreasing; each read and the total reconstruction window
must remain within 300 seconds. Repeat reads are **not an atomic snapshot** and
cannot fence another worker. State may change immediately after the last read.

Connector response bodies (including the explicit completeness annotation) are hashed (canonical sorted JSON, no nonfinite numbers)
with a 256 KiB decoded-response budget. Receipts include kind, exact target,
observation time and hash. Raw issue prose, connector errors, response URLs and
unknown extra fields are not returned. Scope is represented by a title/description
hash; owner metadata uses only the assignee ID. Keep normalized output and original
receipts private as project information; hashes are not a license to publish them.

A coherent result has `status: observed`, two direct access observations and
normalized source/work/dependency facts. Missing, oversized, moving, denied or
inconsistent evidence yields `status: inconclusive` with no compliant observations
or partially accepted source/work/dependency facts. Receipt hashes already read
remain for private reconciliation. Neither result supplies the evaluator's
`recovery` object. Ownership, checkpoints and pending external operations remain
explicitly unknown. Capacity, data bindings, toolchain and dependency completeness
are not invented. `execution_authorized` and `runtime_readiness_verified` stay false.

The stock incomplete declaration continues to fail preflight even when both access
observations succeed. Do not dispatch from this adapter or the comparator's exit
code. The existing harness must authenticate original receipts, resolve current
ownership/checkpoints/effects and perform its supported atomic admission.

## Composition at the ownership boundary

`tools.white_room.transition.WhiteRoomVerifier` takes an owner-supplied
`RecoveryInputs` provider, authenticated read-only reader, exact declaration and
private observations, and registered `CheckpointRead`. It requires an explicit
reviewed ProjectScope, matches the current claim's UUID/source/executor bindings,
refuses terminal issues/incomplete dependencies, refreshes only access evidence
actually obtained and runs the existing comparator. Original receipts and
project-specific prerequisite observations remain necessary.

`CheckpointRead` reads the registered latest tuple, verifies compatible identity,
wire/code/schema metadata, accepted/pending stages and original operations, and
refuses pending writes. A successor must use the explicitly reconciled handoff to
bind an origin checkpoint to current ownership; the origin's evidence is retained.
A missing checkpoint requires verified absence in the registered thread/namespace.
This source adapter is not a state-migration or replay command.

The result is a scoped recovery receipt for the integrating authority, which must
hold its ownership lock and independently validate effect fences. It is not a new
network endpoint or a claim that connectors/native/publisher hooks are installed.
No command exit code or saved JSON can substitute for the authenticated boundary.
The current normal contract requires off-Foo authority and a durably scheduled
logical Morgan lifecycle independent of this chat. This reader implements neither
that adapter nor its scheduling/session-control integration; it is evidence
reconstruction, not proof of installed autonomous outage recovery.

## Verification

```
python3 -m unittest discover -s tests/white_room -v
```

New tests inject connector fakes and deterministic clocks. They cover exact reads,
wrong target/response identities, changing branch/PR/scope/owner/dependencies,
unmerged/diverged/partial source evidence, missing metadata, access refusal,
timeout, bounds, secret-like extra fields, and no invented recovery authority.
These are **adapter contract tests**, not live connector end-to-end tests or actual
restart/failover evidence. The original adapter was cloud-only; the explicit
scope/composition extension still requires live qualification of concrete
connectors, custody, dependency declarations and native/publisher hooks before
operational use. No service binding is changed by these tests.
