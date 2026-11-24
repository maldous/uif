"""Unwired boundary for the one configured cross-host effect authority.

The transport is installed, authenticated and independently qualified by UIF.
No fallback, local mirroring, effect callbacks, actor assertions, retries or
transport selection belong in a request. A matching receipt proves only its
binding at this seam, not linearizability of a particular installation.
"""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import ClassVar

from .handoff_store import Hold, digest, require
from .quota_handoff import LinearKey, Registry, SHA, TERMINAL_OPERATIONS, source_binding, token


MAX_WIRE_SECONDS = (1 << 53) - 1


def wire_seconds(value):
    """UTC epoch seconds, one cross-language safe-integer wire shape."""
    require(type(value) is int and 0 <= value <= MAX_WIRE_SECONDS, "shared_wire_seconds")
    return value


def pinned_sha(value):
    require(type(value) is str and SHA.fullmatch(value), "shared_authority_digest")
    return value


@dataclass(frozen=True)
class AuthorityIdentity:
    authority_id: str
    deployment_sha256: str
    policy_sha256: str
    session_id: str

    def __post_init__(self):
        token(self.authority_id)
        token(self.session_id)
        pinned_sha(self.deployment_sha256)
        pinned_sha(self.policy_sha256)


@dataclass(frozen=True)
class AuthorityBinding:
    identity: AuthorityIdentity
    observation_sha256: str

    def __post_init__(self):
        require(type(self.identity) is AuthorityIdentity, "shared_authority_identity")
        pinned_sha(self.observation_sha256)


@dataclass(frozen=True)
class Scope:
    # The installation maps this immutable ID to the reviewed repo and Linear
    # project; the human-readable SourcePin.repository is not the identity key.
    repository_id: str
    key: LinearKey

    def __post_init__(self):
        token(self.repository_id)
        require(type(self.key) is LinearKey, "shared_authority_scope")


@dataclass(frozen=True)
class SourcePin:
    repository: str
    branch: str
    base: str
    head: str
    tree: str

    def __post_init__(self):
        source_binding(asdict(self))


@dataclass(frozen=True)
class ClaimFence:
    scope: Scope
    source: SourcePin
    owner_session: str
    coordinator_epoch: int
    generation: int
    lease_id: str
    authority_id: str
    deployment_sha256: str
    policy_sha256: str

    def __post_init__(self):
        require(type(self.scope) is Scope and type(self.source) is SourcePin, "shared_claim_scope")
        require(type(self.coordinator_epoch) is int and self.coordinator_epoch > 0
                and type(self.generation) is int and self.generation > 0, "shared_claim_generation")
        token(self.owner_session)
        token(self.lease_id)
        token(self.authority_id)
        pinned_sha(self.deployment_sha256)
        pinned_sha(self.policy_sha256)


@dataclass(frozen=True)
class Lease:
    fence: ClaimFence
    expires_at: int

    def __post_init__(self):
        require(type(self.fence) is ClaimFence, "shared_claim_fence")
        wire_seconds(self.expires_at)


@dataclass(frozen=True)
class ClaimRequest:
    kind: ClassVar[str] = "uif.shared-lease.claim/3"
    authority: AuthorityIdentity
    scope: Scope
    source: SourcePin
    request_id: str
    expected_coordinator_epoch: int
    reconstruction_sha256: str

    def __post_init__(self):
        require(type(self.authority) is AuthorityIdentity, "shared_authority_identity")
        require(type(self.scope) is Scope and type(self.source) is SourcePin, "shared_claim_scope")
        require(type(self.expected_coordinator_epoch) is int and self.expected_coordinator_epoch > 0,
                "shared_claim_epoch")
        token(self.request_id)
        pinned_sha(self.reconstruction_sha256)


@dataclass(frozen=True)
class RenewRequest:
    kind: ClassVar[str] = "uif.shared-lease.renew/3"
    authority: AuthorityIdentity
    current: Lease
    request_id: str

    def __post_init__(self):
        require(type(self.authority) is AuthorityIdentity, "shared_authority_identity")
        require(type(self.current) is Lease, "shared_claim_fence")
        token(self.request_id)


