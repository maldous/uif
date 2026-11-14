# Operating procedure

Use the pinned [policy 2.0.0](agents/uif-worker-policy.md),
[manifest](agents/uif-worker-policy.json), [environment profile](agents/environment-profiles.json)
and selected repository's current instructions. This describes the required
operating contract. Source acceptance is separate from installation, runtime
adoption, successful recovery and the later controlled start.

## 1. Reconstruct the current unit and destination

Native Linear is the sole live intake. Resolve immutable workspace/project/issue
UUIDs, current scope/dependencies, execution restrictions and accepted outcomes.
Preserve historical ledger IDs as provenance without starting a ledger selector
or creating a second queue. Reconcile the original operation before retrying an
uncertain submission, completion or publication.

Follow the existing [white-room procedure](white-room/README.md) and use
[tools/white_room/check.py](../tools/white_room/check.py) to compare the selected
project/executor's declared contract with permitted current observations. Reuse
[remote_evidence.py](../tools/white_room/remote_evidence.py) where its exact
[UIF/cloud target and reader contract](white-room/remote-evidence.md) apply.
The default remains one UIF/cloud target; the source ProjectScope extension
requires reviewed repository/ref/project/executor and native Linear UUID bindings.
[transition.WhiteRoomVerifier and CheckpointRead](../tools/white_room/transition.py)
combine that reader with declared comparison and current owner-scoped persistence
at the ownership boundary. Actual authenticated connectors and native/publisher
hook installation remain separate qualification work; source code is not live
ownership proof.

Read current GitHub source, accepted PR/ancestry and Linear dependencies; inventory
surviving work, custody, claims and original receipts. Preserve dirty/local-only
work and use a clean isolated destination workspace without reset/clean. Compare
declared configuration, installation and service bindings with actual destination
state. Foo requires its applicable host/service/data/resource checks; isolated
cloud work uses its own lighter gates, with Foo prerequisites only when needed.
Record passed, drifted, blocked, unknown and unqualified findings precisely.

Match selected persistence to the current issue/source/schema/stage and original
operations. Resume only compatible unaccepted work. Retained checkpoints and
accepted checks are reusable at their exact valid bindings; do not replay accepted
work merely because execution moves. Verify remote checkpoint readback. A missing
checkpoint or vanished session is not verified absence of work or child effects.

The comparator always returns execution_authorized/runtime_readiness_verified
false; its exit code cannot dispatch or start services. Quota restoration is
only one observed prerequisite, never a white-room readiness pass.

## 2. Claim and route through existing controls

Resolve the [per-unit project contract](project-integration.md), lane, actual
executor/user, source/worktree, owned paths, resource budget and reviewer/publisher.
Use the Foo ownership authority's supported atomic claim with the current task
generation and coordinator epoch. Verify those bindings before dispatch, shared
integration, Linear transitions and publication. Preserve original operation IDs
and fence a superseded owner before replacement effects.

The authority tracks execution claims, not intake or backlog. Until its runtime
integration is installed and qualified, allocations remain explicitly disjoint
under acknowledged owner coordination. During Foo-authority loss pause new
claims/transfers and dependent shared effects; cloud owners may preserve assigned
work on permitted branches without claiming acceptance or late publication.

Choose available native/cloud commands or deterministic MCP for suitable reads,
builds and checks. Use a Foo model session for useful reasoning/parallelism, with
actual allowance and admission. Prefer economical task-appropriate model/effort,
record exceptions, and distinguish requested from observed settings. Load only
relevant [skills and capabilities](agents/skills-and-plugins.md).

Cloud storage/Git is independent of Foo. MCP user/root transport, deterministic
execution and Codex inference are distinct. Root host access does not authorize
root Git/builds/tests or implicit session control. Discover the actual schema and
workspace through [current Foo guidance](agents/windmill-workspace.md), then bind
the command and original terminal receipt. Never retarget the fixed legacy
wrapper from generic connector availability or invent missing arguments.

## 3. Use the token-mode transition

| Source mode | Required behavior |
| --- | --- |
| `local-ready` | Stokowski owns normal admitted local pickup, execution, review, publication, recovery and refill. Morgan supervises; cloud roles retain their independent allocations. |
| `transfer-pending` | Confirm hard local exhaustion, close dependent local model admission, capture source/dirty-work/claim/checkpoint/operation state deterministically and reconcile surviving effects. Require an active Morgan heartbeat before new cloud claims/transfers. |
| `cloud-active` | After fenced transfer and receiver acknowledgment, Morgan actively coordinates fallback while project roles execute their own eligible issues. Deterministic MCP remains available within its actual host/authority/resource limits. |
| `local-recovery` | After fresh supported allowance evidence, applicable white-room checks and one bounded canary, gradually admit independent local issues. Existing cloud issues keep their owner until completion or reconciled failure. Return to local-ready through supported controls. |

Distinguish allowance exhaustion, authentication errors, transient rate limits,
network failure and tool failure. Reset estimates and unknown quota do not justify
repeated admission. A service's stopped state remains independent of these modes.
This procedure assumes active Morgan/cloud coordination; it installs no unattended
wake-up or recurring automation. Reconstruct a replacement Morgan from durable
facts with a new fenced coordinator epoch. If both reasoning routes are unavailable,
preserve state and continue only already-authorized deterministic work.

The local Foo ceiling is 64 Codex sessions, reduced by actual admission and shared
CPU/RAM/disk/I/O/process headroom. MCP jobs and local sessions consume that same
budget. Preserve disjoint writers and relevant project holds, including Foo-only
issues. Increase useful concurrency gradually; a ceiling is not a utilization goal.

## 4. Review, publish and preserve evidence

Independently review the exact candidate and required checks under the project's
existing acceptance rules. Identify affected producers/consumers as unaffected,
requiring a current update or unknown with an owner and next check. Keep the sole
integration writer and exact base/head/tree/output bindings. Coding workers cannot
accept their own integration; source integration and product deployment remain
separate outcomes.

Validate the current claim immediately at each supported effect boundary, persist
intent before an effect and read back its destination. After response loss inspect
the original operation and target; do not create a new identity or re-merge source
to repair failed status metadata. Preserve typed milestone/outcome contracts only
for actual native records using them; never fabricate a native execution receipt
for cloud work.

Route durable issue decisions, owner changes, source, material checks/review,
blockers and next action to the existing native Linear record. Keep detailed
private receipts/checkpoints in their existing custody. Follow
[memory routing](agents/memory-routing.md); do not create a parallel status store
or copy sensitive project history into shared memory. Report known verified
events promptly and distinguish publication, merge, installation, acknowledgment
and actual useful execution.

## 5. Continue and qualify the controlled start

After each accepted finite unit, continue the next eligible issue within current
scope through the established controls. Reconcile transfers per issue rather than
waiting for every cloud unit or stealing active claims. Accepted work is not replayed.

For this restoration programme, complete the selected ledger-to-native-Linear
reconciliation and demonstrate intake/claims, execution, independent review,
publication and refill before the later controlled full-token start. Preserve
project-specific readiness and protected-action holds without making unrelated
product checks a global foundation gate. Documentation/source tests establish
neither service activation nor BAU. Use only verified supported controls for an
actually authorized transition and independently observe the resulting state.
