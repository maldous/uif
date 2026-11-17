"""Thin, explicitly installed seams for native Stokowski's existing lifecycle.

The observed native launcher remains unchanged. An integration owner must wire
dispatch BEFORE native _dispatch/_run_worker metadata, wrap every metadata and
publication effect, and route _schedule_retry through this boundary. Merely
importing this module, running tests or constructing it does not fence upstream.
No polling loop, selector, queue, ledger coordinator or provider client is added.
"""

from dataclasses import dataclass
from hashlib import sha256
import inspect
from uuid import UUID

from .handoff_store import Hold, digest, require
from .quota_handoff import FRESH_SECONDS, SHA, LinearKey, Registry, source_binding, token


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


@dataclass(frozen=True)
class PoolRefusal:
    """Trusted Pool adapter's observed refusal: no reservation or child exists."""
    key: LinearKey
    generation: int
    operation_id: str
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
            # Pool reservation can outlast provider evidence or allow a stop
            # observation to commit. Like the awaited boundary, recheck the
            # admitted intent immediately before launching the native child.
            self.registry.recheck_effect(
                principal=profile.principal, actor=profile.actor, key=profile.key,
                generation=profile.generation, operation_id=operation_id,
                source=profile.source, reasoning=True)
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


async def _await(value):
    return await value if inspect.isawaitable(value) else value


class AsyncNativeQuotaBoundary(NativeQuotaBoundary):
    """Awaited caller seams, not a drop-in replacement for sync _dispatch.

    The owner must await dispatch_stage from both native _tick and
    _handle_retry before their metadata/worker launch, and fence worker prologue
    and each later write. Agent stages use the existing durable Pool adapter;
    gate stages are deterministic. A gate callback receives permit=None.
    Registry fencing is deliberately non-reentrant: installed callsites must
    not nest another effect inside this effect. Scheduled workers must wait for
    their dispatch registration to finish before entering their own prologue.
    A trusted PoolRefusal proves no reservation/child was created and permits
    release of only the native ephemeral slot. Ambiguous Pool exceptions retain
    the original intent and slot for bounded original-operation reconciliation.
    """

    async def dispatch(self, issue, *, operation_id, dispatch_native,
                       release_ephemeral_slot=None):
        return await self.dispatch_stage(issue, stage_type="agent", operation_id=operation_id,
                                         dispatch_native=dispatch_native,
                                         release_ephemeral_slot=release_ephemeral_slot)

    async def dispatch_stage(self, issue, *, stage_type, operation_id, dispatch_native,
                             release_ephemeral_slot=None):
        entered = False
        try:
            require(stage_type in {"agent", "gate"} and callable(dispatch_native), "native_stage_binding")
            require(release_ephemeral_slot is None or callable(release_ephemeral_slot), "native_slot_release_adapter")
            profile, _ = self._profile(issue)
            token(operation_id)

            async def effect():
                nonlocal entered
                entered = True
                permit = None
                if stage_type == "agent":
                    permit = await _await(self.pool_admit(profile, operation_id))
                    if isinstance(permit, PoolRefusal):
                        require(permit.key == profile.key and permit.generation == profile.generation
                                and permit.operation_id == operation_id
                                and isinstance(permit.receipt_sha256, str) and SHA.fullmatch(permit.receipt_sha256),
                                "native_pool_refusal_binding")
                        self.registry._fresh(permit.observed_at)
                        if release_ephemeral_slot is not None:
                            await _await(release_ephemeral_slot(issue))
                        return dict(status="denied", receipt_sha256=permit.receipt_sha256)
                    require(isinstance(permit, PoolPermit) and permit.key == profile.key
                            and permit.generation == profile.generation and permit.operation_id == operation_id
                            and isinstance(permit.receipt_sha256, str) and SHA.fullmatch(permit.receipt_sha256),
                            "native_pool_binding")
                    token(permit.lease_id)
                    self.registry._fresh(permit.observed_at)
                self.registry.recheck_effect(
                    principal=profile.principal, actor=profile.actor, key=profile.key,
                    generation=profile.generation, operation_id=operation_id,
                    source=profile.source, reasoning=stage_type == "agent")
                returned = await _await(dispatch_native(issue, permit))
                return await _await(self.outcome_read(profile, operation_id, permit, returned))

            return await self.registry.perform_effect_async(
                principal=profile.principal, actor=profile.actor, key=profile.key, generation=profile.generation,
                operation_id=operation_id,
                payload_sha256=digest(dict(kind="native-dispatch", stage_type=stage_type, source=profile.source)),
                effect=effect, reasoning=stage_type == "agent")
        except Hold:
            # Native _tick/_handle_retry already reserved an ephemeral slot.
            # Compensate only a guard refusal before any Pool/dispatch action.
            # This callback must be the owner's exact native _release_slot for
            # this issue; it never releases a durable lease or asserts cleanup.
            if not entered and release_ephemeral_slot is not None:
                await _await(release_ephemeral_slot(issue))
            raise

    async def guarded_effect(self, issue, *, operation_id, payload_sha256, effect):
        profile, _ = self._profile(issue)
        return await self.registry.perform_effect_async(
            principal=profile.principal, actor=profile.actor, key=profile.key, generation=profile.generation,
            operation_id=operation_id, payload_sha256=payload_sha256, effect=effect)

    async def observe_provider(self, issue, *, request_id):
        profile, _ = self._profile(issue)
        observed = await _await(self.provider_read(profile))
        require(isinstance(observed, dict) and set(observed) == {
            "classification", "observed_at", "sequence", "evidence_sha256"}, "native_provider_shape")
        return self.registry.invoke(principal=profile.principal, actor=profile.actor,
                                    request=dict(action="provider_observation", request_id=request_id, **observed))

    async def schedule_retry(self, issue, *, attempt, delay_ms, schedule_native):
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

        async def effect():
            _, current = self._profile(issue)
            latest = current["provider"]
            reason = None
            if current["stopped"]:
                reason = "service-stopped"
            elif latest is None or latest["classification"] in {"hard-quota", "auth-failure", "unknown"}:
                reason = "provider-unavailable"
            else:
                try:
                    self.registry._fresh(latest["observed_at"], FRESH_SECONDS)
                except Hold:
                    reason = "provider-evidence-stale"
            if reason is not None:
                # The guarded adapter has not called schedule_native. This
                # deterministic no-start decision is the terminal refusal for
                # this original timer operation, not a provider/model result.
                return dict(status="denied", receipt_sha256=digest(dict(
                    schema="uif-native-retry-refusal/1", key=profile.key.identity,
                    generation=profile.generation, attempt=attempt, delay_ms=delay_ms,
                    provider=latest, stopped=current["stopped"], reason=reason)))
            return await _await(schedule_native(issue, attempt, delay_ms))

        return await self.guarded_effect(issue, operation_id=f"retry-{attempt}",
                                         payload_sha256=digest(dict(attempt=attempt, delay_ms=delay_ms)), effect=effect)