@dataclass(frozen=True)
class HandoffRequest:
    kind: ClassVar[str] = "uif.shared-lease.handoff/3"
    authority: AuthorityIdentity
    current: Lease
    successor_session: str
    request_id: str
    expected_coordinator_epoch: int
    quiesced_sha256: str
    reconstruction_sha256: str

    def __post_init__(self):
        require(type(self.authority) is AuthorityIdentity, "shared_authority_identity")
        require(type(self.current) is Lease, "shared_claim_fence")
        require(token(self.successor_session) != self.current.fence.owner_session, "shared_handoff_successor")
        require(type(self.expected_coordinator_epoch) is int
                and self.expected_coordinator_epoch >= self.current.fence.coordinator_epoch,
                "shared_handoff_epoch")
        token(self.request_id)
        pinned_sha(self.quiesced_sha256)
        pinned_sha(self.reconstruction_sha256)


LEASE_REQUEST_TYPES = (ClaimRequest, RenewRequest, HandoffRequest)
LEASE_REQUEST_KINDS = frozenset(request.kind for request in LEASE_REQUEST_TYPES)


def lease_request_sha256(request: ClaimRequest | RenewRequest | HandoffRequest) -> str:
    """Canonical full intent fingerprint, including kind, holder and proof pins."""
    require(type(request) in LEASE_REQUEST_TYPES, "shared_lease_request")
    return transition_request_sha256(request)


@dataclass(frozen=True)
class LeaseReceipt:
    """Authenticated original transition result, not a current-use grant."""

    request_kind: str
    request_id: str
    request_sha256: str
    lease: Lease

    def __post_init__(self):
        require(type(self.request_kind) is str and self.request_kind in LEASE_REQUEST_KINDS
                and type(self.lease) is Lease, "shared_lease_receipt")
        token(self.request_id)
        pinned_sha(self.request_sha256)


@dataclass(frozen=True)
class Operation:
    fence: ClaimFence
    original_id: str
    payload_sha256: str

    def __post_init__(self):
        require(type(self.fence) is ClaimFence, "shared_claim_fence")
        token(self.original_id)
        pinned_sha(self.payload_sha256)


@dataclass(frozen=True)
class PendingEffect:
    operation: Operation
    dispatch_fence: str

    def __post_init__(self):
        require(type(self.operation) is Operation, "shared_operation_binding")
        token(self.dispatch_fence)


@dataclass(frozen=True)
class TerminalEffect:
    operation: Operation
    status: str
    receipt_sha256: str

    def __post_init__(self):
        require(type(self.operation) is Operation, "shared_operation_binding")
        require(type(self.status) is str and self.status in TERMINAL_OPERATIONS, "shared_terminal_status")
        pinned_sha(self.receipt_sha256)


@dataclass(frozen=True)
class OriginalEffect:
    operation: Operation
    state: str
    terminal: TerminalEffect | None = None

    def __post_init__(self):
        require(type(self.operation) is Operation and type(self.state) is str,
                "shared_operation_binding")
        if self.state == "terminal":
            require(type(self.terminal) is TerminalEffect and self.terminal.operation == self.operation,
                    "shared_terminal_binding")
        else:
            require(self.state in ("pending", "unknown") and self.terminal is None,
                    "shared_original_outcome")


@dataclass(frozen=True)
class Completion:
    fence: ClaimFence
    accepted: bool
    receipt_sha256: str

    def __post_init__(self):
        require(type(self.fence) is ClaimFence and type(self.accepted) is bool, "shared_completion_binding")
        pinned_sha(self.receipt_sha256)


@dataclass(frozen=True)
class ReleaseRequest:
    kind: ClassVar[str] = "uif.shared-lease.release/3"
    authority: AuthorityIdentity
    completion: Completion
    request_id: str

    def __post_init__(self):
        require(type(self.authority) is AuthorityIdentity, "shared_authority_identity")
        require(type(self.completion) is Completion, "shared_completion_binding")
        token(self.request_id)


