# Qualified continuity decisions

Source edition: UIF `5a9d08866066c149e06bb7cde713c77d33b7998b`.
The complete [continuity module](../../../src/uif/continuity.py) and its
[16 existing test-source cases](../../../tests/delivery/test_continuity.py)
were read, not executed. The byte identities are in the
[coverage account](source-coverage-20261116.json).

## Responsibility, representation and owner

`ContinuityDecider` computes a new `ContinuityState` and `ContinuityDecision`.
It creates no role, queue, allocator, lease, credential, dispatcher or service.
The eventual existing shared authority must serialize and persist transitions;
this module supplies no operational storage, timer, retry loop or lock. Its
injected verifier is trusted installed code, not a wire-selected callback or a
JSON approval flag. What that verifier does and who may install it are external
integration questions, not qualified by this pure component.

The frozen dataclass state contains policy/schema identity, an externally supplied
`stopped` flag, monotonic evaluation time, health/capacity evidence identity,
failure-chain counters/times, an outage flag and a reconstruction latch. State is
returned; `to_record` is a dictionary projection, not a durable checkpoint write.
`from_record` validates exact fields, policy/schema, booleans, bounded counters,
timestamps/evidence joins and failure/outage history (lines 116–151).

`initial_state` defaults to stopped with no evidence. `step` never changes the
stop flag. An existing authenticated control record must supply any authorized
change outside this component. There is no terminal process lifecycle here:
stopped, unknown and no-capacity outcomes remain states for later evaluations.

## `UIF-CONT-PROBE`: evidence and sustained failure

The state is the accepted probe identity plus `probe_status`, failure history,
`foo_outage_confirmed` and `requires_foo_reconstruction`. `step` starts by
revalidating state and `now`; an invalid record/time raises `Hold` before a
decision. A backwards evaluation time breaks the failure chain and closes this
step's capacity/route decisions, retaining the prior accepted clock/evidence
identity and reconstruction latch.

Health evidence must be the typed `HealthProbe`, fresh and not from the future,
with valid source/sequence/digest/status and an exactly matching `EvidenceReceipt`
from the verifier. Exceptions or a boolean/wrong-scope receipt fail qualification
(lines 186–212). Default TTL is 90 seconds.

| Event/guard | State transition | Boundary |
| --- | --- | --- |
| New, same-source probe with strictly larger sequence **and** observation time | Replace accepted probe identity; update status/history | Sequence and time must both advance. |
| Exactly identical accepted identity and canonical observation digest repeated while fresh | No new failure or time is added | A cached failure plus elapsed wall time cannot confirm an outage. |
| Different source, changed body at same identity, out-of-order/nonidentical probe | Break failure chain; retain accepted evidence identity | Reason `probe-not-monotonic`; no source-spliced history. |
| Missing, stale, invalid or unqualified probe | Break failure chain | Reason `probe-unqualified`; not proof the host failed. |
| New probe after a gap **greater than** the configured maximum, or nonfailed status | Reset prior failure history before processing it | Default maximum gap is 900 seconds; equality is not a gap reset. |
| New failed probe | Start/extend failure chain; saturate count at 2,147,483,647 | Default confirmation needs at least two failures spanning at least 900 seconds. |
| Confirmed outage | Set outage flag and latch reconstruction requirement | Later nonfailure clears the outage chain, not that latch. |

These transitions are in `step` lines 219–261; `_break_failure_chain` is lines
215–217. Host health and inference quota are different inputs. No health probe is
scheduled or network call made here, and a quota observation does not establish
a host outage.

The observation digest is the module's sorted, compact JSON encoding of the
typed sample's dataclass fields, with nonfinite values refused. It is not a raw
input-file hash, a signature or proof that a supplied evidence reference is
authentic; qualifying that reference is the injected reader's responsibility.

## `UIF-CONT-CAPACITY`: evidence identity

Capacity evidence has a separate accepted source/sequence/time/body identity.
Its typed fields distinguish local/cloud inference, authorized route names,
shared authority, cloud lifecycle, deterministic execution and reconstruction.
Shape/freshness and exact verifier-receipt equality are checked before use.

A newer sample must keep the source, advance its sequence and have an observation
time **at least** the previous time. An exactly identical fresh sample can be
reused. Changed content/source at the same identity, stale or malformed evidence
closes capacity for that step; it does not overwrite the accepted identity or
claim the previous authority is presently available (lines 263–278). There is no
automatic reader/source handover, retry or repair of evidence in this component.

## `UIF-CONT-ROUTES`: product of stop, health and capacity

All three route flags start false. They can be evaluated only with qualified
capacity and `stopped == False`; every allowed route also requires shared
authority `available` and that exact route in `authorized_routes`.

| Decision flag | Additional guards in lines 284–295 |
| --- | --- |
| Local inference | Qualified healthy probe, reconstruction satisfied if latched, local inference `available`. |
| Independent cloud | Cloud inference and cloud lifecycle both `available`; host health/reconstruction are not substitutes for these guards. |
| Authorized deterministic | Deterministic execution `available`; this does not provide reasoning capacity. |

Inside the nonstopped qualified-capacity branch, healthy state plus a qualified
reconstruction observation clears the latch. A return to health without that
reconstruction evidence does not. Local `hard-quota` adds its explanatory reason;
it does not transfer ownership or confirm host outage. The returned
`reasoning_unavailable` is `not (local or cloud)`, even if deterministic work is
allowed. `hold_uncertain_foo_operations` is **always true** in the returned
decision (lines 298–302 construct, revalidate and return it; this is not an
inspection of an external service).

These booleans are conditional decisions, not installed claims or effect grants.
Normal task-specific resources, owners, review and original-effect reconciliation
remain external. The state is revalidated before return. Callers must separately
serialize concurrent steps; two calls with the same input can compute competing
next states because this pure object supplies no compare-and-swap/fence.

## Evidence, issue impacts and unknowns

The existing test source covers distinct failures/grace, cache reuse, healthy/
unknown/unqualified interruptions, gaps/stale/future/clockback, replay and
equivocation, source switching, quota versus host failure, cloud requirements,
deterministic-only work, stop preservation, reconstruction, state roundtrip and
policy drift. These are inspected oracles, not newly run tests or live probes.

| Existing issue | Relevant boundary; not whole-issue acceptance |
| --- | --- |
| [UIF-120](https://linear.app/maldous/issue/UIF-120) | Pure continuity decisions need actual qualified authority/lifecycle consumers; no new controller or live claim observation follows. |
| [UIF-130](https://linear.app/maldous/issue/UIF-130) / [UIF-159](https://linear.app/maldous/issue/UIF-159) | Persistence, ordinary cycle/refill and recovery must be proved at real consuming callsites, not inferred from these flags. |
| [UIF-247](https://linear.app/maldous/issue/UIF-247) | Loaded policy/source agreement and service state preservation are distinct from a policy fingerprint or dictionary roundtrip. |
| [UIF-86](https://linear.app/maldous/issue/UIF-86) / [UIF-87](https://linear.app/maldous/issue/UIF-87) | Health/reconstruction model is not an actual environment promotion or recovery exercise. |
| [UIF-219](https://linear.app/maldous/issue/UIF-219) | A baseline comparison must bind actual consumers and evidence readers; no cross-project completeness follows here. |

Current runtime reader identity, actual persistence/fencing, external probe
operations, legacy unknown effects and installed consumption remain unresolved
in this source-only pass. No retry, cancellation, compensation or service
startup/shutdown mechanism is implemented here; trace those in their real
modules rather than inventing transitions in this model.
