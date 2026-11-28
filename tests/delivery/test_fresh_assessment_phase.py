from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
import os
import stat
import unittest
from unittest.mock import patch

from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, decode, wire
from uif.stokowski_cloudflare.fresh_assessment_render import render_fresh_assessment
from uif.stokowski_cloudflare.fresh_assessment_dispatch import retain_fresh_assessment_dispatch
from uif.stokowski_cloudflare.fresh_assessment_phase import project_fresh_assessment_phase
from uif.stokowski_cloudflare.intake import intake_stages
import test_native_remote_authority as fixture
from test_fresh_assessment_dispatch import enroll


class FreshAssessmentPhaseTest(unittest.TestCase):
    def test_verified_fresh_assess_can_create_one_phase_only(self):
        case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        worker, remote, composition = case.worker, case.r, case.c
        issue = SimpleNamespace(id=case.issue.id, identifier=case.issue.identifier,
                                title='Example', description='Fixture')
        stages = intake_stages({}, 'OffMenu', issue, fixture.GRANT.scope_sha256, 1)
        previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
        grant = replace(case.grant, attempts=(previous, successor))
        composition.assignment = lambda *args: grant
        lease = replace(fixture.RECORD.lease, fence=replace(fixture.RECORD.lease.fence, source=grant.source))
        claim = replace(fixture.RECORD, attempt_id=previous, lease=lease)
        claim_original = grant.claim(fixture.OWNER)
        case.item['workspace_binding']['original_claim_id'] = claim_original.request_id
        grant = enroll(grant, case.item, issue, 1, previous, successor)
        stored_claim = {'request_id': claim_original.request_id, 'request_kind': claim_original.kind,
                        'request_sha256': transition_request_sha256(claim_original), 'lease': asdict(lease)}
        raw_report = wire(dict(summary='Ready', verdict='approve', routing=dict(
            issue=issue.identifier, state='assess', run=1, actionable=True, ownership_clear=True,
            deferred=False, owner_pause=False, uncertain_effects=False, missing_inputs=[])))
        body = render_fresh_assessment(raw_report, issue=issue, run=1,
                                       scope_sha256=grant.scope_sha256, attempt_id=previous)
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

        request = worker.request
        native_result = None

        def native_request(path, payload=None, **options):
            if path == '/v1/claim':
                worker.claim, worker.original = claim, stored_claim
                return stored_claim
            if path == '/v1/attempt/attest/native':
                return {'matches': payload == {'current': asdict(lease), 'original': accepted}
                        and native_result is not False}
            return request(path, payload, **options)

        remote.transport.request = native_request

        def verify_as_existing_coordinator(subject, run, scope, received_lease, prev, next_, raw, rendered):
            if (subject, run, scope, received_lease, prev, next_, raw, rendered) != (
                issue, 1, grant.scope_sha256, lease, previous, successor, raw_report, body
            ):
                raise Hold('fixture_report_or_prerequisite_mismatch')

        def verify_first(owner, target, run, assignment):
            self.assertEqual((owner, target, run, assignment), (case.owner, issue, 1, grant))

        def project():
            return project_fresh_assessment_phase(composition, case.owner, issue, 1, grant.scope_sha256,
                lease, raw_report=raw_report, rendered_body=body, attestation_request_id=original_request,
                verify_first_dispatch_applicability=verify_first,
                verify_original_prerequisites=verify_as_existing_coordinator)

        phase = case.path / '.stokowski/settlement.json'
        with self.assertRaises(FileNotFoundError):
            project()
        self.assertFalse(phase.parent.exists())
        retain_fresh_assessment_dispatch(composition, case.owner, issue, 1, grant.scope_sha256,
            verify_first_dispatch_applicability=verify_first)
        native_result = False
        with self.assertRaisesRegex(Hold, 'original_unverified'):
            project()
        self.assertFalse(phase.parent.exists())
        native_result = True
        clock_before = remote.clock
        real_fsync = os.fsync

        def expire_during_fsync(fd):
            real_fsync(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                remote.clock = lambda: datetime.fromtimestamp(lease.expires_at, timezone.utc)

        with patch('uif.stokowski_cloudflare.native_composition.os.fsync', side_effect=expire_during_fsync):
            with self.assertRaisesRegex(Hold, 'assessment_phase_expired'):
                project()
        self.assertFalse(phase.exists())
        self.assertEqual(list(phase.parent.iterdir()), [])
        remote.clock = clock_before

        def withdraw_attestation_during_fsync(fd):
            nonlocal native_result
            real_fsync(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                native_result = False

        with patch('uif.stokowski_cloudflare.native_composition.os.fsync',
                   side_effect=withdraw_attestation_during_fsync):
            with self.assertRaisesRegex(Hold, 'original_unverified'):
                project()
        self.assertFalse(phase.exists())
        native_result = True

        real_open = os.open
        parked = case.path.parent / 'parked-fixture'
        swapped = False

        def substitute_workspace(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == case.path and not swapped:
                case.path.rename(parked)
                case.path.mkdir(mode=0o700)
                swapped = True
            return real_open(path, flags, *args, **kwargs)

        with patch('uif.stokowski_cloudflare.fresh_assessment_phase.os.open',
                   side_effect=substitute_workspace):
            with self.assertRaisesRegex(Hold, 'workspace_custody'):
                project()
        self.assertTrue(swapped)
        self.assertEqual(list(case.path.iterdir()), [])
        case.path.rmdir()
        parked.rename(case.path)
        self.assertFalse(phase.exists())
        self.assertEqual(project(), sha256(evidence).hexdigest())
        original = phase.read_bytes()
        self.assertEqual(decode(original), {
            'issue_id': issue.id, 'run': 1, 'scope_sha256': grant.scope_sha256,
            'state': 'assess', 'phase': 'complete', 'event': 'complete',
            'target': 'investigate', 'session_id': previous, 'report_posted': True,
        })
        self.assertEqual(phase.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(Hold, 'already_exists'):
            project()
        self.assertEqual(phase.read_bytes(), original)