REPLY_REQUEST_TYPES = (*LEASE_REQUEST_TYPES, ReleaseRequest)
READ_REQUEST_TYPES = (RenewRequest, HandoffRequest, ReleaseRequest, Operation)


def transition_request_sha256(request: ClaimRequest | RenewRequest | HandoffRequest | ReleaseRequest) -> str:
    require(type(request) in REPLY_REQUEST_TYPES, "shared_transition_request")
    return digest({"kind": request.kind, "request": asdict(request)})


@dataclass(frozen=True)
class ReleaseReceipt:
    request_kind: str
    request_id: str
    request_sha256: str
    completion: Completion

    def __post_init__(self):
        require(type(self.request_kind) is str and self.request_kind == ReleaseRequest.kind
                and type(self.completion) is Completion, "shared_release_receipt")
        token(self.request_id)
        pinned_sha(self.request_sha256)


def _realm(value):
    return value.authority_id, value.deployment_sha256, value.policy_sha256


def _reply_binding(request, result):
    require((result.request_kind, result.request_id, result.request_sha256) ==
            (request.kind, request.request_id, transition_request_sha256(request)),
            "shared_lease_request_binding")


def _lease_reply(request, result):
    require(type(result) is LeaseReceipt, "shared_lease_request_binding")
    _reply_binding(request, result)
    lease = result.lease
    fence = lease.fence
    require(_realm(fence) == _realm(request.authority), "shared_fence_authority")
    if type(request) is ClaimRequest:
        require(fence.scope == request.scope and fence.source == request.source
                and fence.coordinator_epoch == request.expected_coordinator_epoch
                and fence.generation == 1, "shared_claim_binding")
        require(fence.owner_session == request.authority.session_id, "shared_claim_owner")
    elif type(request) is RenewRequest:
        require(fence == request.current.fence and lease.expires_at > request.current.expires_at,
                "shared_renewal_binding")
    else:
        current = request.current.fence
        require(fence.scope == current.scope and fence.source == current.source
                and fence.owner_session == request.successor_session and fence.generation == current.generation + 1
                and fence.coordinator_epoch == request.expected_coordinator_epoch
                and fence.lease_id != current.lease_id, "shared_handoff_binding")
    return result


def _release_reply(request, result):
    require(type(result) is ReleaseReceipt, "shared_release_receipt")
    _reply_binding(request, result)
    require(result.completion == request.completion, "shared_completion_binding")
    return result


class SharedAtomicStore(ABC):
    """Authenticated installed port. One authoritative keyspace and clock.

    Compare/commit across peers is the backend's responsibility. Claim/handoff
    serialize by immutable (Linear workspace UUID, issue UUID). A project
    move or changed repository route never creates another claim. Keep full
    current project/repository/source in authorization and receipts; routing
    changes need explicit reconciliation on the same immutable claim. Expiry
    fences new effects, not transfer, receipt observation,
    release or retry. Request IDs must be immutable within authenticated subject
    scope. Begin persists the original intent before returning a dispatch fence.
    A lost response is uncertain; begin must not issue another dispatch on a
    pending replay, including under a different ID. Terminal observation and
    task completion use separately qualified destination readers. Receivers must
    enforce the pending fence and original idempotency key at the actual effect;
    this port's recheck is not by itself an external-resource fence.
    Every request carries the expected authority/session pin, never the
    authentication itself; reject it unless it agrees with the installed
    authenticated transport and current authority policy.
    Lease mutations atomically persist the complete original request fingerprint
    with the issued lease. Authenticate and verify the exact cited evidence,
    compare the expected epoch and (for renew/handoff) the exact prior lease,
    and reject divergent reuse of the request ID. A response must be based on
    the persisted result, not a fresh echo joined to an unrelated lease.
    observe_claim uses that same original full intent and does not grant use.
    An exact old receipt can outlive authority of its lease. begin/recheck and
    the effect receiver still enforce current fencing. An ambiguous renewal,
    handoff or release can be observed through a separately authenticated reader,
    not by replay. Historical observation never reauthorizes an old fence.
    """

    @abstractmethod
    async def claim(self, identity, request: ClaimRequest) -> LeaseReceipt: ...

    @abstractmethod
    async def observe_claim(self, identity, request: ClaimRequest) -> LeaseReceipt | None: ...

    @abstractmethod
    async def renew(self, identity, request: RenewRequest) -> LeaseReceipt: ...

    @abstractmethod
    async def handoff(self, identity, request: HandoffRequest) -> LeaseReceipt: ...

    @abstractmethod
    async def begin(self, identity, operation) -> PendingEffect | TerminalEffect: ...

    @abstractmethod
    async def recheck(self, identity, pending) -> PendingEffect: ...

    @abstractmethod
    async def observe_original(self, identity, operation) -> OriginalEffect: ...

    @abstractmethod
    async def reconcile(self, identity, operation, receipt_sha256) -> TerminalEffect: ...

    @abstractmethod
    async def release(self, identity, request: ReleaseRequest) -> ReleaseReceipt: ...


