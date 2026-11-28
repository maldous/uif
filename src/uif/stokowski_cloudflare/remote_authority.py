"""Authenticated client for the installed, off-Foo V3 authority.

Credentials authorize requests to one fixed TLS origin. JSON identity fields
are expected pins checked against the receiver, never caller authentication.
There is no redirect, provider fallback, request retry or local claim store.
"""
from base64 import b64encode
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException, HTTPSConnection
from io import BytesIO
from math import ceil
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from ..effect_authority import (
    AuthorityIdentity, ClaimRequest, Completion, Lease, LeaseReceipt, OriginalEffect,
    ReleaseReceipt, ReleaseRequest, RenewRequest, Scope, SourcePin, TerminalEffect,
    _lease_reply, _release_reply, pinned_sha, wire_seconds,
)
from ..handoff_store import Hold, decode, digest, require, wire
from ..provider_discovery import ProviderReadCooldown
from ..quota_handoff import LinearKey, token
from ..shared_issue_coordinator import _lease
from .account_admission import MAX_EVIDENCE_AGE, AccountUsageEvidence, FreeRoute
from .client import GLM_MODEL, MAX_RESPONSE_BYTES
from .shared_admission import AccountReservationSnapshot, AccountUsageReceipt, NativeClaim, SharedFreePorts


class AuthorityUnavailable(Hold):
    """Safe to log. Neither raw service responses nor credentials appear here."""


_ABSENT = {'/v1/source/receipt': 'original_source_write_absent', '/v1/claim/read': 'claim_absent', '/v1/claim/observe': 'original_claim_not_found',
           '/v1/reclaim/observe': 'original_reclaim_absent', '/v1/linear/receipt': 'original_absent', '/v1/completion/read': 'original_completion_absent',
           '/v1/transition/original': 'original_transition_absent'}


