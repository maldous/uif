"""Pure synthetic declarations, never execution or deployment evidence."""
import ast
import copy
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'tools/windmill/project_binding.py'
spec = importlib.util.spec_from_file_location('project_binding', SOURCE)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def qualify(registry):
    for profile in registry['profiles']:
        profile['qualification'] = {
            'status': 'deployment-qualified',  # Synthetic fixture, not a real grant.
            'profile_sha256': b.profile_digest(profile),
            'service_sha256': b.digest(registry['service']), 'evidence_sha256': '7' * 64}
    return registry


def fixture_registry():
    limits = dict(workers=64, overhead_processes=8, cpu_millis=19000,
                  memory_bytes=42 * 1024**3, output_bytes=64 * 1024**3,
                  io_slots=32, timeout_seconds=1800)
    registry = {
        'schema': b.REGISTRY, 'host_id': 'foo',
        'service': {'service_id': 'foo-bounded-execution',
                    'root': '/opt/foo-bounded-execution', 'runner_sha256': '1' * 64,
                    'policy_sha256': '2' * 64, 'admission_authority': 'foo-host-admission',
                    'admission_root': '/var/lib/foo-execution/admission',
                    'resource_limits': limits}, 'profiles': []}
    for project, uid in [('offmenu', 985), ('uif', 986)]:
        registry['profiles'].append({
            'project_id': project, 'profile_id': project + '-validation-v1',
            'repository': 'maldous/' + project, 'owner': project + '-owner',
            'uid': uid, 'gid': uid, 'checkout_root': '/home/user/src/' + project,
            'worktree_root': '/var/lib/foo-projects/' + project + '/worktrees',
            'state_root': '/var/lib/foo-projects/' + project + '/state',
            'data_roots': ['/home/user/src/' + project + '-data'],
            'executables': ['/usr/bin/python3'], 'resource_limits': copy.deepcopy(limits),
            'isolation_sha256': '3' * 64})
    return qualify(registry)


def fixture_request(registry, index=0):
    profile = registry['profiles'][index]
    worktree = profile['worktree_root'] + '/task-one'
    request = {'schema': b.ENVELOPE, 'intent': 'new-operation', 'task_id': 'task-one',
               'host_id': 'foo', 'project_id': profile['project_id'],
               'profile_id': profile['profile_id'], 'repository': profile['repository'],
               'owner': profile['owner'], 'registry_sha256': b.digest(registry),
               'source': {'branch': 'feature/shared-validation', 'head': '4' * 40,
                          'tree': '5' * 40, 'worktree': worktree,
                          'uid': profile['uid'], 'gid': profile['gid']},
               'command': {'argv': ['/usr/bin/python3', '-B', '-m', 'unittest'],
                           'cwd': worktree, 'timeout_seconds': 180},
               'resources': {'workers': 1, 'overhead_processes': 2, 'cpu_millis': 1000,
                             'memory_bytes': 1024**3, 'output_bytes': 1024**2, 'io_slots': 1,
                             'read_roots': [worktree], 'write_roots': [worktree + '/output']}}
    review(request)
    observation = {key: copy.deepcopy(request[key]) for key in
                   ('host_id', 'project_id', 'repository', 'owner', 'source')}
    observation.update(schema=b.OBSERVATION, clean=True, canonical_paths=True,
                       evidence_sha256='9' * 64)
    return request, observation


def review(request):
    request['review'] = {'reviewer': 'independent-reviewer', 'disposition': 'approved',
                         'binding_sha256': b.reviewed_binding_digest(request),
                         'evidence_sha256': '8' * 64}


