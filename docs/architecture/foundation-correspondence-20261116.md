# Foundation requirements correspondence: current develop edition

This finite source-only correspondence serves [UIF-193](https://linear.app/maldous/issue/UIF-193),
[UIF-161](https://linear.app/maldous/issue/UIF-161) and the documentation group
under [UIF-260](https://linear.app/maldous/issue/UIF-260). It follows the existing
[UIF-218 current-edition continuation](https://linear.app/maldous/issue/UIF-218#comment-96de2c09-c899-44e7-88e4-af284b6f6345).
It is not a new intake, an acceptance ledger, a replay of the accepted donor
review or a publication/release decision.

## Exact inputs and what changed

The accepted donor is `c8efa916a4bd98c4b59d24c774ff6510f2bd1a32`, tree
`17ebe814b1d6061aa6412384a455bbb3d1827c69`. Current comparison input is develop
`43fa5e645e1cdb91ec75136a7509b8c88c085ab9`, tree
`3e527ec703921bb8efe9e8087b16e5fdcfdcfbae`. All eight original paths exist in
that develop input, with different blob identities. The table at the end pins
those input bytes; this document and its link/evidence corrections are outputs,
not silently substituted input identities.

Authenticated GitHub readback on 16 November 2026 confirms
`refs/heads/worker/uif161-foundation` points to the accepted donor. The old source
is therefore presently published/readable. That does not identify its publishing
actor/operation, repair either original terminal `commit_refs` failure or prove
that the old branch was integrated.

GitHub comparison from donor to this develop input is **diverged**, ahead 178 /
behind 2, merge base `9b692994a56e63c05fb6efa7d8490f280497efdf`.
The same comparison to main `3ff1773fd65cc59744ca44ef8ccb79c126113ddd`
is diverged, ahead 45 / behind 2, with the same merge base. These are dated source
ancestry observations, not semantic acceptance, an old-effect receipt or a
release. Existing UIF-218's 17:11 UTC observation separately records main's missing
six foundation-document paths; this unit does not reclassify main as delivered.

## Requirements and present source correspondence

“Represented” below means an explicit current source location addresses the
stated requirement. It is not a claim of byte equivalence, complete original
acceptance, installed enforcement or useful runtime operation.

| Donor requirement or constraint | Current source representation | Correspondence and unresolved boundary |
| --- | --- | --- |
| Shared physical/virtual foundation, with product-owned source and acceptance | [README](../../README.md), [ADR](../adr/0001-foundation-repository.md), [project boundaries](../project-integration.md#current-role-and-product-boundaries) | Represented. Products remain self-contained; optional shared orchestration is not a mandatory product build/runtime dependency. |
| Local and MCP execution retain truthful source/origin and common requirements | [Policy](../agents/uif-worker-policy.md), [operating procedure](../operating-procedure.md), [dated Morgan checkpoint](../agents/cloud-owner-process-exercise-2026-11-16.md) | Deliberately evolved: five project roles plus the assigned Cloud infrastructure role; cloud reasoning and deterministic MCP are distinct from optional Foo-local Codex. Do not restore the donor's older fallback-only description as current policy. Actual allowance/admission remains independently verified. |
| Canonical Linear scope, decisions, dependencies, acceptance and continuation | [AGENTS](../../AGENTS.md), [operating procedure](../operating-procedure.md#1-reconstruct-the-current-unit-and-destination), [memory routing](../agents/memory-routing.md) | Represented. Historical README/ledger rows are provenance, not a second queue. No issue-key migration or status/claim change is performed by this correspondence. |
| One bounded owner/writer, original operation and consumer bindings | [Project profile](../project-integration.md#select-and-recover-the-current-per-unit-profile), [policy](../agents/uif-worker-policy.md), [review/publication procedure](../operating-procedure.md#4-review-publish-and-preserve-evidence) | Represented as requirements. Current claims, issuer/epoch, original native receiving and actual effect fences still need their own live evidence; prose is not enforcement. |
| Verify actual host/user, source, rights, resources and dependencies | [Repository/setup checklist](../white-room/repositories-and-setup.md), [alignment skill](../../.agents/skills/uif-environment-alignment/SKILL.md), [Windmill routing](../agents/windmill-workspace.md) | Represented with separate cloud and Foo gates. Historical host paths/root transport do not determine a cloud checkout or authorize root Git/builds. |
| Preserve accepted source, suspended work, uncertainty and first unaccepted stage | [Recovery contract](../white-room/startup-and-recovery.md), [recovery reuse](../white-room/recovery-reuse-20261114.md), [evidence](../evidence.md) | Represented. No original operation, held worktree, historical review or unavailable evidence is replayed, overwritten or declared settled here. |
| Preserve original V1 readers, integrity and private evidence custody | [Architecture](../architecture.md#historical-interface-record-6-november-2026), [evidence](../evidence.md), [legacy receipt limits](../operations/legacy-source-free-receipts.md) | Current prose distinguishes historical inspected interfaces from actual current consumers. Exact original acceptance remains valid at its pin; no schema conversion, new cryptographic requirement or native receipt is fabricated. |
| Private source/data/credentials stay out of publication | [Policy](../agents/uif-worker-policy.md), [memory routing](../agents/memory-routing.md), [publication channels](../operations/uif-publication-destinations.md) | Procedural exclusion is represented. The current `.gitignore` contains Python/build exclusions, not the donor's private-data glob list. Neither version is an access-control or complete secret-prevention boundary. No credential values, protected runtime bodies or raw datasets are inspected or reclassified by this unit. |
| Exact independent review, fixed applicable checks, impact and destination readback | [Operating procedure](../operating-procedure.md#4-review-publish-and-preserve-evidence), [evidence](../evidence.md), [documentation map](../documentation-map.md) | Represented. Reuse the donor's accepted review rather than repeating it; this new current-edition correspondence needs its own bounded review. Product checks and runtime acceptance are separate. |
| Develop integration versus accepted main/release and runtime adoption | [ADR](../adr/0001-foundation-repository.md), [publication channels](../operations/uif-publication-destinations.md) | Represented as different channels. Current branch presence and evolved develop documents do not satisfy the original intended main outcome or authorize promotion. |
| Source-outcome finalizer and actual producer/consumer compatibility | [Architecture qualification boundary](../architecture.md#current-consumer-qualification), [evidence](../evidence.md#current-evidence-boundary), [UIF-161](https://linear.app/maldous/issue/UIF-161) | Still operation-specific and unqualified by this document. Read the actual selected edition/consumer; do not impose the historical completed-native limitation on unrelated current source work or invent native proof for cloud output. |
| Foundation delivery independent of broader BAU proof | [ADR consequences](../adr/0001-foundation-repository.md#consequences), [policy](../agents/uif-worker-policy.md) | Source scope remains independently deliverable. Current policy now requires unattended lifecycle/recovery, while explicitly retaining unimplemented/unqualified adapters. The donor's dated BAU deferral is not restored as a permanent present-day stop or mistaken for fulfilled BAU. |

## Current-edition continuation, not original-effect replay

1. Reuse the accepted donor and later accepted foundation/White Room/documentation
   units. PR76 is already merged; this is not a replacement/cherry-pick of c8 or a
   reason to repeat that unit or its checks.
2. Independently review only this correspondence and its current navigation/
   evidence corrections against the pinned inputs. Update an actual missing
   requirement through the existing owner only when that review identifies a
   concrete source delta, rather than inferring equivalence from path presence.
3. Keep the separate main/release destination decision with UIF-193/161/218 and
   the current publisher. Bind a selected current candidate, applicable check,
   acceptance and actual authority before any future promotion. This document
   neither lifts a release-specific hold nor requests an unchanged old push.
4. Preserve the original failed operation receipts, unknown publishing actor and
   denied-read boundaries as historical facts. Those unknowns affect the old
   claim; they are not a global prerequisite for available current source work.

No runtime/claim/service/security/provider action occurs. Existing native
admission pauses and other projects' custody remain outside this source unit.
The current source requirements are mapped; full original foundation delivery,
current main acceptance, runtime adoption and whole-issue closure remain separate.

## Eight-path immutable source index

Each current blob below belongs to input develop `43fa5e6…`, not this output
commit. Different hashes prove different bytes, not an adverse or favorable
semantic verdict. The full pins permit a later reviewer to inspect exactly the
same sources without searching unavailable history.

| Original path | Accepted donor blob | Current-input blob |
| --- | --- | --- |
| `README.md` | `b9daddfa09aad9363f5f365a6a5a019d17519c46` | `c6578a0982b43721beb81bff2659f78c3dbe79f0` |
| `AGENTS.md` | `3fba47002e59cd6c0cf8afed13139704677dbb7c` | `e16ce48b97ac783e807e9299f03f5456891730cf` |
| `docs/architecture.md` | `c4317c96d6db51a6de9108444f261e79e78aec9b` | `8878de9726106b5f2b8db4a5c214b73f102101d0` |
| `docs/project-integration.md` | `e9e1217566c8c7aa3233bbf1959b2b1c9faf87ad` | `3255f95967f7e0132f79a120bdfaa36b05cee040` |
| `docs/operating-procedure.md` | `bf56a2ad3f19876d16ac24c57037ee6f5bb6b2ab` | `0d2f0cb0027fca15ccd64bd1bd7757a89e9c04b8` |
| `docs/evidence.md` | `580133f3253047c0c5ea0bf3869ffbaddbed2eb6` | `6053740f26e192da15e284172c5e34e564300824` |
| `docs/adr/0001-foundation-repository.md` | `dc463810e5739c26c662648bf31b9543a0e00e46` | `01d1fba1df31f66b68b48e8dcb3ba9a484be7605` |
| `.gitignore` | `0e88d2ef60425cf4c59ea367292ef058b26d1a5c` | `0d2f8d204204e7d059517ae398b42f2db52dc066` |
