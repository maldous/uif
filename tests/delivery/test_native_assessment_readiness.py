import hashlib
import unittest

from uif.handoff_store import Hold, wire
from uif.stokowski_cloudflare.assessment_readiness import project_assessment_readiness


class AssessmentReadinessTest(unittest.TestCase):
    def test_prompt_pinned_candidate_not_downstream_approval(self):
        routing = dict(issue='UIF-1', state='assess', run=2, actionable=True, ownership_clear=True,
                       deferred=False, owner_pause=False, uncertain_effects=False, missing_inputs=[])
        report = dict(summary='Bounded work can start according to the report',
                      verdict='approve', claims=[], verification=[], routing=routing)
        def project(raw):
            return project_assessment_readiness(raw, issue='UIF-1', run=2)
        raw = b'```json\n' + wire(report) + b'\n```'
        self.assertEqual(project(raw).readiness, 'reported-ready')
        self.assertEqual(project(raw).report_sha256, hashlib.sha256(raw).hexdigest())
        for field, wrong in [('actionable', 1), ('ownership_clear', False), ('deferred', True),
                             ('owner_pause', True), ('uncertain_effects', True),
                             ('missing_inputs', ['required'])]:
            with self.subTest(field=field), self.assertRaisesRegex(Hold, 'readiness_unsubstantiated'):
                project(wire(report | {'routing': routing | {field: wrong}}))
        for patch in [{'issue': 'UIF-2'}, {'state': 'investigate'}, {'run': True}, {'run': 3}]:
            with self.subTest(patch=patch), self.assertRaisesRegex(Hold, 'report_binding'):
                project(wire(report | {'routing': routing | patch}))
        with self.assertRaisesRegex(Hold, 'duplicate_json_key'):
            project(wire(report).replace(b'"actionable":true', b'"actionable":false,"actionable":true'))
        for verdict in ('blocked', 'deferred'):
            self.assertEqual(project(wire(report | {'verdict': verdict, 'routing': {
                'issue': 'UIF-1', 'state': 'assess', 'run': 2}})).readiness, verdict)