class ProjectBindingTests(unittest.TestCase):
    def setUp(self):
        self.registry = fixture_registry()
        self.request, self.observation = fixture_request(self.registry)

    def plan(self, **kwargs):
        return b.plan_dispatch(self.request, self.registry, self.observation, **kwargs)

    def test_valid_new_operation_is_only_a_plan_never_a_receipt(self):
        plan = self.plan()
        self.assertEqual(plan['schema'], b.PLAN)
        self.assertEqual(plan['status'], 'plan-only')
        self.assertFalse(plan['execution_started'])
        self.assertFalse(plan['accepted_delivery'])
        self.assertFalse(plan['admission']['granted'])
        self.assertTrue(plan['admission']['required'])
        self.assertNotIn('receipt', plan)
        self.assertNotIn('exit_code', plan)
        self.assertEqual(plan['operation_id'], 'foo-project-v1-' + b.digest(self.request))

    def test_every_project_uses_one_global_admission_not_its_state_root(self):
        first = self.plan()
        second = b.plan_dispatch(*self.reverse_fixture())
        self.assertEqual(first['admission'], second['admission'])
        self.assertNotEqual(first['project_state_root'], second['project_state_root'])
        self.assertNotEqual(first['operation_id'], second['operation_id'])
        self.assertNotEqual(first['admission']['root'], first['project_state_root'])

    def reverse_fixture(self):
        request, observation = fixture_request(self.registry, 1)
        return request, self.registry, observation

    def test_canonical_identity_is_stable_and_every_bound_change_changes_it(self):
        first = self.plan()
        reordered = dict(reversed(list(self.request.items())))
        self.assertEqual(b.digest(reordered), first['request_sha256'])
        self.assertEqual(self.plan(), first)
        self.request['command']['timeout_seconds'] += 1
        with self.assertRaisesRegex(b.BindingError, 'review_drift'):
            self.plan()
        review(self.request)
        self.assertNotEqual(self.plan()['operation_id'], first['operation_id'])

    def test_returned_plan_is_detached_from_all_caller_objects(self):
        first = self.plan()
        expected = copy.deepcopy(first)
        self.registry['service']['root'] = '/changed'
        self.request['resources']['read_roots'].append('/changed')
        self.observation['source']['head'] = 'e' * 40
        self.assertEqual(first, expected)

    def test_existing_shared_or_legacy_operations_are_never_replanned(self):
        for existing in ({}, {'job_id': 'offmenu-' + 'a' * 48}, self.plan()):
            with self.subTest(existing=existing), self.assertRaisesRegex(
                    b.BindingError, 'existing_operation_requires_original_reader'):
                self.plan(existing_operation=existing)
        for intent in ('migrate', 'retry', 'resume', 'legacy'):
            self.request['intent'] = intent
            with self.assertRaisesRegex(b.BindingError, 'new_operation_only'):
                self.plan()

    def test_legacy_ids_commands_receipts_or_migration_fields_are_not_inputs(self):
        for key in ('job_id', 'legacy_job_id', 'previous_operation', 'receipt', 'shell', 'resume'):
            request = copy.deepcopy(self.request)
            request[key] = 'legacy'
            with self.subTest(key=key), self.assertRaisesRegex(b.BindingError, 'envelope_fields'):
                b.plan_dispatch(request, self.registry, self.observation)

    def test_all_closed_objects_reject_unknown_fields(self):
        for target in ('source', 'command', 'resources', 'review'):
            request = copy.deepcopy(self.request)
            request[target]['unreviewed'] = True
            with self.subTest(target=target), self.assertRaises(b.BindingError):
                b.plan_dispatch(request, self.registry, self.observation)
        for target in ('registry', 'service', 'profile', 'qualification'):
            registry = copy.deepcopy(self.registry)
            selected = {'registry': registry, 'service': registry['service'],
                        'profile': registry['profiles'][0],
                        'qualification': registry['profiles'][0]['qualification']}[target]
            selected['unreviewed'] = True
            with self.subTest(target=target), self.assertRaises(b.BindingError):
                b.validate_registry(registry)
        observed = dict(self.observation, unreviewed=True)
        with self.assertRaisesRegex(b.BindingError, 'observation_fields'):
            b.plan_dispatch(self.request, self.registry, observed)

    def test_deployment_qualification_and_exact_service_profile_pins_required(self):
        for key, value in [('status', 'proposed'), ('evidence_sha256', ''),
                           ('profile_sha256', 'a' * 64), ('service_sha256', 'b' * 64)]:
            registry = copy.deepcopy(self.registry)
            registry['profiles'][0]['qualification'][key] = value
            with self.subTest(key=key), self.assertRaises(b.BindingError):
                b.validate_registry(registry)
        registry = copy.deepcopy(self.registry)
        registry['profiles'][0]['uid'] += 1
        with self.assertRaisesRegex(b.BindingError, 'qualification_drift'):
            b.validate_registry(registry)
        registry = copy.deepcopy(self.registry)
        registry['service']['runner_sha256'] = 'f' * 64
        with self.assertRaisesRegex(b.BindingError, 'qualification_drift'):
            b.validate_registry(registry)

    def test_profile_changes_cannot_reuse_reviewed_registry_pin(self):
        self.registry['profiles'][0]['isolation_sha256'] = 'c' * 64
        qualify(self.registry)
        with self.assertRaisesRegex(b.BindingError, 'registry_drift'):
            self.plan()

    def test_unknown_or_cross_project_identity_rejected(self):
        for key, value in [('host_id', 'other-host'), ('project_id', 'unknown'),
                           ('profile_id', 'uif-validation-v1'), ('repository', 'maldous/uif'),
                           ('owner', 'uif-owner')]:
            request = copy.deepcopy(self.request); request[key] = value
            with self.subTest(key=key), self.assertRaises(b.BindingError):
                b.plan_dispatch(request, self.registry, self.observation)

    def test_no_root_execution_or_unqualified_project_executable(self):
        for key in ('uid', 'gid'):
            registry = copy.deepcopy(self.registry); registry['profiles'][0][key] = 0
            with self.assertRaisesRegex(b.BindingError, 'nonroot_execution_identity'):
                b.validate_registry(registry)
        self.request['command']['argv'][0] = '/bin/bash'
        with self.assertRaisesRegex(b.BindingError, 'unqualified_executable'):
            self.plan()
        registry = copy.deepcopy(self.registry)
        registry['profiles'][0]['executables'] = ['/home/user/src/uif/runner.py']
        qualify(registry)
        with self.assertRaisesRegex(b.BindingError, 'project_executable'):
            b.validate_registry(registry)

    def test_project_roots_cannot_overlap_service_admission_or_other_projects(self):
        for candidate in ('/', '/opt/foo-bounded-execution/source', '/var/lib/foo-execution',
                          '/var/lib/foo-projects/offmenu/state', '/home/user/src/uif',
                          '/home/user/src/offmenu/data'):
            registry = copy.deepcopy(self.registry)
            registry['profiles'][0]['data_roots'] = [candidate]
            qualify(registry)
            with self.subTest(candidate=candidate), self.assertRaises(b.BindingError):
                b.validate_registry(registry)

    def test_noncanonical_paths_and_traversal_refused(self):
        for candidate in ('relative', '/', '//host/path', '/a//b', '/a/./b',
                          '/a/../b', '/a/', '/a\\b', '/a\nprivate'):
            with self.subTest(candidate=candidate), self.assertRaises(b.BindingError):
                b.path(candidate)
        self.request['source']['worktree'] = '/var/lib/foo-projects/offmenu/worktrees-evil/task'
        with self.assertRaisesRegex(b.BindingError, 'worktree_scope'):
            self.plan()

    def test_declared_paths_cannot_access_another_project_or_coordinator(self):
        for key in ('read_roots', 'write_roots'):
            for candidate in ('/home/user/src/uif', '/var/lib/foo-execution/admission',
                              '/opt/foo-bounded-execution', '/var/lib/foo-projects/offmenu/state',
                              '/var/lib/foo-projects/offmenu/worktrees/another-task'):
                request = copy.deepcopy(self.request); request['resources'][key] = [candidate]
                with self.subTest(key=key, candidate=candidate), self.assertRaisesRegex(
                        b.BindingError, 'resource_scope'):
                    b.plan_dispatch(request, self.registry, self.observation)
        self.request['resources']['write_roots'] = ['/home/user/src/offmenu']
        with self.assertRaisesRegex(b.BindingError, 'resource_scope'):
            self.plan()

    def test_registered_project_data_is_explicitly_bound_not_moved(self):
        data = self.registry['profiles'][0]['data_roots'][0]
        self.request['resources']['read_roots'].append(data)
        self.request['resources']['write_roots'].append(data + '/new-output')
        review(self.request)
        plan = self.plan()
        self.assertIn(data, plan['request']['resources']['read_roots'])
        self.assertFalse(plan['execution_started'])

    def test_resource_limits_and_integer_types_are_strict(self):
        for key in b.RESOURCE_FIELDS:
            for value in (True, -1, 1.5, self.registry['profiles'][0]['resource_limits'][key] + 1):
                request = copy.deepcopy(self.request); request['resources'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(b.BindingError):
                    b.plan_dispatch(request, self.registry, self.observation)
        self.registry['profiles'][0]['resource_limits']['workers'] += 1
        qualify(self.registry)
        with self.assertRaisesRegex(b.BindingError, 'profile_exceeds_host_limits'):
            b.validate_registry(self.registry)

    def test_duplicate_profiles_roots_and_separate_project_pool_refused(self):
        registry = copy.deepcopy(self.registry); registry['profiles'].append(registry['profiles'][0])
        with self.assertRaisesRegex(b.BindingError, 'duplicate_project'):
            b.validate_registry(registry)
        registry = copy.deepcopy(self.registry)
        registry['profiles'][0]['data_roots'] *= 2
        with self.assertRaises(b.BindingError):
            b.validate_registry(registry)
        registry = copy.deepcopy(self.registry)
        registry['profiles'][0]['admission_root'] = '/private/offmenu-pool'
        with self.assertRaisesRegex(b.BindingError, 'profile_fields'):
            b.validate_registry(registry)

    def test_exact_source_observation_owner_clean_and_canonical_required(self):
        for key, value in [('head', 'a' * 40), ('tree', 'b' * 40),
                           ('branch', 'feature/different'),
                           ('worktree', self.request['source']['worktree'] + '-other')]:
            observed = copy.deepcopy(self.observation); observed['source'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(b.BindingError, 'source_drift'):
                b.plan_dispatch(self.request, self.registry, observed)
        for key in ('clean', 'canonical_paths'):
            for value in (False, 1):
                observed = copy.deepcopy(self.observation); observed[key] = value
                with self.assertRaisesRegex(b.BindingError, 'source_not_qualified'):
                    b.plan_dispatch(self.request, self.registry, observed)
        self.request['source']['uid'] += 1
        with self.assertRaisesRegex(b.BindingError, 'worktree_owner'):
            self.plan()

    def test_review_is_independent_and_binds_command_resources_source_and_registry(self):
        for key, value in [('reviewer', self.request['owner']), ('disposition', 'pending'),
                           ('evidence_sha256', 'not-evidence'), ('binding_sha256', 'a' * 64)]:
            request = copy.deepcopy(self.request); request['review'][key] = value
            with self.subTest(key=key), self.assertRaises(b.BindingError):
                b.plan_dispatch(request, self.registry, self.observation)
        for area, key, value in [('source', 'head', 'a' * 40), ('resources', 'cpu_millis', 500),
                                 ('command', 'argv', ['/usr/bin/python3', '-B', '-V'])]:
            request = copy.deepcopy(self.request); request[area][key] = value
            with self.subTest(area=area), self.assertRaisesRegex(b.BindingError, 'review_drift'):
                b.plan_dispatch(request, self.registry, self.observation)

    def test_source_observation_booleans_cannot_match_integer_owner_ids(self):
        self.registry['profiles'][0].update(uid=1, gid=1)
        qualify(self.registry)
        self.request, self.observation = fixture_request(self.registry)
        self.plan()
        for key in ('uid', 'gid'):
            observed = copy.deepcopy(self.observation)
            observed['source'][key] = True
            with self.subTest(key=key), self.assertRaisesRegex(b.BindingError, 'source_drift'):
                b.plan_dispatch(self.request, self.registry, observed)

    def test_schema_branch_command_and_observation_cannot_loosen(self):
        for branch in ('develop', 'feature/../main', 'feature/a//b', 'feature/a..b',
                        'feature/a.lock', 'feature/a.', 'feature/a@{b', 'feature/a/.hidden'):
            request = copy.deepcopy(self.request); request['source']['branch'] = branch
            with self.subTest(branch=branch), self.assertRaisesRegex(b.BindingError, 'branch'):
                b.plan_dispatch(request, self.registry, self.observation)
        self.request['command']['cwd'] = '/home/user/src/offmenu'
        with self.assertRaisesRegex(b.BindingError, 'cwd_scope'):
            self.plan()
        self.request, self.observation = fixture_request(self.registry)
        self.request['schema'] = 'foo-project-operation/v2'
        with self.assertRaisesRegex(b.BindingError, 'new_operation_only'):
            self.plan()

    def test_canonical_serialization_is_bounded_without_silent_coercion(self):
        for value in ({1: 'stringified-key'}, float('nan'), float('inf'), 1.0,
                      2**53, ('not', 'a', 'list'), {'bad': '\ud800'}, 'a' * 65537):
            with self.subTest(kind=type(value)), self.assertRaises(b.BindingError):
                b.canonical_bytes(value)
        deep = {}; deep['self'] = deep
        with self.assertRaisesRegex(b.BindingError, 'json_complexity'):
            b.canonical_bytes(deep)

    def test_planner_has_no_execution_network_filesystem_or_receipt_writer(self):
        tree = ast.parse(SOURCE.read_text())
        imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
        self.assertEqual(imports, {'hashlib', 'json', 're'})
        froms = {(node.module, tuple(a.name for a in node.names)) for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom)}
        self.assertEqual(froms, {('pathlib', ('PurePosixPath',))})
        forbidden = {'open', 'exec', 'eval', '__import__', 'compile', 'input'}
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id in forbidden for node in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
