# Informational owner labels and native context

The approved single-select `Coordinating owner` labels describe the fixed
coordinating project or Morgan, not a person assignee, executor, claim, epoch,
reviewer or publisher grant. Preserve unrelated labels and real assignee/delegate
fields. The seven exact values are `Owner: UIF`, `Owner: Cloud`, `Owner: ANQR`,
`Owner: OffMenu`, `Owner: CircularQuay`, `Owner: CIX` and `Owner: Morgan`.

The installed native orchestrator source
`7bea80afc96650b93fdaab2ae43df4cb86d0172c4a759371e84405660dd57dea`
has no routing rule for these informational values, but its `_management_context`
still hashes every label except existing classification/confidence labels.
Thus routing neutrality did not establish context neutrality. Adding these
labels may make an old gate's cached context stale; disappearance from a live
gate panel is not acceptance, original settlement or a custody release.

`tools/delivery/owner_label_context.py` prepares a single exact source correction:
union only those seven values into the existing machine-label exclusion inside
`_management_context`. It preserves all other source bytes. Unknown `Owner:`
names, case variants, meaningful labels, ordinary comments, priority,
dependencies, state, native custody and scope retain their existing behavior.
It does not weaken `human_context`, original review authentication or effects.

The preparer requires the full pinned bounded source and original expression.
Its default is a metadata-only plan. Optional `--emit-candidate` creates a NEW
private file with exclusive/no-follow creation and fsync; it never replaces
installed code, invokes a module/worker, restarts a service, edits Linear,
changes labels, clears a gate or transfers a claim. Wrong/changed source refuses.

```sh
python3 tools/delivery/owner_label_context.py --source "$PINNED_NATIVE_SOURCE"
```

Source review, focused synthetic contracts, actual native-function comparison,
candidate bytes, installation and live adoption are separate evidence. The
currently observed native install is a root-owned source snapshot without Git
metadata; this repository preserves the reviewed corrective transformation,
not a fabricated upstream commit. Installation must preserve the original bytes
and its actual writer/mode/custody. Live adoption needs the supported service
transition and original retry/gate/claim recovery, not an unverified restart or
unsupported in-memory injection. Do not replay accepted reviews, rewrite old
machine envelopes, roll back informational labels or infer gate clearance.

The previously used ANQR future-hook pause proof pins the old generic source.
Any later operation requiring that proof must qualify its actual successor
source rather than silently treating the old hash as current. Completed
future-hook applications and source-specific checks retain their original pins.
