# uif
UIF: shared physical and virtual infrastructure, cross-project Linear integration, and full harness / full durable delivery methodology.

## Directory, repository and Linear reconciliation ledger

This ledger indexes recorded reconciliation candidates under `/home/user/src`, including dated directories, `offmenu-worktrees`, and unresolved top-level or relocated items. It lives in this README.

**Inventory status: provisional.** The latest reported filesystem observation is 57 immediate entries from `ls -1d 20*/* offmenu-worktrees/* | wc -l`, run from `/home/user/src`. The corresponding names have not been independently verified for this ledger. That command does not enumerate all top-level, hidden or nested candidates.

The 70 named records below come from retained handoffs that identify unresolved work. They are not a verified current candidate count and are not asserted to be the 57 entries in that command. Current presence, ownership, repository state and Linear status remain unverified for every row. Completed relocations appear only where a handoff records a remaining reconciliation requirement. Historical inventories and derived ancestor directories are not additional active candidates.

### How to use the ledger

- Match each recorded path against a current named inventory before treating it as present or outstanding.
- Record the exact repository, branch and commit, Linear issue, remaining work and final disposition. Keep unknown values explicit.
- Preserve source and consumer bindings when deciding a disposition. A proposed destination is not evidence that a move occurred.
- Keep completed or absent items out of the current outstanding index once their disposition is verified. Do not turn historical evidence or layout containers into new tasks.
- Updating this document does not perform directory cleanup, relocation, repository integration, Linear changes or runtime changes.

### Candidate index

All paths in the tables are relative to `/home/user/src/`. IDs are ledger references, not Linear issue identifiers.

#### Dated-directory candidates

| ID | Recorded path | Repository | Linear | Remaining reconciliation |
|---|---|---|---|---|
| SRC-001 | `2026-11-03/circularquay-dev` | maldous/CircularQuay | UIF-213 | Required common-store compatibility alias until supported saved-root/consumer release. |
| SRC-002 | `2026-11-05/circularquay-e103-frontend-validation` | maldous/CircularQuay | UIF-213 | Required frontend compatibility alias until supported saved-root/consumer release. |
| SRC-003 | `2026-11-05/circularquay-typescript-coverage` | maldous/CircularQuay | UIF-213 | Held R181 boundary. |

#### Explicit outstanding candidates elsewhere in SRC

