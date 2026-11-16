"""Synthetic immutable-archive compatibility; no acceptance or runtime effects."""
import copy
from hashlib import sha256
import json
import unittest

from uif.handoff_store import Hold, wire
from uif.legacy_receipts import ArchivePin, build_legacy_receipt, LEGACY_POLICY_SHA256

SELECTED = '6' * 64
BINDING = dict(candidate='issue-uuid', human_context_sha256='4' * 64,
               invocation='review-invocation', issue='UIF-SYNTHETIC', issue_id='issue-uuid',
               kind='source-free', run=5, scope_sha256='5' * 64, state='independent-review')


def identity(archive):
    # Synthetic identity recipe only. Actual native constructor is qualified separately.
    return sha256(wire(archive)).hexdigest()


def fixture(stage='assess', artifacts=None):
    original = dict(state=stage, run=5, binding=None,
                    context=dict(issue='UIF-SYNTHETIC', issue_id='issue-uuid',
                                 issue_scope_sha256='5' * 64, session_id='original-session'),
                    artifacts=[] if artifacts is None else artifacts,
                    report=dict(verdict='approve' if stage == 'assess' else 'complete',
                                claims=[dict(evidence='selected documentary SHA ' + SELECTED)]))
    review = dict(state='independent-review', run=5, binding=copy.deepcopy(BINDING),
                  context=dict(issue='UIF-SYNTHETIC', issue_id='issue-uuid',
                               issue_scope_sha256='5' * 64, session_id='review-session'), artifacts=[],
                  report=dict(verdict='approve', routing=copy.deepcopy(BINDING),
                              verification=[dict(detail='original retained record ' + identity(original))],
                              claims=[dict(evidence='selected documentary SHA ' + SELECTED)]))
    return original, review


def arguments(original, review, **extra):
    before = (json.dumps(original, indent=2) + '\n').encode()
    after = (json.dumps(review, indent=2) + '\n').encode()
    return dict(original_bytes=before, review_bytes=after,
                original_pin=ArchivePin(identity(original), sha256(before).hexdigest()),
                review_pin=ArchivePin(identity(review), sha256(after).hexdigest()),
                published_review_binding=copy.deepcopy(BINDING), **extra)


