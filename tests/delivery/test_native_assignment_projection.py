"""Source-only integration fixture for the native assignment consumer."""
from dataclasses import replace
from pathlib import Path
import unittest

from uif.handoff_store import Hold, decode
from uif.stokowski_cloudflare.native_assignment_projection import project_clean_assignment
from uif.stokowski_cloudflare.native_composition import observe_clean_assignment_workspace
import test_native_remote_authority as fixture


class NativeAssignmentProjectionTest(unittest.TestCase):
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