| ID | Recorded path | Repository | Linear | Remaining reconciliation |
|---|---|---|---|---|
| SRC-004 | `feature/CIX-507-colour-consolidation` | cix-project/cix | CIX-507 | The exact four-root CIX naming guard was stopped before admission; naming reconciliation remains unresolved. |
| SRC-005 | `feature/CIX-507-colour-legacy-source` | maldous/colour-index-compression | CIX-507 | The exact four-root CIX naming guard was stopped before admission; naming reconciliation remains unresolved. |
| SRC-006 | `feature/CIX-508-documents-reconciliation` | UNKNOWN | CIX-508 | The exact four-root CIX naming guard was stopped before admission; naming reconciliation remains unresolved. |
| SRC-007 | `feature/CircularQuay-sonar-41a8da186c71-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-008 | `feature/CircularQuay-sonar-42a3806d68a3-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-009 | `feature/CircularQuay-sonar-4886fc866070-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-010 | `feature/CircularQuay-sonar-4a485258c3a7-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-011 | `feature/CircularQuay-sonar-54fb84a2d3cc-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-012 | `feature/CircularQuay-sonar-8ed18f35be81-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-013 | `feature/CircularQuay-sonar-97b757af8a91-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-014 | `feature/CircularQuay-sonar-e55327d513b0-20261104` | maldous/CircularQuay | UIF-174, UIF-212, UIF-213 | One of the eight CQ sonar naming guards cancelled unsubmitted at terminal quiescence; no new naming qualification completed. |
| SRC-015 | `feature/cix` | UNKNOWN | CIX-508 | The exact four-root CIX naming guard was stopped before admission; naming reconciliation remains unresolved. |
| SRC-016 | `feature/offmenu-all-held-reconciliation-20261028` | maldous/offmenu | UIF-107, UIF-108 | Relocation completed, but final quiescence handoff explicitly leaves the whole-container Linear association and compliant name unresolved. |
| SRC-017 | `feature/offmenu-detached-dc39d0d7-20261108` | maldous/offmenu | UNKNOWN | Relocation completed, but final quiescence handoff explicitly leaves the detached historical develop checkout review issue and compliant name unresolved. |

#### Original offmenu-worktrees candidates

| ID | Recorded path | Repository | Linear | Remaining reconciliation |
|---|---|---|---|---|
| SRC-018 | `offmenu-worktrees/delivery-evidence-inspection` | maldous/offmenu | UIF-106 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-019 | `offmenu-worktrees/pantry-duration-expiry` | maldous/offmenu | UIF-114 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-020 | `offmenu-worktrees/pr136-off-optional-projection-20261106` | maldous/offmenu | UIF-108, UIF-127, UIF-130, UIF-155, UIF-184, UIF-198 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-021 | `offmenu-worktrees/scanner-known-source-facts` | maldous/offmenu | UIF-93 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-022 | `offmenu-worktrees/shared-meal-overflow` | maldous/offmenu | UIF-118 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-023 | `offmenu-worktrees/shopping-positive-deficit` | maldous/offmenu | UIF-115 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-024 | `offmenu-worktrees/source-index-deadline-budget` | maldous/offmenu | UIF-94 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-025 | `offmenu-worktrees/stale-validation-wrapper-recovery` | maldous/offmenu | UIF-117 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-026 | `offmenu-worktrees/stokowski-rebuild-terminal-gate` | maldous/offmenu | UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-027 | `offmenu-worktrees/uif-125-native-langgraph` | maldous/offmenu | UIF-125 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-028 | `offmenu-worktrees/uif-125-native-langgraph/.delivery/uif-125-source/canonical-4c352131-readview` | maldous/offmenu | UIF-120, UIF-130 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-029 | `offmenu-worktrees/uif-126-native-windmill` | maldous/offmenu | UIF-126 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-030 | `offmenu-worktrees/uif-127-native-github` | maldous/offmenu | UIF-127 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-031 | `offmenu-worktrees/uif-128-native-release` | maldous/offmenu | UIF-128 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-032 | `offmenu-worktrees/uif-general-harness-docs-122` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-033 | `offmenu-worktrees/uif127-exact-lf-integration-20261106` | maldous/offmenu | UIF-108, UIF-127, UIF-130, UIF-155, UIF-184, UIF-198 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-034 | `offmenu-worktrees/uif130-dependency-bound-integration-20261106` | maldous/offmenu | UIF-108, UIF-127, UIF-130, UIF-155, UIF-184, UIF-198 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-035 | `offmenu-worktrees/uif130-native-publication-producer` | maldous/offmenu | UIF-130, UIF-216, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-036 | `offmenu-worktrees/uif130-native-refill-terminal-history` | maldous/offmenu | UIF-130, UIF-216, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-037 | `offmenu-worktrees/uif130-native-result-progression` | maldous/offmenu | UIF-130, UIF-216, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-038 | `offmenu-worktrees/uif130-owner-policy-intake` | maldous/offmenu | UIF-130, UIF-216, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-039 | `offmenu-worktrees/uif130-project-aware-live` | maldous/offmenu | UIF-130, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-040 | `offmenu-worktrees/uif130-windmill-boundary-fixture` | maldous/offmenu | UIF-130, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-041 | `offmenu-worktrees/uif130-windmill-resource-receipt` | maldous/offmenu | UIF-130, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-042 | `offmenu-worktrees/uif133-dynamic-integration-admission` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-043 | `offmenu-worktrees/uif133-identity-dispatcher` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-044 | `offmenu-worktrees/uif133-identity-projection` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-045 | `offmenu-worktrees/uif133-native-publication-continuity` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-046 | `offmenu-worktrees/uif133-retained-review-continuation` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-047 | `offmenu-worktrees/uif133-staged-successor` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-048 | `offmenu-worktrees/uif134-native-first-dispatch` | maldous/offmenu | UIF-134, UIF-216, UIF-217 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-049 | `offmenu-worktrees/uif142-javascript-delivery` | maldous/offmenu | UIF-142, UIF-161 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-050 | `offmenu-worktrees/uif159-javascript-refill` | maldous/offmenu | UIF-159 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-051 | `offmenu-worktrees/uif161-prepare-alignment` | maldous/offmenu | UIF-161 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-052 | `offmenu-worktrees/uif161-sealer-cloud-20261106-a7f31` | maldous/offmenu | UIF-161 | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-053 | `offmenu-worktrees/worker-25` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-054 | `offmenu-worktrees/worker00-production-landing-idp-scanner-20261025` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-055 | `offmenu-worktrees/worker00-vocabulary-source-catalogue-batch-20261025` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |
| SRC-056 | `offmenu-worktrees/worker26-openprices-contract--worker-26` | maldous/offmenu | UNKNOWN | Explicitly remaining in the terminal 39-registration handoff; child-specific source/Linear/directory reconciliation not complete. |

#### SRC top-level unresolved candidates

| ID | Recorded path | Repository | Linear | Remaining reconciliation |
|---|---|---|---|---|
| SRC-057 | `.agents` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-058 | `.aws` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-059 | `.claude` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-060 | `.cleanup-custody` | UNKNOWN | UNKNOWN | Preserve current physical layout. Eight recorded UIF-named child groups bind OffMenu history/custody under UIF-217; other project custody remains separate. |
| SRC-061 | `.codex` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-062 | `.freebuff` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-063 | `.git` | UNKNOWN | UNKNOWN | Repository association and disposition remain unverified. Do not assign or retire by name. |
| SRC-064 | `.impeccable` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-065 | `.openclaw` | UNKNOWN | UNKNOWN | Repository association and disposition remain unverified. Do not assign or retire by name. |
| SRC-066 | `.remember` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-067 | `.usf` | UNKNOWN | UNKNOWN | Role, consumer, repository association and disposition remain unverified. Do not infer a role from the directory name. |
| SRC-068 | `archive` | UNKNOWN | UNKNOWN | Archive whole-root disposition/consumer and restore-map qualification remains unresolved; do not count each historical archived child as a current candidate by default. |
| SRC-069 | `offmenu-assessments` | https://github.com/maldous/offmenu; individual nested composition/schema repositories retain separate source bindings | UIF-107, UIF-108, UIF-161 | Final quiescence handoff explicitly says whole-root placement remains incomplete; preserve store and consumer bindings. |
| SRC-070 | `offmenu-data` | https://github.com/maldous/offmenu as owning software; raw datasets remain external data, not committed repository contents | UIF-107, UIF-108 | Final quiescence handoff explicitly says whole-root placement remains incomplete; preserve store and consumer bindings. |

### Recorded branch and disposition evidence

These values describe retained evidence, not a fresh filesystem or repository check. A recorded destination may be a proposal, an existing alias target, or a completed move with separate work remaining. It is not a cleanup instruction. Receipt links, where available, identify the supporting observation.

#### SRC-001 · `2026-11-03/circularquay-dev`

- Recorded branch: `UNKNOWN`; HEAD: `3ecffe7cdd06`.
- Recorded destination: `/home/user/src/feature/CircularQuay-initial-implementation`.
- Destination status: Existing symlink target, not authorization to remove alias.
- Observation: 2026-11-08T09:44:37.564155+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1ba8c-0771-3285-821b-f09a3f8e014f?workspace=offmenu).

#### SRC-002 · `2026-11-05/circularquay-e103-frontend-validation`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-e103-frontend-validation`.
- Destination status: Existing symlink target, not authorization to remove alias.
- Observation: 2026-11-08T09:44:37.564155+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1ba8c-0771-3285-821b-f09a3f8e014f?workspace=offmenu).