class LegacyReceiptContracts(unittest.TestCase):
    def test_directory_name_without_native_digest_proof_stays_unresolved(self):
        result = build_legacy_receipt(**arguments(*fixture()))
        self.assertEqual(result['status'], 'input-join-unresolved')
        self.assertTrue(result['directory_reference_without_digest_proof'])
        self.assertFalse(result['record_identity_verified'])

    def test_verified_native_record_identity_qualifies_exact_legacy_join(self):
        result = build_legacy_receipt(**arguments(*fixture(), verified_record_identity=identity))
        self.assertEqual(result['status'], 'bounded-input-join')
        self.assertEqual(result['proof'][0]['kind'], 'verified-native-record-id')
        self.assertEqual(result['proof'][0]['review_pointers'], ['/report/verification/0/detail'])

    def test_exact_file_sha_join_needs_no_directory_identity_claim(self):
        original, review = fixture()
        pin = arguments(original, review)['original_pin']
        review['report']['verification'][0]['detail'] = pin.file_sha256
        result = build_legacy_receipt(**arguments(original, review))
        self.assertEqual(result['status'], 'bounded-input-join')
        self.assertEqual(result['proof'][0]['kind'], 'original-evidence-file-sha256')
        self.assertFalse(result['record_identity_verified'])

    def test_assess_approve_and_explicit_empty_artifacts_preserved(self):
        result = build_legacy_receipt(**arguments(*fixture(), selected_input_sha256=(SELECTED,)))
        self.assertEqual((result['original_stage'], result['original_verdict']), ('assess', 'approve'))
        self.assertEqual(result['selected_artifacts'], [])
        self.assertEqual(result['selected_artifacts_sha256'], sha256(wire([])).hexdigest())

    def test_investigate_complete_and_nonempty_artifacts_preserved(self):
        artifacts = [dict(path='bounded-result.json', sha256='7' * 64, size_bytes=43)]
        result = build_legacy_receipt(**arguments(*fixture('investigate', artifacts), selected_input_sha256=(SELECTED,)))
        self.assertEqual((result['original_stage'], result['original_verdict']), ('investigate', 'complete'))
        self.assertEqual(result['selected_artifacts'], artifacts)

    def test_no_modern_field_fabrication_or_receiving_authority(self):
        original, review = fixture()
        result = build_legacy_receipt(**arguments(original, review, selected_input_sha256=(SELECTED,)))
        self.assertNotIn('reviewed_disposition', review['report'])
        self.assertNotIn('reviewed_disposition', result)
        for field in ('receiving_authorized', 'gate_clearance', 'full_issue_acceptance',
                      'git_publication_authorized', 'runtime_installed',
                      'artifact_payloads_verified', 'selected_input_payloads_verified'):
            self.assertIs(result[field], False)
        self.assertTrue(result['published_tuple_authentication_required'])
        self.assertTrue(result['current_scope_and_custody_check_required'])

    def test_shared_policy_hash_cannot_be_selected_input_proof(self):
        original, review = fixture()
        for archive in (original, review):
            archive['report']['claims'][0]['evidence'] = LEGACY_POLICY_SHA256
        with self.assertRaisesRegex(Hold, 'policy_hash_is_not_selected_input'):
            build_legacy_receipt(**arguments(original, review, selected_input_sha256=(LEGACY_POLICY_SHA256,)))
        result = build_legacy_receipt(**arguments(original, review))
        self.assertEqual(result['status'], 'input-join-unresolved')

    def test_selected_documentary_subset_is_not_original_archive_join(self):
        result = build_legacy_receipt(**arguments(*fixture(), selected_input_sha256=(SELECTED,)))
        self.assertEqual([p['kind'] for p in result['proof']], ['selected-documentary-input-sha256'])
        self.assertFalse(result['record_identity_verified'])
        self.assertEqual(result['proof'][0]['original_pointers'], ['/report/claims/0/evidence'])

    def test_absent_or_duplicated_selected_input_pin_refused(self):
        for pins in (('8' * 64,), (SELECTED, SELECTED)):
            with self.subTest(pins=pins), self.assertRaises(Hold):
                build_legacy_receipt(**arguments(*fixture(), selected_input_sha256=pins))

    def test_corrupted_archive_file_pin_refused(self):
        args = arguments(*fixture())
        args['original_bytes'] += b' '
        with self.assertRaisesRegex(Hold, 'legacy_archive_file_hash'):
            build_legacy_receipt(**args)

    def test_published_binding_exact_types_and_routing_required(self):
        for field, value in [('run', True), ('run', 5.0), ('scope_sha256', '9' * 64), ('kind', 'git')]:
            original, review = fixture()
            review['binding'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(Hold):
                build_legacy_receipt(**arguments(original, review))
        original, review = fixture()
        del review['report']['routing']
        with self.assertRaisesRegex(Hold, 'legacy_published_review_binding_mismatch'):
            build_legacy_receipt(**arguments(original, review))

    def test_current_git_and_modern_dispositions_keep_separate_validators(self):
        original, review = fixture()
        original['binding'] = dict(kind='git')
        with self.assertRaisesRegex(Hold, 'legacy_original_route'):
            build_legacy_receipt(**arguments(original, review))

    def test_source_free_candidate_is_bound_to_exact_issue(self):
        original, review = fixture()
        binding = copy.deepcopy(BINDING)
        binding['candidate'] = 'another-candidate'
        review['binding'] = copy.deepcopy(binding)
        review['report']['routing'] = copy.deepcopy(binding)
        args = arguments(original, review)
        args['published_review_binding'] = binding
        with self.assertRaisesRegex(Hold, 'legacy_source_free_candidate_identity'):
            build_legacy_receipt(**args)
        original, review = fixture()
        review['report']['reviewed_disposition'] = {}
        with self.assertRaisesRegex(Hold, 'use_modern_disposition_route'):
            build_legacy_receipt(**arguments(original, review))

    def test_scope_issue_run_stage_and_session_joins_are_strict(self):
        changes = [lambda o, r: o.update(run=True), lambda o, r: o.update(state='publish'),
                   lambda o, r: r['report'].update(verdict='reject'),
                   lambda o, r: o['context'].update(issue_id='another-issue'),
                   lambda o, r: r['context'].update(issue_scope_sha256='9' * 64),
                   lambda o, r: r['context'].update(session_id='original-session')]
        for mutate in changes:
            original, review = fixture()
            mutate(original, review)
            with self.subTest(mutate=mutate), self.assertRaises(Hold):
                build_legacy_receipt(**arguments(original, review))

    def test_missing_artifact_list_not_conflated_with_explicit_empty(self):
        for value in (None, {}, 'empty'):
            original, review = fixture()
            original['artifacts'] = value
            with self.subTest(value=value), self.assertRaisesRegex(Hold, 'legacy_artifact_list'):
                build_legacy_receipt(**arguments(original, review))

    def test_unsafe_artifact_metadata_is_not_reinterpreted(self):
        for path in ('../result.json', '/result.json', 'a//b', 'a\\b', 'line\nbreak'):
            original, review = fixture(artifacts=[dict(path=path, sha256='7' * 64, size_bytes=0)])
            with self.subTest(path=path), self.assertRaises(Hold):
                build_legacy_receipt(**arguments(original, review))

    def test_record_verifier_requires_code_and_validates_both_inputs(self):
        for verifier in (True, 'verified', lambda _: '0' * 64):
            with self.subTest(verifier=verifier), self.assertRaises(Hold):
                build_legacy_receipt(**arguments(*fixture(), verified_record_identity=verifier))
        calls = []
        def verify(archive):
            calls.append(archive['state'])
            return identity(archive)
        build_legacy_receipt(**arguments(*fixture(), verified_record_identity=verify))
        self.assertEqual(calls, ['assess', 'independent-review'])

    def test_record_validator_cannot_mutate_checked_input(self):
        def mutate(archive):
            before = identity(archive)
            archive['report']['verdict'] = 'changed'
            return before
        with self.assertRaisesRegex(Hold, 'legacy_record_validator_mutated_input'):
            build_legacy_receipt(**arguments(*fixture(), verified_record_identity=mutate))

    def test_duplicate_json_keys_are_rejected_even_with_matching_file_pin(self):
        args = arguments(*fixture())
        args['original_bytes'] = args['original_bytes'].replace(b'"run": 5,', b'"run": 5, "run": 5,')
        args['original_pin'] = ArchivePin(args['original_pin'].record_id, sha256(args['original_bytes']).hexdigest())
        with self.assertRaisesRegex(Hold, 'duplicate_json_key'):
            build_legacy_receipt(**args)

    def test_receipt_is_deterministic_and_does_not_mutate_external_tuple(self):
        args = arguments(*fixture(), selected_input_sha256=(SELECTED,))
        external = copy.deepcopy(args['published_review_binding'])
        first, second = build_legacy_receipt(**args), build_legacy_receipt(**args)
        self.assertEqual(first, second)
        self.assertEqual(args['published_review_binding'], external)
        pin = first.pop('receipt_sha256')
        self.assertEqual(pin, sha256(wire(first)).hexdigest())


if __name__ == '__main__':
    unittest.main()
