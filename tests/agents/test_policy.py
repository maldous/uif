import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('policy_check', ROOT / 'tools/agents/check_policy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = (
            module.POLICY_PATH, module.MANIFEST_PATH, module.PROFILES_PATH,
            'docs/agents/skills-and-plugins.md', 'docs/agents/memory-routing.md',
            'docs/agents/startup-template.md', 'AGENTS.md',
        )
        for relative in self.paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        self.expected = (ROOT / module.POLICY_PATH).read_bytes()

    def check(self, **kwargs):
        return module.check(self.root, self.expected, 'maldous/uif', **kwargs)

    def edit_json(self, path, edit):
        target = self.root / path
        data = json.loads(target.read_text())
        edit(data)
        target.write_text(json.dumps(data))
        return data

    def manifest(self, key, value):
        return self.edit_json(module.MANIFEST_PATH, lambda data: data.update({key: value}))

    def profiles(self, edit):
        return self.edit_json(module.PROFILES_PATH, edit)

    def test_current_bundle_and_each_role_without_product_checkouts(self):
        self.assertEqual(self.check(), [])
        profiles = json.loads((self.root / module.PROFILES_PATH).read_text())
        for role, data in profiles['logical_roles'].items():
            with self.subTest(role=role):
                self.assertEqual(self.check(role=role), [])
                for repository in data['repository_binding']:
                    self.assertEqual(self.check(role=role, project_repository=repository), [])
        self.assertFalse((self.root / 'offmenu').exists())

    def test_stale_versions_are_not_current_conformance(self):
        for version in ('1.1.0', '1.1.1', '1.2.0', '1.2.1', '1.2.2', '1.2.3', '1.2.4'):
            with self.subTest(version=version):
                self.manifest('version', version)
                self.assertIn('Invalid version', self.check())

    def test_tamper_and_different_selected_source(self):
        (self.root / module.POLICY_PATH).write_text('changed\n')
        self.assertIn('Policy hash mismatch', self.check())
        self.assertIn('Policy differs from selected UIF source', self.check())

    def test_wrong_bundle_identity(self):
        self.manifest('repository', 'other/repo')
        self.assertIn('Repository identity mismatch', self.check())

    def test_escape_manifest_reference_and_registry_reference(self):
        self.manifest('policy_path', '../missing.md')
        self.assertIn('Invalid policy_path', self.check())
        self.profiles(lambda data: data['inventory'].update(memory='../outside.md'))
        self.assertIn('Invalid environment profiles inventory', self.check())

    def test_missing_entrypoint_and_inventory(self):
        (self.root / 'AGENTS.md').write_text('hello\n')
        self.assertIn('Missing root startup references', self.check())
        (self.root / 'docs/agents/memory-routing.md').unlink()
        self.assertTrue(any('Unreadable instruction' in item for item in self.check()))

    def test_bad_json_and_non_object(self):
        path = self.root / module.MANIFEST_PATH
        path.write_text('bad')
        self.assertTrue(self.check())
        path.write_text('[]')
        self.assertEqual(self.check(), ['Manifest must be an object'])

    def test_duplicate_json_field_is_rejected(self):
        target = self.root / module.PROFILES_PATH
        target.write_text('{"schema":"uif-environment-profiles-v1","schema":"other"}')
        self.assertTrue(any('Duplicate JSON field' in item for item in self.check()))

    def test_symlink_file_and_parent_are_rejected(self):
        path = self.root / module.POLICY_PATH
        path.unlink()
        path.symlink_to(ROOT / module.POLICY_PATH)
        self.assertTrue(any('Nonportable' in item for item in self.check()))
        path.unlink()
        path.write_bytes(self.expected)
        docs = self.root / 'docs/agents'
        held = self.root / 'held-agents'
        docs.rename(held)
        docs.symlink_to(held, target_is_directory=True)
        self.assertTrue(any('Nonportable' in item for item in self.check()))

    def test_invalid_utf8_and_line_endings(self):
        target = self.root / 'docs/agents/memory-routing.md'
        target.write_bytes(b'bad\xff\n')
        self.assertTrue(self.check())
        target.write_bytes(b'windows\r\n')
        self.assertTrue(any('LF and final newline' in item for item in self.check()))

    def test_wrong_role_and_repository(self):
        self.assertIn('Unknown selected role', self.check(role='missing'))
        self.assertIn('Selected repository does not belong to selected role',
                      self.check(role='offmenu', project_repository='cix-project/cix'))
        self.assertIn('A selected repository requires --role',
                      self.check(project_repository='maldous/offmenu'))

    def test_cloud_repository_binding_is_exact_and_cannot_expand(self):
        for bindings in ([], ['other/repo'], ['maldous/aldous-info-platform', 'other/repo']):
            with self.subTest(bindings=bindings):
                self.profiles(lambda data: data['logical_roles']['cloud'].update(repository_binding=bindings))
                self.assertIn('Cloud infrastructure role must retain its exact assigned repository and non-product boundary', self.check())

    def test_cloud_cannot_become_sixth_product_gate(self):
        self.profiles(lambda data: data['project_delivery_matrix'].append('cloud'))
        self.assertIn('Project delivery matrix must contain exactly the five project roles', self.check())

    def test_delivery_matrix_rejects_duplicates_and_missing_roles(self):
        for matrix in (['uif'] * 5, ['uif', 'offmenu', 'anqr', 'cix']):
            with self.subTest(matrix=matrix):
                self.profiles(lambda data: data.update(project_delivery_matrix=matrix))
                self.assertIn('Project delivery matrix must contain exactly the five project roles', self.check())

    def test_cloud_role_does_not_inherit_provider_or_publication_authority(self):
        for change in (dict(baseline='develop'), dict(role_kind='product'),
                       dict(project_delivery_matrix_member=0), dict(product_instructions=None),
                       dict(infrastructure_details='public'), dict(repository_visibility_changes=0)):
            with self.subTest(change=change):
                original = (ROOT / module.PROFILES_PATH).read_bytes()
                (self.root / module.PROFILES_PATH).write_bytes(original)
                self.profiles(lambda data: data['logical_roles']['cloud'].update(change))
                self.assertIn('Cloud infrastructure role must retain its exact assigned repository and non-product boundary', self.check())

    def test_cloud_routing_reference_selects_only_assigned_infrastructure_repository(self):
        reference = self.reference(repository='maldous/aldous-info-platform', environment_profile='cloud')
        self.assertEqual(module.check_reference(self.root, reference, role='cloud'), [])
        reference = self.reference(repository='maldous/offmenu', environment_profile='cloud')
        self.assertIn('Policy reference repository does not belong to its role', module.check_reference(self.root, reference))

    def test_products_cannot_require_a_local_operational_copy(self):
        self.profiles(lambda data: data['logical_roles']['offmenu'].update(operational_policy='reviewed-local-copy'))
        self.assertIn('Operational policy must remain environment-owned: offmenu', self.check())

    def test_shared_memory_and_private_data_refused(self):
        for mutation in (
            dict(namespace='uif/shared'), dict(private_data=True), dict(shared='offmenu/private'),
        ):
            with self.subTest(mutation=mutation):
                original = (ROOT / module.PROFILES_PATH).read_bytes()
                (self.root / module.PROFILES_PATH).write_bytes(original)
                self.profiles(lambda data: data['logical_roles']['cix']['memory'].update(mutation))
                self.assertIn('Invalid memory isolation declaration: cix', self.check())

    def test_capability_typos_and_required_optional_overlap(self):
        self.profiles(lambda data: data['executor_profiles']['foo_codex']['capabilities']['required'].append('undefined-capability'))
        self.assertIn('Capability absent from inventory: undefined-capability', self.check())
        self.profiles(lambda data: data['executor_profiles']['cloud']['capabilities']['optional'].append(
            data['executor_profiles']['cloud']['capabilities']['required'][0]))
        self.assertIn('Required and optional capabilities overlap: cloud', self.check())

    def test_mcp_is_not_reasoning_or_session_control(self):
        self.profiles(lambda data: data['executor_profiles']['foo_mcp_user'].update(provides_reasoning=True))
        self.assertIn('Deterministic transport cannot declare reasoning or session control: foo_mcp_user', self.check())
        self.profiles(lambda data: data['executor_profiles']['foo_mcp_root'].update(repository_commands_as='root'))
        self.assertIn('Root transport must preserve checkout ownership', self.check())

    def test_foo_filesystem_and_concurrency_types(self):
        self.profiles(lambda data: data['executor_profiles']['cloud'].update(foo_filesystem_shared=True))
        self.assertIn('Cloud environment must not declare a shared Foo filesystem', self.check())
        self.profiles(lambda data: data.update(local_session_ceiling=True))
        self.assertIn('Invalid local session ceiling', self.check())

    def test_ledger_intake_and_foo_only_normal_authority_refused(self):
        self.profiles(lambda data: data['intake'].update(kind='ledger-or-linear', claim_authority='foo'))
        self.assertIn('Invalid Linear-only intake declaration', self.check())
        self.assertIn('Invalid shared off-Foo authority declaration', self.check())

    def test_normal_contract_can_be_unimplemented_without_claiming_runtime_readiness(self):
        self.profiles(lambda data: data['intake'].update(authority_implementation='unimplemented'))
        self.profiles(lambda data: data['morgan_continuity'].update(implementation='unqualified'))
        self.assertEqual(self.check(), [])

    def test_current_chat_or_unfenced_morgan_replacement_refused(self):
        self.profiles(lambda data: data.update(morgan_continuity='active-coordinator-required-no-unattended-wakeup'))
        self.assertIn('Invalid durable Morgan lifecycle declaration', self.check())
        self.profiles(lambda data: data.update(morgan_continuity={
            'lifecycle': 'durable-logical-role', 'conversation_required': True,
            'replacement': 'unfenced-restart', 'wake': 'independent-scheduled-or-event-trigger',
            'implementation': 'unqualified'}))
        self.assertIn('Invalid durable Morgan lifecycle declaration', self.check())

    def test_brief_glitch_or_unqualified_single_probe_does_not_meet_outage_contract(self):
        self.profiles(lambda data: data['foo_outage'].update(sustained_grace_seconds=1, qualified_probes_required=1))
        self.assertIn('Invalid sustained Foo outage declaration', self.check())

    def test_canonical_outage_default_and_explicit_configured_override(self):
        declared = json.loads((ROOT / module.PROFILES_PATH).read_text())
        self.assertEqual(declared['foo_outage']['sustained_grace_seconds'], 900)
        self.profiles(lambda data: data['foo_outage'].update(sustained_grace_seconds=1200))
        self.assertEqual(self.check(), [])
        self.profiles(lambda data: data['foo_outage'].update(configurable=False))
        self.assertIn('Invalid sustained Foo outage declaration', self.check())

    def test_outage_grace_requires_a_positive_integer(self):
        for value in (0, -1, True, 900.0):
            with self.subTest(value=value):
                self.profiles(lambda data: data['foo_outage'].update(sustained_grace_seconds=value))
                self.assertIn('Invalid sustained Foo outage declaration', self.check())

    def test_healthy_probe_resets_sustained_outage_grace(self):
        self.profiles(lambda data: data['foo_outage'].update(healthy_probe_resets_grace=False))
        self.assertIn('Invalid sustained Foo outage declaration', self.check())

    def test_all_capacity_unavailable_preserves_work_without_new_provider(self):
        self.profiles(lambda data: data['inference_continuity'].update(new_provider_fallback=True))
        self.assertIn('Invalid existing inference capacity declaration', self.check())
        self.profiles(lambda data: data['inference_continuity'].update(
            new_provider_fallback=False, when_unavailable='abandon-unfinished-work'))
        self.assertIn('Invalid existing inference capacity declaration', self.check())

    def test_unverified_service_installation_is_not_a_current_source_contract(self):
        self.profiles(lambda data: data['intake'].update(authority_implementation='installed-and-ready'))
        self.assertIn('Invalid shared off-Foo authority declaration', self.check())

    def qualification(self, subject, **changes):
        # Entirely synthetic evidence for a possible future source declaration.
        # These fixtures do not assert an installed service or real receipt.
        report = dict(subject=subject, status='qualified', source_commit='e' * 40,
                      observed_at='2020-01-01T00:00:00+00:00',
                      receipt_refs=['fixture:original-receipt-not-a-real-deployment'])
        report.update(changes)
        relative = 'docs/agents/' + subject + '-fixture.json'
        raw = (json.dumps(report) + '\n').encode()
        (self.root / relative).write_bytes(raw)
        return dict(path=relative, sha256=hashlib.sha256(raw).hexdigest())

    def test_future_qualified_states_require_bound_subject_and_original_evidence(self):
        authority = self.qualification('off-foo-authority')
        lifecycle = self.qualification('morgan-lifecycle')
        self.profiles(lambda data: data['intake'].update(
            authority_implementation='qualified', authority_qualification=authority))
        self.profiles(lambda data: data['morgan_continuity'].update(
            implementation='qualified', qualification=lifecycle))
        self.assertEqual(self.check(), [])
        # Old observations are allowed as historical source evidence; this
        # checker deliberately provides no runtime freshness/authenticity proof.
        (self.root / authority['path']).write_text('{}\n')
        self.assertIn('off-foo-authority qualification hash mismatch', self.check())

    def test_qualified_status_without_evidence_and_wrong_subject_refused(self):
        self.profiles(lambda data: data['intake'].update(authority_implementation='qualified'))
        self.assertIn('Missing off-foo-authority qualification evidence', self.check())
        wrong = self.qualification('morgan-lifecycle')
        self.profiles(lambda data: data['intake'].update(authority_qualification=wrong))
        self.assertIn('Invalid off-foo-authority qualification record', self.check())

    def test_qualification_requires_revision_aware_time_and_receipt_shape(self):
        for change in (dict(source_commit='main'), dict(observed_at='2026-11-14T00:00:00'),
                       dict(receipt_refs=[])):
            with self.subTest(change=change):
                reference = self.qualification('off-foo-authority', **change)
                self.profiles(lambda data: data['intake'].update(
                    authority_implementation='qualified', authority_qualification=reference))
                self.assertIn('Invalid off-foo-authority qualification record', self.check())

    def test_numeric_false_does_not_pass_queue_constraint(self):
        self.profiles(lambda data: data['intake'].update(registry_is_queue=0))
        self.assertIn('Invalid Linear-only intake declaration', self.check())

    def test_new_baseline_needs_documented_exception(self):
        self.profiles(lambda data: data['logical_roles']['offmenu'].update(baseline='feature/new-source'))
        self.assertIn('Nonstandard baseline needs an explicit exception: offmenu', self.check())

    def reference(self, **changes):
        value = {
            'schema': 'uif-worker-policy-reference-v1', 'policy_id': 'uif-worker-policy',
            'required_policy_version': module.VERSION, 'repository': 'maldous/offmenu',
            'environment_profile': 'offmenu', 'distribution': 'environment-owned-overlay',
            'maintenance_repository': 'maldous/uif', 'maintenance_path': module.POLICY_PATH,
            'manifest_path': module.MANIFEST_PATH,
            'installed_digest_source': 'selected execution environment manifest',
            'historical_policy_path': 'docs/agents/history/uif-worker-policy-1.2.3.md',
            'historical_manifest_path': 'docs/agents/history/uif-worker-policy-1.2.3.json',
        }
        value.update(changes)
        reference = self.root / 'product-reference.json'
        reference.write_text(json.dumps(value))
        return reference

    def test_product_reference_requires_no_policy_copy(self):
        reference = self.reference()
        self.assertEqual(module.check_reference(self.root, reference), [])
        self.assertFalse((self.root / 'docs/agents/history').exists())

    def test_reference_wrong_role_version_and_installed_claim(self):
        reference = self.reference(environment_profile='cix', required_policy_version='1.2.4', sha256='a' * 64)
        errors = module.check_reference(self.root, reference, role='offmenu')
        self.assertIn('Policy reference repository does not belong to its role', errors)
        self.assertIn('Invalid policy reference required_policy_version', errors)
        self.assertIn('A routing reference cannot assert an installed policy digest', errors)
        self.assertIn('Policy reference differs from selected role', errors)

    def test_reference_private_operations_surface_and_historical_escape(self):
        reference = self.reference(repository='cix-project/cix-operations', environment_profile='cix',
                                   repository_surface='cix-project/cix')
        self.assertIn('Policy reference repository surface mismatch', module.check_reference(self.root, reference))
        reference = self.reference(historical_policy_path='../private.md')
        self.assertIn('Nonportable policy reference historical_policy_path', module.check_reference(self.root, reference))

    def test_cli_bundle_and_original_spelling(self):
        for args in (['--bundle', str(self.root), '--role', 'offmenu', '--reference', str(self.reference())],
                     ['maldous/uif=' + str(self.root)]):
            output = io.StringIO()
            with redirect_stdout(output):
                module.main(args)
            self.assertIn('Declared source conformance only', output.getvalue())


if __name__ == '__main__':
    unittest.main()