#### SRC-003 · `2026-11-05/circularquay-typescript-coverage`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/2026-11-05/circularquay-typescript-coverage`.
- Destination status: No qualified destination.
- Observation: 2026-11-08T09:44:37.564155+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1ba8c-0771-3285-821b-f09a3f8e014f?workspace=offmenu).

#### SRC-004 · `feature/CIX-507-colour-consolidation`

- Recorded branch: `chore/cix507-colour-consolidation`; HEAD: `e7ea2923e9f81d2812ea3cb893edd925e51548ed`.
- Recorded destination: `/home/user/src/feature/cix-CIX-507-colour-consolidation`.
- Destination status: Unexecuted naming proposal only.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `reconciliation-supplement.json`.

#### SRC-005 · `feature/CIX-507-colour-legacy-source`

- Recorded branch: `main`; HEAD: `7904a130fdea8e121f44381cfa0bad383af749d3`.
- Recorded destination: `/home/user/src/feature/cix-CIX-507-colour-legacy-source`.
- Destination status: Unexecuted naming proposal only.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `reconciliation-supplement.json`.

#### SRC-006 · `feature/CIX-508-documents-reconciliation`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/cix-CIX-508-documents-reconciliation`.
- Destination status: Unexecuted naming proposal only.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `reconciliation-supplement.json`.

