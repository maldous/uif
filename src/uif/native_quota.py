"""Thin, explicitly installed seams for native Stokowski's existing lifecycle.

The observed native launcher remains unchanged. An integration owner must wire
dispatch BEFORE native _dispatch/_run_worker metadata, wrap every metadata and
publication effect, and route _schedule_retry through this boundary. Merely
importing this module, running tests or constructing it does not fence upstream.
No polling loop, selector, queue, ledger coordinator or provider client is added.
"""

from dataclasses import dataclass
import inspect

from .handoff_store import Hold, digest, require
from .quota_handoff import FRESH_SECONDS, LinearKey, Registry, source_binding, token


@dataclass(frozen=True)
class NativeProfile:
    key: LinearKey
    principal: str
    actor: str
    generation: int
    source: dict
    max_retry_attempts: int
    max_retry_delay_ms: int


@dataclass(frozen=True)
class PoolPermit:
    key: LinearKey
    generation: int
    operation_id: str
    lease_id: str
    receipt_sha256: str
    observed_at: float


class NativeQuotaBoundary:
    """Inject actual admitted profiles, Pool and destination readers, or refuse.

    pool_admit(profile, operation_id) reserves the REAL existing resource pool;
    outcome_read(profile, operation_id, permit, returned) independently reads the
    admitted operation and returns Registry's {status,receipt_sha256} receipt.
    A verified dispatch receipt means observed dispatch only, never coding,
    source acceptance or final-unit completion. Child cleanup is still verified
    by Registry's handoff reader before any transfer.
    """

    def __init__(self, registry, *, profile_for, pool_admit, outcome_read, provider_read):
        require(isinstance(registry, Registry) and all(callable(value) for value in
                (profile_for, pool_admit, outcome_read, provider_read)), "native_adapters_required")
        self.registry = registry
        self.profile_for = profile_for
        self.pool_admit = pool_admit
        self.outcome_read = outcome_read
        self.provider_read = provider_read

    def _profile(self, issue):
        profile = self.profile_for(issue)
        require(isinstance(profile, NativeProfile) and isinstance(profile.key, LinearKey)
                and getattr(issue, "id", None) == profile.key.issue_id
                and type(profile.generation) is int and profile.generation > 0
                and type(profile.max_retry_attempts) is int and profile.max_retry_attempts > 0
                and type(profile.max_retry_delay_ms) is int and profile.max_retry_delay_ms > 0,
                "native_profile_binding")
        source_binding(profile.source)
        token(profile.actor)
        token(profile.principal)
        state = self.registry.invoke(principal=profile.principal, actor=profile.actor, request={"action": "status"})
        task = state["tasks"].get(profile.key.identity)
        require(task is not None and task["generation"] == profile.generation
                and task["owner"] == profile.actor and task["source"] == profile.source
                and task["lane"] == "local" and task["state"] == "active", "native_claim_drift")
        return profile, state

    def dispatch(self, issue, *, operation_id, dispatch_native):
        """Owner wires this before native dispatch metadata or a child launch."""
        profile, _ = self._profile(issue)
        require(callable(dispatch_native) and not inspect.iscoroutinefunction(dispatch_native),
                "bounded_native_dispatch_adapter_required")
        token(operation_id)

        def effect():
            permit = self.pool_admit(profile, operation_id)
            require(isinstance(permit, PoolPermit) and permit.key == profile.key
                    and permit.generation == profile.generation and permit.operation_id == operation_id,
                    "native_pool_binding")
            token(permit.lease_id)
            self.registry._fresh(permit.observed_at)
            # dispatch_native retains the original Stokowski state machine and
            # its exact supported runtime configuration; no replacement runner.
            returned = dispatch_native(issue, permit)
            require(not inspect.isawaitable(returned), "bounded_native_dispatch_adapter_required")
            return self.outcome_read(profile, operation_id, permit, returned)

        return self.registry.perform_effect(principal=profile.principal, actor=profile.actor,
                                            key=profile.key, generation=profile.generation,
                                            operation_id=operation_id,
                                            payload_sha256=digest(dict(kind="native-dispatch", source=profile.source)),
                                            effect=effect, reasoning=True)

    def guarded_effect(self, issue, *, operation_id, payload_sha256, effect):
        """Wire native state transitions/report writes and publisher callsites."""
        profile, _ = self._profile(issue)
        return self.registry.perform_effect(principal=profile.principal, actor=profile.actor,
                                            key=profile.key, generation=profile.generation,
                                            operation_id=operation_id, payload_sha256=payload_sha256,
                                            effect=effect)

    def observe_provider(self, issue, *, request_id):
        profile, _ = self._profile(issue)
        observed = self.provider_read(profile)
        require(isinstance(observed, dict) and set(observed) == {
            "classification", "observed_at", "sequence", "evidence_sha256"}, "native_provider_shape")
        return self.registry.invoke(principal=profile.principal, actor=profile.actor,
                                    request=dict(action="provider_observation", request_id=request_id, **observed))

    def schedule_retry(self, issue, *, attempt, delay_ms, schedule_native):
        """Keep bounded native backoff; never enqueue a known exhausted route."""
        profile, state = self._profile(issue)
        require(type(attempt) is int and 0 < attempt <= profile.max_retry_attempts
                and type(delay_ms) is int and 0 < delay_ms <= profile.max_retry_delay_ms,
                "native_retry_budget")
        require(callable(schedule_native), "native_retry_adapter_required")
        if state["stopped"]:
            return dict(scheduled=False, reason="service-stopped")
        observed = state["provider"]
        if observed is None or observed["classification"] in {"hard-quota", "auth-failure", "unknown"}:
            return dict(scheduled=False, reason="provider-unavailable")
        try:
            self.registry._fresh(observed["observed_at"], FRESH_SECONDS)
        except Hold:
            return dict(scheduled=False, reason="provider-evidence-stale")
        # A retry timer confers no dispatch permission. dispatch() repeats fresh
        # provider, claim, white-room and real Pool admission before work.
        return self.guarded_effect(
            issue, operation_id=f"retry-{attempt}", payload_sha256=digest(dict(attempt=attempt, delay_ms=delay_ms)),
            effect=lambda: schedule_native(issue, attempt, delay_ms))