class SharedEffectAuthority:
    """Client-side exact binding; no authentication or trust from wire fields."""

    def __init__(self, store, identity, verify_binding):
        require(isinstance(store, SharedAtomicStore) and type(identity) is AuthorityIdentity
                and callable(verify_binding), "shared_atomic_authority_unavailable")
        try:
            observed = verify_binding(store, identity)
        except OSError:
            raise Hold("shared_authority_binding_unavailable") from None
        require(type(observed) is AuthorityBinding and observed.identity == identity,
                "shared_authority_binding")
        self._store = store
        self._identity = identity

    async def _once(self, method, *args):
        try:
            return await method(self._identity, *args)
        except OSError:
            # Never replay after a transport failure; inspect original request
            # or effect using the authority's authenticated receipt read path.
            raise Hold("shared_authority_outcome_unknown") from None

    def _bound(self, fence):
        require(type(fence) is ClaimFence, "shared_claim_fence")
        require((fence.authority_id, fence.deployment_sha256, fence.policy_sha256) ==
                (self._identity.authority_id, self._identity.deployment_sha256,
                 self._identity.policy_sha256), "shared_fence_authority")

    def _owns(self, fence):
        self._bound(fence)
        require(fence.owner_session == self._identity.session_id, "shared_claim_owner")

    def _request(self, request, expected):
        require(type(request) is expected, "shared_lease_request")
        require(request.authority == self._identity, "shared_request_authority")

    async def claim(self, request: ClaimRequest) -> LeaseReceipt:
        self._request(request, ClaimRequest)
        return _lease_reply(request, await self._once(self._store.claim, request))

    async def observe_claim(self, request: ClaimRequest) -> LeaseReceipt | None:
        self._request(request, ClaimRequest)
        result = await self._once(self._store.observe_claim, request)
        return None if result is None else _lease_reply(request, result)

    async def renew(self, request: RenewRequest) -> LeaseReceipt:
        self._request(request, RenewRequest)
        self._owns(request.current.fence)
        return _lease_reply(request, await self._once(self._store.renew, request))

    async def handoff(self, request: HandoffRequest) -> LeaseReceipt:
        self._request(request, HandoffRequest)
        self._bound(request.current.fence)
        return _lease_reply(request, await self._once(self._store.handoff, request))

    async def begin(self, operation):
        require(type(operation) is Operation, "shared_operation_binding")
        self._owns(operation.fence)
        result = await self._once(self._store.begin, operation)
        require(type(result) in (PendingEffect, TerminalEffect) and result.operation == operation,
                "shared_operation_binding")
        return result

    async def recheck(self, pending):
        require(type(pending) is PendingEffect, "shared_operation_binding")
        self._owns(pending.operation.fence)
        result = await self._once(self._store.recheck, pending)
        require(type(result) is PendingEffect and result == pending, "shared_dispatch_fence")
        return result

    async def observe_original(self, operation):
        # Original status has no dispatch permit; an unknown/missing result
        # is never evidence that the original call did not occur.
        require(type(operation) is Operation, "shared_operation_binding")
        self._bound(operation.fence)
        result = await self._once(self._store.observe_original, operation)
        require(type(result) is OriginalEffect and result.operation == operation,
                "shared_original_binding")
        return result

    async def reconcile(self, operation, receipt_sha256):
        # A properly scoped supervisor can observe an original effect without
        # becoming its owner; authenticated server policy decides this.
        require(type(operation) is Operation, "shared_operation_binding")
        self._bound(operation.fence)
        result = await self._once(self._store.reconcile, operation, pinned_sha(receipt_sha256))
        require(type(result) is TerminalEffect and result.operation == operation
                and result.receipt_sha256 == receipt_sha256, "shared_terminal_binding")
        return result

    async def release(self, request: ReleaseRequest) -> ReleaseReceipt:
        self._request(request, ReleaseRequest)
        self._owns(request.completion.fence)
        return _release_reply(request, await self._once(self._store.release, request))


