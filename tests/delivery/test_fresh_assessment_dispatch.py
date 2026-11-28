from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from contextlib import contextmanager
from hashlib import sha256
from unittest.mock import patch

from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, digest
from uif.stokowski_cloudflare import fresh_assessment_dispatch as dispatch
from uif.stokowski_cloudflare.fresh_assessment_dispatch import (
    observe_fresh_assessment_dispatch, retain_fresh_assessment_dispatch,
)
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable
import test_native_remote_authority as fixture


def enroll(grant, item, issue, run, previous, successor):
    # The enrolled claim-input digest uses the v1 grant fields predating the
    # transport mark. Computing it independently catches self-referential pins.
    grant_fields = ('scope', 'source', 'reconstruction_sha256', 'scope_sha256',
                    'expected_coordinator_epoch', 'attempts', 'previous', 'previous_completion_sha256')
    data = asdict(grant)
    original = grant.claim(fixture.OWNER)
    intent = dict(schema='uif.fresh-assessment-first-dispatch/1',
                  grant_sha256=digest({k: data[k] for k in grant_fields}), claim=asdict(original),
                  issue_identifier=issue.identifier, run=run, previous=previous, successor=successor,
                  workspace_binding=item['workspace_binding'], git_config_sha256=item['git_config_sha256'],
                  prepared_tree_sha256=item['prepared_tree_sha256'])
    pin = digest(intent)
    item['fresh_assessment'] = dict(attempt_id=previous, first_intent_sha256=pin)
    return replace(grant, first_intent_sha256=pin)


def ready_owner(owner):
    owner._issue_state_runs = {}
    owner.claimed, owner.completed = set(), set()
    owner._issue_current_state, owner._tasks, owner._pending_gates, owner.retry_attempts = {}, {}, {}, {}
    owner.pool = SimpleNamespace(is_paused=lambda project: False)


