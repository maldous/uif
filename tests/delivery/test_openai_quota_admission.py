from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from uif.openai_quota_admission import (
    ApiCreditEvidence, ApiRoute, CodexEvidence, CodexRoute, CodexWindow,
    allows_codex, allows_openai_api,
)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        self.deadline = self.now + timedelta(seconds=20)
        self.reset = self.now + timedelta(hours=1)
        self.valid = self.now + timedelta(seconds=25)
        self.codex = CodexRoute('subscription-account', 'a' * 64, 'model', ('short', 'week'))
        self.windows = (CodexWindow('short', 50, 100, 2, 3, self.reset),
                        CodexWindow('week', 21, 100, 2, 3, self.reset))
        self.sub = CodexEvidence(self.codex, self.now, self.valid, True, True, self.windows)
        self.api = ApiRoute('org', 'project', 'b' * 64, 'model', 'responses.create')
        self.credit = ApiCreditEvidence(self.api, self.now, self.valid, self.reset, 100, 80, True, True, True)

    def codex_allows(self, proof, operation='new', route=None):
        return allows_codex(route or self.codex, proof, operation=operation,
                            now=self.now, deadline=self.deadline)

    def api_allows(self, proof, *, own=34, pending=30, route=None, intent="new"):
        return allows_openai_api(route or self.api, proof, intent=intent, now=self.now, deadline=self.deadline,
                                 unsettled_upper_microusd=pending, request_upper_microusd=own)

    def test_most_restrictive_projection_soft_and_hard(self):
        for projected in (16, 15, 11, 10, 0):
            week = replace(self.windows[1], remaining_lower=projected + 5)
            proof = replace(self.sub, windows=(self.windows[0], week))
            self.assertEqual(self.codex_allows(proof), projected > 15)
            for mode in ('continue', 'retry'):
                self.assertEqual(self.codex_allows(proof, mode), projected > 10)

    def test_codex_fails_closed_on_identity_and_missing_window(self):
        for proof in (None, self.credit, replace(self.sub, windows=self.windows[:1]),
                      replace(self.sub, windows=(self.windows[0], self.windows[0])),
                      replace(self.sub, route=replace(self.codex, credential_epoch='b' * 64)),
                      replace(self.sub, subscription_only=False),
                      replace(self.sub, complete_binding=1)):
            self.assertFalse(self.codex_allows(proof, 'retry'))

    def test_no_rollover_or_stale_snapshot(self):
        week = replace(self.windows[1], reset_at=self.deadline)
        self.assertFalse(self.codex_allows(replace(self.sub, windows=(self.windows[0], week))))
        self.assertFalse(self.codex_allows(replace(self.sub, observed_at=self.now-timedelta(seconds=91))))
        self.assertFalse(self.api_allows(replace(self.credit, credit_expires_at=self.deadline)))

    def test_direct_api_included_exact_bound(self):
        self.assertTrue(self.api_allows(self.credit))
        self.assertFalse(self.api_allows(self.credit, own=35))
        self.assertFalse(self.api_allows(self.credit, own=True))
        self.assertFalse(self.api_allows(self.credit, pending=-1))

    def test_api_soft_hard_floors_include_outstanding_spend(self):
        for projected in (16, 15, 11, 10, 0):
            request = 50 - projected
            self.assertEqual(self.api_allows(self.credit, own=request), projected > 15)
            for intent in ('continue', 'retry'):
                self.assertEqual(self.api_allows(self.credit, own=request, intent=intent),
                                 projected > 10)

    def test_api_unknown_intent_or_bad_grant_total_refuses(self):
        for intent in (None, True, 'other'):
            self.assertFalse(self.api_allows(self.credit, intent=intent))
        for total in (None, True, 0, 79):
            self.assertFalse(self.api_allows(
                replace(self.credit, included_grant_total_microusd=total)))

    def test_unknown_paid_or_route_drift_remains_disabled(self):
        for proof in (None, self.sub, replace(self.credit, included_only=False),
                      replace(self.credit, hard_no_overage=False),
                      replace(self.credit, complete_binding=False),
                      replace(self.credit, route=replace(self.api, project='different')),
                      replace(self.credit, route=replace(self.api, organization='different')),
                      replace(self.credit, route=replace(self.api, model='fallback')),
                      replace(self.credit, route=replace(self.api, operation='other'))):
            self.assertFalse(self.api_allows(proof))
