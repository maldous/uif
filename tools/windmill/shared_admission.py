"""Transactional, shared admission bookkeeping; NOT an installed Foo service.

The database location comes only from the trusted service registry, outside all
project roots. SQLite BEGIN IMMEDIATE serializes every project's reservations.
This module neither launches commands nor enforces resource/process confinement.
``submit_offline`` exercises an injected SYNTHETIC adapter for offline tests only;
there is deliberately no production execution, transport, shell or root backend.

The embedding authority must authenticate callers and supply current, independent
binding/cleanup verifiers. A verifier returning True is a trust seam, not evidence
that production verification exists. In particular cleanup verification must
fence *future* launches as well as verify stopped executors/descendants and effect
reconciliation. A future installed adapter must enforce that fence atomically at
its launch boundary. The offline callback is not that confinement boundary.

Provisioning, ownership, symlink/mount races, database replacement, durable-volume
loss and authority migration require the installed service's trusted storage
boundary. This code does not change permissions, install a service, migrate old
jobs, emit execution receipts or establish delivery acceptance. There are no TTLs,
automatic stale-lease reclamation, retry/resume callbacks, or tombstone deletion.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

try:
    from . import project_binding as binding
except ImportError:  # Direct import with tools/windmill on sys.path.
    import project_binding as binding


CAPACITY_SCHEMA = 'foo-shared-capacity/v1'
RECORD_SCHEMA = 'foo-shared-admission-record/v1'
CLEANUP_SCHEMA = 'foo-shared-cleanup/v1'
CAPACITY_FIELDS = binding.RESOURCE_FIELDS | {'operations', 'processes'}
STATES = {'intent', 'effect-possible', 'running', 'cancel-observed', 'exited',
          'uncertain', 'released'}
OBSERVATIONS = {'running', 'cancel-observed', 'exited', 'unknown'}
TRANSITIONS = {
    'intent': {'cancel-observed', 'uncertain'},
    'effect-possible': {'running', 'cancel-observed', 'exited', 'uncertain'},
    'running': {'running', 'cancel-observed', 'exited', 'uncertain'},
    'cancel-observed': {'cancel-observed', 'exited', 'uncertain'},
    'exited': {'exited', 'uncertain'},
    'uncertain': {'running', 'cancel-observed', 'exited', 'uncertain'},
    'released': set(),
}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed-out', 'not-started'}


class AdmissionError(ValueError):
    """Fail-closed admission, identity, capacity, or reconciliation refusal."""


def require(condition, reason):
    if not condition:
        raise AdmissionError(reason)


def cleanup_evidence(value):
    """Validate declarations only; this does not independently verify cleanup."""
    evidence = binding.detached(value)
    binding.fields(evidence, {'schema', 'operation_id', 'request_sha256', 'fence',
                              'revision', 'terminal', 'launch_fenced', 'executor_stopped',
                              'descendants_stopped', 'effects_reconciled', 'evidence_sha256'},
                   'cleanup_fields')
    require(evidence['schema'] == CLEANUP_SCHEMA and
            type(evidence['terminal']) is str and evidence['terminal'] in TERMINAL,
            'cleanup_terminal')
    binding.pin(evidence['evidence_sha256'], 'cleanup_evidence_pin')
    require(all(evidence[key] is True for key in
                ('launch_fenced', 'executor_stopped', 'descendants_stopped',
                 'effects_reconciled')), 'cleanup_not_verified')
    return evidence


def capacity_policy(value, registry):
    """Validate the service-pinned aggregate policy, not per-job allocations."""
    policy = binding.detached(value)
    binding.fields(policy, {'schema', 'host_id', 'service_id', 'authority',
                            'admission_root', 'global_limits', 'project_limits'},
                   'capacity_policy_fields')
    service = registry['service']
    require(policy['schema'] == CAPACITY_SCHEMA, 'capacity_policy_version')
    require(policy['host_id'] == registry['host_id'] and
            policy['service_id'] == service['service_id'] and
            policy['authority'] == service['admission_authority'] and
            policy['admission_root'] == service['admission_root'], 'capacity_identity')
    require(binding.digest(policy) == service['policy_sha256'], 'capacity_policy_pin')
    projects = policy['project_limits']
    require(type(projects) is dict and set(projects) == {
        profile['project_id'] for profile in registry['profiles']}, 'capacity_projects')
    for limits in [policy['global_limits'], *projects.values()]:
        binding.resource_numbers(limits, CAPACITY_FIELDS)
        require(limits['processes'] >= 2, 'capacity_processes')
    for limits in projects.values():
        require(all(limits[key] <= policy['global_limits'][key]
                    for key in CAPACITY_FIELDS), 'capacity_project_exceeds_global')
    return policy


class SharedAdmission:
    """One fixed database per trusted admission root, shared by every project.

    ``binding_verifier(plan, phase)`` must return exactly True only for current
    authenticated registry, source, review, ownership and isolation bindings.
    ``cleanup_verifier(record, evidence)`` must independently verify the exact
    operation/fence/revision and all cleanup assertions. Both are read-only seams.
    Callbacks receive detached copies. No built-in verifier assumes trust.
    """

    def __init__(self, registry, policy, *, binding_verifier, cleanup_verifier,
                 initialize_offline=False):
        self._registry = binding.validate_registry(registry)
        self._policy = capacity_policy(policy, self._registry)
        require(callable(binding_verifier) and callable(cleanup_verifier),
                'independent_verifiers_required')
        self._binding_verifier = binding_verifier
        self._cleanup_verifier = cleanup_verifier
        self._root = Path(self._registry['service']['admission_root'])
        self.database_path = self._root / 'shared-admission.sqlite3'
        require(type(initialize_offline) is bool, 'offline_initialization_flag')
        # This explicit bootstrap switch exists for offline temporary fixtures.
        # Normal reopen MUST fail on a missing store, not invent an empty pool.
        # Real first-install/custody verification is intentionally not implemented.
        require(not initialize_offline or not self.database_path.exists(),
                'admission_database_already_exists')
        self._configuration = binding.canonical_bytes({
            'schema': 'foo-shared-admission-store/v1',
            'registry_sha256': binding.digest(self._registry),
            'capacity_sha256': binding.digest(self._policy),
            'host_id': self._registry['host_id'],
            'service': self._registry['service']}).decode('ascii')
        with self._transaction(initializing=initialize_offline) as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            require(not tables or tables == {'configuration', 'operations'},
                    'store_schema_conflict')
            if not tables:
                db.execute('CREATE TABLE configuration (id INTEGER PRIMARY KEY '
                           'CHECK(id = 1), body TEXT NOT NULL)')
                db.execute('''CREATE TABLE operations (
                    operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL,
                    fence TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    reserved INTEGER NOT NULL CHECK(reserved IN (0, 1)),
                    cancel_requested INTEGER NOT NULL CHECK(cancel_requested IN (0, 1)),
                    cleanup_json TEXT, UNIQUE(project_id, task_id))''')
                db.execute('INSERT INTO configuration VALUES (1, ?)',
                           (self._configuration,))
                db.execute('''CREATE TRIGGER immutable_intent BEFORE UPDATE OF
                    operation_id, project_id, task_id, request_sha256, plan_sha256,
                    plan_json, fence ON operations BEGIN
                    SELECT RAISE(ABORT, 'immutable_operation_intent'); END''')
                for table in ('configuration', 'operations'):
                    db.execute(f'''CREATE TRIGGER preserve_{table} BEFORE DELETE ON
                        {table} BEGIN SELECT RAISE(ABORT, 'retained_identity'); END''')
                db.execute('''CREATE TRIGGER immutable_configuration BEFORE UPDATE ON
                    configuration BEGIN SELECT RAISE(ABORT, 'immutable_pool'); END''')
            self._check_configuration(db)

    @contextmanager
    def _transaction(self, *, initializing=False):
        # The service provisions the root. Never create a project-selected pool
        # or silently create a missing authority after its database is lost.
        require(self._root.is_dir() and self._root.resolve() == self._root,
                'admission_root_unavailable_or_aliased')
        require(not self.database_path.is_symlink(), 'admission_database_aliased')
        require(initializing or self.database_path.is_file(), 'admission_database_missing')
        db = sqlite3.connect(str(self.database_path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous = FULL')
            db.execute('BEGIN IMMEDIATE')
            if not initializing:
                self._check_configuration(db)
            yield db
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def _check_configuration(self, db):
        rows = db.execute('SELECT body FROM configuration').fetchall()
        require(len(rows) == 1 and rows[0]['body'] == self._configuration,
                'admission_pool_configuration_conflict')

    def _decode(self, row):
        require(row is not None, 'unknown_operation')
        plan = json.loads(row['plan_json'])
        request = plan['request']
        require(binding.digest(plan) == row['plan_sha256'] and
                binding.digest(request) == row['request_sha256'] and
                plan['operation_id'] == row['operation_id'] ==
                'foo-project-v1-' + row['request_sha256'] and
                row['project_id'] == request['project_id'] and
                row['task_id'] == request['task_id'], 'stored_intent_corrupt')
        require(row['state'] in STATES and row['revision'] > 0 and
                row['reserved'] == int(row['state'] != 'released') and
                row['cancel_requested'] in (0, 1) and
                (row['cleanup_json'] is not None) == (row['state'] == 'released'),
                'stored_state_corrupt')
        cleanup = None
        if row['state'] == 'released':
            # A malformed retained cleanup must not manufacture free capacity.
            # Revalidate its declarations/bindings, not the external evidence:
            # the independent verifier was required at the release transition.
            try:
                cleanup = cleanup_evidence(json.loads(row['cleanup_json']))
                require(cleanup['operation_id'] == row['operation_id'] and
                        cleanup['request_sha256'] == row['request_sha256'] and
                        cleanup['fence'] == row['fence'] and
                        type(cleanup['revision']) is int and cleanup['revision'] >= 1 and
                        cleanup['revision'] == row['revision'] - 1,
                        'stored_cleanup_binding')
            except (ValueError, TypeError) as error:
                raise AdmissionError('stored_cleanup_corrupt') from error
        return {'schema': RECORD_SCHEMA, 'operation_id': row['operation_id'],
                'request_sha256': row['request_sha256'], 'plan_sha256': row['plan_sha256'],
                'project_id': row['project_id'], 'task_id': row['task_id'],
                'fence': row['fence'], 'revision': row['revision'], 'state': row['state'],
                'reserved': bool(row['reserved']),
                'cancel_requested': bool(row['cancel_requested']), 'plan': plan,
                'cleanup': cleanup,
                'accepted_delivery': False}

    def _get(self, db, operation_id):
        return self._decode(db.execute('SELECT * FROM operations WHERE operation_id = ?',
                                      (operation_id,)).fetchone())

    def get(self, operation_id):
        """Observe the same identity, without reclaiming or replaying anything."""
        with self._transaction() as db:
            return self._get(db, operation_id)

    def _check_binding(self, plan, phase):
        require(self._binding_verifier(binding.detached(plan), phase) is True,
                'binding_not_current')

    def _check_capacity(self, plan, active):
        request = plan['request']
        resources = request['resources']

        def demand(item):
            values = {key: item[key] for key in binding.RESOURCE_FIELDS}
            return dict(values, operations=1,
                        processes=item['workers'] + item['overhead_processes'])

        for scope, limits, records in (
                ('global', self._policy['global_limits'], active),
                ('project', self._policy['project_limits'][request['project_id']],
                 [record for record in active
                  if record['project_id'] == request['project_id']])):
            values = [demand(record['plan']['request']['resources']) for record in records]
            values.append(demand(resources))
            for key in sorted(CAPACITY_FIELDS):
                require(sum(value[key] for value in values) <= limits[key],
                        'capacity_' + scope + '_' + key)
        for record in active:
            other = record['plan']['request']['resources']
            conflict = (any(binding.overlaps(binding.path(a), binding.path(b))
                            for a in resources['write_roots']
                            for b in other['write_roots'] + other['read_roots']) or
                        any(binding.overlaps(binding.path(a), binding.path(b))
                            for a in resources['read_roots'] for b in other['write_roots']))
            require(not conflict, 'capacity_paths_conflict')

    def _admit(self, plan, observation):
        plan = binding.detached(plan)
        expected = binding.plan_dispatch(plan['request'], self._registry, observation)
        require(binding.canonical_bytes(plan) == binding.canonical_bytes(expected),
                'plan_binding_conflict')
        plan_sha = binding.digest(plan)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM operations WHERE operation_id = ?',
                             (plan['operation_id'],)).fetchone()
            if row is not None:
                require(row['plan_sha256'] == plan_sha, 'operation_identity_conflict')
                return self._decode(row), False
            request = plan['request']
            require(db.execute('SELECT 1 FROM operations WHERE project_id = ? AND task_id = ?',
                               (request['project_id'], request['task_id'])).fetchone() is None,
                    'task_identity_conflict')
            self._check_binding(plan, 'admission')
            # Unknown/corrupt state is never silently omitted from accounting.
            records = [self._decode(row) for row in db.execute('SELECT * FROM operations')]
            self._check_capacity(plan, [record for record in records if record['reserved']])
            db.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, NULL)',
                       (plan['operation_id'], request['project_id'], request['task_id'],
                        plan['request_sha256'], plan_sha,
                        binding.canonical_bytes(plan).decode('ascii'), uuid.uuid4().hex, 'intent'))
            return self._get(db, plan['operation_id']), True

    def admit(self, plan, observation):
        """Commit immutable intent/reservation only. This never executes or resumes.

        A subsequent submit_offline of this identity is a duplicate and will NOT
        call its adapter. Recover this record only through observation/cleanup.
        """
        return self._admit(plan, observation)[0]

    @staticmethod
    def _match(record, request_sha256, fence, revision):
        require(type(revision) is int and record['revision'] == revision and
                record['request_sha256'] == request_sha256 and record['fence'] == fence,
                'operation_fence_conflict')

    def request_cancel(self, operation_id, *, request_sha256, fence, revision):
        """Record cancellation intent, not delivery, stopped children, or cleanup."""
        with self._transaction() as db:
            record = self._get(db, operation_id)
            self._match(record, request_sha256, fence, revision)
            if record['state'] != 'released' and not record['cancel_requested']:
                db.execute('UPDATE operations SET cancel_requested = 1, revision = revision + 1 '
                           'WHERE operation_id = ?', (operation_id,))
            return self._get(db, operation_id)

    def observe(self, operation_id, observation, *, request_sha256, fence, revision):
        """Retain capacity for running, exited, cancelled-observed and unknown work."""
        require(type(observation) is str and observation in OBSERVATIONS, 'observation_state')
        with self._transaction() as db:
            record = self._get(db, operation_id)
            self._match(record, request_sha256, fence, revision)
            require(record['state'] != 'released', 'operation_already_released')
            state = 'uncertain' if observation == 'unknown' else observation
            require(state in TRANSITIONS[record['state']], 'observation_transition')
            db.execute('UPDATE operations SET state = ?, revision = revision + 1 '
                       'WHERE operation_id = ?', (state, operation_id))
            return self._get(db, operation_id)

    def _offline_observe(self, record, observation):
        try:
            return self.observe(record['operation_id'], observation,
                                request_sha256=record['request_sha256'], fence=record['fence'],
                                revision=record['revision'])
        except AdmissionError as error:
            if str(error) not in ('operation_fence_conflict', 'operation_already_released'):
                raise
            # Cancellation or reconciliation advanced the fence. Late callback
            # results cannot regress it, reacquire capacity or overwrite cleanup.
            return self.get(record['operation_id'])

    def submit_offline(self, plan, observation, offline_adapter):
        """Offline test seam only. At most one callback attempt per immutable intent.

        The commit happens before any adapter callback. A crash at any later
        instruction retains the intent/reservation; duplicates never call again.
        Adapter results are observations, NEVER successful execution receipts.
        """
        require(callable(offline_adapter), 'offline_adapter_required')
        record, created = self._admit(plan, observation)
        if not created:
            return record
        try:
            self._check_binding(record['plan'], 'before-offline-effect')
        except Exception:
            self._offline_observe(record, 'unknown')
            raise
        with self._transaction() as db:
            current = self._get(db, record['operation_id'])
            if current['revision'] != record['revision'] or current['cancel_requested']:
                return current
            db.execute("UPDATE operations SET state = 'effect-possible', revision = revision + 1 "
                       'WHERE operation_id = ?', (record['operation_id'],))
            record = self._get(db, record['operation_id'])
        try:
            observation = offline_adapter(binding.detached(record))
            require(type(observation) is str and observation in OBSERVATIONS,
                    'offline_adapter_observation')
        except Exception:
            self._offline_observe(record, 'unknown')
            raise
        return self._offline_observe(record, observation)

    def reconcile_cleanup(self, evidence):
        """Release only after exact, current, independently verified terminal cleanup.

        Neither cancellation requests, exit observations, heartbeat age nor
        callback errors enter this transition. Released identities remain forever.
        """
        evidence = cleanup_evidence(evidence)
        with self._transaction() as db:
            record = self._get(db, evidence['operation_id'])
            if record['state'] == 'released':
                require(binding.canonical_bytes(record['cleanup']) ==
                        binding.canonical_bytes(evidence), 'cleanup_identity_conflict')
                return record
            self._match(record, evidence['request_sha256'], evidence['fence'], evidence['revision'])
            require(self._cleanup_verifier(binding.detached(record),
                                           binding.detached(evidence)) is True,
                    'cleanup_verifier_refused')
            db.execute("UPDATE operations SET state = 'released', reserved = 0, "
                       'revision = revision + 1, cleanup_json = ? WHERE operation_id = ?',
                       (binding.canonical_bytes(evidence).decode('ascii'), evidence['operation_id']))
            return self._get(db, evidence['operation_id'])