@dataclass(frozen=True)
class ObserverIdentity:
    observer_id: str
    deployment_sha256: str
    policy_sha256: str
    session_id: str

    def __post_init__(self):
        token(self.observer_id)
        token(self.session_id)
        pinned_sha(self.deployment_sha256)
        pinned_sha(self.policy_sha256)


@dataclass(frozen=True)
class AuthorityEpoch:
    authority_id: str
    deployment_sha256: str
    policy_sha256: str
    epoch: int

    def __post_init__(self):
        token(self.authority_id)
        pinned_sha(self.deployment_sha256)
        pinned_sha(self.policy_sha256)
        require(type(self.epoch) is int and self.epoch > 0, "shared_observation_epoch")


def observation_original_sha256(original: RenewRequest | HandoffRequest | ReleaseRequest | Operation) -> str:
    require(type(original) in READ_REQUEST_TYPES, "shared_observation_original")
    if type(original) is Operation:
        # No lease expiry representation is present in the V2 effect identity.
        return digest({"kind": "uif.shared-effect.original/1", "operation": asdict(original)})
    return transition_request_sha256(original)


@dataclass(frozen=True)
class ObservationRoute:
    mode: str
    original_authority: AuthorityIdentity
    current: AuthorityEpoch
    retained: AuthorityEpoch
    scope: Scope
    source: SourcePin
    original_sha256: str

    def __post_init__(self):
        require(type(self.original_authority) is AuthorityIdentity and type(self.current) is AuthorityEpoch
                and type(self.retained) is AuthorityEpoch and type(self.scope) is Scope
                and type(self.source) is SourcePin and type(self.mode) is str,
                "shared_observation_route")
        pinned_sha(self.original_sha256)
        require(_realm(self.original_authority) == _realm(self.retained)
                and self.current.authority_id == self.retained.authority_id,
                "shared_observation_route")
        require((self.mode == "current" and self.current == self.retained)
                or (self.mode == "historical" and self.retained.epoch < self.current.epoch),
                "shared_observation_epoch")


@dataclass(frozen=True)
class ObservationBinding:
    identity: ObserverIdentity
    route: ObservationRoute
    valid_from: int
    expires_at: int
    proof_sha256: str

    def __post_init__(self):
        require(type(self.identity) is ObserverIdentity and type(self.route) is ObservationRoute,
                "shared_observation_binding")
        require(self.identity.session_id != self.route.original_authority.session_id,
                "shared_observer_distinct")
        wire_seconds(self.valid_from)
        wire_seconds(self.expires_at)
        require(self.valid_from < self.expires_at, "shared_observer_expiry")
        pinned_sha(self.proof_sha256)


def observation_lookup_sha256(binding, original) -> str:
    require(type(binding) is ObservationBinding, "shared_observation_binding")
    return digest({"kind": "uif.shared-effect.observation/1", "binding": asdict(binding),
                   "original_sha256": observation_original_sha256(original)})


@dataclass(frozen=True)
class ObservationReceipt:
    lookup_sha256: str
    result: LeaseReceipt | ReleaseReceipt | OriginalEffect | None

    def __post_init__(self):
        pinned_sha(self.lookup_sha256)
        require(type(self.result) in (LeaseReceipt, ReleaseReceipt, OriginalEffect, type(None)),
                "shared_observation_result")