#### SRC-007 · `feature/CircularQuay-sonar-41a8da186c71-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-41a8da186c71-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-008 · `feature/CircularQuay-sonar-42a3806d68a3-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-42a3806d68a3-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-009 · `feature/CircularQuay-sonar-4886fc866070-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-4886fc866070-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-010 · `feature/CircularQuay-sonar-4a485258c3a7-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-4a485258c3a7-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-011 · `feature/CircularQuay-sonar-54fb84a2d3cc-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-54fb84a2d3cc-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-012 · `feature/CircularQuay-sonar-8ed18f35be81-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-8ed18f35be81-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-013 · `feature/CircularQuay-sonar-97b757af8a91-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-97b757af8a91-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-014 · `feature/CircularQuay-sonar-e55327d513b0-20261104`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/CircularQuay-sonar-e55327d513b0-20261104`.
- Destination status: Unknown; no relocation selected by this ledger.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-015 · `feature/cix`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/cix-CIX-508-review-custody`.
- Destination status: Unexecuted naming proposal only.
- Observation: UNKNOWN. Current state: **not reverified**.
- Source record: `reconciliation-supplement.json`.

#### SRC-016 · `feature/offmenu-all-held-reconciliation-20261028`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/offmenu-all-held-reconciliation-20261028`.
- Destination status: Completed; final issue-based parent name remains unresolved.
- Observation: 2026-11-08T13:19:05.150035+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1bb50-4ba5-51da-5aa6-fc40b9b389c6?workspace=offmenu).

#### SRC-017 · `feature/offmenu-detached-dc39d0d7-20261108`

- Recorded branch: `DETACHED`; HEAD: `dc39d0d7f29f0b0650be374f0e7e2111ec45c4b7`.
- Recorded destination: `/home/user/src/feature/offmenu-detached-dc39d0d7-20261108`.
- Destination status: Completed relocation; not a new proposal.
- Observation: 2026-11-08T13:52:06.419816+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1bb6e-70ea-6f98-adac-5d194e6cfbc4?workspace=offmenu).

#### SRC-018 · `offmenu-worktrees/delivery-evidence-inspection`

- Recorded branch: `refs/heads/feature/delivery-evidence-inspection`; HEAD: `b356a9c82a89d2b8849a83dbbfe6af147f922c7e`.
- Recorded destination: `/home/user/src/feature/offmenu-UIF-106-delivery-evidence-inspection`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-019 · `offmenu-worktrees/pantry-duration-expiry`

