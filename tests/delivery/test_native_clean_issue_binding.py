"""Compose the maintained new-issue path only as far as the real claim binding."""
import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, decode
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.native_assignment_projection import project_clean_assignment
from uif.stokowski_cloudflare.native_composition import NativeComposition
from uif.stokowski_cloudflare.native_workspace_preparation import (
    _git, place_prepared_workspace, prepare_detached_workspace,
)
from uif.stokowski_cloudflare.shared_admission import NativeClaim
import test_native_remote_authority as fixture


class NativeCleanIssueBindingTest(unittest.TestCase):
    def test_current_intake_binds_one_new_original_and_restart_only_observes(self):
        case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        issue = SimpleNamespace(id=case.issue.id, identifier=case.issue.identifier,
                                title='Read a public guide', description='Synthetic exercise')
        stage = intake_stages({}, 'OffMenu', issue, case.grant.scope_sha256, 1)['assess']
        grant = replace(case.grant, attempts=(stage['attempt'],))
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

        def receiver(path, body=None, **options):
            if path != '/v1/claim':
                return event(path, body, **options)
            case.worker.calls.append((path, body, options))
            self.assertIsNone(case.worker.claim)
            expected = asdict(request)
            del expected['authority']
            self.assertEqual(body, expected | {'attempt_id': stage['attempt']})
            case.worker.claim = claim
            case.worker.original = {'request_id': request.request_id, 'request_kind': request.kind,
                'request_sha256': transition_request_sha256(request), 'lease': asdict(lease)}
            return case.worker.original

        case.r.transport.request = receiver
        case.c.installation = configuration
        self.assertTrue(case.c.selected('OffMenu', issue, stage['scope_sha256']))
        with self.assertRaisesRegex(Hold, 'attempt_not_selected'):
            with case.c.bind_dispatch(case.owner, issue, stage['scope_sha256'], stage | {'attempt': 'wrong'}):
                self.fail('unauthorized stage entered')
        self.assertEqual(case.worker.calls, [])
        with case.c.bind_dispatch(case.owner, issue, stage['scope_sha256'], stage):
            self.assertEqual(case.c.current(issue.id), claim)
        self.assertIsNone(case.c._running.get())

        # No native settlement writer was run and no result was invented.
        restarted = NativeComposition(case.r, '/unused-fixture')
        restarted.installation = configuration
        asyncio.run(restarted.reconcile_originals(case.owner, Mock()))
        self.assertEqual(restarted.remote.original_lease(grant), lease)
        self.assertEqual([p for p, _, _ in case.worker.calls].count('/v1/claim'), 1)
        self.assertNotIn('/v1/release', [p for p, _, _ in case.worker.calls])
        self.assertFalse((case.path / '.git/uif-original-effects').exists())
        self.assertEqual(case.worker.claim, claim)
