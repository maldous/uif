"""Typed opt-in glue for the existing Workers AI router and shared effect authority.

The installer must authenticate the quota issuer, owner reader and effect
receiver out of band. No implementation here acquires a claim, opens a
network connection, resolves a credential, writes a policy or retries.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from threading import Lock
from uuid import UUID

from ..effect_authority import (AuthorityIdentity, Lease, Operation, PendingEffect,
                                Scope, SharedEffectAuthority, wire_seconds)
from ..handoff_store import Hold, decode, digest
from ..quota_handoff import LinearKey, SHA, token
from .account_admission import AccountUsageEvidence, FreeRoute, allows_request
from .budget import BudgetRefused
from .client import FencedFreeCall, FreeAdmissionWindow, MAX_OUTPUT_TOKENS, MODEL_RESERVATIONS


@dataclass(frozen=True)
class AccountReservationSnapshot:
    """Read-only account debt and freeze state at one exact quota watermark."""

    receipt_sha256: str
    observed_at: int
    debt_millineurons: int
    ceiling_breached: bool


@dataclass(frozen=True)
class AccountUsageReceipt:
    issuer: AuthorityIdentity
    receipt_sha256: str
    projection: AccountUsageEvidence
    reservation_snapshot: AccountReservationSnapshot | None = None


@dataclass(frozen=True)
class NativeClaim:
    """Authoritatively bound native assignment, not a claim request."""
    project: str
    attempt_id: str
    lease: Lease


@dataclass(frozen=True)
class SendIntent:
    operation: Operation
    route: FreeRoute
    attempt_id: str
    request_sha256: str
    quota_receipt_sha256: str
    request_not_after: int
    maximum_millineurons: int


def _utc(value):
    return type(value) is datetime and value.tzinfo is timezone.utc


def _sha(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def _refuse():
    return BudgetRefused('Shared Workers AI quota, custody or send permit unavailable')


def _wire(route):
    headers = {'Content-Type': 'application/json'}
    if route.gateway_id is not None:
        headers.update({'cf-aig-gateway-id': route.gateway_id, 'cf-aig-max-attempts': '1',
                        'cf-aig-skip-cache': 'true'})
    return 'POST', f'/client/v4/accounts/{route.account_id}/ai/v1/chat/completions', headers


def _body_qualified(route, body, body_sha):
    if (type(body) is not bytes or not 0 < len(body) <= 128_000
            or not _sha(body_sha) or sha256(body).hexdigest() != body_sha):
        return False
    try:
        request = decode(body, limit=128_000)
    except Hold:
        return False
    return (request.get('model') == route.model
            and type(request.get('max_tokens')) is int
            and request['max_tokens'] == MAX_OUTPUT_TOKENS and request.get('stream') is False)


def _payload(route, attempt, body_sha, quota_sha, deadline, maximum):
    method, path, headers = _wire(route)
    return digest({'kind': 'uif.workers-ai-free-send/1', 'route': asdict(route),
                   'attempt': attempt, 'request_sha256': body_sha, 'quota': quota_sha,
                   'not_after': deadline, 'maximum_millineurons': maximum,
                   'method': method, 'path': path, 'headers': headers})


class SharedFreePorts:
    """Bind parked launcher's exact three ports for a single OffMenu scope.

    The explicitly injected trusted receiver must atomically consume the
    original operation under the current lease, independently recheck the
    Free route and entire-account budget, and own the physical send. An
    observation, local SQLite reservation or a separately checked lease is
    never substituted for a shared dispatch fence.
    """

    def __init__(self, *, routes, repository_id, workspace_id, project_id,
                 claim_authority, quota_issuer, read_usage, verify_usage,
                 read_claim, verify_claim, send_once, clock):
        if (type(routes) is not dict or routes.keys() != {'OffMenu'}
                or type(routes['OffMenu']) is not FreeRoute
                or type(routes['OffMenu'].account_id) is not str
                or type(routes['OffMenu'].model) is not str
                or routes['OffMenu'].model not in MODEL_RESERVATIONS
                or type(claim_authority) is not AuthorityIdentity
                or type(quota_issuer) is not AuthorityIdentity
                or claim_authority.authority_id == quota_issuer.authority_id
                or not all(map(callable, (read_usage, verify_usage, read_claim,
                                           verify_claim, send_once, clock)))):
            raise _refuse()
        self._routes = dict(routes)
        # Reuse the shared scope validator, without inventing any issue identity.
        self._repository = token(repository_id)
        for value in (workspace_id, project_id):
            try:
                if type(value) is not str or str(UUID(value)) != value:
                    raise _refuse()
            except (ValueError, AttributeError):
                raise _refuse() from None
        self._workspace, self._project = workspace_id, project_id
        self._claim_authority, self._quota_issuer = claim_authority, quota_issuer
        self._read_usage, self._verify_usage = read_usage, verify_usage
        self._read_claim, self._verify_claim = read_claim, verify_claim
        self._send_once, self._clock = send_once, clock

    def launcher_options(self):
        return {'read_account_usage': self.read_account_usage,
                'allows_native_dispatch': self.allows_native_dispatch,
                'open_fenced_connection': self.open_fenced_connection}

    def _now(self):
        value = self._clock()
        if not _utc(value):
            raise _refuse()
        return value

    def _usage(self, route, now, deadline):
        try:
            receipt = self._read_usage(route)
            if (type(receipt) is AccountUsageReceipt and receipt.issuer == self._quota_issuer
                    and _sha(receipt.receipt_sha256)
                    and self._verify_usage(self._quota_issuer, receipt) is True
                    and allows_request(route, receipt.projection, now=now,
                                       request_not_after=deadline, outstanding_local=0,
                                       request_maximum=MODEL_RESERVATIONS[route.model])):
                return receipt
        except (OSError, ValueError):
            pass
        return None

    def read_account_usage(self, timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise _refuse()
        now = self._now()
        deadline = datetime.fromtimestamp(math.floor(now.timestamp() + timeout), timezone.utc)
        snapshots = {}
        for name, route in self._routes.items():
            receipt = self._usage(route, now, deadline)
            if receipt is not None:
                snapshots[name] = receipt.projection
        if not snapshots or self._now() >= deadline:
            raise _refuse()
        return FreeAdmissionWindow(deadline, snapshots)

    def _claim(self, identifier, *, by_attempt, deadline=None):
        try:
            if type(identifier) is not str:
                return None
            token(identifier)
            record = self._read_claim('OffMenu', identifier, by_attempt)
            if type(record) is not NativeClaim or record.project != 'OffMenu' or type(record.lease) is not Lease:
                return None
            token(record.attempt_id)
            lease = record.lease
            fence = lease.fence
            key = fence.scope.key
            if (type(key) is not LinearKey or fence.scope.repository_id != self._repository
                    or (key.workspace_id, key.project_id) != (self._workspace, self._project)
                    or identifier != (record.attempt_id if by_attempt else key.issue_id)
                    or (fence.authority_id, fence.deployment_sha256, fence.policy_sha256,
                        fence.owner_session) != (self._claim_authority.authority_id,
                        self._claim_authority.deployment_sha256, self._claim_authority.policy_sha256,
                        self._claim_authority.session_id)
                    or not self._verify_claim(self._claim_authority, record) is True
                    or self._now().timestamp() >= lease.expires_at
                    or deadline is not None and deadline.timestamp() > lease.expires_at):
                return None
            return record
        except (OSError, ValueError, BudgetRefused):
            return None

    def allows_native_dispatch(self, project, issue_id):
        return project == 'OffMenu' and self._claim(issue_id, by_attempt=False) is not None

    def open_fenced_connection(self, call):
        if (type(call) is not FencedFreeCall or call.project != 'OffMenu'
                or type(call.route) is not FreeRoute or call.route != self._routes['OffMenu']
                or not all(map(_sha, (call.request_id, call.request_sha256)))
                or not _utc(call.request_not_after) or call.request_not_after.microsecond != 0):
            raise _refuse()
        return _OneShot(self, call)

    def _send(self, call, method, path, body, headers):
        if ((method, path, headers) != _wire(call.route)
                or not _body_qualified(call.route, body, call.request_sha256)):
            raise _refuse()
        now, deadline = self._now(), call.request_not_after
        receipt = self._usage(call.route, now, deadline)
        claim = self._claim(call.attempt_id, by_attempt=True, deadline=deadline)
        if receipt is None or claim is None or self._now() >= deadline:
            raise _refuse()
        maximum = MODEL_RESERVATIONS[call.route.model]
        end = int(deadline.timestamp())
        payload = _payload(call.route, call.attempt_id, call.request_sha256,
                           receipt.receipt_sha256, end, maximum)
        intent = SendIntent(Operation(claim.lease.fence, call.request_id, payload),
                            call.route, call.attempt_id, call.request_sha256,
                            receipt.receipt_sha256, end, maximum)
        try:
            return self._send_once(intent, body)
        except Hold:
            # Preserve the ledger original; the native caller knows BudgetRefused
            # as a normal admission outcome rather than crashing/retrying the unit.
            raise _refuse() from None


class _OneShot:
    """Adapts the existing client protocol; no authenticated transport here."""
    def __init__(self, ports, call):
        self._ports, self._call = ports, call
        self._lock = Lock()
        self._consumed = False
        self._response = None

    def request(self, method, path, *, body, headers):
        with self._lock:
            if self._consumed:
                raise _refuse()
            self._consumed = True
            self._response = self._ports._send(self._call, method, path, body, headers)

    def getresponse(self):
        with self._lock:
            if self._response is None:
                raise _refuse()
            return self._response

    def close(self):
        with self._lock:
            self._consumed = True
            if self._response is not None:
                self._response.close()
                self._response = None


async def receive_original(authority, intent, body, *, scope, allowed_routes, clock,
                           consume_current_once):
    """Protected receiver glue; its atomic consume/send backend is not supplied.

    The backend must validate its own authenticated route/evidence/deadline and
    this exact pending fence together with the global account reservation at
    physical egress. Recheck alone cannot fence the sink or settle a send.
    """
    if (type(authority) is not SharedEffectAuthority or type(intent) is not SendIntent
            or not callable(consume_current_once) or not callable(clock)
            or type(scope) is not Scope or type(allowed_routes) is not tuple
            or type(intent.route) is not FreeRoute or intent.route not in allowed_routes
            or intent.route.model not in MODEL_RESERVATIONS
            or not _body_qualified(intent.route, body, intent.request_sha256)
            or not _sha(intent.quota_receipt_sha256)
            or type(intent.maximum_millineurons) is not int
            or intent.maximum_millineurons != MODEL_RESERVATIONS[intent.route.model]
            or type(intent.operation) is not Operation
            or intent.operation.fence.scope != scope or not _sha(intent.operation.original_id)
            or intent.operation.payload_sha256 != _payload(
                intent.route, intent.attempt_id, intent.request_sha256,
                intent.quota_receipt_sha256, intent.request_not_after, intent.maximum_millineurons)):
        raise _refuse()
    deadline = wire_seconds(intent.request_not_after)
    token(intent.attempt_id)

    def still_current():
        now = clock()
        if not _utc(now) or now.timestamp() >= deadline:
            raise _refuse()

    still_current()
    pending = await authority.begin(intent.operation)
    if type(pending) is not PendingEffect:
        # An original terminal (or ambiguity) is not permission to send again.
        raise _refuse()
    pending = await authority.recheck(pending)
    still_current()
    return await consume_current_once(pending, intent, body)