- Recorded branch: `refs/heads/bugfix/pantry-duration-expiry`; HEAD: `a0d06b64b92dbab0278c018bc2eee8632ec29fdf`.
- Recorded destination: `/home/user/src/bugfix/offmenu-UIF-114-pantry-duration-expiry`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-020 · `offmenu-worktrees/pr136-off-optional-projection-20261106`

- Recorded branch: `refs/heads/bugfix/off-optional-projection-reconciliation-20261106`; HEAD: `3540b2d4d5f4e96a518a63c832ccd6c3db809686`.
- Recorded destination: `/home/user/src/bugfix/offmenu-UIF-155-optional-projection-reconciliation-20261106`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-021 · `offmenu-worktrees/scanner-known-source-facts`

- Recorded branch: `refs/heads/bugfix/scanner-known-source-facts`; HEAD: `72d1b1e0c9ffaa4cc9da2ba368fe5a6575253ae1`.
- Recorded destination: `/home/user/src/bugfix/offmenu-UIF-93-scanner-known-source-facts`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-022 · `offmenu-worktrees/shared-meal-overflow`

- Recorded branch: `refs/heads/bugfix/shared-meal-overflow`; HEAD: `e857bcf87e1d6cd80b5c17bbaebacfde787d93c4`.
- Recorded destination: `/home/user/src/bugfix/offmenu-UIF-118-shared-meal-overflow`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-023 · `offmenu-worktrees/shopping-positive-deficit`

- Recorded branch: `refs/heads/bugfix/shopping-positive-deficit`; HEAD: `2bb150053faef85afdc89ee92d1052643a085354`.
- Recorded destination: `/home/user/src/bugfix/offmenu-UIF-115-shopping-positive-deficit`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-024 · `offmenu-worktrees/source-index-deadline-budget`

- Recorded branch: `refs/heads/bugfix/source-index-deadline-budget`; HEAD: `d9120c7a539f1427904a4742de64abc003848814`.
- Recorded destination: `/home/user/src/bugfix/offmenu-UIF-94-source-index-deadline-budget`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-025 · `offmenu-worktrees/stale-validation-wrapper-recovery`

- Recorded branch: `refs/heads/bugfix/stale-validation-wrapper-recovery`; HEAD: `6636997b2defec012c777e6387c673a12eaa89ae`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-117-stale-validation-wrapper-recovery`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-026 · `offmenu-worktrees/stokowski-rebuild-terminal-gate`

- Recorded branch: `refs/heads/bugfix/stokowski-rebuild-terminal-gate`; HEAD: `c3b0dff190c9eb3c87e8349d50e4e10fcf64be72`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-stokowski-rebuild-terminal-gate`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-027 · `offmenu-worktrees/uif-125-native-langgraph`

- Recorded branch: `refs/heads/feature/uif-125-native-langgraph`; HEAD: `7d25c9c413ec7d7c18e6284c12173a2474629722`.
- Recorded destination: `/home/user/src/feature/uif-UIF-125-native-langgraph`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-028 · `offmenu-worktrees/uif-125-native-langgraph/.delivery/uif-125-source/canonical-4c352131-readview`

- Recorded branch: `DETACHED`; HEAD: `4c3521312ad80a22717c494c5db450cfe64897ac`.
- Recorded destination: `UNKNOWN`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-029 · `offmenu-worktrees/uif-126-native-windmill`

- Recorded branch: `refs/heads/feature/uif-126-native-windmill`; HEAD: `c70fb522187e630086e940ccfd510d760778396e`.
- Recorded destination: `/home/user/src/feature/uif-UIF-126-native-windmill`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-030 · `offmenu-worktrees/uif-127-native-github`

