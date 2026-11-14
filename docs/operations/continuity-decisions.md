# Continuity decision integration

`uif.continuity.ContinuityDecider` provides pure, versioned decision transitions
for the existing delivery lifecycle. It does not install an off-Foo authority,
elect a coordinator, wake a cloud role, create a queue or admit an operation.
The current private Foo registry cannot provide independent Foo-outage BAU.

The shared authority must serialize and persist each returned `ContinuityState`,
using its own authenticated control record for the independent `stopped` field
and its trusted clock for `now`. The policy fingerprint and snapshot schema are
checked before reuse. The snapshot retains bounded counters and latest evidence
pins; original probe/operation receipts stay in their existing evidence custody.

Supply qualified host probes and existing-route capacity through an installed
reader. Its typed receipt must bind the kind, source, sequence, observation time,
original evidence hash and complete observation digest. A JSON flag, callback
returning `True`, healthy process name or available tool is insufficient.

The default Foo-outage decision requires at least two distinct consecutive
qualified failures spanning 900 seconds, with no probe gap over 900 seconds.
An explicitly reviewed positive grace override changes the policy fingerprint.
A timer or replay of the same cached failure never advances that history.
Healthy, unknown, unqualified, stale, backward or interrupted evidence breaks the
failure interval. Foo inference quota exhaustion closes dependent admission
immediately; it does not establish host outage or authorize ownership transfer.
Persisted source identities and complete observation digests reject equivocation
under an existing sequence. Source changes require authority-owned reconciliation;
they cannot splice a new producer's failures into the previous interval.

Independent cloud admission requires qualified existing capacity, shared
authority and actual cloud lifecycle availability. If all authorized inference
routes are unavailable, only already-authorized deterministic work can remain
eligible through its normal resource/claim/effect gates. Unknown Foo operations
retain their original identities and remain held until actual reconciliation.
An explicit stop remains effective across every evidence or quota change.

After a qualified Foo outage, healthy host probes alone do not reopen local
inference. The installed reader must qualify current white-room reconstruction.
Existing cloud claims remain with their owners; this module never moves them.
An admission-eligible decision is still subject to the existing task-specific
source, claim, resource, review and publication controls. It neither cancels nor
declares termination of an already-running process.

Source tests use synthetic readers and evidence. They establish decision
behavior, not a deployed authority, supervised service or cloud continuation.
