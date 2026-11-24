"""Typed in-process entry for the shared issue authority, not a transport.

The installed receiver must supply an independently authenticated connection
context and a bounded live grant/revocation verifier. Build neither from a
request body. This exposes only the current V3 coordinator interface. It
cannot open a listener, select a journal, consume provider sends or grant
historical reader access.
"""
from dataclasses import dataclass
from types import MappingProxyType

from .effect_authority import (
    AuthorityIdentity, ClaimRequest, HandoffRequest, Operation, PendingEffect,
    ReleaseRequest, RenewRequest, Scope, SharedAtomicStore, SourcePin, pinned_sha,
)
from .handoff_store import Hold, require
from .quota_handoff import Registry, token
from .shared_issue_coordinator import Admission, RegistrySharedStore, Session


ACTIONS = frozenset({
    "claim", "observe_claim", "renew", "handoff", "receive_handoff",
    "begin", "recheck", "observe_original", "reconcile", "release",
})


@dataclass(frozen=True)
class VerifiedTransportPeer:
    """Result of an installed transport verifier; not an input credential."""
    principal: str
    session_id: str

    def __post_init__(self):
        token(self.principal)
        token(self.session_id)


@dataclass(frozen=True)
class PeerGrant:
    scope: Scope
    source: SourcePin
    actions: frozenset[str]

    def __post_init__(self):
        require(type(self.scope) is Scope and type(self.source) is SourcePin
                and type(self.actions) is frozenset and self.actions
                and all(type(action) is str and action in ACTIONS for action in self.actions),
                "shared_peer_exact_grant_required")


@dataclass(frozen=True)
class PeerRegistration:
    session: Session
    grants: tuple[PeerGrant, ...]

    def __post_init__(self):
        require(type(self.session) is Session and type(self.grants) is tuple and self.grants
                and all(type(grant) is PeerGrant for grant in self.grants)
                and len({(grant.scope, grant.source) for grant in self.grants}) == len(self.grants),
                "shared_peer_registration")


def _realm(identity):
    return identity.authority_id, identity.deployment_sha256, identity.policy_sha256


class SharedPeerEntry:
    """Configured peers share the one supplied Registry and its existing fences.

    Verifiers must be installed, bounded synchronous local checks; no I/O
    dispatch or untrusted callbacks under the journal lock. The live grant
    check must qualify the exact admission (including receive_handoff for its
    distinct successor), not just that an actor was once registered.
    """

    def __init__(self, registry, *, authority, peers, authenticate_transport,
                 verify_current_grant, verify_reconstruction, verify_handoff,
                 verify_completion, verify_terminal, lease_seconds=60):
        require(type(registry) is Registry and type(authority) is AuthorityIdentity
                and type(peers) is tuple and peers
                and all(type(peer) is PeerRegistration
                        and _realm(peer.session.identity) == _realm(authority) for peer in peers)
                and all(callable(check) for check in (authenticate_transport, verify_current_grant,
                    verify_reconstruction, verify_handoff, verify_completion, verify_terminal))
                and type(lease_seconds) is int and 1 <= lease_seconds <= 300,
                "shared_peer_entry_unconfigured")
        principals = {peer.session.principal: peer for peer in peers}
        sessions = {peer.session.identity.session_id: peer for peer in peers}
        require(len(principals) == len(sessions) == len(peers), "shared_peer_registration_ambiguous")
        self._principals, self._sessions = MappingProxyType(principals), MappingProxyType(sessions)
        self._registry, self._authority = registry, authority
        self._authenticate_transport, self._verify_current_grant = authenticate_transport, verify_current_grant
        self._verify_reconstruction, self._verify_handoff = verify_reconstruction, verify_handoff
        self._verify_completion, self._verify_terminal = verify_completion, verify_terminal
        self._lease_seconds = lease_seconds

    def _session(self, connection):
        require(connection is not None, "shared_peer_not_authenticated")
        try:
            peer = self._authenticate_transport(connection)
        except Exception:
            raise Hold("shared_peer_not_authenticated") from None
        require(type(peer) is VerifiedTransportPeer, "shared_peer_not_authenticated")
        registered = self._principals.get(peer.principal)
        require(registered is not None and registered.session.identity.session_id == peer.session_id,
                "shared_peer_not_authenticated")
        return registered.session

    def _successor(self, session_id):
        registered = self._sessions.get(session_id)
        return None if registered is None else registered.session

    def _allows(self, access):
        peer = self._principals.get(access.session.principal)
        if peer is None or peer.session != access.session or not any(
                grant.scope == access.scope and grant.source == access.source and access.action in grant.actions
                for grant in peer.grants):
            return False
        try:
            return self._verify_current_grant(access) is True
        except Exception:
            return False

    def bind(self, connection) -> SharedAtomicStore:
        """Bind server-side connection context, never a JSON principal."""
        bound = self._session(connection)

        def authenticate(identity):
            live = self._session(connection)
            return live if live == bound and live.identity == identity else None

        coordinator = RegistrySharedStore(self._registry, authority=self._authority,
            authenticate_peer=authenticate, resolve_session=self._successor, verify_admission=self._allows,
            verify_reconstruction=self._verify_reconstruction, verify_handoff=self._verify_handoff,
            verify_completion=self._verify_completion, verify_terminal=self._verify_terminal,
            lease_seconds=self._lease_seconds)
        return _PeerStore(bound, authenticate, self._allows, coordinator)


