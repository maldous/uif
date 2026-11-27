from hashlib import sha256
from types import SimpleNamespace
import unittest

from uif.handoff_store import Hold, wire
from uif.stokowski_cloudflare.fresh_assessment_render import render_fresh_assessment
from uif.stokowski_cloudflare.intake import intake_stages


class FreshAssessmentRenderTest(unittest.TestCase):
    def test_exact_bounded_render_excludes_untrusted_text_and_binds_raw(self):
        issue = SimpleNamespace(id='synthetic-id', identifier='OFF-1', title='Never echo topic',
                                description='Never echo description')
        scope = 'a'*64
        attempt = intake_stages({}, 'OffMenu', issue, scope, 1)['assess']['attempt']
        raw = wire(dict(summary='Never echo @person or arbitrary text', verdict='approve', routing=dict(
            issue='OFF-1', state='assess', run=1, actionable=True, ownership_clear=True,
            deferred=False, owner_pause=False, uncertain_effects=False, missing_inputs=[])))
        args = dict(issue=issue, scope_sha256=scope, run=1, attempt_id=attempt)
        body = render_fresh_assessment(raw, **args)
        self.assertEqual(body, render_fresh_assessment(raw, **args))
        self.assertTrue(body.startswith('<!-- stokowski:report -->\n'))
        self.assertIn(sha256(raw).hexdigest(), body)
        self.assertIn(attempt, body)
        self.assertNotIn('Never echo', body)
        self.assertNotIn('@person', body)
        self.assertNotEqual(body, render_fresh_assessment(raw + b' ', **args))
        with self.assertRaisesRegex(Hold, 'report_binding'):
            render_fresh_assessment(raw, **(args | {'run': 2}))
        with self.assertRaisesRegex(Hold, 'render_attempt'):
            render_fresh_assessment(raw, **(args | {'attempt_id': attempt + '-other'}))
