"""Server-side V3 adapter over the existing single-owner UIF journal.

No listener, credential store, provisioning, worker launch or provider is
included. Serve this only behind an independently authenticated receiver on
one configured authority host, never by giving peers direct filesystem access.
"""
from dataclasses import asdict, dataclass
from inspect import iscoroutine
from secrets import token_hex

from .effect_authority import (
    AuthorityIdentity, ClaimFence, ClaimRequest, HandoffRequest, Lease,
    LeaseReceipt, Operation, OriginalEffect, PendingEffect, ReleaseReceipt,
    ReleaseRequest, RenewRequest, Scope, SharedAtomicStore, SourcePin, TerminalEffect,
    pinned_sha, transition_request_sha256, wire_seconds,
)
from .handoff_store import TERMINAL_OPERATIONS, digest, require
from .quota_handoff import (CLOUD_ACTIVE, LOCAL_READY, LOCAL_RECOVERY, MODES, RAMP,
                            TRANSFER_PENDING, LinearKey, Registry, timestamp, token)


@dataclass(frozen=True)
class Session:
    """An installed peer binding; never decoded from the request body."""
    identity: AuthorityIdentity
    principal: str
    actor: str

    def __post_init__(self):
        require(type(self.identity) is AuthorityIdentity, 'shared_session_identity')
        token(self.principal)
        token(self.actor)


@dataclass(frozen=True)
class Admission:
    session: Session
    scope: Scope
    source: SourcePin
    action: str
    exact_intent: object


@dataclass(frozen=True)
class CommittedSendIntent:
    """Exact journal commitment for a trusted sink; not a reusable send permit."""
    pending: PendingEffect
    authority_revision: int


def _realm(identity):
    return identity.authority_id, identity.deployment_sha256, identity.policy_sha256


def _fence(value):
    parts = dict(value)
    sc = parts.pop('scope')
    return ClaimFence(scope=Scope(sc['repository_id'], LinearKey.from_record(sc['key'])),
                      source=SourcePin(**parts.pop('source')), **parts)


def _lease(value):
    return Lease(_fence(value['fence']), value['expires_at'])