class FreshAssessmentDispatchTest(unittest.TestCase):
    def exercise(self, *, admitted, old=False, drift=None, permission=None):
        case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        issue = SimpleNamespace(id=case.issue.id, identifier=case.issue.identifier,
                                title='Fixture', description='Synthetic')
        scope = case.grant.scope_sha256
        stages = intake_stages({}, 'OffMenu', issue, scope, 1)
        previous, successor = stages['assess']['attempt'], stages['investigate']['attempt']
        grant = replace(case.grant, attempts=(previous, successor))
        case.c.assignment = lambda *args: grant
        original = grant.claim(fixture.OWNER)
        case.item['workspace_binding']['original_claim_id'] = original.request_id
        grant = enroll(grant, case.item, issue, 1, previous, successor)
        lease = replace(fixture.RECORD.lease, fence=replace(fixture.RECORD.lease.fence, source=grant.source))
        claim = replace(fixture.RECORD, attempt_id=previous, lease=lease)
        old_request = case.worker.request
        journal = case.path / '.git/uif-original-effects'
        markers = [
            journal / (original.request_id + '.assessment-first-intent'),
            journal / (original.request_id + '.assessment-first-bound'),
        ]
        sent = []
        still_owned = True

        def transport(path, body=None, **options):
            if path in ('/v1/claim', '/v1/claim/observe'):
                expected = asdict(original)
                del expected['authority']
                self.assertEqual(body, dict(expected, attempt_id=previous, first_intent_sha256=grant.first_intent_sha256))
            if (path == '/v1/claim/read' and body['by_attempt'] and case.worker.claim is not None
                    and body['identifier'] != case.worker.claim.attempt_id):
                return None
            if path != '/v1/claim':
                return old_request(path, body, **options)
            self.assertTrue(markers[0].is_file())
            self.assertEqual(markers[0].stat().st_mode & 0o777, 0o600)
            self.assertFalse(markers[1].exists())
            sent.append(body)
            if admitted:
                case.worker.claim = claim
                case.worker.original = {'request_id': original.request_id, 'request_kind': original.kind,
                    'request_sha256': transition_request_sha256(original), 'lease': asdict(lease)}
            raise AuthorityUnavailable('synthetic reception uncertainty')

        if old:
            case.worker.claim = claim
        ready_owner(case.owner)
        case.r.transport.request = transport

        def verify(owner, verified_issue, run, assignment):
            self.assertEqual((owner, verified_issue, run, assignment), (case.owner, issue, 1, grant))
            if not still_owned:
                raise Hold('fixture_lost_dispatch_ownership')

        def enter():
            return retain_fresh_assessment_dispatch(
                case.c, case.owner, issue, 1, scope, verify_first_dispatch_applicability=verify)

        def observe():
            return observe_fresh_assessment_dispatch(
                case.c, case.owner, issue, 1, scope, lease, verify_first_dispatch_applicability=verify)

        if drift:
            config = case.path / '.git/config'
            original_config = config.read_bytes()
            real_publish = dispatch._publish_private_original

            def on_publish(*args, **kwargs):
                nonlocal still_owned
                real_publish(*args, **kwargs)
                if drift == 'config':
                    config.write_bytes(original_config + b'# changed after fsync\n')
                else:
                    still_owned = False

            with patch.object(dispatch, '_publish_private_original', side_effect=on_publish):
                with self.assertRaisesRegex(Hold, 'private_git_config' if drift == 'config'
                                            else 'fixture_lost_dispatch_ownership'):
                    enter()
            self.assertTrue(markers[0].is_file())
            self.assertFalse(markers[1].exists())
            config.write_bytes(original_config)
            still_owned = True
            with self.assertRaisesRegex(Hold, 'original_claim_unverified'):
                enter()
            self.assertEqual(sent, [])
        elif old:
            with self.assertRaisesRegex(Hold, 'existing_claim'):
                enter()
            self.assertEqual(sent, [])
            self.assertFalse(journal.exists())
        elif admitted:
            self.assertEqual(enter(), claim)
            before = tuple(path.read_bytes() for path in markers)
            self.assertEqual(enter(), claim)
            self.assertEqual(tuple(path.read_bytes() for path in markers), before)
            self.assertEqual(observe(), claim)
            self.assertEqual(tuple(path.read_bytes() for path in markers), before)
            if permission is not None:
                case.owner._issue_state_runs = {issue.id: 1}
                selected = {'attempt_id': previous, 'first_intent_sha256':
                            sha256(before[0]).hexdigest() if permission != 'pin' else '0' * 64}
                case.item['fresh_assessment'] = selected
                entries, launch = [], []
                observe_permit = case.r.transport.observe_fresh_assessment_permission = unittest.mock.Mock(
                    return_value=False)
                case.r.transport.fresh_assessment_permission = permit = unittest.mock.Mock()
                @contextmanager
                def entered(observed_claim, admission):
                    entries.append((observed_claim, admission))
                    if permission == 'lost':
                        raise AuthorityUnavailable('lost fixture reply')
                    yield
                permit.side_effect = entered
                if permission != 'unpatched':
                    case.c._verified_assessment_report_sink = object()  # Qualified in the pinned native test.
                with patch.object(case.r, 'acquire', side_effect=AssertionError('must only read retained claim')):
                    if permission == 'first':
                        with case.c.bind_dispatch(case.owner, issue, scope, stages['assess'], stage='assess'):
                            launch.append(case.c._running.get()[1])
                        self.assertEqual(launch, [claim])
                    else:
                        failure = {'pin': 'fresh_assessment_scope|authority_intent', 'unpatched': 'native_report_sink', 'lost': 'lost'}
                        with self.assertRaisesRegex(Hold, failure[permission]):
                            with case.c.bind_dispatch(case.owner, issue, scope, stages['assess'], stage='assess'):
                                self.fail('native dispatch launched')
                    if permission not in {'pin', 'unpatched'}:
                        with self.assertRaisesRegex(Hold, 'already_attempted'):
                            with case.c.bind_dispatch(case.owner, issue, scope, stages['assess'], stage='assess'):
                                self.fail('observed transport attempt launched')
                        observe_permit.assert_called_once_with(claim, selected)
                self.assertEqual(entries, [] if permission in {'pin', 'unpatched'} else [(claim, selected)])
                if permission == 'unpatched':
                    self.assertFalse((journal / (original.request_id + '.assessment-dispatch-enter')).exists())
                self.assertIsNone(case.c._running.get())
                self.assertEqual(tuple(path.read_bytes() for path in markers), before)
                return
            for record, original_bytes in zip(markers, before):
                record.write_bytes(original_bytes + b' ')
                with self.assertRaisesRegex(Hold, 'original_changed'):
                    observe()
                self.assertEqual(record.read_bytes(), original_bytes + b' ')
                record.write_bytes(original_bytes)
            verifications = 0
            original_clock = case.r.clock

            def verify_with_expiry(*args):
                nonlocal verifications
                verify(*args)
                verifications += 1
                if verifications == 2:
                    case.r.clock = lambda: datetime.fromtimestamp(lease.expires_at, timezone.utc)

            with self.assertRaisesRegex(Hold, 'not_current'):
                observe_fresh_assessment_dispatch(case.c, case.owner, issue, 1, scope, lease,
                    verify_first_dispatch_applicability=verify_with_expiry)
            self.assertEqual(verifications, 2)
            case.r.clock = original_clock
            case.worker.claim = replace(claim, attempt_id=successor)
            with self.assertRaisesRegex(Hold, 'not_current'):
                observe()
            self.assertEqual(len(sent), 1)
        else:
            for _ in range(2):
                with self.assertRaisesRegex(Hold, 'original_claim_unverified'):
                    enter()
            self.assertEqual(len(sent), 1)
            self.assertTrue(markers[0].is_file())
            self.assertFalse(markers[1].exists())

    def test_lost_reply_existing_original_is_reconciled_without_resend(self):
        self.exercise(admitted=True)

    def test_uncertain_absent_original_keeps_intent_without_resend(self):
        self.exercise(admitted=False)

    def test_preexisting_gen_one_claim_is_not_grandfathered(self):
        self.exercise(admitted=True, old=True)

    def test_after_fsync_config_drift_preserves_intent_and_never_sends(self):
        self.exercise(admitted=True, drift='config')

    def test_after_fsync_ownership_loss_preserves_intent_and_never_sends(self):
        self.exercise(admitted=True, drift='ownership')

    def test_marked_first_current_dispatch_enters_once_and_inherits_claim(self):
        self.exercise(admitted=True, permission='first')

    def test_marked_intent_pin_mismatch_never_enters_or_dispatches(self):
        self.exercise(admitted=True, permission='pin')

    def test_marked_uncertain_permission_never_enables_dispatch(self):
        self.exercise(admitted=True, permission='lost')

    def test_incompatible_native_does_not_consume_marked_dispatch(self):
        self.exercise(admitted=True, permission='unpatched')
