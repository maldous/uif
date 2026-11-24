"""Temporary SQLite stores and synthetic callbacks, never production execution.

Fixture verifiers below deliberately stand in for the still-unimplemented
installed authentication/confinement/effect-reconciliation authority. They are
not reusable production qualification or cleanup evidence.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/windmill'))
import shared_admission as admission

fixture_spec = importlib.util.spec_from_file_location(
    'shared_admission_fixtures', Path(__file__).with_name('test_project_binding.py'))
fixtures = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixtures)
b = admission.binding


class SharedAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.registry = fixtures.fixture_registry()
        self.registry['service']['admission_root'] = self.directory.name
        limits = dict(operations=2, workers=4, overhead_processes=8, processes=12,
                      cpu_millis=4000, memory_bytes=4 * 1024**3,
                      output_bytes=16 * 1024**2, io_slots=4)
        self.policy = {
            'schema': admission.CAPACITY_SCHEMA, 'host_id': 'foo',
            'service_id': self.registry['service']['service_id'],
            'authority': self.registry['service']['admission_authority'],
            'admission_root': self.directory.name, 'global_limits': limits,
            'project_limits': {project: copy.deepcopy(limits) for project in ('offmenu', 'uif')}}
        self.current = True
        self.cleanup_valid = True
        self.calls = []

    def qualify(self):
        self.registry['service']['policy_sha256'] = b.digest(self.policy)
        fixtures.qualify(self.registry)

    def store(self, *, initialize=True, binding_verifier=None, cleanup_verifier=None):
        self.qualify()
        return admission.SharedAdmission(
            self.registry, self.policy,
            binding_verifier=binding_verifier or (lambda plan, phase: self.current),
            cleanup_verifier=cleanup_verifier or (lambda record, evidence: self.cleanup_valid),
            initialize_offline=initialize)

    def plan(self, project=0, task='task-one', resources=None):
        self.qualify()
        request, observed = fixtures.fixture_request(self.registry, project)
        request['task_id'] = task
        request['source']['worktree'] = self.registry['profiles'][project]['worktree_root'] + '/' + task
        worktree = request['source']['worktree']
        request['command']['cwd'] = worktree
        request['resources']['read_roots'] = [worktree]
        request['resources']['write_roots'] = [worktree + '/output']
        if resources:
            request['resources'].update(resources)
        fixtures.review(request)
        observed['source'] = copy.deepcopy(request['source'])
        return b.plan_dispatch(request, self.registry, observed), observed

    def adapter(self, record):
        self.calls.append(record['operation_id'])
        return 'running'

    @staticmethod
    def identity(record):
        return {key: record[key] for key in ('request_sha256', 'fence', 'revision')}

    @staticmethod
    def cleanup(record, terminal='succeeded'):
        return dict(schema=admission.CLEANUP_SCHEMA, operation_id=record['operation_id'],
                    request_sha256=record['request_sha256'], fence=record['fence'],
                    revision=record['revision'], terminal=terminal, launch_fenced=True,
                    executor_stopped=True, descendants_stopped=True, effects_reconciled=True,
                    evidence_sha256='e' * 64)

    def test_two_projects_share_one_database_and_separate_private_state(self):
        first, second = self.store(), self.store(initialize=False)
        p0, o0 = self.plan(0)
        p1, o1 = self.plan(1)
        self.assertEqual(first.database_path, second.database_path)
        self.assertNotEqual(p0['project_state_root'], p1['project_state_root'])
        r0 = first.admit(p0, o0)
        r1 = second.admit(p1, o1)
        self.assertEqual(second.get(r0['operation_id']), r0)
        self.assertEqual(first.get(r1['operation_id']), r1)
        self.assertFalse(r0['accepted_delivery'])
        self.assertEqual(list(Path(self.directory.name).iterdir()), [first.database_path])

    def test_simultaneous_cross_project_admission_has_one_global_winner(self):
        self.policy['global_limits']['operations'] = 1
        for limit in self.policy['project_limits'].values():
            limit['operations'] = 1
        store = self.store()
        plans = [self.plan(project) for project in (0, 1)]
        barrier = threading.Barrier(2)

        def attempt(item):
            independent = self.store(initialize=False)
            barrier.wait(timeout=10)
            try:
                return independent.admit(*item)['state']
            except admission.AdmissionError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, plans))
        self.assertCountEqual(results, ['intent', 'capacity_global_operations'])
        with sqlite3.connect(store.database_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 1)

    def test_simultaneous_duplicate_calls_attempt_callback_exactly_once(self):
        self.store()
        plan, observation = self.plan()
        barrier = threading.Barrier(2)

        def submit(_):
            store = self.store(initialize=False)
            barrier.wait(timeout=10)
            return store.submit_offline(plan, observation, self.adapter)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(self.calls, [plan['operation_id']])
        self.assertTrue(all(result['reserved'] for result in results))

    def test_intent_is_durable_before_synthetic_callback_and_never_replayed(self):
        store = self.store()
        plan, observed = self.plan()

        def inspect(record):
            reopened = self.store(initialize=False)
            saved = reopened.get(record['operation_id'])
            self.assertEqual(saved, record)
            self.assertEqual(saved['state'], 'effect-possible')
            self.assertTrue(saved['reserved'])
            return self.adapter(record)

        result = store.submit_offline(plan, observed, inspect)
        self.assertEqual(result['state'], 'running')
        self.assertEqual(store.submit_offline(plan, observed, self.adapter), result)
        self.assertEqual(self.calls, [plan['operation_id']])

    def test_crash_after_intent_before_callback_never_replays_on_restart(self):
        store = self.store()
        plan, observed = self.plan()
        committed = store.admit(plan, observed)
        self.assertEqual(committed['state'], 'intent')
        recovered = self.store(initialize=False).submit_offline(plan, observed, self.adapter)
        self.assertEqual(recovered, committed)
        self.assertEqual(self.calls, [])

    def test_crash_at_callback_boundary_retains_possible_effect_and_capacity(self):
        store = self.store()
        plan, observed = self.plan()

        def crash(record):
            self.calls.append(record['operation_id'])
            raise SystemExit('synthetic abrupt interruption')

        with self.assertRaises(SystemExit):
            store.submit_offline(plan, observed, crash)
        recovered = self.store(initialize=False).submit_offline(plan, observed, self.adapter)
        self.assertEqual(recovered['state'], 'effect-possible')
        self.assertTrue(recovered['reserved'])
        self.assertEqual(self.calls, [plan['operation_id']])

    def test_adapter_error_is_uncertain_and_does_not_release_or_retry(self):
        store = self.store()
        plan, observed = self.plan()

        def crash(record):
            self.calls.append(record['operation_id'])
            raise RuntimeError('synthetic uncertain result')

        with self.assertRaises(RuntimeError):
            store.submit_offline(plan, observed, crash)
        recovered = self.store(initialize=False).submit_offline(plan, observed, self.adapter)
        self.assertEqual(recovered['state'], 'uncertain')
        self.assertTrue(recovered['reserved'])
        self.assertEqual(self.calls, [plan['operation_id']])

    def test_callback_cannot_return_success_receipt_as_cleanup(self):
        store = self.store()
        plan, observed = self.plan()
        with self.assertRaisesRegex(admission.AdmissionError, 'offline_adapter_observation'):
            store.submit_offline(plan, observed, lambda record: {'success': True, 'cleaned': True})
        self.assertTrue(store.get(plan['operation_id'])['reserved'])
        self.assertEqual(store.get(plan['operation_id'])['state'], 'uncertain')

    def test_changed_request_for_same_task_is_refused_even_after_cleanup(self):
        store = self.store()
        original, observed = self.plan()
        record = store.admit(original, observed)
        changed, changed_observed = self.plan(resources={'cpu_millis': 1001})
        self.assertNotEqual(changed['operation_id'], original['operation_id'])
        for released in (False, True):
            if released:
                store.reconcile_cleanup(self.cleanup(record, 'not-started'))
            with self.assertRaisesRegex(admission.AdmissionError, 'task_identity_conflict'):
                store.submit_offline(changed, changed_observed, self.adapter)
        self.assertEqual(self.calls, [])

    def test_same_request_cannot_reuse_changed_source_observation_identity(self):
        store = self.store()
        plan, observed = self.plan()
        store.admit(plan, observed)
        observed['evidence_sha256'] = 'd' * 64
        changed = b.plan_dispatch(plan['request'], self.registry, observed)
        self.assertEqual(changed['operation_id'], plan['operation_id'])
        with self.assertRaisesRegex(admission.AdmissionError, 'operation_identity_conflict'):
            store.admit(changed, observed)

    def test_tampered_plan_fields_never_reach_verifier_or_adapter(self):
        store = self.store(binding_verifier=lambda *args: self.fail('invalid plan reached verifier'))
        for key, value in [('execution_started', True), ('accepted_delivery', True),
                           ('operation_id', 'foo-project-v1-' + 'a' * 64),
                           ('execution_identity', {'uid': 0, 'gid': 0})]:
            plan, observed = self.plan()
            plan[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(
                    admission.AdmissionError, 'plan_binding_conflict'):
                store.submit_offline(plan, observed, self.adapter)
        self.assertEqual(self.calls, [])

    def test_stale_registry_or_source_verifier_blocks_admission(self):
        store = self.store()
        plan, observed = self.plan()
        self.current = False
        with self.assertRaisesRegex(admission.AdmissionError, 'binding_not_current'):
            store.submit_offline(plan, observed, self.adapter)
        with self.assertRaisesRegex(admission.AdmissionError, 'unknown_operation'):
            store.get(plan['operation_id'])
        self.assertEqual(self.calls, [])

    def test_binding_rechecked_after_commit_before_offline_effect(self):
        phases = []

        def verify(plan, phase):
            phases.append(phase)
            return phase == 'admission'

        store = self.store(binding_verifier=verify)
        plan, observed = self.plan()
        with self.assertRaisesRegex(admission.AdmissionError, 'binding_not_current'):
            store.submit_offline(plan, observed, self.adapter)
        self.assertEqual(phases, ['admission', 'before-offline-effect'])
        self.assertEqual(store.get(plan['operation_id'])['state'], 'uncertain')
        self.assertTrue(store.get(plan['operation_id'])['reserved'])
        self.assertEqual(self.calls, [])

    def test_verifier_truthy_nonboolean_does_not_authorize(self):
        store = self.store(binding_verifier=lambda *args: 1)
        with self.assertRaisesRegex(admission.AdmissionError, 'binding_not_current'):
            store.admit(*self.plan())

    def test_cancellation_observation_and_exit_are_not_completed_cleanup(self):
        store = self.store()
        record = store.submit_offline(*self.plan(), self.adapter)
        record = store.request_cancel(record['operation_id'], **self.identity(record))
        self.assertEqual(record['state'], 'running')
        self.assertTrue(record['cancel_requested'])
        for observation in ('cancel-observed', 'exited', 'unknown'):
            record = store.observe(record['operation_id'], observation, **self.identity(record))
            self.assertTrue(record['reserved'])
            self.assertIsNone(record['cleanup'])
        result = store.reconcile_cleanup(self.cleanup(record, 'cancelled'))
        self.assertFalse(result['reserved'])
        self.assertFalse(result['accepted_delivery'])

    def test_cancel_between_commit_and_effect_prevents_offline_callback(self):
        def verifier(plan, phase):
            if phase == 'before-offline-effect':
                record = store.get(plan['operation_id'])
                store.request_cancel(record['operation_id'], **self.identity(record))
            return True

        store = self.store(binding_verifier=verifier)
        record = store.submit_offline(*self.plan(), self.adapter)
        self.assertEqual(record['state'], 'intent')
        self.assertTrue(record['cancel_requested'])
        self.assertTrue(record['reserved'])
        self.assertEqual(self.calls, [])

    def test_cleanup_requires_every_assertion_and_independent_verifier(self):
        store = self.store()
        record = store.admit(*self.plan())
        for field in ('launch_fenced', 'executor_stopped', 'descendants_stopped', 'effects_reconciled'):
            for value in (False, 1, 'true', None):
                evidence = self.cleanup(record)
                evidence[field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                        admission.AdmissionError, 'cleanup_not_verified'):
                    store.reconcile_cleanup(evidence)
        for value in (False, 1):
            self.cleanup_valid = value
            with self.assertRaisesRegex(admission.AdmissionError, 'cleanup_verifier_refused'):
                store.reconcile_cleanup(self.cleanup(record))
        self.assertTrue(store.get(record['operation_id'])['reserved'])

    def test_cleanup_fences_request_owner_and_revision(self):
        store = self.store()
        record = store.admit(*self.plan())
        for field, value in [('request_sha256', 'f' * 64), ('fence', 'foreign-fence'),
                             ('revision', record['revision'] + 1), ('revision', True)]:
            evidence = self.cleanup(record)
            evidence[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    admission.AdmissionError, 'operation_fence_conflict'):
                store.reconcile_cleanup(evidence)
        stale = self.cleanup(record)
        advanced = store.request_cancel(record['operation_id'], **self.identity(record))
        with self.assertRaisesRegex(admission.AdmissionError, 'operation_fence_conflict'):
            store.reconcile_cleanup(stale)
        self.assertTrue(advanced['reserved'])

    def test_verified_cleanup_is_idempotent_and_keeps_no_replay_tombstone(self):
        store = self.store()
        plan, observed = self.plan()
        record = store.submit_offline(plan, observed, self.adapter)
        evidence = self.cleanup(record)
        released = store.reconcile_cleanup(evidence)
        self.assertEqual(store.reconcile_cleanup(evidence), released)
        self.assertEqual(self.store(initialize=False).submit_offline(
            plan, observed, self.adapter), released)
        self.assertEqual(self.calls, [plan['operation_id']])
        changed = dict(evidence, terminal='failed')
        with self.assertRaisesRegex(admission.AdmissionError, 'cleanup_identity_conflict'):
            store.reconcile_cleanup(changed)

    def test_late_offline_callback_cannot_overwrite_verified_cleanup(self):
        store = self.store()

        def callback(record):
            # Synthetic verifier models an external launch/cleanup fence. This
            # does NOT qualify real process fencing or safe concurrent cleanup.
            store.reconcile_cleanup(self.cleanup(record))
            return 'running'

        result = store.submit_offline(*self.plan(), callback)
        self.assertEqual(result['state'], 'released')
        self.assertFalse(result['reserved'])

    def test_stale_observation_cannot_overwrite_cancellation(self):
        store = self.store()
        old = store.submit_offline(*self.plan(), self.adapter)
        current = store.request_cancel(old['operation_id'], **self.identity(old))
        with self.assertRaisesRegex(admission.AdmissionError, 'operation_fence_conflict'):
            store.observe(old['operation_id'], 'running', **self.identity(old))
        self.assertEqual(store.get(old['operation_id']), current)

    def test_observation_state_machine_refuses_running_after_exited(self):
        store = self.store()
        record = store.submit_offline(*self.plan(), self.adapter)
        record = store.observe(record['operation_id'], 'exited', **self.identity(record))
        with self.assertRaisesRegex(admission.AdmissionError, 'observation_transition'):
            store.observe(record['operation_id'], 'running', **self.identity(record))
        self.assertTrue(store.get(record['operation_id'])['reserved'])

    def test_each_global_ceiling_counts_outstanding_cross_project_reservations(self):
        baseline = copy.deepcopy(self.policy)
        demands = dict(operations=1, workers=1, overhead_processes=2, processes=3,
                       cpu_millis=1000, memory_bytes=1024**3, output_bytes=1024**2, io_slots=1)
        for dimension, demand in demands.items():
            with self.subTest(dimension=dimension), tempfile.TemporaryDirectory() as directory:
                self.policy = copy.deepcopy(baseline)
                self.policy['admission_root'] = directory
                self.registry['service']['admission_root'] = directory
                self.policy['global_limits'][dimension] = demand
                for limits in self.policy['project_limits'].values():
                    limits[dimension] = demand
                store = self.store()
                store.admit(*self.plan(0))
                with self.assertRaisesRegex(admission.AdmissionError,
                                            'capacity_global_' + dimension):
                    store.admit(*self.plan(1))

    def test_per_project_ceiling_leaves_capacity_for_another_project(self):
        self.policy['project_limits']['offmenu']['operations'] = 1
        store = self.store()
        store.admit(*self.plan(0))
        with self.assertRaisesRegex(admission.AdmissionError, 'capacity_project_operations'):
            store.admit(*self.plan(0, 'task-two'))
        self.assertTrue(store.admit(*self.plan(1))['reserved'])

    def test_unknown_and_cancelled_work_keep_global_capacity_after_restart(self):
        store = self.store()
        record = store.submit_offline(*self.plan(0), self.adapter)
        record = store.observe(record['operation_id'], 'unknown', **self.identity(record))
        cancelled = store.submit_offline(*self.plan(1), self.adapter)
        cancelled = store.request_cancel(cancelled['operation_id'], **self.identity(cancelled))
        store.observe(cancelled['operation_id'], 'cancel-observed', **self.identity(cancelled))
        reopened = self.store(initialize=False)
        with self.assertRaisesRegex(admission.AdmissionError, 'capacity_global_operations'):
            reopened.admit(*self.plan(0, 'task-two'))
        reopened.reconcile_cleanup(self.cleanup(record, 'failed'))
        self.assertTrue(reopened.admit(*self.plan(0, 'task-two'))['reserved'])

    def test_logical_write_read_overlap_conflicts_but_reads_share(self):
        store = self.store()
        first, observed0 = self.plan(0)
        second, observed1 = self.plan(0, 'task-two')
        root = self.registry['profiles'][0]['data_roots'][0]
        for plan, write in ((first, True), (second, False)):
            plan['request']['resources']['read_roots'] = [root]
            plan['request']['resources']['write_roots'] = [root + '/shared'] if write else []
            fixtures.review(plan['request'])
        first = b.plan_dispatch(first['request'], self.registry, observed0)
        second = b.plan_dispatch(second['request'], self.registry, observed1)
        store.admit(first, observed0)
        with self.assertRaisesRegex(admission.AdmissionError, 'capacity_paths_conflict'):
            store.admit(second, observed1)
        first_record = store.get(first['operation_id'])
        store.reconcile_cleanup(self.cleanup(first_record, 'not-started'))
        store.admit(second, observed1)
        third, observed2 = self.plan(0, 'task-three', {'read_roots': [root], 'write_roots': []})
        self.assertTrue(store.admit(third, observed2)['reserved'])

    def test_negative_zero_boolean_and_fractional_capacities_are_rejected(self):
        for key in admission.CAPACITY_FIELDS:
            values = [-1, True, 1.5, '2'] + ([] if key == 'output_bytes' else [0])
            for value in values:
                policy = copy.deepcopy(self.policy)
                policy['global_limits'][key] = value
                registry = copy.deepcopy(self.registry)
                # Floats cannot even acquire a canonical policy pin.
                with self.subTest(key=key, value=value), self.assertRaises(
                        (admission.AdmissionError, b.BindingError)):
                    registry['service']['policy_sha256'] = b.digest(policy)
                    fixtures.qualify(registry)
                    admission.SharedAdmission(registry, policy, binding_verifier=lambda *args: True,
                                              cleanup_verifier=lambda *args: True,
                                              initialize_offline=True)

    def test_changed_capacity_or_registry_cannot_reset_existing_pool(self):
        store = self.store()
        record = store.admit(*self.plan())
        original_policy = copy.deepcopy(self.policy)
        self.policy['global_limits']['operations'] += 1
        with self.assertRaisesRegex(admission.AdmissionError, 'admission_pool_configuration_conflict'):
            self.store(initialize=False)
        self.policy = original_policy
        self.registry['profiles'][0]['isolation_sha256'] = 'f' * 64
        with self.assertRaisesRegex(admission.AdmissionError, 'admission_pool_configuration_conflict'):
            self.store(initialize=False)
        self.assertEqual(store.get(record['operation_id']), record)

    def test_missing_authority_database_is_not_silently_recreated(self):
        store = self.store()
        self.assertRaisesRegex(admission.AdmissionError, 'admission_database_already_exists',
                               self.store)
        saved = store.database_path.with_suffix('.saved')
        store.database_path.rename(saved)
        with self.assertRaisesRegex(admission.AdmissionError, 'admission_database_missing'):
            self.store(initialize=False)
        self.assertFalse(store.database_path.exists())

    def test_database_protects_immutable_intent_config_and_tombstones(self):
        store = self.store()
        store.admit(*self.plan())
        for statement in ("UPDATE operations SET fence = 'new-owner'",
                          "UPDATE operations SET plan_json = '{}'",
                          'DELETE FROM operations', 'DELETE FROM configuration',
                          "UPDATE configuration SET body = '{}'"):
            with self.subTest(statement=statement), sqlite3.connect(store.database_path) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)

    def test_corrupt_unknown_record_state_never_creates_free_capacity(self):
        store = self.store()
        store.admit(*self.plan())
        with sqlite3.connect(store.database_path) as db:
            db.execute("UPDATE operations SET state = 'expired', reserved = 0")
        with self.assertRaisesRegex(admission.AdmissionError, 'stored_state_corrupt'):
            store.admit(*self.plan(1))

    def test_corrupt_retained_cleanup_never_creates_free_capacity(self):
        store = self.store()
        record = store.admit(*self.plan())
        evidence = self.cleanup(record, 'not-started')
        store.reconcile_cleanup(evidence)
        corruptions = ['{}', '{', 'null']
        for field, value in (
                ('schema', 'unknown'), ('terminal', 'running'),
                ('operation_id', 'foo-project-v1-' + 'a' * 64),
                ('request_sha256', 'b' * 64), ('fence', 'foreign-fence'),
                ('revision', record['revision'] + 1), ('revision', True),
                ('revision', 0), ('evidence_sha256', 'not-a-pin'),
                ('unexpected', True)):
            corruptions.append(json.dumps(dict(evidence, **{field: value})))
        for field in ('launch_fenced', 'executor_stopped', 'descendants_stopped',
                      'effects_reconciled'):
            for value in (False, 1):
                corruptions.append(json.dumps(dict(evidence, **{field: value})))
        next_plan = self.plan(1)
        for raw in corruptions:
            with self.subTest(cleanup=raw):
                with sqlite3.connect(store.database_path) as db:
                    db.execute('UPDATE operations SET cleanup_json = ?', (raw,))
                with self.assertRaisesRegex(admission.AdmissionError, 'stored_cleanup_corrupt'):
                    store.get(record['operation_id'])
                with self.assertRaisesRegex(admission.AdmissionError, 'stored_cleanup_corrupt'):
                    store.admit(*next_plan)

    def test_retained_cleanup_read_does_not_reinvoke_external_verifier(self):
        store = self.store()
        record = store.admit(*self.plan())
        released = store.reconcile_cleanup(self.cleanup(record, 'not-started'))
        reopened = self.store(initialize=False, cleanup_verifier=lambda *args: self.fail(
            'retained cleanup read must not invoke external verification'))
        self.assertEqual(reopened.get(record['operation_id']), released)
        self.assertTrue(reopened.admit(*self.plan(1))['reserved'])

    def test_parent_input_and_callback_mutation_cannot_change_durable_intent(self):
        store = self.store()
        plan, observed = self.plan()
        expected = copy.deepcopy(plan)

        def mutate(record):
            record['plan']['request']['resources']['workers'] = 999
            record['fence'] = 'changed'
            return 'running'

        record = store.submit_offline(plan, observed, mutate)
        plan['request']['resources']['workers'] = 999
        self.assertEqual(record['plan'], expected)
        self.assertEqual(store.get(record['operation_id'])['plan'], expected)


if __name__ == '__main__':
    unittest.main()