class RegistrySharedStore(SharedAtomicStore):
    """No second database. Legacy and typed paths share the same claims/locks.

    Authenticate a distinct transport principal and prequalify external proof
    outside this adapter. Installed verifier callbacks under the journal lock
    must be bounded local checks; neither a request nor a boolean field is a
    designation of an actor, issuer or immutable source route.
    """

    def __init__(self, registry, *, authority, authenticate_peer, resolve_session,
                 verify_admission, verify_reconstruction, verify_handoff,
                 verify_completion, verify_terminal, lease_seconds=60):
        require(type(registry) is Registry and type(authority) is AuthorityIdentity
                and all(map(callable, (authenticate_peer, resolve_session,
                                       verify_admission, verify_reconstruction,
                                       verify_handoff, verify_completion, verify_terminal)))
                and type(lease_seconds) is int and 1 <= lease_seconds <= 300,
                'shared_installed_authority_required')
        self.registry, self.store = registry, registry.store
        self.authority = authority
        self.authenticate_peer, self.resolve_session = authenticate_peer, resolve_session
        self.verify_admission, self.verify_terminal = verify_admission, verify_terminal
        self.verify_reconstruction, self.verify_handoff = verify_reconstruction, verify_handoff
        self.verify_completion = verify_completion
        self.lease_seconds = lease_seconds

    def _peer(self, reg, state, identity, scope, source, action, intent, *, successor=None):
        require(state.get('schema') in {'uif-quota-authority/1','uif-quota-authority/2'}
                and state.get('mode') in MODES,'shared_registry_state_unqualified')
        require(type(identity) is AuthorityIdentity and _realm(identity) == _realm(self.authority),
                'shared_authority_changed')
        session = (self.authenticate_peer(identity) if successor is None else
                   self.resolve_session(successor))
        require(type(session) is Session, 'shared_peer_not_authenticated')
        if successor is None:
            require(session.identity == identity, 'shared_peer_mismatch')
        else:
            require(_realm(session.identity) == _realm(identity)
                    and session.identity.session_id == successor, 'shared_successor_not_registered')
        role = self.registry._authenticate(reg, session.principal, session.actor)
        key = scope.key
        require(key.workspace_id == reg['workspace_id'] and key.project_id in role['projects'],
                'shared_project_authority')
        require(self.verify_admission(Admission(session, scope, source, action, intent)) is True,
                'shared_admission_unverified')
        return session, role

    def _now(self):
        # The *installed* authority clock can return fractional POSIX seconds.
        # Wire requests/receipts still have only the V3 canonical integer form.
        return wire_seconds(int(timestamp(self.registry.clock())))

    def _end(self):
        return wire_seconds(self._now() + self.lease_seconds)

    @staticmethod
    def _identity(session, request):
        return 'shared-v3:' + digest({'peer': asdict(session.identity), 'id': request.request_id})

    def _original_receipt(self, state, session, request):
        key = self._identity(session, request)
        prior = state['requests'].get(key) or self.store.request_receipt(key, readonly=True)
        if prior is not None:
            require(prior['sha256'] == transition_request_sha256(request), 'shared_original_request_changed')
            return prior['result']['value']
        return None

    def _record(self, state, session, request, result, save):
        state['revision'] += 1
        identity = self._identity(session, request)
        fingerprint = transition_request_sha256(request)
        state['requests'][identity] = {'sha256': fingerprint, 'result': {
            'revision': state['revision'], 'value': asdict(result)}}
        state['events'].append({'revision': state['revision'], 'actor': session.actor,
                                'action': request.kind, 'request_id': request.request_id,
                                'request_sha256': fingerprint, 'at': self._now()})
        save()
        return result

    @staticmethod
    def _lease_result(request, lease):
        return LeaseReceipt(request.kind, request.request_id, transition_request_sha256(request), lease)

    def _route(self, state, reg, role, delta=0):
        require(state['stopped'] is False and state['coordinator']['pending'] is None,
                'shared_admission_stopped')
        if role['role'] == 'native':
            self.registry._provider(state)
            require(state['mode'] in (LOCAL_READY, LOCAL_RECOVERY), 'local_admission_closed')
            active = sum(task['lane']=='local' and task['state'] in {'active','handoff-pending'}
                         for task in state['tasks'].values())
            require(active + delta <= min(state['local_limit'], reg['local_ceiling']),
                    'local_ramp_capacity')
            return 'local'
        require(role['role'] == 'project', 'shared_peer_not_worker')
        self.registry._morgan(reg,state)
        return 'cloud'

    @staticmethod
    def _cloud_lane_seen(state, lane):
        if lane == 'cloud' and state['mode'] == TRANSFER_PENDING and not any(
                task['lane'] == 'local' and task['state'] in {'active','handoff-pending'}
                for task in state['tasks'].values()):
            state['mode'] = CLOUD_ACTIVE

    def _task(self, state, scope, *, readonly=False):
        key = scope.key
        task = state['tasks'].get(key.identity) or self.store.archived_task(key.identity, readonly=readonly)
        require(task is not None and 'shared_lease' in task and task['key'] == asdict(key)
                and _lease(task['shared_lease']).fence.scope == scope
                and not self.registry._has_other_project_claim(state,key), 'shared_issue_needs_reconciliation')
        lease = _lease(task['shared_lease'])
        require(task['generation'] == lease.fence.generation and
                task['source'] == asdict(lease.fence.source), 'shared_lease_record_drift')
        return task, lease

    def _owner(self, reg, state, identity, fence, action, intent, *, live=True):
        session, role = self._peer(reg,state,identity,fence.scope,fence.source,action,intent)
        task, lease = self._task(state,fence.scope)
        require(lease.fence == fence and task['owner'] == session.actor
                and fence.owner_session == identity.session_id and task['state']=='active',
                'shared_owner_not_current')
        if live:
            require(fence.coordinator_epoch == state['coordinator']['generation']
                    and self._now() < lease.expires_at,'shared_lease_expired_or_superseded')
            require(self._route(state, reg,role) == task['lane'], 'shared_worker_lane_changed')
        return session, role, task, lease

    async def claim(self, identity, request):
        require(type(request) is ClaimRequest and request.authority==identity,'shared_claim_request')
        with self.store.task_fence(request.scope.key.claim_identity), self.store.transaction() as (reg,state,save):
            key = request.scope.key
            session, role = self._peer(reg,state,identity,request.scope,request.source,'claim',request)
            prior = self._original_receipt(state,session,request)
            if prior is not None:
                return self._lease_result(request,_lease(prior['lease']))
            require(request.expected_coordinator_epoch == state['coordinator']['generation'],
                    'shared_coordinator_epoch_changed')
            require(key.identity not in state['tasks'] and self.store.archived_task(key.identity) is None
                    and not self.registry._has_other_project_claim(state,key),'shared_issue_needs_reconciliation')
            require(self.verify_reconstruction(session,request) is True,
                    'shared_reconstruction_unverified')
            self.store.require_headroom(state,active_delta=1)
            lane = self._route(state,reg,role,delta=1)
            fence = ClaimFence(request.scope,request.source,identity.session_id,request.expected_coordinator_epoch,
                               1,token_hex(24),*_realm(identity))
            lease = Lease(fence,self._end())
            state['tasks'][key.identity] = dict(key=asdict(key),source=asdict(request.source),
                owner=session.actor,lane=lane,generation=1,state='active',shared_lease=asdict(lease),
                recovery_ref=request.reconstruction_sha256,original_operation_id=request.request_id,
                operations={},handoffs=[],attempts=[],checkpoint_origin=None)
            self._cloud_lane_seen(state,lane)
            return self._record(state,session,request,self._lease_result(request,lease),save)

    async def observe_claim(self, identity, request):
        require(type(request) is ClaimRequest and request.authority==identity,'shared_claim_request')
        with self.store.transaction(readonly=True) as (reg,state,_):
            session,_ = self._peer(reg,state,identity,request.scope,request.source,'observe_claim',request)
            value = self._original_receipt(state,session,request)
            return None if value is None else self._lease_result(request,_lease(value['lease']))

    async def renew(self, identity, request):
        require(type(request) is RenewRequest and request.authority==identity,'shared_renew_request')
        fence = request.current.fence
        with self.store.task_fence(fence.scope.key.claim_identity), self.store.transaction() as (reg,state,save):
            session,_ = self._peer(reg,state,identity,fence.scope,fence.source,'renew',request)
            prior = self._original_receipt(state,session,request)
            if prior is not None:
                return self._lease_result(request,_lease(prior['lease']))
            _,_,task,lease = self._owner(reg,state,identity,fence,'renew',request)
            end = self._end()
            require(lease == request.current and end > lease.expires_at,'shared_lease_changed')
            renewed = Lease(fence,end)
            task['shared_lease'] = asdict(renewed)
            return self._record(state,session,request,self._lease_result(request,renewed),save)

    async def handoff(self, identity, request):
        require(type(request) is HandoffRequest and request.authority==identity,'shared_handoff_request')
        fence = request.current.fence
        with self.store.task_fence(fence.scope.key.claim_identity), self.store.transaction() as (reg,state,save):
            session,_ = self._peer(reg,state,identity,fence.scope,fence.source,'handoff',request)
            prior = self._original_receipt(state,session,request)
            if prior is not None:
                return self._lease_result(request,_lease(prior['lease']))
            task,lease = self._task(state,fence.scope)
            require(lease == request.current and task['state']=='active'
                    and request.expected_coordinator_epoch==state['coordinator']['generation']
                    and all(o['status'] in TERMINAL_OPERATIONS for o in task['operations'].values()),
                    'shared_handoff_requires_reconciliation')
            # The installed verifier must attest quiescence and checkpoint for
            # the exact predecessor, and authenticate the separate successor.
            successor,role = self._peer(reg,state,identity,fence.scope,fence.source,
                                         'receive_handoff',request,successor=request.successor_session)
            require(identity.session_id in (fence.owner_session, successor.identity.session_id),
                    'shared_handoff_actor')
            require(self.verify_handoff(session,successor,request) is True,
                    'shared_handoff_evidence_unverified')
            other = self._route(state,reg,role,delta=1 if role['role']=='native' and task['lane']!='local' else 0)
            next_fence = ClaimFence(fence.scope,fence.source,successor.identity.session_id,
                                   request.expected_coordinator_epoch,fence.generation+1,
                                   token_hex(24),*_realm(identity))
            next_lease = Lease(next_fence,self._end())
            task['handoffs'].append({'previous':asdict(lease),'quiesced_sha256':request.quiesced_sha256,
                                     'reconstruction_sha256':request.reconstruction_sha256})
            task.update(shared_lease=asdict(next_lease),owner=successor.actor,lane=other,
                        generation=next_fence.generation,recovery_ref=request.reconstruction_sha256)
            self._cloud_lane_seen(state,other)
            return self._record(state,session,request,self._lease_result(request,next_lease),save)

    @staticmethod
    def _operation(task,operation):
        record = task['operations'].get(operation.original_id)
        require(record is not None and record['original'] == asdict(operation),'shared_original_effect_changed')
        return record

    @staticmethod
    def _terminal(operation, record):
        return TerminalEffect(operation,record['status'],record['receipt_sha256'])

    async def begin(self, identity, operation):
        require(type(operation) is Operation,'shared_effect_request')
        fence = operation.fence
        with self.store.task_fence(fence.scope.key.claim_identity), self.store.transaction() as (reg,state,save):
            _,_,task,_ = self._owner(reg,state,identity,fence,'begin',operation)
            prior = task['operations'].get(operation.original_id)
            if prior is not None:
                prior = self._operation(task,operation)
                require(prior['status'] in TERMINAL_OPERATIONS,'shared_original_effect_uncertain')
                return self._terminal(operation,prior)
            require(all(o['status'] in TERMINAL_OPERATIONS for o in task['operations'].values()),
                    'shared_original_effect_uncertain')
            self.store.require_headroom(state)
            receipt = PendingEffect(operation,token_hex(24))
            task['operations'][operation.original_id] = {'original':asdict(operation),'status':'pending',
                                                          'dispatch_fence':receipt.dispatch_fence}
            state['revision'] += 1
            save()
            return receipt

    @staticmethod
    def _pending(record, pending):
        require(record['status']=='pending' and record['dispatch_fence']==pending.dispatch_fence,
                'shared_dispatch_already_consumed_or_changed')

    async def recheck(self, identity, pending):
        require(type(pending) is PendingEffect,'shared_pending_effect_required')
        op = pending.operation
        with self.store.transaction(readonly=True) as (reg,state,_):
            _,_,task,_ = self._owner(reg,state,identity,op.fence,'recheck',pending)
            self._pending(self._operation(task,op),pending)
        return pending

    async def observe_original(self, identity, operation):
        require(type(operation) is Operation,'shared_effect_request')
        f = operation.fence
        with self.store.transaction(readonly=True) as (reg,state,_):
            self._peer(reg,state,identity,f.scope,f.source,'observe_original',operation)
            task,_ = self._task(state,f.scope,readonly=True)
            if operation.original_id not in task['operations']:
                return OriginalEffect(operation,'unknown')
            record = self._operation(task,operation)
            if record['status'] in TERMINAL_OPERATIONS:
                return OriginalEffect(operation,'terminal',self._terminal(operation,record))
            return OriginalEffect(operation,'pending' if record['status']=='pending' else 'unknown')

    async def reconcile(self, identity, operation, receipt_sha256):
        require(type(operation) is Operation,'shared_effect_request')
        f = operation.fence
        with self.store.task_fence(f.scope.key.claim_identity), self.store.transaction() as (reg,state,save):
            session,_ = self._peer(reg,state,identity,f.scope,f.source,'reconcile',operation)
            task,_ = self._task(state,f.scope)
            return self._accept_terminal(state,session,task,operation,receipt_sha256,save)

    def _accept_terminal(self, state, session, task, operation, receipt_sha256, save):
        record = self._operation(task,operation)
        if record['status'] in TERMINAL_OPERATIONS:
            require(record['receipt_sha256']==receipt_sha256,'shared_terminal_immutable')
            return self._terminal(operation,record)
        verified = self.verify_terminal(session,operation,pinned_sha(receipt_sha256))
        require(type(verified) is TerminalEffect and verified.operation==operation
                and verified.receipt_sha256==receipt_sha256,'shared_destination_not_verified')
        record.update(status=verified.status,receipt_sha256=verified.receipt_sha256)
        state['revision'] += 1
        save()
        return verified

    def consume_once(self, identity, pending, *, accept, observe_receipt):
        """Order exact send acceptance with stop, quota and coordinator changes.

        Only an installed receiver supplies these callbacks. Under the same
        authority lock as revocation, accept must perform one bounded,
        synchronous and irreversible acceptance of the exact intent into the
        protected sink, or raise. It must enforce the destination, deadline
        and budget there; it cannot return a permission or defer acceptance.
        No network trip, await or response wait belongs under the lock.
        A split or queued transport that cannot satisfy this boundary is
        unqualified here. The journal alone cannot fence physical egress.

        observe_receipt runs outside the authority lock. An exception,
        crash or missing receipt after commitment leaves the original in
        sending/unknown; only independent original evidence can resolve it.
        """
        require(type(pending) is PendingEffect and callable(accept)
                and callable(observe_receipt),'shared_receiver_unavailable')
        op = pending.operation
        with self.store.task_fence(op.fence.scope.key.claim_identity):
            with self.store.transaction() as (reg,state,save):
                _,_,task,_ = self._owner(reg,state,identity,op.fence,'consume',pending)
                record = self._operation(task,op)
                self._pending(record,pending)
                intent = CommittedSendIntent(pending,state['revision']+1)
                record.update(status='sending',send_intent_revision=intent.authority_revision)
                state['revision'] = intent.authority_revision
                save()
                # A durable write may outlast a time-bound lease, admission
                # proof or provider observation. Recheck without dropping the
                # global lock; no other valid signer can commit a stop in
                # between this qualification and synchronous acceptance.
                self._owner(reg,state,identity,op.fence,'receive',pending)
                accepted = accept(intent)
                if iscoroutine(accepted):
                    accepted.close()
                require(accepted is None,'shared_receive_requires_synchronous_acceptance')
            result = observe_receipt(intent)
            with self.store.transaction() as (reg,state,save):
                session,_ = self._peer(reg,state,identity,op.fence.scope,op.fence.source,
                                       'record_original',op)
                task,_ = self._task(state,op.fence.scope)
                return self._accept_terminal(state,session,task,op,result,save)

    async def release(self, identity, request):
        require(type(request) is ReleaseRequest and request.authority==identity,'shared_release_request')
        proof = request.completion
        f = proof.fence
        with self.store.task_fence(f.scope.key.claim_identity), self.store.transaction() as (reg,state,save):
            session,_ = self._peer(reg,state,identity,f.scope,f.source,'release',request)
            previous = self._original_receipt(state,session,request)
            if previous is not None:
                return ReleaseReceipt(request.kind,request.request_id,transition_request_sha256(request),proof)
            _,_,task,_ = self._owner(reg,state,identity,f,'release',request,live=False)
            require(f.coordinator_epoch == state['coordinator']['generation']
                    and all(o['status'] in TERMINAL_OPERATIONS for o in task['operations'].values()),
                    'shared_release_requires_reconciliation')
            require(self.verify_completion(session,proof) is True,'shared_completion_unverified')
            task['state'] = 'complete' if proof.accepted else 'failed'
            task['completion'] = {'accepted':proof.accepted,'receipt_sha256':proof.receipt_sha256}
            if task['lane'] == 'local' and state['mode'] == LOCAL_RECOVERY:
                capacity = state['local_limit']
                state['local_limit'] = (min(reg['local_ceiling'],next((n for n in RAMP if n > capacity),64))
                                        if proof.accepted else 1)
                if proof.accepted and state['local_limit'] == reg['local_ceiling']:
                    state['mode'] = LOCAL_READY
            result = ReleaseReceipt(request.kind,request.request_id,transition_request_sha256(request),proof)
            return self._record(state,session,request,result,save)