- Recorded branch: `refs/heads/feature/uif-127-native-github`; HEAD: `731ce20341c053e0c07a4ed439e6e86ec4cfffa6`.
- Recorded destination: `/home/user/src/feature/uif-UIF-127-native-github`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-031 · `offmenu-worktrees/uif-128-native-release`

- Recorded branch: `refs/heads/feature/uif-128-native-release`; HEAD: `a6437a4168d8e6d6cece2220e8e761e673490e26`.
- Recorded destination: `/home/user/src/feature/uif-UIF-128-native-release`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-032 · `offmenu-worktrees/uif-general-harness-docs-122`

- Recorded branch: `refs/heads/feature/uif-general-harness-docs-122`; HEAD: `deb8c111e0bc47996c940beffa3c5c847747beca`.
- Recorded destination: `/home/user/src/feature/offmenu-uif-general-harness-docs-122`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-033 · `offmenu-worktrees/uif127-exact-lf-integration-20261106`

- Recorded branch: `refs/heads/bugfix/uif127-exact-commit-message-projection-20261106`; HEAD: `3540b2d4d5f4e96a518a63c832ccd6c3db809686`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-127-exact-lf-integration-20261106`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-034 · `offmenu-worktrees/uif130-dependency-bound-integration-20261106`

- Recorded branch: `refs/heads/bugfix/uif130-dependency-manifest-bound-20261106`; HEAD: `3540b2d4d5f4e96a518a63c832ccd6c3db809686`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-130-dependency-bound-integration-20261106`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-035 · `offmenu-worktrees/uif130-native-publication-producer`

- Recorded branch: `refs/heads/feature/uif130-native-publication-producer`; HEAD: `a1dfc72f9fd3b24337e31ab96723dba5aea129fb`.
- Recorded destination: `/home/user/src/feature/uif-UIF-217-native-publication-producer`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-036 · `offmenu-worktrees/uif130-native-refill-terminal-history`

- Recorded branch: `refs/heads/bugfix/uif130-native-refill-terminal-history`; HEAD: `f015ca72f0310303fe34ef65ba3491da0c358a67`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-native-refill-terminal-history`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-037 · `offmenu-worktrees/uif130-native-result-progression`

- Recorded branch: `refs/heads/bugfix/uif130-native-result-progression`; HEAD: `95a216faa55939be26774cf14774eb40fce52a81`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-native-result-progression`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-038 · `offmenu-worktrees/uif130-owner-policy-intake`

- Recorded branch: `refs/heads/bugfix/uif130-owner-policy-intake`; HEAD: `728aa02a761bbc42204105d7d7959f5fb2e8bbe7`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-owner-policy-intake`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-039 · `offmenu-worktrees/uif130-project-aware-live`

- Recorded branch: `refs/heads/bugfix/uif130-project-aware-live`; HEAD: `cd8b8bb371a21b47e4897ed7ca83d41635782119`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-project-aware-live`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-040 · `offmenu-worktrees/uif130-windmill-boundary-fixture`

