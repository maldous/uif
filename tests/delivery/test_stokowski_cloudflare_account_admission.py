from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from uif.stokowski_cloudflare.account_admission import (
    AccountUsageEvidence, FreeRoute, allows_request,
)


class AccountAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        self.route = FreeRoute('a' * 32, '@cf/openai/gpt-oss-20b', 'stokowski', 'b' * 64)
        self.evidence = AccountUsageEvidence(
            self.route, self.now.date().isoformat(), self.now - timedelta(seconds=1),
            self.now + timedelta(seconds=30), 6_000_000, 500_000, True, True,
        )

    def check(self, evidence, **fields):
        args = dict(now=self.now, request_not_after=self.now + timedelta(seconds=29),
                    outstanding_local=500_000, request_maximum=499_999)
        args.update(fields)
        return allows_request(self.route, evidence, **args)

    def test_total_is_strict_and_counts_activity_outside_ledger(self):
        self.assertTrue(self.check(self.evidence))
        self.assertFalse(self.check(self.evidence, request_maximum=500_000))
        self.assertFalse(self.check(replace(self.evidence, upper=7_000_000), outstanding_local=0))

    def test_route_account_gateway_model_and_credential_epoch_are_bound(self):
        for key, value in dict(account_id='c' * 32, gateway_id='different', model='@cf/b/c',
                               credential_epoch_sha256='d' * 64).items():
            with self.subTest(key=key):
                self.assertFalse(self.check(replace(self.evidence, route=replace(self.route, **{key: value}))))

    def test_direct_route_is_explicit_and_cannot_use_gateway_evidence(self):
        direct = replace(self.route, gateway_id=None)
        args = dict(now=self.now, request_not_after=self.now + timedelta(seconds=29),
                    outstanding_local=0, request_maximum=1)
        direct_evidence = replace(self.evidence, route=direct)
        self.assertTrue(allows_request(direct, direct_evidence, **args))
        self.assertFalse(allows_request(direct, self.evidence, **args))
        self.assertFalse(allows_request(self.route, direct_evidence, **args))
        empty = replace(self.route, gateway_id='')
        self.assertFalse(allows_request(empty, replace(self.evidence, route=empty), **args))

    def test_default_or_unqualified_never_grants_headroom(self):
        self.assertFalse(self.check(None))
        self.assertFalse(self.check(replace(self.evidence, free_standard_no_fallback=False)))
        self.assertFalse(self.check(replace(self.evidence, completeness_verified=1)))
        self.assertFalse(self.check(replace(self.evidence, gap_upper=True)))
        self.assertFalse(self.check(self.evidence, outstanding_local=-1))
        self.assertFalse(self.check(self.evidence, request_maximum=0))

    def test_exact_clock_window_and_utc_rollover(self):
        self.assertFalse(self.check(replace(self.evidence,
            covered_through=self.now - timedelta(seconds=91))))
        self.assertFalse(self.check(replace(self.evidence, utc_day='2026-11-22')))
        self.assertFalse(self.check(replace(self.evidence, covered_through=self.now + timedelta(seconds=1))))
        self.assertFalse(self.check(self.evidence, request_not_after=self.now + timedelta(seconds=31)))
        later = self.now.replace(hour=23, minute=59, second=59)
        near_boundary = replace(self.evidence, covered_through=later,
                                valid_until=later + timedelta(seconds=2))
        self.assertFalse(self.check(near_boundary, now=later, request_not_after=later + timedelta(seconds=1)))


class StrictRouterTests(unittest.TestCase):
    def setUp(self):
        AccountAdmissionTests.setUp(self)
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from uif.stokowski_cloudflare.budget import ProjectAccountRouter

        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'ledger.sqlite'
        self.accounts = dict(zip(('OffMenu', 'UIF', 'ANQR', 'CIX', 'CQ'),
                                 (digit * 32 for digit in 'abcde')))
        self.router = ProjectAccountRouter(
            self.db, self.accounts, strict_routes={'OffMenu': self.route})
        self.snapshots = {'OffMenu': replace(self.evidence, upper=7_000_000, gap_upper=0)}
        self.kwargs = dict(now=self.now, request_not_after=self.now + timedelta(seconds=29))

    def reserve(self, marker, *, attempt=None, snapshots=None):
        return self.router.reserve_request(
            'OffMenu', attempt or marker, marker, 300_000, {'OffMenu'},
            usage_snapshots=snapshots, request_sha256='e' * 64, **self.kwargs)

    def test_strict_router_denies_missing_snapshot_and_never_falls_back(self):
        from uif.stokowski_cloudflare.budget import BudgetRefused

        self.assertFalse(self.router.can_start('OffMenu', {'OffMenu'}, 300_000, **self.kwargs))
        with self.assertRaises(BudgetRefused):
            self.reserve('missing')
        self.assertTrue(self.router.can_start(
            'OffMenu', {'OffMenu'}, 300_000, usage_snapshots=self.snapshots, **self.kwargs))
        self.assertEqual(self.reserve('one', snapshots=self.snapshots).account_id,
                         self.route.account_id)
        with self.assertRaises(BudgetRefused):
            self.reserve('continuation', attempt='one', snapshots=self.snapshots)

    def test_strict_discovery_rejects_invalid_maximum_even_without_evidence(self):
        from uif.stokowski_cloudflare.budget import BudgetRefused, ProjectAccountRouter

        other_route = replace(self.route, account_id=self.accounts['UIF'])
        router = ProjectAccountRouter(
            self.db, self.accounts, strict_routes={'OffMenu': self.route, 'UIF': other_route})
        account = self.reserve('one', snapshots=self.snapshots)
        # An actual settled author makes independent-review discovery reachable.
        account.settle('one', 1, now=self.now)
        self.assertIsNone(router.independent_reviewer('OffMenu', 'one', {'OffMenu'}, 1))
        for value in (0, -1, True, False, 1.0, 10_000_001):
            with self.subTest(value=value):
                self.assertFalse(router.can_start('OffMenu', {'OffMenu'}, value, **self.kwargs))
                self.assertIsNone(router.independent_reviewer('OffMenu', 'one', {'OffMenu'}, value))
                with self.assertRaises((BudgetRefused, ValueError)):
                    router.reserve_request(
                        'OffMenu', 'new', 'new', value, {'OffMenu'}, request_sha256='e' * 64,
                        **self.kwargs)

    def test_two_transports_cannot_reserve_same_snapshot_headroom(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from uif.stokowski_cloudflare.budget import BudgetRefused

        start = Barrier(2)

        def reserve(marker):
            start.wait(5)
            try:
                self.reserve(marker, snapshots=self.snapshots)
            except BudgetRefused:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(reserve, ('one', 'two')), [True, False])