def protected_record(path, *, secret=False):
    """Read one bounded regular file from a root-owned installation tree."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'authority_config_path')
    for directory in path.parents:
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'authority_config_directory')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        # Service-specific root:service 0440 or systemd LoadCredential 0400.
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & (0o027 if secret else 0o022), 'authority_file_custody')
        if secret:
            mode = stat.S_IMODE(info.st_mode)
            require(mode in (0o400, 0o600) or
                    (mode in (0o440, 0o640) and info.st_gid == os.getegid() and info.st_gid != 0),
                    'authority_credential_custody')
        return decode(stream.read(128_001), limit=128_000)


def _origin(value):
    parsed = urlsplit(value)
    require(parsed.scheme == 'https' and parsed.hostname and parsed.netloc == parsed.hostname
            and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',
                             parsed.hostname) and not parsed.query and not parsed.fragment
            and parsed.path in ('', '/'), 'authority_exact_https_origin')
    return parsed.hostname


class _DispatchPermit:
    """Copied task contexts share this object, never an independent token."""

    def __init__(self, claim, secret):
        self.claim = claim
        self._secret = secret
        self.winner = None

    def attach(self, task):
        require(self.winner is None and self._secret is not None and not task.done(),
                'authority_dispatch_winner')
        self.winner = task
        task.add_done_callback(self.revoke)

    def revoke(self, _task=None):
        self._secret = None
        self.winner = None

    def for_effect(self):
        secret, winner = self._secret, self.winner
        require(secret is not None and winner is not None and not winner.done(),
                'authority_dispatch_permit_revoked')
        return secret


def _metadata_retry_delay(header):
    if type(header) is not str or not 0 < len(header) <= 48:
        return None
    if re.fullmatch(r'[0-9]{1,10}', header):
        delay = int(header)
    else:
        try:
            deadline = parsedate_to_datetime(header)
            if deadline.utcoffset() is None:
                return None
            delay = max(0, ceil((deadline - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None
    return delay if delay <= 2**31 - 1 else None


class AccessTransport:
    def __init__(self, origin, credential_file):
        self.host = _origin(origin)
        self._credential_file = Path(credential_file)
        self._dispatch_permit = ContextVar("fresh_assessment_dispatch_permit", default=None)

    def request(self, path, data=None, *, timeout=4, allow_absent=False, provider=False):
        require(path.startswith('/v1/') and re.fullmatch(r'/v1/[a-z/]+', path),
                'authority_route')
        record = protected_record(self._credential_file, secret=True)
        require(record.keys() == {'client_id', 'client_secret'} and
                all(isinstance(v, str) and 0 < len(v) <= 1024 and v.isascii()
                    and all(33 <= ord(c) <= 126 for c in v) for v in record.values()),
                'authority_credential_format')
        headers = {'CF-Access-Client-Id': record['client_id'],
                   'CF-Access-Client-Secret': record['client_secret'],
                   'Accept': 'application/json', 'Content-Type': 'application/json'}
        permit = self._dispatch_permit.get()
        if permit is not None and path in {'/v1/send', '/v1/linear/comment'}:
            claim = permit.claim
            request = (data.get('intent') if path == '/v1/send' and type(data) is dict else data)
            operation = request.get('operation') if type(request) is dict else None
            require(type(operation) is dict and operation.get('fence') == asdict(claim.lease.fence)
                    and request.get('attempt_id') == claim.attempt_id,
                    'authority_dispatch_egress_scope')
            headers['uif-dispatch-permit'] = permit.for_effect()
        conn = HTTPSConnection(self.host, timeout=timeout)
        try:
            conn.request('GET' if data is None else 'POST', path,
                         body=None if data is None else wire(data), headers=headers)
            reply = conn.getresponse()
            if path == '/v1/usage/read' and reply.status in (429, 503):
                delay = _metadata_retry_delay(reply.getheader('Retry-After'))
                if delay is not None:
                    raise ProviderReadCooldown(delay)
            if allow_absent and reply.status == 404:
                # An Access login page, unknown Worker route or intermediary
                # 404 never proves absence of the original operation.
                expected = _ABSENT.get(path)
                require(expected is not None and reply.getheader('Content-Type', '').split(';')[0].lower()
                        == 'application/json' and decode(reply.read(1025), limit=1024) == {'error': expected},
                        'authority_absence_not_authenticated')
                return None
            require(reply.status == 200, f'authority_http_{reply.status}')
            require(reply.getheader('Content-Type', '').split(';')[0].lower() == 'application/json',
                    'authority_response_media')
            limit = MAX_RESPONSE_BYTES if provider else 64_000
            if path == '/v1/source/read':
                limit = 360_000
            elif path == '/v1/source/checkpoint':
                limit = 1_500_000
            raw = reply.read(limit + 1)
            require(len(raw) <= limit, 'authority_response_size')
            if provider:
                return ProviderReply(raw, reply.getheader('cf-aig-log-id'), reply.getheader('uif-receipt-sha256'))
            return decode(raw, limit=limit)
        except (OSError, HTTPException):
            raise AuthorityUnavailable('authority_transport_outcome_unknown') from None
        finally:
            conn.close()


    def observe_fresh_assessment_permission(self, claim, admission):
        observed = self.request('/v1/dispatch/observe', {'current': asdict(claim.lease), **admission})
        require(type(observed) is dict and observed.keys() == {'entered'}
                and type(observed['entered']) is bool, 'authority_dispatch_observation_shape')
        return observed['entered']

    @contextmanager
    def fresh_assessment_permission(self, claim, admission):
        """Bind a first affirmative reply to this native task; never retry entry."""
        require(type(claim) is NativeClaim and claim.project == 'OffMenu'
                and type(admission) is dict and admission.keys() == {'attempt_id', 'first_intent_sha256'}
                and admission['attempt_id'] == claim.attempt_id and self._dispatch_permit.get() is None,
                'authority_dispatch_scope')
        pinned_sha(admission['first_intent_sha256'])
        body = {'current': asdict(claim.lease), **admission}
        try:
            result = self.request('/v1/dispatch/enter', body)
        except AuthorityUnavailable:
            # Neither a verified observation nor a still-live lease can recover
            # a capability whose first reply was lost.
            self.observe_fresh_assessment_permission(claim, admission)
            raise AuthorityUnavailable('authority_dispatch_entry_outcome_unknown') from None
        require(type(result) is dict and result.keys() == {'entered', 'permit'}
                and result['entered'] is True and type(result['permit']) is str
                and re.fullmatch(r'[a-f0-9]{64}', result['permit']) is not None,
                'authority_dispatch_entry_not_first_confirmed')
        holder = _DispatchPermit(claim, result['permit'])
        binding = self._dispatch_permit.set(holder)
        try:
            yield holder
        finally:
            if holder.winner is None:
                holder.revoke()
            self._dispatch_permit.reset(binding)


class ProviderReply(BytesIO):
    status = 200

    def __init__(self, body, log_id, receipt_sha256):
        super().__init__(body)
        self._log_id, self.receipt_sha256 = log_id, receipt_sha256

    def getheader(self, name, default=None):
        return self._log_id if name.lower() == 'cf-aig-log-id' else default


@dataclass(frozen=True)
class NativeAssignment:
    """A root-protected copy of the independently registered exact admission."""
    scope: Scope
    source: SourcePin
    reconstruction_sha256: str
    scope_sha256: str
    expected_coordinator_epoch: int
    attempts: tuple[str, ...]
    previous: Lease | None = None
    previous_completion_sha256: str | None = None
    first_intent_sha256: str | None = None

    def __post_init__(self):
        require(type(self.scope) is Scope and type(self.source) is SourcePin
                and type(self.expected_coordinator_epoch) is int and self.expected_coordinator_epoch > 0
                and type(self.attempts) is tuple and self.attempts
                and len(set(self.attempts)) == len(self.attempts), 'authority_native_assignment')
        pinned_sha(self.reconstruction_sha256)
        pinned_sha(self.scope_sha256)
        if self.first_intent_sha256 is not None:
            pinned_sha(self.first_intent_sha256)
            require(self.previous is None and self.previous_completion_sha256 is None,
                    'authority_marked_initial_claim_only')
        if self.previous is not None or self.previous_completion_sha256 is not None:
            require(type(self.previous) is Lease and self.previous.fence.scope.key.claim_identity ==
                    self.scope.key.claim_identity, 'authority_previous_claim_scope')
            pinned_sha(self.previous_completion_sha256)
        for attempt in self.attempts:
            token(attempt)

    def claim(self, identity):
        # One original issue claim, independent of scheduling polls and restarts.
        return ClaimRequest(identity, self.scope, self.source,
                            digest({'claim': asdict(self.scope), 'recovery': self.reconstruction_sha256,
                                    'authority': asdict(identity), 'epoch': self.expected_coordinator_epoch}),
                            self.expected_coordinator_epoch, self.reconstruction_sha256)


def _mutation_body(request):
    body = asdict(request)
    del body['authority']  # authenticated by the service token at ingress
    return body


def _receipt(value):
    return LeaseReceipt(value['request_kind'], value['request_id'], value['request_sha256'],
                        _lease(value['lease']))


def _native_claim(value):
    return NativeClaim(value['project'], value['attempt_id'], _lease(value['lease']))


def _recovery_lease(value):
    try:
        lease = _lease(value)
    except (KeyError, TypeError, ValueError):
        raise Hold('authority_recovery_lease') from None
    require(asdict(lease) == value, 'authority_recovery_lease')
    return lease


def unstarted_transition(grant, identity, realm, body):
    """Validate and fingerprint a retained recipient request, without authenticating it."""
    pinned_sha(realm)
    require(type(body) is dict and body.keys() == {'request_id', 'current', 'successor_session',
            'expected_coordinator_epoch', 'quiesced_sha256', 'reconstruction_sha256', 'recovery'},
            'authority_recovery_request')
    request_id = token(body['request_id'])
    pinned_sha(body['quiesced_sha256'])
    recovery = body['recovery']
    require(type(recovery) is dict and recovery.keys() == {'attempt_id', 'revocation_id'},
            'authority_recovery_originals')
    token(recovery['revocation_id'])
    fence = _recovery_lease(body['current']).fence
    require(type(grant) is NativeAssignment and grant.previous is None
            and fence.scope == grant.scope and fence.source == grant.source and fence.generation == 1
            and (fence.authority_id, fence.deployment_sha256, fence.policy_sha256)
                == (identity.authority_id, identity.deployment_sha256, identity.policy_sha256)
            and fence.owner_session != body['successor_session'] == identity.session_id
            and type(body['expected_coordinator_epoch']) is int
            and fence.coordinator_epoch == body['expected_coordinator_epoch'] == grant.expected_coordinator_epoch
            and body['reconstruction_sha256'] == grant.reconstruction_sha256
            and recovery['attempt_id'] == grant.attempts[0], 'authority_recovery_assignment')
    kind = 'uif.shared-lease.recover-unstarted/1'
    return fence, {
        'installation_sha256': realm, 'session_id': identity.session_id, 'request_id': request_id,
        'request_kind': kind, 'request_sha256': digest({'kind': kind, 'request': dict(body, authority=asdict(identity))}),
    }


class RemoteAuthority:
    def __init__(self, transport, *, native, quota_issuer, route, repository_id,
                 workspace_id, project_id, reviewer_route=None, clock=None):
        require(type(native) is AuthorityIdentity and type(quota_issuer) is AuthorityIdentity
                and native.session_id != quota_issuer.session_id and route.model == GLM_MODEL,
                'authority_native_identity_or_route')
        self.transport = transport
        self.identity, self.quota_issuer, self.route = native, quota_issuer, route
        self.repository_id, self.workspace_id, self.project_id = repository_id, workspace_id, project_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.retain_intent = None
        self._verified_claim = ContextVar('verified_claim', default=None)
        self._verified_usage = ContextVar('verified_quota', default=None)
        self.routes = {'OffMenu': route}
        if reviewer_route is not None:
            require(type(reviewer_route) is FreeRoute and reviewer_route.account_id != route.account_id
                    and reviewer_route.model == route.model, 'authority_review_route')
            self.routes['UIF'] = reviewer_route
        self.account_ports = {
            name: SharedFreePorts(routes={'OffMenu': selected}, repository_id=repository_id,
                workspace_id=workspace_id, project_id=project_id, claim_authority=native,
                quota_issuer=quota_issuer, read_claim=self.read_claim, verify_claim=self.verify_claim,
                read_usage=self.read_usage, verify_usage=self.verify_usage, send_once=self.send,
                clock=self.clock) for name, selected in self.routes.items()
        }
        self.ports = self.account_ports['OffMenu']

    def status(self):
        status = self.transport.request('/v1/status')
        require(all(status.get(k) == v for k, v in asdict(self.identity).items())
                and status.get('quota_issuer') == asdict(self.quota_issuer)
                and status.get('role') == 'native' and type(status.get('stopped')) is bool
                and type(status.get('coordinator_epoch')) is int and status['coordinator_epoch'] > 0
                and type(status.get('lease_seconds')) is int and 1 <= status['lease_seconds'] <= 300,
                'authority_status_binding')
        return status

    def begin_identity_readback(self, expected):
        """Optional in-process renewal readback using this same native transport."""
        from ..service_identity_readback import begin_same_role_readback
        return begin_same_role_readback(expected, role="native", observe=self.status)

    def read_claim(self, project, identifier, by_attempt):
        require(project == 'OffMenu' and type(by_attempt) is bool, 'authority_read_project')
        token(identifier)
        raw = self.transport.request('/v1/claim/read', {'project': project, 'identifier': identifier,
                                                       'by_attempt': by_attempt}, allow_absent=True)
        if raw is None:
            return None
        record = _native_claim(raw)
        f = record.lease.fence
        require(record.project == project
                and (record.attempt_id if by_attempt else f.scope.key.issue_id) == identifier
                and (f.scope.repository_id, f.scope.key.workspace_id, f.scope.key.project_id) ==
                    (self.repository_id, self.workspace_id, self.project_id)
                and (f.authority_id, f.deployment_sha256, f.policy_sha256, f.owner_session) ==
                    (self.identity.authority_id, self.identity.deployment_sha256,
                     self.identity.policy_sha256, self.identity.session_id),
                'authority_native_claim_binding')
        self._verified_claim.set(record)
        return record

    def verify_claim(self, issuer, claim):
        return issuer == self.identity and claim is self._verified_claim.get()

    def read_usage(self, route):
        require(route in self.routes.values(), 'authority_quota_route')
        raw = self.transport.request('/v1/usage/read', {'route': asdict(route)})
        p = raw['projection']
        require(raw['issuer'] == asdict(self.quota_issuer) and p['route'] == asdict(route),
                'authority_quota_binding')
        def utc(name):
            return datetime.fromtimestamp(wire_seconds(p[name]), timezone.utc)
        pinned_sha(raw['source_receipt_sha256'])
        pinned_sha(raw['admission_binding_sha256'])
        receipt_sha = pinned_sha(raw['receipt_sha256'])
        projection = AccountUsageEvidence(route, p['utc_day'], utc('covered_through'), utc('valid_until'),
            p['upper'], p['gap_upper'], p['free_standard_no_fallback'], p['completeness_verified'])
        snapshot = None
        if 'reservation_snapshot' in raw:
            value = raw['reservation_snapshot']
            require(type(value) is dict and value.keys() == {
                        'receipt_sha256', 'observed_at', 'debt_millineurons', 'ceiling_breached'}
                    and value['receipt_sha256'] == receipt_sha
                    and type(value['observed_at']) is int
                    and p['covered_through'] <= value['observed_at'] < p['valid_until']
                    and type(value['debt_millineurons']) is int
                    and 0 <= value['debt_millineurons'] < 2**53
                    and type(value['ceiling_breached']) is bool, 'authority_quota_reservation_binding')
            observed = datetime.fromtimestamp(value['observed_at'], timezone.utc)
            require(observed.date().isoformat() == projection.utc_day
                    and observed - projection.covered_through <= MAX_EVIDENCE_AGE,
                    'authority_quota_reservation_binding')
            snapshot = AccountReservationSnapshot(**value)
        record = AccountUsageReceipt(self.quota_issuer, receipt_sha, projection, snapshot)
        self._verified_usage.set(record)
        return record

    def verify_usage(self, issuer, receipt):
        return issuer == self.quota_issuer and receipt is self._verified_usage.get()

    def _submit_initial_claim_once(self, grant, retained_intent_sha256):
        """The marked journal winner submits the independently enrolled digest."""
        require(type(grant) is NativeAssignment and grant.previous is None
                and grant.first_intent_sha256 == pinned_sha(retained_intent_sha256) and
                (grant.scope.repository_id, grant.scope.key.workspace_id, grant.scope.key.project_id)
                == (self.repository_id, self.workspace_id, self.project_id),
                'authority_native_initial_scope')
        request = grant.claim(self.identity)
        body = dict(_mutation_body(request), attempt_id=grant.attempts[0], first_intent_sha256=retained_intent_sha256)
        return _lease_reply(request, _receipt(self.transport.request('/v1/claim', body))).lease

    def observe_unstarted_recovery(self, grant, binding_path):
        """Observe a recipient's protected original handoff input and current claim.

        The producer must retain the commissioned installation digest and the
        exact submitted handoff body together. This readback cannot prepare a
        workspace, issue a claim, or recreate a missing transition.
        """
        return self._observe_unstarted_binding(grant, protected_record(binding_path))

    def _observe_unstarted_binding(self, grant, binding, *, workspace_transition=None):
        """Caller loads both inputs through the protected installation reader."""
        require(type(binding) is dict and binding.keys() == {'installation_sha256', 'handoff'},
                'authority_recovery_binding')
        realm = binding['installation_sha256']
        body = binding['handoff']
        identity = self.identity
        fence, transition = unstarted_transition(grant, identity, realm, body)
        if workspace_transition is not None:
            require(transition == workspace_transition, 'authority_recovery_workspace_transition')
        attempt = body['recovery']['attempt_id']
        require((fence.scope.repository_id, fence.scope.key.workspace_id, fence.scope.key.project_id)
                == (self.repository_id, self.workspace_id, self.project_id), 'authority_recovery_assignment')
        status = self.status()
        require(status.get('installation_sha256') == status.get('commissioned_sha256') == realm
                and not status['stopped'] and status['coordinator_epoch'] == fence.coordinator_epoch,
                'authority_recovery_realm_or_epoch')
        lookup = {k: transition[k] for k in ('installation_sha256', 'session_id', 'request_id')}
        reply = self.transport.request('/v1/transition/original', lookup, allow_absent=True)
        require(type(reply) is dict and reply.keys() ==
                {'request_id', 'request_kind', 'request_sha256', 'attempt_id', 'lease'}
                and all(reply[k] == transition[k] for k in ('request_kind', 'request_id', 'request_sha256'))
                and reply['attempt_id'] == attempt, 'authority_recovery_receipt')
        moved = _recovery_lease(reply['lease'])
        require(moved.fence.lease_id != fence.lease_id
                and moved.fence == replace(fence, owner_session=identity.session_id,
                    generation=fence.generation + 1, lease_id=moved.fence.lease_id), 'authority_recovery_fence')
        current = self.read_claim('OffMenu', attempt, True)
        require(current is not None and current.lease.fence == moved.fence
                and current.lease.expires_at >= moved.expires_at
                and self.clock().timestamp() < current.lease.expires_at, 'authority_recovery_not_current')
        return current

    def original_lease(self, grant, *, allow_acquire=False):
        request = grant.claim(self.identity)
        if grant.previous is None:
            body = dict(_mutation_body(request), attempt_id=grant.attempts[0])
            if grant.first_intent_sha256 is not None:
                body['first_intent_sha256'] = grant.first_intent_sha256
            raw = self.transport.request('/v1/claim/observe', body, allow_absent=True)
            if raw is None:
                require(allow_acquire and grant.first_intent_sha256 is None, 'authority_original_claim_unverified')
                raw = self.transport.request('/v1/claim', body)
            return _lease_reply(request, _receipt(raw)).lease
        kind = 'uif.shared-lease.reclaim/1'
        body = dict(_mutation_body(request), attempt_id=grant.attempts[0], previous=asdict(grant.previous),
                    previous_completion_sha256=grant.previous_completion_sha256)
        raw = self.transport.request('/v1/reclaim/observe', body, allow_absent=True)
        if raw is None:
            require(allow_acquire, 'authority_original_reclaim_unverified')
            raw = self.transport.request('/v1/reclaim', body)
        require(raw['request_id'] == request.request_id and raw['request_kind'] == kind
                and raw['request_sha256'] == digest({'kind': kind, 'request': dict(body, authority=asdict(self.identity))}),
                'authority_reclaim_receipt')
        lease = _lease(raw['lease'])
        fence = lease.fence
        require(fence.scope == grant.scope and fence.source == grant.source
                and fence.owner_session == self.identity.session_id and fence.coordinator_epoch == grant.expected_coordinator_epoch
                and fence.generation == grant.previous.fence.generation + 1 and fence.lease_id != grant.previous.fence.lease_id
                and (fence.authority_id, fence.deployment_sha256, fence.policy_sha256) ==
                    (self.identity.authority_id, self.identity.deployment_sha256, self.identity.policy_sha256),
                'authority_reclaim_fence')
        return lease

    def acquire(self, grant, attempt):
        require(type(grant) is NativeAssignment and attempt in grant.attempts
                and (grant.scope.repository_id, grant.scope.key.workspace_id, grant.scope.key.project_id)
                    == (self.repository_id, self.workspace_id, self.project_id), 'authority_native_scope')
        status = self.status()
        require(grant.first_intent_sha256 is None or attempt != grant.attempts[0],
                'authority_marked_first_dispatch_required')
        require(not status['stopped'] and status['coordinator_epoch'] == grant.expected_coordinator_epoch,
                'authority_paused_or_epoch')
        issue_id = grant.scope.key.issue_id
        if grant.previous is None:
            record = self.read_claim('OffMenu', issue_id, False)
            first = record is None and attempt == grant.attempts[0]
        else:
            # Reclaim itself checks released predecessor, completion and all
            # original effects atomically; a released claim is never eligible
            # for an ordinary gen-1 claim, or by inferred absence.
            first = attempt == grant.attempts[0]
        original = self.original_lease(grant, allow_acquire=first)
        record = self.read_claim('OffMenu', issue_id, False)
        require(record is not None and record.lease.fence == original.fence, 'authority_original_claim_not_current')
        fence = record.lease.fence
        require(fence.source == grant.source and fence.scope == grant.scope and
                fence.coordinator_epoch == grant.expected_coordinator_epoch
                and self.clock().timestamp() < record.lease.expires_at, 'authority_claim_changed_or_expired')
        if record.attempt_id != attempt:
            body = {'current': asdict(record.lease), 'expected_attempt_id': record.attempt_id, 'attempt_id': attempt,
                    'request_id': digest({'lease': fence.lease_id, 'attempt': attempt})}
            self.transport.request('/v1/claim/attempt', body)
            record = self.read_claim('OffMenu', attempt, True)
            require(record is not None and record.lease.fence == fence, 'authority_attempt_unverified')
        return record

    def renew(self, claim):
        current = claim.lease
        request = RenewRequest(self.identity, current, digest({'renew': asdict(current)}))
        reply = _lease_reply(request, _receipt(self.transport.request('/v1/renew', _mutation_body(request))))
        observed = self.read_claim('OffMenu', claim.attempt_id, True)
        require(observed is not None and observed.lease == reply.lease, 'authority_renewal_not_current')
        return observed

    def read_original(self, operation):
        record = self.transport.request('/v1/original', {'operation': asdict(operation)})
        require(record['operation'] == asdict(operation), 'authority_original_mismatch')
        terminal = record.get('terminal')
        if terminal is not None:
            require(terminal['operation'] == asdict(operation), 'authority_terminal_mismatch')
            terminal = TerminalEffect(operation, terminal['status'], terminal['receipt_sha256'])
        return OriginalEffect(operation, record['state'], terminal)

    def release_verified(self, lease):
        raw = self.transport.request('/v1/completion/read', {'current': asdict(lease)}, allow_absent=True)
        if raw is None:
            return None
        require(raw['fence'] == asdict(lease.fence), 'authority_original_completion_changed')
        return self.release(Completion(lease.fence, raw['accepted'], raw['receipt_sha256']))

    def release(self, completion):
        require(type(completion) is Completion and completion.fence.owner_session == self.identity.session_id,
                'authority_release_owner')
        request = ReleaseRequest(self.identity, completion, digest({'release': asdict(completion)}))
        try:
            raw = self.transport.request('/v1/release', _mutation_body(request))
        except AuthorityUnavailable as uncertain:
            # One read of the same authenticated receipt; a missing or
            # unverified answer leaves the release outcome uncertain.
            try:
                status = self.status()
                realm = status.get('installation_sha256')
                pinned_sha(realm)
                require(status.get('commissioned_sha256') == realm, 'authority_release_realm')
                raw = self.transport.request('/v1/transition/original',
                    {'installation_sha256': realm, 'session_id': self.identity.session_id,
                     'request_id': request.request_id}, allow_absent=True)
                return self._validated_release(request, raw)
            except (Hold, KeyError, TypeError, ValueError):
                raise uncertain from None
        return self._validated_release(request, raw)

    @staticmethod
    def _validated_release(request, raw):
        require(type(raw) is dict and raw.keys() == {'request_kind', 'request_id', 'request_sha256', 'completion'}
                and raw['completion'] == asdict(request.completion), 'authority_completion_changed')
        return _release_reply(request, ReleaseReceipt(raw['request_kind'], raw['request_id'],
                                                      raw['request_sha256'], request.completion))

    def send(self, intent, body):
        # Only the receiver holding the account credential may send the physical request.
        require(callable(self.retain_intent), 'authority_original_intent_recorder_unavailable')
        self.retain_intent(intent)
        remaining = intent.request_not_after - self.clock().timestamp()
        require(remaining > 0, 'authority_send_deadline')
        try:
            return self.transport.request('/v1/send', {'intent': asdict(intent),
                                      'body_base64': b64encode(body).decode('ascii')},
                                      timeout=min(remaining, 600), provider=True)
        except AuthorityUnavailable:
            # An interrupted HTTPS response is uncertain. One authenticated
            # read of the same operation may recover the original bytes. It
            # never sends to the provider or executes a repository tool.
            return self.provider_result(intent.operation)

    def provider_result(self, operation):
        result = self.transport.request('/v1/provider/receipt', {'operation': asdict(operation)},
                                        timeout=4, provider=True)
        observed = self.read_original(operation)
        require(observed.state == 'terminal' and observed.terminal.status == 'verified'
                and observed.terminal.receipt_sha256 == result.receipt_sha256,
                'authority_provider_original_unverified')
        return result