- Recorded branch: `refs/heads/bugfix/uif130-windmill-boundary-fixture`; HEAD: `15f2a22b26ee3816169da7d60555fd686a96cdeb`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-windmill-boundary-fixture`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-041 · `offmenu-worktrees/uif130-windmill-resource-receipt`

- Recorded branch: `refs/heads/feature/uif130-windmill-resource-receipt`; HEAD: `fc4d15eb58389fe788b1a61fcef91405114645bc`.
- Recorded destination: `/home/user/src/feature/uif-UIF-217-windmill-resource-receipt`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-042 · `offmenu-worktrees/uif133-dynamic-integration-admission`

- Recorded branch: `refs/heads/bugfix/uif133-dynamic-integration-admission`; HEAD: `35c73439fc0cdde062a8aa14c5eea5f7608b870d`.
- Recorded destination: `/home/user/src/bugfix/offmenu-uif133-dynamic-integration-admission`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-043 · `offmenu-worktrees/uif133-identity-dispatcher`

- Recorded branch: `refs/heads/fix/uif133-identity-dispatcher`; HEAD: `1248d0832706fb3d8ded1948cfb0fa29a0df85a8`.
- Recorded destination: `/home/user/src/bugfix/offmenu-uif133-identity-dispatcher`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-044 · `offmenu-worktrees/uif133-identity-projection`

- Recorded branch: `refs/heads/bugfix/uif133-identity-source-projection`; HEAD: `036993a464da0d3724764d2d1048cc7e9d001a9f`.
- Recorded destination: `/home/user/src/bugfix/offmenu-uif133-identity-projection`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-045 · `offmenu-worktrees/uif133-native-publication-continuity`

- Recorded branch: `refs/heads/bugfix/uif133-native-publication-continuity`; HEAD: `5745264f78390d99fb09afd2c198ea0cbd23854d`.
- Recorded destination: `/home/user/src/bugfix/offmenu-uif133-native-publication-continuity`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-046 · `offmenu-worktrees/uif133-retained-review-continuation`

- Recorded branch: `refs/heads/bugfix/uif133-retained-review-continuation`; HEAD: `aa433b5f972bed14caa7e0c3ff2d022755668e69`.
- Recorded destination: `/home/user/src/bugfix/offmenu-uif133-retained-review-continuation`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-047 · `offmenu-worktrees/uif133-staged-successor`

- Recorded branch: `refs/heads/bugfix/uif133-staged-successor`; HEAD: `4f40ebaa7de0549e274b94f9368acb8e0fee1159`.
- Recorded destination: `/home/user/src/bugfix/offmenu-uif133-staged-successor`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-048 · `offmenu-worktrees/uif134-native-first-dispatch`

- Recorded branch: `refs/heads/bugfix/uif134-native-first-dispatch`; HEAD: `4fadf7cb4bc9c974d9d38889d158e96674345e3b`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-217-native-first-dispatch`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-049 · `offmenu-worktrees/uif142-javascript-delivery`

- Recorded branch: `refs/heads/feature/uif142-javascript-delivery`; HEAD: `92a44b135ba9750110c77e9d372bb8e8057879c3`.
- Recorded destination: `/home/user/src/feature/uif-UIF-142-javascript-delivery`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-050 · `offmenu-worktrees/uif159-javascript-refill`

- Recorded branch: `refs/heads/feature/uif159-javascript-refill`; HEAD: `59dbf516c277622fd26f6c0095cb544a6745587a`.
- Recorded destination: `/home/user/src/feature/uif-UIF-159-javascript-refill`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-051 · `offmenu-worktrees/uif161-prepare-alignment`

- Recorded branch: `refs/heads/feature/uif161-prepare-alignment`; HEAD: `57c3e8d3ac4c91b7349bcdb65d626617130b3c0a`.
- Recorded destination: `/home/user/src/feature/uif-UIF-161-prepare-alignment`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-052 · `offmenu-worktrees/uif161-sealer-cloud-20261106-a7f31`

- Recorded branch: `refs/heads/bugfix/uif161-sealer-retained-reader-cloud-a7f31`; HEAD: `5c14ac9fede6c4b938a43fe75ed0e098f41fdfa7`.
- Recorded destination: `/home/user/src/bugfix/uif-UIF-161-sealer-cloud-20261106-a7f31`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-053 · `offmenu-worktrees/worker-25`

- Recorded branch: `refs/heads/sync/worker-25/f6cb92ae-20261015`; HEAD: `f6cb92aef52a5cc8193717d7e794556bd84f8b3b`.
- Recorded destination: `UNKNOWN`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-054 · `offmenu-worktrees/worker00-production-landing-idp-scanner-20261025`

- Recorded branch: `refs/heads/codex/production-landing-idp-scanner-20261025`; HEAD: `39f7f8aed5dba2c4b98fdfde32b1f4b30c7e950d`.
- Recorded destination: `UNKNOWN`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-055 · `offmenu-worktrees/worker00-vocabulary-source-catalogue-batch-20261025`

