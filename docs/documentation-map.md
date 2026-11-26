# UIF documentation and source-interface map

UIF-260 owns this inventory/navigation unit. It reuses UIF-193, UIF-247,
UIF-249 and UIF-161 rather than replacing their accepted work. Linear remains
the only current intake, owner, dependency and acceptance system. This map and
its [machine-readable inventory](documentation-inventory.json) are frozen source
references, not a second work queue, current claim or operational gate.

## Original inventory edition and later navigation

The input is `maldous/uif` commit
`98e810989148db793d9fe5e352b4be5405e0f0a6`, tree
`b6ebb7a5c404a323a0f493c2966e7adc94da7529`. The inventory accounts for all
40 tracked Markdown/text files at that revision, with path, Git blob, SHA256,
audience, owner, category and disposition. The explicit repository-docs-review
workflow indexed all 40 with no skipped files. Incremental refresh from the
preceding accepted revision reused 39 and updated 1; source-universe digest is
`31cea78dbe18ac214a660ae033201150e84f6d2e92fc5b15300b99c765201a94`.
Indexing proves source coverage, not that every document or runtime is correct.
The original delivery comprised this map, inventory JSON and README pointer;
the inventory does not recursively hash itself or relabel baseline blobs.

Those 40 files remain the inventory's frozen edition, not a count of every later
documentation addition. The accepted maps preserved from documentation composition
`10000a245087c1a13dedfb8a2ada0e91d93cf7ed`, tree
`25f8e6e96c0ea16be7dfe1b0f64c29eb475d7c4d`, are now linked through the bounded
[state-machine index](architecture/state-machines/README.md): ten maps with 39
stable IDs. The original eleven IDs and five fully read source/test files belong
to its dated 134-path primary edition. Seven later maps add 28 IDs at source
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`; they retain separate input/read
boundaries. This navigation update neither extends the old 40-file inventory by
implication nor establishes complete UIF-249/UIF-260 or installed-runtime coverage.

The package metadata declares Python >=3.12, version 2.0.1, no runtime dependencies,
`uif` and `uif.white_room` packages, and the `uif-quota-handoff` entrypoint.
That is a source/build contract, not proof of a released registry package,
installed service, current provider grants or useful unattended operation.

## Canonical navigation and instruction precedence

| Reader or task | Canonical location | Boundary |
| --- | --- | --- |
| Repository worker | [AGENTS](../AGENTS.md) | Entry point; preserve applicable nested/product instructions. Only one AGENTS file exists in this source inventory. |
| Shared execution rules | [Policy](agents/uif-worker-policy.md), [manifest](agents/uif-worker-policy.json), [profiles](agents/environment-profiles.json) | Read the actual version/digest and selected profile. Published prose is not runtime enforcement. |
| Normal BAU service | [Business-as-usual contract](operations/business-as-usual.md) | Unattended Morgan–Stokowski delivery, no paid fallback, recovery and end-to-end acceptance. |
| Current operating model | [Operating procedure](operating-procedure.md), [project integration](project-integration.md) | Native Linear intake, disjoint owners and existing effect custody; no inferred claim transfer. |
| Startup/recovery/transition | [Recovery contract](white-room/startup-and-recovery.md), [White Room guide](white-room/README.md), [remote evidence](white-room/remote-evidence.md), [discovery](white-room/discovery.md) | Compare the selected operation; source-only comparators never authorize execution. |
| Practical operation-scoped readiness | [Alignment skill](../.agents/skills/uif-environment-alignment/SKILL.md), [evidence matrix](../.agents/skills/uif-environment-alignment/references/evidence.md) | Source, guidance, actual auth/tools and original receipts; dependent gaps only, not blanket product compliance. |
| Native supervision | [Supervision skill](../.agents/skills/uif-supervision/SKILL.md), [service interfaces](../.agents/skills/uif-supervision/references/service-interfaces.md) | Verify supported installed controls and preserve explicit pauses. A skill file is not service health. |
| Foo installer and release recovery | [Foo installer workflow](windmill/foo-installer-workflow.md) | Established Windmill carrier; separate verifier, protected application and restart proofs. |
| Deterministic Foo checks | [Workspace](agents/windmill-workspace.md), [exact-branch checks](operations/project-checks-source-branch.md), [Windmill qualification](windmill/qualification.md) | Actual deployed schema/catalog and immutable expected commit; original operation status, not repeat submission. |
| Capability selection | [Skills/plugins](agents/skills-and-plugins.md), [startup template](agents/startup-template.md), [memory routing](agents/memory-routing.md) | File access, discovery, engine invocation and permission are separate observations. |
| Shared architecture | [Architecture](architecture.md), [foundation ADR](adr/0001-foundation-repository.md), [evidence](evidence.md) | Current boundary statements coexist with explicitly dated historical sections. |
| Implicit machines and adapter protocols | [Bounded machine index](architecture/state-machines/README.md), [source coverage account](architecture/state-machines/source-coverage-20261116.json) | Ten source maps and their distinct editions/read scopes; not exhaustive coverage, live state or execution authority. |
| Original foundation versus current edition | [Requirements correspondence](architecture/foundation-correspondence-20261116.md) | Accepted donor, evolved develop source, old operation outcome and main/release acceptance remain separate. |
| Runtime seams and limitations | [Native quota realization](operations/native-quota-realization.md), [continuity decisions](operations/continuity-decisions.md), [drift](operations/foo-stokowski-drift.md) | Code-injected verifiers and actual installed callsites are required. Prototype source is not an off-Foo authority. |
| Retained source-free compatibility | [Legacy receipts](operations/legacy-source-free-receipts.md) | File, native canonical-record and documentary joins differ; no receiving/clearance/Git/runtime authorization. |
| Future-clone corrections | [CIX migration](white-room/cix-source-selection.md), [explicit profiles](white-room/future-clone-profiles.md) | Selected source only; preserve old workspaces, publication intents and claim/admission controls. |

`docs/ledger/*`, `docs/current-source-role-map-*`, dated adoption/owner-alignment
records, the dated process exercise, rollout qualification and recovery-reuse
checkpoints remain provenance. Their filenames, historical pins and accepted
bounded results are not current source selection, another ledger reader or a
reason to repeat accepted work. The inventory classifies each exact file.

## Implemented source interfaces, not installed guarantees

| Source | Public surface and present responsibility | Deliberate limit |
| --- | --- | --- |
| `src/uif/handoff_store.py` | `PrivateStore`, strict wire/decode and digest helpers; private single-host expected-byte persistence | Every client must reach the same host; no distributed lock or host-outage availability. |
| `src/uif/quota_handoff.py` | `Registry`, typed source/context/operation/recovery records | Records claims/intents/mode, not work selection or models. Unadapted executors are not fenced. |
| `src/uif/quota_cli.py` | `main`, packaged `uif-quota-handoff` JSON adapter | Actual OS UID is its principal; no invented RecoveryVerifier, network, installation or dispatch. |
| `src/uif/native_quota.py` | Sync/async quota boundaries and guarded Linear client seams | Requires explicit dispatch/retry/effect integration; importing it does not fence native code. |
| `src/uif/continuity.py` | `ContinuityDecider` and typed probe/capacity/operation evidence | Pure decisions require trusted injected verification; no election, schedule, credential or effect. |
| `src/uif/linear_scope.py` | `native_scope_sha256`, `normalize_issue_scope` | Pure scope projection; does not authenticate the caller's expected digest or grant admission. |
| `src/uif/legacy_receipts.py` | `ArchivePin`, `native_retained_record_id`, `build_legacy_receipt` | Pure bounded receipt normalization; actual tuple authentication and receiver/fence remain separate. |
| `tools/white_room/` packaged as `uif.white_room` | Comparison/inventory, scoped remote evidence and `WhiteRoomVerifier` transitions | Declared observations do not prove current remote permission, ownership or runtime readiness. |
| `tools/agents/` | Policy/reference checking, prefix-preserving policy installation, migration audit | Installation needs selected protected source/preimages and actual readback; source merge is not adoption. |
| `tools/windmill/`, `tools/delivery/` | Declared/offline recreation, selected drift and quota adapters | Mock reconstruction/source checks do not change or qualify the live service. |

The JSON inventory lists every top-level public function/class definition in all eight Python
modules, including the inert package initializer. It is navigation, not a claim
that every method is a stable external API. Read the selected source/docstring
and existing contract tests before using a symbol.

## Gap/deduplication disposition before further edits

| Finding or remaining outcome | Existing owner/issue and next concrete unit | This unit's disposition |
| --- | --- | --- |
| No single current documentation/API navigation index | UIF-260: this exact source inventory, map and thin README pointer | Accepted bounded source/navigation unit integrated through [PR66](https://github.com/maldous/uif/pull/66); broader completeness/usability remains open. |
| Environment-owned skills rebuilt after reboot | Cloud owns shared distribution under UIF-266; UIF-247 owns shared policy/adoption and this project consumes its verified source | Do not copy skills into runtime dependencies or rewrite dated original installation receipts as reconstructed bytes. |
| Unimplemented independent off-Foo authority/scheduling | Existing UIF-247/UIF-120 lifecycle units: select and qualify the actual adapter without fabricating an endpoint/provider | Current policy and source boundaries already say unimplemented/unqualified; no new blanket gate. |
| Original source-free receiving and publisher effects | UIF-120 and original project owners: preserve exact accepted archives/tuples, current custody and supported original status | Receipt compatibility is source evidence, not original effect clearance. Do not repeat content review to repair an adapter. |
| Source/caller/consumer role binding | UIF-140 and UIF-161: inspect actual affected consumer and versioned handoff | Future-hook correction does not retarget sealed original publication intents. |
| Complete implicit state-machine documentation | UIF-249: reuse the accepted core, adapter, White Room, policy, Windmill and inventory maps; reconcile only uncovered scope and interactions | The machine index and coverage account retain partial/unknown dispositions; neither 39 IDs nor this API table completes the broader behavior audit. |
| Actual production destination/release readiness | UIF-268 with UIF-267/UIF-127/UIF-218/UIF-160: current destination, publisher, immutable artifact and original operation | Package version, a build or a declared pipeline is not deployment/release readiness. |
| Cross-project terminology alignment | UIF-260 with fixed peer documentation owners: compare selected public contracts, not implementation identity | Five product roles plus the separate Cloud infrastructure role; no common runtime/release gate. |
| Rendered design/accessibility | UI work remains with its actual surface/owner, including Cloud dashboard work | This repository unit is documentation/source interfaces, with no visual product surface to justify an Impeccable engine run. |

Current capability facts must be recorded per actual environment/operation using
the alignment workflow. Context7 is not presumed installed; provider MCP identity
does not imply CLI authentication or a publication grant. Relevant Python,
format/security skills are selected for their actual work, not installed as a
documentation side effect. Readable reconstructed docs-review files, automatic
catalog discovery and a successful invocation are different facts.

No new security/privacy setting, provider, permission, service, queue or release
gate is introduced. Validate only this delta's source coverage, links and
examples; reuse unchanged accepted qualification. PR66's bounded inventory review remains credited; the full UIF-260 outcome still
needs broader completeness/usability/interface review and genuinely missing
content follow-through.
