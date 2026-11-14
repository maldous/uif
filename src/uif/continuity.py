"""Pure continuity decisions for the existing supervised delivery lifecycle.

The eventual shared authority serializes/persists these transitions. This
module creates no role, queue, allocator, lease, API credential or dispatch.
Typed, code-injected evidence verification is mandatory; caller JSON booleans
cannot qualify a probe, capacity or operation outcome.
"""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import math
import re

from .handoff_store import Hold, require

SCHEMA = "uif-continuity-state/1"
SHA = re.compile(r"[a-f0-9]{64}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
ROUTES = {"local-inference", "cloud-inference", "deterministic"}
INFERENCE = {"available", "hard-quota", "unavailable", "unknown"}
AVAILABILITY = {"available", "unavailable", "unknown"}


def _time(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "continuity_timestamp")


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ContinuityPolicy:
    foo_outage_grace_seconds: int = 900
    minimum_failures: int = 2
    evidence_ttl_seconds: int = 90
    maximum_probe_gap_seconds: int = 900

    def __post_init__(self):
        require(type(self.foo_outage_grace_seconds) is int and self.foo_outage_grace_seconds > 0,
                "continuity_outage_grace")
        require(type(self.minimum_failures) is int and self.minimum_failures >= 2,
                "continuity_minimum_failures")
        require(type(self.evidence_ttl_seconds) is int and self.evidence_ttl_seconds > 0
                and type(self.maximum_probe_gap_seconds) is int and self.maximum_probe_gap_seconds > 0,
                "continuity_freshness_policy")

    @property
    def fingerprint(self):
        return _digest(asdict(self))


@dataclass(frozen=True)
class EvidenceReceipt:
    """Returned by an installed qualified reader, not a wire-format authority."""
    kind: str
    source_id: str
    sequence: int
    observed_at: float
    evidence_sha256: str
    observation_sha256: str


@dataclass(frozen=True)
class HealthProbe:
    source_id: str
    sequence: int
    observed_at: float
    evidence_sha256: str
    status: str  # healthy, failed, unknown; this is host health, never model quota


@dataclass(frozen=True)
class CapacityEvidence:
    source_id: str
    sequence: int
    observed_at: float
    evidence_sha256: str
    authorized_routes: tuple
    local_inference: str = "unknown"
    cloud_inference: str = "unknown"
    shared_authority: str = "unknown"
    cloud_lifecycle: str = "unknown"
    deterministic_execution: str = "unknown"
    foo_reconstruction: str = "unknown"  # qualified or unknown; actual white-room receipt


@dataclass(frozen=True)
class ContinuityState:
    schema: str
    policy_sha256: str
    stopped: bool = True
    last_now: float | None = None
    probe_sequence: int = 0
    probe_at: float | None = None
    probe_sha256: str | None = None
    probe_source_id: str | None = None
    probe_observation_sha256: str | None = None
    probe_status: str = "unknown"
    failure_since: float | None = None
    last_failure_at: float | None = None
    failure_count: int = 0
    foo_outage_confirmed: bool = False
    requires_foo_reconstruction: bool = False
    capacity_sequence: int = 0
    capacity_at: float | None = None
    capacity_sha256: str | None = None
    capacity_source_id: str | None = None
    capacity_observation_sha256: str | None = None

    def to_record(self):
        return asdict(self)

    @classmethod
    def from_record(cls, value, policy):
        require(isinstance(value, dict) and set(value) == set(cls.__dataclass_fields__), "continuity_state_fields")
        state = cls(**value)
        require(state.schema == SCHEMA and state.policy_sha256 == policy.fingerprint, "continuity_state_version")
        require(all(type(v) is bool for v in (state.stopped, state.foo_outage_confirmed,
                                             state.requires_foo_reconstruction)), "continuity_state_flags")
        require(state.probe_status in {"healthy", "failed", "unknown"}, "continuity_probe_status")
        for value in (state.last_now, state.probe_at, state.capacity_at, state.failure_since, state.last_failure_at):
            if value is not None:
                _time(value)
        for value in (state.probe_sequence, state.capacity_sequence, state.failure_count):
            require(type(value) is int and 0 <= value <= 2147483647, "continuity_state_counter")
        for sequence, at, pin, source_id, observation in (
                (state.probe_sequence, state.probe_at, state.probe_sha256,
                 state.probe_source_id, state.probe_observation_sha256),
                (state.capacity_sequence, state.capacity_at, state.capacity_sha256,
                 state.capacity_source_id, state.capacity_observation_sha256)):
            require((sequence == 0 and at is None and pin is None and source_id is None and observation is None)
                    or (sequence > 0 and at is not None and isinstance(pin, str) and SHA.fullmatch(pin)
                        and isinstance(source_id, str) and TOKEN.fullmatch(source_id)
                        and isinstance(observation, str) and SHA.fullmatch(observation)),
                    "continuity_state_evidence")
            require(at is None or (state.last_now is not None and at <= state.last_now), "continuity_state_clock")
        if state.failure_count == 0:
            require(state.failure_since is None and state.last_failure_at is None
                    and not state.foo_outage_confirmed, "continuity_failure_history")
        else:
            require(state.failure_since is not None and state.last_failure_at is not None
                    and state.probe_at is not None
                    and state.failure_since <= state.last_failure_at <= state.probe_at
                    and state.probe_status == "failed", "continuity_failure_history")
        if state.foo_outage_confirmed:
            require(state.failure_count >= policy.minimum_failures
                    and state.last_failure_at - state.failure_since >= policy.foo_outage_grace_seconds
                    and state.requires_foo_reconstruction, "continuity_outage_history")
        return state


@dataclass(frozen=True)
class ContinuityDecision:
    local_inference_allowed: bool
    independent_cloud_allowed: bool
    authorized_deterministic_allowed: bool
    foo_outage_confirmed: bool
    requires_foo_reconstruction: bool
    hold_uncertain_foo_operations: bool
    reasoning_unavailable: bool
    reasons: tuple


class ContinuityDecider:
    """Qualify evidence with actual owner readers, then compute decisions only.

    verifier(kind, sample) must independently qualify the immutable evidence
    reference and return matching EvidenceReceipt. It is installed code and
    cannot be selected by a request or reconstructed from a boolean flag.
    The stopped field comes from the existing authenticated control record;
    step never changes it. Returned flags still require normal task-specific
    claims, resources, review and original-effect reconciliation.
    """

    def __init__(self, *, verifier, policy=None):
        self.policy = policy or ContinuityPolicy()
        require(callable(verifier), "continuity_reader_required")
        self.verifier = verifier

    def initial_state(self, *, stopped=True):
        require(type(stopped) is bool, "continuity_stopped_flag")
        return ContinuityState(SCHEMA, self.policy.fingerprint, stopped=stopped)

    def _qualified(self, kind, sample, now):
        expected_type = HealthProbe if kind == "foo-health" else CapacityEvidence
        if not isinstance(sample, expected_type):
            return False
        try:
            require(isinstance(sample.source_id, str) and TOKEN.fullmatch(sample.source_id)
                    and type(sample.sequence) is int and 0 < sample.sequence <= 2147483647
                    and isinstance(sample.evidence_sha256, str) and SHA.fullmatch(sample.evidence_sha256),
                    "continuity_evidence_identity")
            _time(sample.observed_at)
            require(0 <= now - sample.observed_at <= self.policy.evidence_ttl_seconds, "continuity_stale_evidence")
            if kind == "foo-health":
                require(sample.status in {"healthy", "failed", "unknown"}, "continuity_health_shape")
            else:
                require(isinstance(sample.authorized_routes, tuple)
                        and len(sample.authorized_routes) == len(set(sample.authorized_routes))
                        and set(sample.authorized_routes) <= ROUTES
                        and sample.local_inference in INFERENCE and sample.cloud_inference in INFERENCE
                        and all(v in AVAILABILITY for v in (sample.shared_authority, sample.cloud_lifecycle,
                                                          sample.deterministic_execution))
                        and sample.foo_reconstruction in {"qualified", "unknown"}, "continuity_capacity_shape")
            receipt = self.verifier(kind, sample)
            return isinstance(receipt, EvidenceReceipt) and receipt == EvidenceReceipt(
                kind, sample.source_id, sample.sequence, sample.observed_at,
                sample.evidence_sha256, _digest(asdict(sample)))
        except Exception:
            return False

    @staticmethod
    def _break_failure_chain(state):
        return replace(state, probe_status="unknown", failure_since=None, last_failure_at=None,
                       failure_count=0, foo_outage_confirmed=False)

    def step(self, state, *, now, probe=None, capacity=None):
        state = ContinuityState.from_record(state.to_record(), self.policy)
        _time(now)
        reasons = []
        clock_ok = state.last_now is None or now >= state.last_now
        if not clock_ok:
            state = self._break_failure_chain(state)
            reasons.append("clock-backwards")
        else:
            state = replace(state, last_now=now)
        healthy = False
        if clock_ok and self._qualified("foo-health", probe, now):
            same_source = state.probe_source_id is None or probe.source_id == state.probe_source_id
            observation_sha = _digest(asdict(probe))
            newer = same_source and probe.sequence > state.probe_sequence and (
                state.probe_at is None or probe.observed_at > state.probe_at)
            identical = (probe.source_id, probe.sequence, probe.observed_at, probe.evidence_sha256, observation_sha) == (
                state.probe_source_id, state.probe_sequence, state.probe_at,
                state.probe_sha256, state.probe_observation_sha256)
            if newer:
                gap = state.probe_at is not None and probe.observed_at - state.probe_at > self.policy.maximum_probe_gap_seconds
                if gap or probe.status != "failed":
                    state = self._break_failure_chain(state)
                since = state.failure_since
                count = state.failure_count
                if probe.status == "failed":
                    since = probe.observed_at if since is None else since
                    count = min(count + 1, 2147483647)
                confirmed = (probe.status == "failed" and count >= self.policy.minimum_failures
                             and probe.observed_at - since >= self.policy.foo_outage_grace_seconds)
                state = replace(state, probe_sequence=probe.sequence, probe_at=probe.observed_at,
                                probe_sha256=probe.evidence_sha256, probe_status=probe.status,
                                probe_source_id=probe.source_id, probe_observation_sha256=observation_sha,
                                failure_since=since, last_failure_at=probe.observed_at if count else None,
                                failure_count=count, foo_outage_confirmed=confirmed,
                                requires_foo_reconstruction=state.requires_foo_reconstruction or confirmed)
            elif not identical:
                state = self._break_failure_chain(state)
                reasons.append("probe-not-monotonic")
            healthy = (newer or identical) and state.probe_status == "healthy"
        else:
            state = self._break_failure_chain(state)
            reasons.append("probe-unqualified")

        capacity_ok = clock_ok and self._qualified("execution-capacity", capacity, now)
        if capacity_ok:
            same_source = state.capacity_source_id is None or capacity.source_id == state.capacity_source_id
            observation_sha = _digest(asdict(capacity))
            newer = same_source and capacity.sequence > state.capacity_sequence and (
                state.capacity_at is None or capacity.observed_at >= state.capacity_at)
            identical = (capacity.source_id, capacity.sequence, capacity.observed_at, capacity.evidence_sha256, observation_sha) == (
                state.capacity_source_id, state.capacity_sequence, state.capacity_at,
                state.capacity_sha256, state.capacity_observation_sha256)
            capacity_ok = newer or identical
            if newer:
                state = replace(state, capacity_sequence=capacity.sequence, capacity_at=capacity.observed_at,
                                capacity_sha256=capacity.evidence_sha256, capacity_source_id=capacity.source_id,
                                capacity_observation_sha256=observation_sha)
        if not capacity_ok:
            reasons.append("capacity-unqualified")
        if state.foo_outage_confirmed:
            reasons.append("foo-outage-qualified")
        if state.stopped:
            reasons.append("explicit-stop")
        local = cloud = deterministic = False
        if capacity_ok and not state.stopped:
            routes = set(capacity.authorized_routes)
            authority = capacity.shared_authority == "available"
            restored = not state.requires_foo_reconstruction or capacity.foo_reconstruction == "qualified"
            local = (healthy and restored and authority and "local-inference" in routes
                     and capacity.local_inference == "available")
            cloud = (authority and "cloud-inference" in routes and capacity.cloud_inference == "available"
                     and capacity.cloud_lifecycle == "available")
            deterministic = (authority and "deterministic" in routes
                             and capacity.deterministic_execution == "available")
            if healthy and restored and capacity.foo_reconstruction == "qualified":
                state = replace(state, requires_foo_reconstruction=False)
            if capacity.local_inference == "hard-quota":
                reasons.append("foo-inference-quota-exhausted")
        decision = ContinuityDecision(local, cloud, deterministic, state.foo_outage_confirmed,
                                      state.requires_foo_reconstruction, True, not (local or cloud), tuple(reasons))
        # Revalidate the bounded persisted snapshot before returning it.
        ContinuityState.from_record(state.to_record(), self.policy)
        return state, decision