- Recorded branch: `refs/heads/codex/scanner-feedback-layout-20261025`; HEAD: `776a36a2f19be6b8af78f2af736781846b46cecf`.
- Recorded destination: `UNKNOWN`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-056 · `offmenu-worktrees/worker26-openprices-contract--worker-26`

- Recorded branch: `refs/heads/codex/worker26-openprices-contract-20261021`; HEAD: `8098bcbec7a7a95274bc7291cfd2c17de1ad875d`.
- Recorded destination: `UNKNOWN`.
- Destination status: Naming association only; not moved or fresh-qualified.
- Observation: 2026-11-08T14:37:53.329802+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.

#### SRC-057 · `.agents`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-058 · `.aws`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-059 · `.claude`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-060 · `.cleanup-custody`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.

#### SRC-061 · `.codex`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-062 · `.freebuff`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-063 · `.git`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-064 · `.impeccable`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-065 · `.openclaw`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-066 · `.remember`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-067 · `.usf`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-068 · `archive`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `UNKNOWN`.
- Destination status: Historical plan only unless a later completed relocation is recorded.
- Observation: 2026-11-08T09:50:00.812065+00:00. Current state: **not reverified**.
- Source record: `current28-root-disposition-handoff.json`.

#### SRC-069 · `offmenu-assessments`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/offmenu-assessments`.
- Destination status: Planned only; parent relocation not currently qualified.
- Observation: 2026-11-08T14:52:23.542394+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

#### SRC-070 · `offmenu-data`

- Recorded branch: `UNKNOWN`; HEAD: `UNKNOWN`.
- Recorded destination: `/home/user/src/feature/offmenu-data`.
- Destination status: Proposed destination within requested layout; operational cutover is not yet qualified or performed.
- Observation: 2026-11-08T14:52:23.542394+00:00. Current state: **not reverified**.
- Source record: `quiescence-handoff-20261108.json`.
- [Observation receipt](https://windmill.aldous.info/run/01a1afb5-4c79-c422-48bf-6462c9f8bbb0?workspace=offmenu).

### Scope context

Canonical repository roots and layout containers are listed for comparison. Their presence in this section does not make them outstanding reconciliation tasks.

- `/home/user/src/2026-11-03`: dated_parent. Repository: maldous/CircularQuay.
- `/home/user/src/2026-11-05`: dated_parent. Repository: maldous/CircularQuay.
- `/home/user/src/anqr-v3`: repository_root. Repository: maldous/anqr-v3.
- `/home/user/src/bugfix`: shared_git_flow_container.
- `/home/user/src/CircularQuay`: repository_root. Repository: maldous/CircularQuay.
- `/home/user/src/cix`: repository_root. Repository: cix-project/cix.
- `/home/user/src/cix-operations`: repository_root. Repository: cix-project/cix-operations.
- `/home/user/src/feature`: shared_git_flow_container.
- `/home/user/src/hotfix`: shared_git_flow_container.
- `/home/user/src/offmenu`: repository_root. Repository: maldous/offmenu.
- `/home/user/src/uif`: repository_root. Repository: maldous/uif.

### Remaining verification

1. Obtain the current names matching `20*/*` and `offmenu-worktrees/*`, plus the SRC top-level directory list. Treat 57 only as a cross-check for the same filesystem snapshot.
2. Reconcile exact paths and relevant nested candidates with this index. Remove completed or absent historical entries from the current outstanding view, and add current candidates missing from the retained records.
3. Verify repository, Linear, branch, ownership and intended disposition for each remaining item. Keep missing evidence marked as unknown.
4. Verify this README in the canonical `/home/user/src/uif` checkout separately from publication to GitHub. GitHub publication does not establish that the local checkout has been updated.

No separate `TBD.md` is used for this ledger. Canonical Foo checkout synchronization has not been verified by this update.
