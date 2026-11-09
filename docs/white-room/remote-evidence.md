# UIF cloud remote reconstruction adapter

`tools/white_room/remote_evidence.py` adds bounded remote reconstruction to the
read-only comparison foundation. It does not change the existing evaluator,
declarations, README reconciliation ledger or AGENTS instructions. The selected
scope is **one isolated UIF cloud worker**; it is not a generic executor or
cross-project dispatcher.

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
specific PR establishing source integration. Repository `maldous/uif`, ref
`develop`, project `uif` and profile `cloud` are fixed for this version. A null
Linear project is an explicit expected absence, not an omitted field. Issue
assignee is recorded as metadata, never promoted to execution owner or lease.

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
  `relations`. Request explicit nullable fields so absence is not confused with an
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

## Verification

```
python3 -m unittest discover -s tests/white_room -v
```

New tests inject connector fakes and deterministic clocks. They cover exact reads,
wrong target/response identities, changing branch/PR/scope/owner/dependencies,
unmerged/diverged/partial source evidence, missing metadata, access refusal,
timeout, bounds, secret-like extra fields, and no invented recovery authority.
These are **adapter contract tests**, not live connector end-to-end tests or actual
restart/failover evidence. No new Foo runtime experiment is needed for this
cloud-only additive module. A later host integration must qualify its concrete
connector mapping and custody independently before relying on observed facts.
