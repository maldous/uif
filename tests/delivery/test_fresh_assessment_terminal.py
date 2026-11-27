from dataclasses import asdict, replace
from hashlib import sha256
from types import SimpleNamespace
import unittest

from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, decode, wire
from uif.stokowski_cloudflare.fresh_assessment_terminal import observe_fresh_assessment_terminal
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.native_composition import NativeComposition
import test_native_remote_authority as fixture


class FreshAssessmentTerminalTest(unittest.TestCase):
    def test_report_and_control_original_join_without_any_effect(self):
        worker, remote = fixture.make()
        composition = NativeComposition(remote, '/unused-fixture')
        issue = SimpleNamespace(id=fixture.ISSUE, identifier='UIF-1', title='Example', description='Fixture')
        stages = intake_stages({}, 'OffMenu', issue, fixture.GRANT.scope_sha256, 1)
        previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
        grant = replace(fixture.GRANT, attempts=(previous, successor))
        composition.assignment = lambda *args: grant
        lease = fixture.RECORD.lease
        worker.claim = replace(fixture.RECORD, attempt_id=previous)
        claim_original = grant.claim(fixture.OWNER)
        worker.original = {'request_id': claim_original.request_id, 'request_kind': claim_original.kind,
                           'request_sha256': transition_request_sha256(claim_original),
                           'lease': asdict(lease)}
        raw_report = wire(dict(summary='Ready', verdict='approve', routing=dict(
            issue=issue.identifier, state='assess', run=1, actionable=True, ownership_clear=True,
            deferred=False, owner_pause=False, uncertain_effects=False, missing_inputs=[])))
        body = 'Exact rendered fixture'
        operation = composition._linear_operation(lease.fence, previous, '_publish_run_report',
                                                   'comment', {'body': body})
        comment_id = fixture.ISSUE
        worker.effects[operation.original_id] = (asdict(operation), {
            'id': comment_id, 'receipt_sha256': 'f' * 64,
        })
        original_request = 'exact-attestation-original'
        evidence = wire({
            'schema': 'uif.fresh-offmenu-assessment-terminal/1',
            'issue_id': issue.id, 'issue_identifier': issue.identifier, 'run': 1,
            'state': 'assess', 'successor_state': 'investigate', 'scope_sha256': grant.scope_sha256,
            'previous': previous, 'successor': successor, 'report_sha256': sha256(raw_report).hexdigest(),
            'posted_comment_id': comment_id, 'posted_effect': {
                'operation': asdict(operation), 'status': 'verified', 'receipt_sha256': 'f' * 64,
            },
        })
        accepted = {'request_id': original_request, 'fence': asdict(lease.fence),
                    'previous': previous, 'successor': successor,
                    'terminal_receipt_sha256': sha256(evidence).hexdigest()}

        attestation_calls = []
        request = worker.request
        native_result = None
        change_on_match = False

        def native_request(path, payload=None, **options):
            if path != '/v1/attempt/attest/native':
                return request(path, payload, **options)
            attestation_calls.append(payload)
            matches = payload == {'current': asdict(lease), 'original': accepted}
            if matches and change_on_match:
                composition.assignment = lambda *args: changed_attempts
            return {'matches': matches if native_result is None else native_result}

        remote.transport.request = native_request

        def verify_as_existing_coordinator(subject, run, scope, received_lease, prev, next_, raw, rendered):
            if (subject, run, scope, received_lease, prev, next_, raw, rendered) != (
                issue, 1, grant.scope_sha256, lease, previous, successor, raw_report, body
            ):
                raise Hold('fixture_report_or_prerequisite_mismatch')

        options = dict(raw_report=raw_report, rendered_body=body, attestation_request_id=original_request,
                       verify_original_prerequisites=verify_as_existing_coordinator)
        def join(**changes):
            return observe_fresh_assessment_terminal(composition, issue, 1, grant.scope_sha256,
                                                     lease, **(options | changes))
        self.assertEqual(join(), evidence)
        self.assertEqual(decode(evidence)['successor_state'], 'investigate')
        self.assertEqual(attestation_calls, [{'current': asdict(lease), 'original': accepted}])
        with self.assertRaisesRegex(Hold, 'original_unverified'):
            join(attestation_request_id='unaccepted-original')
        with self.assertRaisesRegex(Hold, 'prerequisite_mismatch'):
            join(rendered_body='different')
        with self.assertRaisesRegex(Hold, 'verifier_result'):
            join(verify_original_prerequisites=lambda *args: True)
        native_result = 1
        with self.assertRaisesRegex(Hold, 'original_unverified'):
            join()
        native_result = None
        worker.claim = replace(worker.claim, attempt_id=successor)
        with self.assertRaisesRegex(Hold, 'claim'):
            join()
        worker.claim = replace(worker.claim, attempt_id=previous)
        valid_original = worker.original
        worker.original = None
        with self.assertRaisesRegex(Hold, 'original_claim_unverified'):
            join()
        worker.original = valid_original
        changed_reconstruction = replace(grant, reconstruction_sha256='9'*64)
        composition.assignment = lambda *args: changed_reconstruction
        with self.assertRaisesRegex(Hold, 'request_binding'):
            join()
        changed_attempts = replace(grant, attempts=(*grant.attempts, 'extra-attempt'))
        grants = iter([grant, grant, changed_attempts])
        composition.assignment = lambda *args: next(grants)
        attestation_calls.clear()
        with self.assertRaisesRegex(Hold, 'grant_changed'):
            join()
        self.assertEqual(attestation_calls, [])
        composition.assignment = lambda *args: grant
        change_on_match = True
        with self.assertRaisesRegex(Hold, 'grant_changed'):
            join()
        change_on_match = False
        composition.assignment = lambda *args: grant
        worker.effects.clear()
        with self.assertRaisesRegex(Hold, 'report_unverified'):
            join()
        self.assertEqual({path for path, _, _ in worker.calls},
            {'/v1/status', '/v1/claim/read', '/v1/claim/observe', '/v1/linear/receipt', '/v1/original'})
