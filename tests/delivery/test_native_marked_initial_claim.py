from contextlib import contextmanager
from dataclasses import asdict, replace
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_fresh_assessment_dispatch import enroll, ready_owner
import test_native_remote_authority as fixture
from uif.effect_authority import transition_request_sha256
from uif.handoff_store import Hold, digest
from uif.stokowski_cloudflare import fresh_assessment_dispatch as dispatch
from uif.stokowski_cloudflare.intake import intake_stages
from uif.stokowski_cloudflare.remote_authority import AuthorityUnavailable


class MarkedInitialClaimTest(unittest.TestCase):
    def setUp(self):
        self.case = case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        ready_owner(case.owner)
        self.issue = SimpleNamespace(id=case.issue.id, identifier=case.issue.identifier,
                                     title='Fixture', description='Original scoped assessment')
        self.scope = case.grant.scope_sha256
        self.stage = stages = intake_stages({}, 'OffMenu', self.issue, self.scope, 1)
        first, next_attempt = (stages[key]['attempt'] for key in ('assess', 'investigate'))
        grant = replace(case.grant, attempts=(first, next_attempt))
        self.request = request = grant.claim(fixture.OWNER)
        case.item.update(scope_sha256=self.scope, attempts=list(grant.attempts), source=asdict(grant.source),
                         reconstruction_sha256=grant.reconstruction_sha256)
        case.item['workspace_binding']['original_claim_id'] = request.request_id
        self.grant = enroll(grant, case.item, self.issue, 1, first, next_attempt)
        case.c.installation = lambda: dict(assignments={self.issue.id: case.item},
                                workspace_root=str(case.path.parent), coordinator_epoch=1)
        self.current = replace(fixture.RECORD, attempt_id=first, lease=replace(
            fixture.RECORD.lease, fence=replace(fixture.FENCE, source=grant.source)))
        self.reply = dict(request_id=request.request_id, request_kind=request.kind,
                          request_sha256=transition_request_sha256(request), lease=asdict(self.current.lease))
        self.expected_body = asdict(request) | dict(attempt_id=first, first_intent_sha256=self.grant.first_intent_sha256)
        del self.expected_body['authority']
        original_call = case.worker.request
        self.posts, self.permits, self.observations = [], [], []
        self.uncertain = False

        def call(path, body=None, **kwargs):
            if path in ('/v1/claim', '/v1/claim/observe'):
                self.assertEqual(body, self.expected_body)
            if path == '/v1/claim':
                self.posts.append(body)
                if self.uncertain:
                    raise AuthorityUnavailable('lost before result')
                case.worker.claim, case.worker.original = self.current, self.reply
                return self.reply
            return original_call(path, body, **kwargs)

        case.r.transport.request = call
        @contextmanager
        def permit(claim, admission):
            self.permits.append((claim, admission))
            yield object()
        case.r.transport.fresh_assessment_permission = permit
        case.r.transport.observe_fresh_assessment_permission = lambda *p: self.observations.append(p)
        self.journal = case.path / '.git/uif-original-effects'
        self.bound = self.journal / (request.request_id + '.assessment-first-bound')
        self.entry = self.journal / (request.request_id + '.assessment-dispatch-enter')
        case.c._verified_assessment_report_sink = object()

    def bind(self):
        return self.case.c.bind_dispatch(self.case.owner, self.issue, self.scope, self.stage['assess'], stage='assess')

    def restart_composition(self):
        installation = self.case.c.installation
        _, remote = fixture.make()
        remote.transport = self.case.r.transport
        self.case.r = remote
        self.case.c = fixture.NativeComposition(remote, '/unused')
        self.case.c.installation = installation
        self.case.c._verified_assessment_report_sink = object()

    def test_initial_native_entry_recovers_by_exact_observation_after_restart(self):
        synced = []
        real_sync = os.fsync
        parent = (self.case.path / '.git').stat()
        def record_sync(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                self.assertEqual(self.posts, [])
                synced.append(True)
            return real_sync(fd)
        with patch.object(dispatch.os, 'fsync', side_effect=record_sync), self.bind():
            self.assertEqual(self.case.c.current(self.issue.id), self.current)
        self.assertTrue(synced)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.permits), 1)
        self.restart_composition()
        with self.assertRaisesRegex(Hold, 'already_attempted'), self.bind():
            self.fail('second dispatch')
        self.assertEqual((len(self.posts), len(self.permits), len(self.observations)), (1, 1, 1))

    def test_post_committed_before_bound_crash_repairs_only_from_original(self):
        with patch.object(dispatch, '_retain_handoff_original', side_effect=OSError('lost local bound record')):
            with self.assertRaisesRegex(OSError, 'lost local bound'), self.bind():
                self.fail('unconfirmed local original')
        self.assertFalse(self.bound.exists())
        self.assertEqual((len(self.posts), len(self.permits)), (1, 0))
        self.restart_composition()
        with self.bind():
            self.assertTrue(self.bound.is_file())
        self.assertEqual((len(self.posts), len(self.permits)), (1, 1))

    def test_missing_intent_with_preexisting_claim_never_invents_a_marker(self):
        self.case.worker.claim, self.case.worker.original = self.current, self.reply
        with self.assertRaisesRegex(Hold, 'existing_claim_without_intent'), self.bind():
            self.fail('existing claim adopted')
        self.assertFalse(self.journal.exists())
        self.assertEqual((self.posts, self.permits), ([], []))

    def test_lost_reply_with_absent_original_never_submits_again(self):
        self.uncertain = True
        for _ in range(2):
            with self.assertRaisesRegex(Hold, 'original_claim_unverified'), self.bind():
                self.fail('uncertain claim')
            self.restart_composition()
        self.assertFalse(self.bound.exists())
        self.assertEqual((len(self.posts), len(self.permits)), (1, 0))
        self.assertFalse(any(path in ('/v1/release', '/v1/send') for path, _, _ in self.case.worker.calls))

    def test_local_parent_sync_failure_never_posts_even_after_restart(self):
        parent = (self.case.path / '.git').stat()
        original = os.fsync
        def fail_parent(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                raise OSError('parent sync failed')
            original(fd)
        with patch.object(dispatch.os, 'fsync', side_effect=fail_parent):
            with self.assertRaisesRegex(OSError, 'parent sync failed'), self.bind():
                self.fail('undurable parent')
        with self.assertRaisesRegex(Hold, 'original_claim_unverified'), self.bind():
            self.fail('retried post')
        self.assertEqual((self.posts, self.permits), ([], []))

    def test_busy_recovered_owner_cannot_consume_first_dispatch(self):
        with patch.object(dispatch, '_retain_handoff_original', side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError, 'interrupted'), self.bind():
                self.fail('interrupted')
        self.case.owner.completed.add(self.issue.id)
        with self.assertRaisesRegex(Hold, 'owner_busy'), self.bind():
            self.fail('completed issue dispatched')
        self.assertTrue(self.bound.exists())
        self.assertFalse(self.entry.exists())
        self.assertEqual((len(self.posts), len(self.permits)), (1, 0))

    def test_generic_acquire_cannot_open_a_marked_original(self):
        grant = self.case.c.assignment('OffMenu', self.issue.id, self.scope)
        self.assertEqual(grant, self.grant)
        with self.assertRaisesRegex(Hold, 'marked_first_dispatch_required'):
            self.case.r.acquire(grant, grant.attempts[0])
        with self.assertRaisesRegex(Hold, 'original_claim_unverified'):
            self.case.r.original_lease(grant, allow_acquire=True)
        self.assertEqual(self.posts, [])

    def test_recomputed_pin_and_current_owner_precede_first_claim(self):
        mark = self.case.item['fresh_assessment']
        mark['first_intent_sha256'] = 'e' * 64
        with self.assertRaisesRegex(Hold, 'assessment_dispatch_authority_intent'), self.bind():
            self.fail('different enrolled intent')
        mark['first_intent_sha256'] = self.grant.first_intent_sha256
        self.case.owner.pool = SimpleNamespace(is_paused=lambda project: True)
        with self.assertRaisesRegex(Hold, 'owner_busy'), self.bind():
            self.fail('paused owner')
        self.assertFalse(self.journal.exists())
        self.assertEqual((self.posts, self.permits), ([], []))

    def test_pre_marker_v1_digest_is_stable(self):
        grant = fixture.GRANT
        request = grant.claim(fixture.OWNER)
        item = dict(workspace_binding=dict(path='/fixture/UIF-1', device=1, inode=2, original_claim_id=request.request_id),
                    git_config_sha256='3'*64, prepared_tree_sha256='4'*64)
        expected = 'd8ce07a45410efb09acbe53c3f77a1e3813e73eb9b7d5cd83b1f7cf51aa50441'
        for form in (grant, replace(grant, first_intent_sha256=expected)):
            self.assertEqual(digest(dispatch._first_dispatch_intent(
                form, request, SimpleNamespace(identifier='UIF-1'), 1, 'assess', 'investigate', item)), expected)