class SharedAtomicObserver(ABC):
    """Read-only installed transport with separate authentication.

    The supplied binding is an expected scope pin, never an auth credential.
    Read the original result under the exact authorized route; never derive an
    unavailable result from a new mutation or from the current task projection.
    Older deployment/policy receipts require independently bound archive access.
    """

    @abstractmethod
    async def lookup(self, binding: ObservationBinding,
                     original: RenewRequest | HandoffRequest | ReleaseRequest | Operation) -> ObservationReceipt: ...


class SharedEffectObserver:
    """Exact scoped observation. Offers no authorizer API or dispatch permit."""

    def __init__(self, reader, identity, route, *, verify_binding, clock):
        require(isinstance(reader, SharedAtomicObserver) and not isinstance(reader, SharedAtomicStore)
                and type(identity) is ObserverIdentity and type(route) is ObservationRoute
                and callable(verify_binding) and callable(clock), "shared_observer_unavailable")
        require(identity.session_id != route.original_authority.session_id, "shared_observer_distinct")
        self._reader, self._identity, self._route = reader, identity, route
        self._verify, self._clock = verify_binding, clock
        self._binding = self._verified()

    def _verified(self):
        # Trusted implementation independently observes current issuer, epoch,
        # route, reader identity and scope. A client assertion is not evidence.
        try:
            proof = self._verify(self._reader, self._identity, self._route)
            now = self._clock()
        except OSError:
            raise Hold("shared_observer_binding_unavailable") from None
        require(type(proof) is ObservationBinding and proof.identity == self._identity
                and proof.route == self._route, "shared_observer_binding")
        require(proof.valid_from <= wire_seconds(now) < proof.expires_at, "shared_observer_expiry")
        return proof

    def _original(self, original):
        require(observation_original_sha256(original) == self._route.original_sha256,
                "shared_observation_original")
        if type(original) is Operation:
            fence = original.fence
            require(fence.owner_session == self._route.original_authority.session_id,
                    "shared_observation_subject")
        else:
            require(original.authority == self._route.original_authority, "shared_observation_subject")
            fence = original.completion.fence if type(original) is ReleaseRequest else original.current.fence
            if type(original) in (RenewRequest, ReleaseRequest):
                require(fence.owner_session == original.authority.session_id, "shared_observation_subject")
        epoch = (original.expected_coordinator_epoch if type(original) is HandoffRequest
                 else fence.coordinator_epoch)
        require(fence.scope == self._route.scope and fence.source == self._route.source
                and _realm(fence) == _realm(self._route.retained) and epoch == self._route.retained.epoch,
                "shared_observation_scope")

    async def lookup(self, original) -> ObservationReceipt:
        self._original(original)
        before = self._verified()
        require(before == self._binding, "shared_observer_binding_changed")
        try:
            observed = await self._reader.lookup(before, original)
        except OSError:
            raise Hold("shared_observation_unavailable") from None
        require(self._verified() == before, "shared_observer_binding_changed")
        require(type(observed) is ObservationReceipt
                and observed.lookup_sha256 == observation_lookup_sha256(before, original),
                "shared_observation_receipt_binding")
        value = observed.result
        # None is bound unresolved evidence, never permission to retry.
        if value is None:
            return observed
        if type(original) is Operation:
            require(type(value) is OriginalEffect and value.operation == original,
                    "shared_original_binding")
        elif type(original) is ReleaseRequest:
            _release_reply(original, value)
        else:
            _lease_reply(original, value)
        return observed


def bind_effect_authority(*, mode, local=None, shared_store=None, identity=None, verify_binding=None):
    """Explicit protected choice. Shared absence never becomes Foo-local."""
    if mode == "local":
        require(isinstance(local, Registry) and shared_store is None and identity is None
                and verify_binding is None, "local_authority_configuration")
        return local  # unchanged synchronous/async Registry semantics
    require(mode == "shared" and local is None, "shared_authority_configuration")
    return SharedEffectAuthority(shared_store, identity, verify_binding)
