# White-room validation v1

This is executable, read-only **preflight comparison**, not a new scheduler,
lease manager, auto-installer, or service recovery mechanism. It implements a
bounded first part of UIF-247 without waiting for portfolio reconciliation.
It does not claim complete dependency discovery or deployed failover.
The root AGENTS.md remains authoritative; no existing custody ledger is replaced.

## Language and tooling

Python 3.12 standard library matches the observed delivery environment and avoids
new packages, account grants, model calls and shell dependencies in validation.
Git is needed only for optional local inventory and its isolated fixture test.
No credentials are accepted, saved or forwarded. No CI workflow, scheduler or
service unit is added. Run on each executor independently, using its own files.

```
python3 -m unittest discover -s tests/white_room -v
python3 tools/white_room/inventory.py /absolute/checkout
python3 tools/white_room/check.py --contract config/white-room.json \
  --evidence /private/observations.json --profile cloud --project uif
```

Exit 0 means the **supplied** evidence passed comparison; 1 means failed/unknown
preflight, and 2 means invalid input or an inventory error. `execution_authorized`
and `runtime_readiness_verified` are always false. A valid comparison cannot
acquire a lease, fence an old process, authenticate evidence, grant access or
prevent a concurrent writer. The existing authenticated harness must do those
steps atomically and recheck current state at admission. Do not wire this command's
exit code directly to dispatch, publication or service start.

## Declaration and observation contract

`config/white-room.json` distinguishes Foo-only prerequisites from isolated cloud
requirements and names exact discovered Windmill interfaces. Null expectations,
unqualified repository mappings and `discovery_complete: false` deliberately
prevent a readiness pass. Fill them only through reviewed, project-specific
source discovery. This is an incomplete inventory, not permission to redefine
requirements to match the running host. A cloud task requiring Foo data or
Windmill must explicitly include those dependencies in its reviewed cloud profile.
An unrelated cloud task does not inherit them merely because Foo is unavailable.

Every observation is bound to SHA-256 of canonical JSON of the entire declaration
(`check.digest`), target project/profile, timestamp, source reference, state and
exact observed configuration. States are compliant, drifted, failed, blocked,
unknown and not-applicable. Required dependencies cannot be waived as
not-applicable. Changed MCP aliases are drift, not fuzzy matches. Duplicate
observation identities, missing proof, future timestamps and expired observations
fail closed. Freshness defaults to each requirement's declared 300-second window;
this is an observation validity bound, not a scheduler interval or live lease.

Evidence is operator-supplied input, not an authenticated attestation. Keep real
observations private, exclude secret values/raw datasets, and reference original
operation receipts without copying sensitive command output. `source` is a
pointer for an authorized reviewer, not a URL fetched or trusted by this program.
Use `tests/white_room/test_check.py` for a complete **synthetic** input example.

Recovery input requires current accepted and selected commit, repository, issue,
owner and epoch; fresh remote reconstruction, unexpired ownership, isolated
workspace, preserved recoverable work, prior-owner fencing, explicit supersession,
no uncertain operations, checkpoint disposition and verified remote checkpoint.
A resumed checkpoint additionally binds issue/commit/owner/epoch, compatibility
and its original receipt. A missing checkpoint needs verified absence, not silence.
These are necessary comparisons, not proof that supplied assertions are true.

## Non-destructive sequence

1. Discover current remote GitHub and Linear, existing ownership and original
   operation receipts. Do not infer remote freshness from local tracking refs.
2. Run inventory as the checkout owner, never root merely because transport is
   root. It reads tracked filenames, local refs, HEAD and branch; dirtiness stays explicitly unknown;
   never reads file bodies or remotes containing credentials, and never
   fetches/resets/cleans. Status/diff are excluded because Git filters can execute
   arbitrary commands; hooks, fsmonitor and inherited Git overrides are disabled.
   Detached or partial/promisor checkouts return an explicit inventory error;
   lazy fetch is disabled and promisor configuration is refused for portability. This bounded
   discovery helper does not inspect all worktrees or all external dependencies.
3. Resolve project-specific declarations and all undiscovered entries. Obtain
   permitted live health/configuration observations. Compare deployed script
   source and schema, not only Windmill names or misleading summary labels.
4. Reconcile uncertain effects under their original IDs. Retain dirty/local-only
   work; choose a separate clean task checkout rather than rewriting it.
5. Compare evidence with this tool. Carry precise gaps to the current owner.
   Only the existing harness may admit or resume after its own authoritative
   ownership and effect reconciliation. Do not resume historical workers here.

## Recovery test boundary

Unit scenarios cover unavailable Foo while cloud continues, renamed Windmill,
source supersession, owner/epoch conflicts, lost worker/coordinator fencing,
uncertain original operations, unpublished work, dirty-work preservation,
checkpoint schema mismatch and bound resume. These are pure synthetic comparisons.
An isolated temporary Git fixture verifies inventory preserves file bytes, reports unknown dirtiness, and
never runs configured fsmonitor/clean filters or returns file contents. Running these tests on Foo proves interpreter and
logic compatibility on Foo, **not** actual reboot recovery or distributed fencing.

Remaining implementation: authenticated evidence adapters for each project;
complete managed dependency declarations and effective bindings; durable shared
ownership/CAS fencing; checkpoint migration and supersession consumers; integrated
harness admission; independent cloud worker/coordinator reconstruction; qualified
actual Foo/cloud handoffs and crash/termination recovery. No disruptive service
experiment is authorized or performed by these tools.