@dataclass(frozen=True)
class LinearWriteBinding:
    key: LinearKey
    generation: int
    operation_id: str
    method: str
    payload_sha256: str


@dataclass(frozen=True)
class LinearWriteObservation:
    binding: LinearWriteBinding
    status: str
    receipt_sha256: str
    value: object


class AsyncLinearClient:
    """Issue-bound proxy for the explicit public Stokowski 339cbf4 writes.

    binding_for(profile, method, payload_sha256) supplies an original operation
    identity from the existing stage/attempt journal, never from a retry counter
    minted after uncertainty. observe(profile,binding,payload,returned,mode)
    reads the actual destination/original receipt and returns its typed result.
    It must support mode='observe' for a replay of an already-terminal effect;
    raw client callback success alone is never sufficient. Payload stays private.

    This is a worker/issue client. It is not a drop-in project-wide poll client;
    project discovery remains with the native controller's existing read client.
    No __getattr__ or arbitrary GraphQL/mutation passthrough is exposed.
    """

    def __init__(self, boundary, issue, client, *, team_id, binding_for, observe):
        require(isinstance(boundary, AsyncNativeQuotaBoundary) and callable(binding_for)
                and callable(observe), "linear_adapters_required")
        try:
            require(isinstance(team_id, str) and str(UUID(team_id)) == team_id, "reviewed_team_uuid_required")
        except (ValueError, AttributeError):
            raise Hold("reviewed_team_uuid_required") from None
        self.boundary, self.issue, self.client = boundary, issue, client
        self.team_id, self.binding_for, self.observe = team_id, binding_for, observe
        self.boundary._profile(issue)

    def _issue(self, issue_id):
        require(issue_id == self.issue.id, "linear_issue_scope")

    @staticmethod
    def _value(method, observation):
        value = observation.value
        require(observation.status in {"verified", "failed", "denied"}, "linear_observation_status")
        if method in {"post_comment", "update_issue_state", "add_label"}:
            require(type(value) is bool and (not value or observation.status == "verified"), "linear_result_shape")
        elif observation.status != "verified":
            require(value is None, "linear_result_shape")
        elif method == "create_label":
            try:
                require(isinstance(value, str) and str(UUID(value)) == value, "linear_result_shape")
            except (ValueError, AttributeError):
                raise Hold("linear_result_shape") from None
        elif method == "upload_file":
            require(isinstance(value, str) and value.startswith("https://") and len(value) <= 4096,
                    "linear_result_shape")
        return value

    async def _write(self, method, payload, callback):
        profile, _ = self.boundary._profile(self.issue)
        payload_sha256 = digest(payload)
        binding = await _await(self.binding_for(profile, method, payload_sha256))
        require(isinstance(binding, LinearWriteBinding) and binding.key == profile.key
                and binding.generation == profile.generation and binding.method == method
                and binding.payload_sha256 == payload_sha256, "linear_operation_binding")
        token(binding.operation_id)
        observed = None

        async def observe(returned, mode):
            result = await _await(self.observe(profile, binding, payload, returned, mode))
            require(isinstance(result, LinearWriteObservation) and result.binding == binding
                    and isinstance(result.receipt_sha256, str) and SHA.fullmatch(result.receipt_sha256),
                    "linear_destination_observation")
            self._value(method, result)
            return result

        async def effect():
            nonlocal observed
            returned = await callback()
            observed = await observe(returned, "invoke")
            return dict(status=observed.status, receipt_sha256=observed.receipt_sha256)

        operation = await self.boundary.guarded_effect(
            self.issue, operation_id=binding.operation_id, payload_sha256=payload_sha256, effect=effect)
        if observed is None:
            observed = await observe(None, "observe")
        require(operation["status"] == observed.status
                and operation["receipt_sha256"] == observed.receipt_sha256, "linear_original_receipt_drift")
        return self._value(method, observed)

    async def post_comment(self, issue_id, body):
        self._issue(issue_id)
        return await self._write("post_comment", dict(issue_id=issue_id, body=body),
                                 lambda: self.client.post_comment(issue_id, body))

    async def update_issue_state(self, issue_id, state_name):
        self._issue(issue_id)
        return await self._write("update_issue_state", dict(issue_id=issue_id, state_name=state_name),
                                 lambda: self.client.update_issue_state(issue_id, state_name))

    async def add_label(self, issue_id, label_id):
        self._issue(issue_id)
        return await self._write("add_label", dict(issue_id=issue_id, label_id=label_id),
                                 lambda: self.client.add_label(issue_id, label_id))

    async def create_label(self, team_id, name, color=None):
        require(team_id == self.team_id, "linear_team_scope")
        return await self._write("create_label", dict(team_id=team_id, name=name, color=color),
                                 lambda: self.client.create_label(team_id, name, color))

    async def upload_file(self, filename, content_type, data):
        require(isinstance(data, bytes), "linear_upload_bytes")
        payload = dict(issue_id=self.issue.id, filename=filename, content_type=content_type,
                       size=len(data), sha256=sha256(data).hexdigest())
        return await self._write("upload_file", payload,
                                 lambda: self.client.upload_file(filename, content_type, data))

    async def fetch_comments(self, issue_id):
        self._issue(issue_id)
        return await self.client.fetch_comments(issue_id)

    async def fetch_issue_states_by_ids(self, ids):
        require(isinstance(ids, list) and ids == [self.issue.id], "linear_issue_scope")
        return await self.client.fetch_issue_states_by_ids(ids)

    async def fetch_team_labels(self, issue_id):
        self._issue(issue_id)
        return await self.client.fetch_team_labels(issue_id)