class _PeerStore(SharedAtomicStore):
    def __init__(self, bound, authenticate, allows, coordinator):
        self._bound, self._authenticate, self._allows, self._coordinator = bound, authenticate, allows, coordinator

    def _check(self, identity, action, intent, kind):
        require(type(identity) is AuthorityIdentity and identity == self._bound.identity
                and self._authenticate(identity) == self._bound, "shared_peer_identity_mismatch")
        require(type(intent) is kind, "shared_peer_typed_request_required")
        if kind is ClaimRequest:
            scope, source = intent.scope, intent.source
        else:
            if kind is PendingEffect:
                fence = intent.operation.fence
            elif kind is Operation:
                fence = intent.fence
            elif kind is ReleaseRequest:
                fence = intent.completion.fence
            else:
                fence = intent.current.fence  # exact RenewRequest or HandoffRequest
            scope, source = fence.scope, fence.source
        # Deny an unregistered route before touching even its temporary lock.
        # The coordinator then independently rechecks inside the transaction.
        require(self._allows(Admission(self._bound, scope, source, action, intent)),
                "shared_peer_scope_or_grant_unavailable")

    async def claim(self, identity, request):
        self._check(identity, "claim", request, ClaimRequest)
        return await self._coordinator.claim(identity, request)

    async def observe_claim(self, identity, request):
        self._check(identity, "observe_claim", request, ClaimRequest)
        return await self._coordinator.observe_claim(identity, request)

    async def renew(self, identity, request):
        self._check(identity, "renew", request, RenewRequest)
        return await self._coordinator.renew(identity, request)

    async def handoff(self, identity, request):
        self._check(identity, "handoff", request, HandoffRequest)
        return await self._coordinator.handoff(identity, request)

    async def begin(self, identity, operation):
        self._check(identity, "begin", operation, Operation)
        return await self._coordinator.begin(identity, operation)

    async def recheck(self, identity, pending):
        self._check(identity, "recheck", pending, PendingEffect)
        return await self._coordinator.recheck(identity, pending)

    async def observe_original(self, identity, operation):
        self._check(identity, "observe_original", operation, Operation)
        return await self._coordinator.observe_original(identity, operation)

    async def reconcile(self, identity, operation, receipt_sha256):
        self._check(identity, "reconcile", operation, Operation)
        return await self._coordinator.reconcile(identity, operation, pinned_sha(receipt_sha256))

    async def release(self, identity, request):
        self._check(identity, "release", request, ReleaseRequest)
        return await self._coordinator.release(identity, request)
