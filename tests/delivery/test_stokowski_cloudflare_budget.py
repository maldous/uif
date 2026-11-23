from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from uif.stokowski_cloudflare.budget import (
    BudgetRefused, HARD_LIMIT, NORMAL_LIMIT, ProjectAccountRouter, WorkersAIBudget,
)


class CloudflareBudgetTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'budget.sqlite'
        self.budget = WorkersAIBudget(self.database, 'a' * 32)
        self.now = datetime(2026, 11, 23, tzinfo=timezone.utc)

    def test_thresholds_and_settlement(self):
        self.budget.reserve('first', NORMAL_LIMIT, new_task=True, now=self.now)
        with self.assertRaises(BudgetRefused):
            self.budget.reserve('second', 1, new_task=True, now=self.now)
        self.budget.reserve('second', HARD_LIMIT - NORMAL_LIMIT, new_task=False, now=self.now)
        with self.assertRaises(BudgetRefused):
            self.budget.reserve('third', 1, new_task=False, now=self.now)
        self.budget.settle('first', NORMAL_LIMIT - 1)
        self.budget.reserve('third', 1, new_task=False, now=self.now)

    def test_unsettled_request_survives_restart_and_cannot_be_replayed(self):
        self.budget.reserve('first', NORMAL_LIMIT, new_task=True, now=self.now)
        restarted = WorkersAIBudget(self.database, 'a' * 32)
        with self.assertRaises(BudgetRefused):
            restarted.reserve('replacement', 1, new_task=True, now=self.now)
        tomorrow = self.now + timedelta(days=1)
        with self.assertRaises(BudgetRefused):
            restarted.reserve('first', 1, new_task=True, now=tomorrow)
        restarted.reserve('tomorrow', NORMAL_LIMIT, new_task=True, now=tomorrow)

    def test_concurrent_reservations_cannot_oversubscribe(self):
        def reserve(index):
            try:
                self.budget.reserve(str(index), NORMAL_LIMIT, new_task=True, now=self.now)
            except BudgetRefused:
                return False
            return True

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(8))), 1)

    def test_accounts_have_separate_records(self):
        self.budget.reserve('first', NORMAL_LIMIT, new_task=True, now=self.now)
        other = WorkersAIBudget(self.database, 'b' * 32)
        other.reserve('first', NORMAL_LIMIT, new_task=True, now=self.now)
        self.budget.settle('first', 0)
        with self.assertRaises(BudgetRefused):
            other.reserve('second', 1, new_task=True, now=self.now)

    def test_usage_above_reservation_blocks_further_requests_that_day(self):
        self.budget.reserve('first', 1, new_task=True, now=self.now)
        self.budget.settle('first', 2)
        with self.assertRaises(BudgetRefused):
            self.budget.reserve('second', 1, new_task=False, now=self.now)
        self.budget.reserve('tomorrow', 1, new_task=True, now=self.now + timedelta(days=1))

    def test_paid_cross_day_request_preserves_headroom_until_and_after_settlement(self):
        budget = WorkersAIBudget(self.database, 'f' * 32, carry_across_days=True)
        today = self.now + timedelta(days=1)
        budget.reserve('overnight', NORMAL_LIMIT, new_task=True, now=self.now)
        with self.assertRaises(BudgetRefused):
            budget.reserve('before-receipt', 1, new_task=True, now=today)
        budget.settle('overnight', NORMAL_LIMIT - 1, now=today)
        with self.assertRaises(BudgetRefused):
            budget.reserve('after-receipt', 2, new_task=True, now=today)
        budget.reserve('remaining', 1, new_task=True, now=today)


class ProjectAccountTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'budget.sqlite'
        self.accounts = dict(zip(('UIF', 'OffMenu', 'ANQR', 'CIX', 'CQ'),
                                 (digit * 32 for digit in 'abcde')))
        self.router = ProjectAccountRouter(self.database, self.accounts)
        self.now = datetime(2026, 11, 23, tzinfo=timezone.utc)

    def reserve(self, project, attempt, active, *, request=None, maximum=1, **review):
        return self.router.reserve_request(project, attempt, request or attempt, maximum, active,
                                           request_sha256='0' * 64, now=self.now, **review)

    def test_unverified_accounts_cannot_receive_new_reservations(self):
        self.router = ProjectAccountRouter(self.database, self.accounts, eligible_projects={'OffMenu'})
        self.reserve('OffMenu', 'first', {'OffMenu'}, maximum=NORMAL_LIMIT)
        with self.assertRaisesRegex(BudgetRefused, 'No eligible account'):
            self.reserve('OffMenu', 'next', {'OffMenu'})

    def test_all_active_projects_use_their_own_accounts(self):
        for project, account in self.accounts.items():
            budget = self.reserve(project, project, set(self.accounts))
            self.assertEqual(budget.account_id, account)

    def test_only_offmenu_active_can_use_all_five_accounts(self):
        accounts = [self.reserve('OffMenu', str(i), {'OffMenu'}).account_id
                    for i in range(5)]
        self.assertEqual(accounts[0], self.accounts['OffMenu'])
        self.assertEqual(set(accounts), set(self.accounts.values()))

    def test_paid_account_shares_the_same_caps_when_it_is_explicitly_eligible(self):
        accounts = dict(self.accounts, Paid='f' * 32)
        self.router = ProjectAccountRouter(self.database, accounts)
        self.assertNotIn('Paid', self.router.eligible_projects)
        self.router = ProjectAccountRouter(self.database, accounts, eligible_projects={'Paid'})
        self.assertEqual(self.reserve('OffMenu', 'paid', {'OffMenu'}, maximum=NORMAL_LIMIT).account_id,
                         accounts['Paid'])
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'extra', {'OffMenu'})
        self.reserve('OffMenu', 'paid', {'OffMenu'}, request='continue',
                     maximum=HARD_LIMIT - NORMAL_LIMIT)
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'paid', {'OffMenu'}, request='over-cap')

    def test_paid_account_joins_idle_capacity_and_keeps_project_preferences(self):
        accounts = dict(self.accounts, Paid='f' * 32)
        self.router = ProjectAccountRouter(self.database, accounts, eligible_projects=accounts)
        for project, account in self.accounts.items():
            self.assertEqual(self.reserve(project, project, set(self.accounts)).account_id, account)
        used = [self.reserve('OffMenu', f'pool-{i}', {'OffMenu'}).account_id for i in range(6)]
        self.assertEqual(set(used), set(accounts.values()))

    def test_paid_is_available_when_an_active_projects_own_account_cannot_admit(self):
        accounts = dict(self.accounts, Paid='f' * 32)
        self.router = ProjectAccountRouter(self.database, accounts, eligible_projects=accounts)
        active = set(self.accounts)
        self.assertEqual(self.reserve('OffMenu', 'primary', active, maximum=NORMAL_LIMIT).account_id,
                         accounts['OffMenu'])
        self.assertEqual(self.reserve('OffMenu', 'fallback', active).account_id, accounts['Paid'])

    def test_resume_restores_account_for_new_attempts_and_preserves_existing_assignment(self):
        self.reserve('OffMenu', 'first', {'OffMenu'})
        borrowed = self.reserve('OffMenu', 'second', {'OffMenu'})
        self.assertEqual(borrowed.account_id, self.accounts['ANQR'])
        self.router = ProjectAccountRouter(self.database, self.accounts)
        active = set(self.accounts)
        self.assertEqual(self.reserve('OffMenu', 'second', active, request='continue').account_id,
                         borrowed.account_id)
        self.assertEqual(self.reserve('ANQR', 'third', active).account_id,
                         self.accounts['ANQR'])
        with self.assertRaises(BudgetRefused):
            self.reserve('ANQR', 'second', active)

    def test_paused_project_cannot_claim_attempt(self):
        with self.assertRaises(BudgetRefused):
            self.reserve('CIX', 'first', {'OffMenu'})
        self.reserve('OffMenu', 'running', {'OffMenu'})
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'running', {'CIX'}, request='continue')

    def test_request_cannot_be_replayed_under_another_attempt(self):
        self.reserve('OffMenu', 'author', {'OffMenu'}, request='one')
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'different', {'OffMenu'}, request='one')

    def test_new_work_uses_headroom_but_existing_attempt_cannot_move(self):
        active = {'OffMenu'}
        for project in ('CIX', 'CQ', 'UIF'):
            self.reserve(project, project, set(self.accounts), maximum=NORMAL_LIMIT)
        self.reserve('OffMenu', 'author', active, maximum=NORMAL_LIMIT)
        borrowed = self.reserve('OffMenu', 'next', active)
        self.assertEqual(borrowed.account_id, self.accounts['ANQR'])
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'author', active, request='continue',
                         maximum=HARD_LIMIT - NORMAL_LIMIT + 1)

    def test_review_requires_explicit_distinct_account_and_retains_binding(self):
        active = set(self.accounts)
        self.reserve('OffMenu', 'author', active)
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'review', active, review_of='author',
                         reviewer_project='OffMenu')
        review = self.reserve('OffMenu', 'review', active, review_of='author',
                              reviewer_project='UIF')
        self.assertEqual(review.account_id, self.accounts['UIF'])
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'review', active, request='review2', review_of='author',
                         reviewer_project='CQ')
        with self.assertRaises(BudgetRefused):
            self.reserve('OffMenu', 'review', active, request='review2')
