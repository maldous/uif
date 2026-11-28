"""Source-only integration fixture for the native assignment consumer."""
import asyncio
from dataclasses import asdict, replace
from pathlib import Path
import unittest

from uif.handoff_store import Hold, decode, digest, wire
from uif.stokowski_cloudflare.native_assignment_projection import project_clean_assignment, project_unstarted_assignment
from uif.stokowski_cloudflare.native_composition import observe_clean_assignment_workspace
from uif.stokowski_cloudflare.native_composition import _retain_handoff_original
import test_native_remote_authority as fixture
import test_native_unstarted_recovery as recovery_fixture


class NativeAssignmentProjectionTest(unittest.TestCase):
    def test_recovered_workspace_uses_the_recipient_transition_through_restart(self):
        case = fixture.RealCheckoutTest()
        self.addCleanup(case.doCleanups)
        case.setUp()
        peer = recovery_fixture.UnstartedRecoveryTest()
        peer.setUp()
        grant = case.grant
        peer.body['current']['fence']['source'] = asdict(grant.source)
        peer.current = replace(peer.current, lease=replace(peer.current.lease,
                                  fence=replace(peer.current.lease.fence, source=grant.source)))
        peer.receipt['lease'] = asdict(peer.current.lease)
        peer.receipt['request_sha256'] = digest(dict(kind=peer.receipt['request_kind'],
                                          request=dict(peer.body, authority=asdict(fixture.OWNER))))
        observed = observe_clean_assignment_workspace(case.path, grant.source, case.item['git_config_sha256'])
        def project(body):
            return project_unstarted_assignment(grant, fixture.OWNER, installation_sha256=peer.realm,
                submitted_handoff=wire(body), review_of={'review': 'investigate'},
                workspace_root=case.path.parent, issue_identifier=case.issue.identifier, observation=observed)
        with self.assertRaisesRegex(Hold, 'recovery_assignment'):
            project(peer.body | {'successor_session': 'other-peer'})
        entries = decode(project(peer.body), limit=8192)
        entry = entries[fixture.ISSUE]
        self.assertEqual(entry['recipient_original'], peer.binding)
        binding = entry['workspace_binding']
        self.assertNotIn('original_claim_id', binding)
        self.assertEqual(binding['original_transition'], dict(peer.lookup, request_kind=peer.receipt['request_kind'],
                                                              request_sha256=peer.receipt['request_sha256']))
        config = {'workspace_root': str(case.path.parent), 'coordinator_epoch': 1, 'assignments': entries}
        def composition():
            native = fixture.NativeComposition(peer.remote, '/unused')
            native.installation = lambda: config
            return native
        for native in (composition(), composition()):
            self.assertFalse(native.selected('OffMenu', case.issue, grant.scope_sha256))
            asyncio.run(native.receive_pending(case.owner))
            native.receive_late_completions(case.owner)
            self.assertEqual(native.observe_unstarted_assignment(case.owner, case.issue, grant.scope_sha256),
                             peer.current)
        self.assertEqual([c.args[0] for c in peer.remote.transport.request.call_args_list],
                         ['/v1/status', '/v1/transition/original', '/v1/claim/read'] * 2)
        peer.remote.transport.request.reset_mock()
        binding['original_transition']['request_id'] = 'substitution'
        with self.assertRaisesRegex(Hold, 'workspace_transition'):
            native.observe_unstarted_assignment(case.owner, case.issue, grant.scope_sha256)
        binding['original_transition']['request_id'] = peer.body['request_id']
        binding['inode'] += 1
        with self.assertRaisesRegex(Hold, 'workspace_changed'):
            native.observe_unstarted_assignment(case.owner, case.issue, grant.scope_sha256)
        peer.remote.transport.request.assert_not_called()
        binding['inode'] -= 1
        for journal in ('uif-original-effects', 'uif-owner-handoffs'):
            with self.subTest(journal=journal):
                folder = case.path / '.git' / journal
                original = _retain_handoff_original(folder, 'retained.json', b'pending original', 8192)
                self.assertEqual(observe_clean_assignment_workspace(
                    case.path, grant.source, case.item['git_config_sha256']), observed)
                with self.assertRaisesRegex(Hold, 'recipient_journal_needs_reconciliation'):
                    native.observe_unstarted_assignment(case.owner, case.issue, grant.scope_sha256)
                self.assertEqual(original.path.read_bytes(), b'pending original')
                self.assertFalse(native.selected('OffMenu', case.issue, grant.scope_sha256))
                with self.assertRaisesRegex(Hold, 'attempt_not_selected'), native.bind_dispatch(
                        case.owner, case.issue, grant.scope_sha256, {'attempt': grant.attempts[0]}):
                    self.fail('recovered attempt redispatched')
                original.path.unlink()
        peer.remote.transport.request.assert_not_called()
        def effect_during_read(path, data=None, **options):
            result = peer.read(path, data, **options)
            if path == '/v1/claim/read':
                _retain_handoff_original(case.path / '.git/uif-original-effects', 'concurrent.json',
                                         b'pending at readback', 8192)
            return result
        peer.remote.transport.request.side_effect = effect_during_read
        with self.assertRaisesRegex(Hold, 'recipient_journal_needs_reconciliation'):
            native.observe_unstarted_assignment(case.owner, case.issue, grant.scope_sha256)
        self.assertEqual([c.args[0] for c in peer.remote.transport.request.call_args_list],
                         ['/v1/status', '/v1/transition/original', '/v1/claim/read'])

    def test_exact_checkout_and_grant_round_trip_without_claim(self):
        case = fixture.RealCheckoutTest()
        try:
            case.setUp()
            grant = case.grant
            observation = observe_clean_assignment_workspace(
                case.path, grant.source, case.item['git_config_sha256'])
            args = dict(review_of={'review': 'investigate'}, workspace_root=case.path.parent,
                        issue_identifier=case.path.name, observation=observation)
            raw = project_clean_assignment(grant, fixture.OWNER, **args)
            case.c.installation = lambda: {'assignments': decode(raw, limit=8192),
                'workspace_root': str(case.path.parent), 'coordinator_epoch': 1}
            self.assertEqual(case.c.assignment('OffMenu', fixture.ISSUE, grant.scope_sha256), grant)
            self.assertEqual(case.c.verify_workspace(case.owner, case.issue, grant), case.path)
            self.assertIsNone(case.c._first_workspace(case.path, grant, None))
            self.assertEqual(case.worker.calls, [])
            fetched = case.c.installation()['assignments'][fixture.ISSUE]
            self.assertEqual(fetched['workspace_binding']['original_claim_id'],
                             grant.claim(fixture.OWNER).request_id)
            self.assertEqual(fetched['reconstruction_sha256'], grant.reconstruction_sha256)
            self.assertEqual(project_clean_assignment(grant, fixture.OWNER, **args), raw)
            with self.assertRaisesRegex(Hold, 'candidate_observation'):
                project_clean_assignment(grant, fixture.OWNER, **(
                    args | {'observation': observation | {'path': str(case.path.parent / '.stage')}}))
            with self.assertRaisesRegex(Hold, 'candidate_grant'):
                project_clean_assignment(replace(grant, previous=fixture.RECORD.lease,
                    previous_completion_sha256='a'*64), fixture.OWNER, **args)
        finally:
            case.doCleanups()
