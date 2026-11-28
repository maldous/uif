"""Compose the maintained new-issue path only as far as the real claim binding."""
import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, decode
from uif.stokowski_cloudflare.fresh_assessment_dispatch import observe_fresh_assessment_dispatch
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.launcher import _native_dispatch_binding
from uif.stokowski_cloudflare.native_assignment_projection import project_clean_assignment
from uif.stokowski_cloudflare.native_composition import NativeComposition
from uif.stokowski_cloudflare.native_workspace_preparation import (
    _git, place_prepared_workspace, prepare_detached_workspace,
)
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable
from uif.stokowski_cloudflare.shared_admission import NativeClaim
import test_native_remote_authority as fixture


class NativeCleanIssueBindingTest(unittest.TestCase):
    def test_current_intake_binds_one_new_original_and_restart_only_observes(self):
        self.exercise_intake(lost_reply=False)

    def test_placed_checkout_and_guarded_initial_claim_recover_same_original_without_resend(self):
        self.exercise_intake(lost_reply=True)

    def test_guarded_dispatch_with_unobserved_original_does_not_enter_or_reacquire(self):
        self.exercise_intake(lost_reply=True, committed=False)

    def exercise_intake(self, *, lost_reply, committed=True):
        case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        issue = SimpleNamespace(id=case.issue.id, identifier=case.issue.identifier,
                                title='Read a public guide', description='Synthetic exercise')
        stages = intake_stages({}, 'OffMenu', issue, case.grant.scope_sha256, 1)
        stage = stages['assess']
        attempts = (stage['attempt'], stages['investigate']['attempt']) if lost_reply else (stage['attempt'],)
        grant = replace(case.grant, attempts=attempts)
        original_workspace = case.path
        root = original_workspace.parent / 'native-root'
        root.mkdir(mode=0o700)
        bundle = root.parent / 'input.bundle'
        _git(original_workspace, 'bundle', 'create', str(bundle), 'develop')
        staged, staged_observation = prepare_detached_workspace(
            root, bundle, sha256(bundle.read_bytes()).hexdigest(), grant.source)
        status = place_prepared_workspace(root, staged, issue.identifier, grant.source, staged_observation)
        case.path = root / issue.identifier
        self.assertEqual(status['inode'], staged_observation['inode'])
        self.assertFalse(staged.exists())
        self.assertTrue(original_workspace.is_dir())
        raw = project_clean_assignment(grant, fixture.OWNER, review_of={},
            workspace_root=case.path.parent, issue_identifier=issue.identifier, observation=status)

        def configuration():
            return {'assignments': decode(raw, limit=8192),
                    'workspace_root': str(case.path.parent), 'coordinator_epoch': 1}

        request = grant.claim(fixture.OWNER)
        lease = replace(fixture.RECORD.lease, fence=replace(fixture.RECORD.lease.fence, source=grant.source))
        claim = NativeClaim('OffMenu', stage['attempt'], lease)
        event = case.worker.request
        directory = case.path / '.git/uif-original-effects'
        intent = directory / (request.request_id + '.assessment-first-intent')
        bound = directory / (request.request_id + '.assessment-first-bound')

        def receiver(path, body=None, **options):
            if path != '/v1/claim':
                return event(path, body, **options)
            case.worker.calls.append((path, body, options))
            self.assertIsNone(case.worker.claim)
            expected = asdict(request)
            del expected['authority']
            self.assertEqual(body, expected | {'attempt_id': stage['attempt']})
            if lost_reply and not committed:
                self.assertTrue(intent.is_file())
                raise AuthorityUnavailable('synthetic uncertain claim with no observed commit')
            case.worker.claim = claim
            case.worker.original = {'request_id': request.request_id, 'request_kind': request.kind,
                'request_sha256': transition_request_sha256(request), 'lease': asdict(lease)}
            if lost_reply:
                self.assertTrue(intent.is_file())
                self.assertFalse(bound.exists())
                raise AuthorityUnavailable('synthetic committed claim, acknowledgement lost')
            return case.worker.original

        case.r.transport.request = receiver
        case.c.installation = configuration
        self.assertTrue(case.c.selected('OffMenu', issue, stage['scope_sha256']))
        with self.assertRaisesRegex(Hold, 'attempt_not_selected'):
            with case.c.bind_dispatch(case.owner, issue, stage['scope_sha256'], stage | {'attempt': 'wrong'}):
                self.fail('unauthorized stage entered')
        self.assertEqual(case.worker.calls, [])

        def verify_trusted_fixture_owner(owner, target, run, assignment):
            self.assertEqual((owner, target, run, assignment), (case.owner, issue, 1, grant))

        case.owner._issue_state_runs = {issue.id: 1}
        verifier = verify_trusted_fixture_owner if lost_reply else None
        if lost_reply:
            def deny(*args):
                raise Hold('fixture_exclusive_owner_not_verified')

            with self.assertRaisesRegex(Hold, 'exclusive_owner_not_verified'):
                with _native_dispatch_binding(case.c, case.owner, issue, 'assess', stage, deny):
                    self.fail('denied proof entered scheduler context')
            self.assertFalse(directory.exists())
            self.assertNotIn('/v1/claim', [path for path, _, _ in case.worker.calls])

        if not committed:
            for _ in range(2):
                with self.assertRaisesRegex(Hold, 'original_claim_unverified'):
                    with _native_dispatch_binding(case.c, case.owner, issue, 'assess', stage, verifier):
                        self.fail('uncertain claim entered scheduler context')
            self.assertIsNone(case.c._running.get())
            self.assertEqual(set(directory.iterdir()), {intent})
            self.assertEqual([path for path, _, _ in case.worker.calls].count('/v1/claim'), 1)
            return

        with _native_dispatch_binding(case.c, case.owner, issue, 'assess', stage, verifier):
            self.assertEqual(case.c.current(issue.id), claim)
            if lost_reply:
                markers = (intent.read_bytes(), bound.read_bytes())
        self.assertIsNone(case.c._running.get())

        # No native settlement writer was run and no result was invented.
        _, clean_remote = fixture.make()
        clean_remote.transport.request = receiver
        restarted = NativeComposition(clean_remote, '/unused-fixture')
        restarted.installation = configuration
        if lost_reply:
            self.assertEqual(observe_fresh_assessment_dispatch(restarted, case.owner, issue, 1,
                grant.scope_sha256, lease, verify_first_dispatch_applicability=verify_trusted_fixture_owner), claim)
            with _native_dispatch_binding(restarted, case.owner, issue, 'assess', stage, verifier):
                self.assertEqual(restarted.current(issue.id), claim)
            self.assertEqual((intent.read_bytes(), bound.read_bytes()), markers)
        asyncio.run(restarted.reconcile_originals(case.owner, Mock()))
        self.assertEqual(restarted.remote.original_lease(grant), lease)
        self.assertEqual([p for p, _, _ in case.worker.calls].count('/v1/claim'), 1)
        self.assertNotIn('/v1/release', [p for p, _, _ in case.worker.calls])
        if lost_reply:
            self.assertEqual(set(directory.iterdir()), {intent, bound})
        else:
            self.assertFalse(directory.exists())
        self.assertEqual(case.worker.claim, claim)
