# Recovery reuse and integration checkpoint — 14 November 2026

The quota transition builds on accepted white-room work and retained recovery
contracts. It does not restart their implementation, replay accepted effects or
install a replacement runtime. Follow the [startup/recovery contract](startup-and-recovery.md)
and [policy 2.0.0](../agents/uif-worker-policy.md); the current live work queue is
native Linear, with historical ledger identities retained only as provenance.

## Reused source and its limits

| Source | Reuse in this change | Evidence boundary |
| --- | --- | --- |
| Accepted [989cb0d](https://github.com/maldous/uif/commit/989cb0deb28ce09fd8a0740f17260bc278e913f2) | `tools/white_room/check.py` supplies exact declaration/evidence comparison, per-Foo/cloud requirements, freshness, recovery/ownership/checkpoint bindings and explicit unknowns. | Accepted comparison logic; supplied evidence is not authenticated authority and the result never authorizes execution or proves runtime readiness. |
| Accepted [e6ecba5](https://github.com/maldous/uif/commit/e6ecba5e391d86f4464cbcd381bb6aed960f91f4) | `remote_evidence.py` retains bounded GitHub branch/commit/PR/ancestry and native Linear scope/dependency reads. Current source adds explicit reviewed `ProjectScope`. | Original accepted scope is UIF/cloud; extended project/executor bindings require qualified authenticated readers. No generic automatic discovery, writes or dispatch is added. |
| Retained [PR42 candidate e40103c](https://github.com/maldous/uif/tree/e40103c55f749a3a2835f8d14ad7f10ab87a5a3d) | Reuse its durable ownership/fencing, latest-checkpoint, original-effect reconciliation and shared-resource contracts when integrating the existing native lifecycle. | Candidate source is not the observed native-core deployment. Do not start its ledger coordinator, restore a historical selector or replace native Linear intake. |
| Retained [monitor candidate 1fc47c8](https://github.com/maldous/uif/tree/1fc47c88509d915c488a7d15005040986ec0d02b) | Reuse its supported allowance-reader evidence contract: exact executable/account/workspace, app-server metadata, successful observation time and no secrets. | Telemetry is not admission or local quota proof without current qualification. The monitor supplies no Morgan wake-up, issue dispatch or transfer acknowledgment. |
| Current change | `tools.white_room.transition.WhiteRoomVerifier` and `CheckpointRead` compose reviewed scope, authenticated-reader mapping, declared comparison and owner-scoped persistence at the supported ownership boundary. | Source adapter/test coverage only. Actual connector mapping, native dispatch/publisher hooks, installed settings and end-to-end handoff remain separate qualification. |

The two accepted commits are ancestors of the selected UIF develop baseline.
Their acceptance is credited. Later contract changes need focused verification
of changed bindings; they do not justify repeating unchanged completed work.

## Origin, successor and accepted work

Preserve the origin's immutable issue/source, graph/thread/namespace/checkpoint,
code/schema pins, completed/pending stages and original operations. A successor
claim has its own current owner/generation and must bind explicitly to that
reconciled origin through the verified handoff. Do not rewrite historical
checkpoint metadata to make predecessor work appear successor-authored.

Read the registered latest checkpoint tuple rather than selecting an old saved
ID that hides a newer superseding tuple. Verify identity, wire/code/schema/source,
metadata and pending writes. Verified absence requires a read of the registered
thread/namespace; silence or a missing local file is insufficient. Incompatible,
superseded or pending checkpoint state holds its dependent resume.

The stopped-child and original-operation observers must establish terminal effects
before claim acknowledgment. Unknown outcomes remain owned under their original
operation IDs. The receiver revalidates source/claim/reconstruction and resumes
only the first unaccepted stage. Accepted code, validation and review retain their
exact source/evidence scope; a lane change does not rerun them or make an old
acceptance cover changed source.

## Destination reconstruction

1. Read current GitHub and native Linear, claims and original receipts. Inventory
   surviving processes/worktrees, dirty/local-only work and protected custody
   before selecting a separate clean destination checkout.
2. Select the applicable project/executor declaration. Compare actual destination
   installation/configuration and required behavior with that current declaration;
   retain drift, missing access and incomplete discovery as explicit holds.
3. Apply Foo host/service/data/resource requirements to dependent Foo work and the
   lighter isolated cloud requirements to cloud work. Add Foo dependencies to a
   cloud unit only when it actually uses them. A reset estimate or quota reset is
   not a white-room pass.
4. Authenticate remote-reader evidence, reconcile current persistence and prior
   outcomes, then obtain fenced admission through the supported ownership boundary.
   A comparator exit code, saved JSON or a copied hash cannot perform this step.
5. After transfer, verify successor acknowledgment and actual subsequent execution.
   After local recovery, admit independent local issues gradually while cloud-owned
   issues keep their current owner until completion or reconciled failure.

This source contract covers quota exhaustion first. Foo is the ownership authority;
an outage pauses new claims/transfers and dependent effects. Morgan must already
be active for new cloud claims/transfers. No unattended wake-up, replacement
watchdog, new recurring schedule or promise of permanent chat availability is
provided by this change.

## Qualification remains explicit

Tests use isolated stores, synthetic readers/checkpoint savers and deterministic
clocks. They can establish refusal/recovery logic and preservation of original
IDs; they do not demonstrate production ownership, provider reset, stopped live
children, live connector behavior, service installation or autonomous BAU.

Use the [current rollout qualification](../operations/policy-2.0-rollout-qualification-20261114.md)
for observed native-host bindings and the remaining integration boundary. Preserve
[the November 13 drift report](../operations/foo-stokowski-drift.md) as its dated
supplied-evidence account; it is not today's effective runtime selection.
