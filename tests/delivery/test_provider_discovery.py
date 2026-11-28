import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from uif.provider_discovery import (
    Adapter, MetadataAdapter, NormalProviderDiscovery, ProviderReadCooldown, neutral_route_order,
)
from uif.provider_pool import PoolRequest, ProviderRoute, RouteAvailability
from uif.quota_handoff import LinearKey


class TestRuntimeDiscovery(unittest.IsolatedAsyncioTestCase):
    def test_unprioritized_routes_have_stable_operation_order_and_no_fixed_winner(self):
        routes = ("codex", "groq", "workers-ai")
        winners = set()
        for i in range(24):
            operation = f"original-{i}"
            ordered = neutral_route_order(routes, operation_id=operation)
            self.assertEqual(ordered, neutral_route_order(routes[::-1], operation_id=operation))
            self.assertCountEqual(ordered, routes)
            winners.add(ordered[0])
        self.assertEqual(winners, set(routes))
        self.assertEqual(neutral_route_order((), operation_id="idle"), ())
        for routes, operation in ((["codex"], "original"), (("repeat", "repeat"), "original"),
                                  (("bad/id",), "original"), (("codex",), None)):
            with self.assertRaises(ValueError):
                neutral_route_order(routes, operation_id=operation)

    async def test_idle_runtime_probes_all_routes_and_recovers_without_rebinding_original(self):
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        codex = ProviderRoute("codex", "codex", "codex", "exact-codex", "a"*64, "b"*64,
                             "c"*64, "d"*64, key.workspace_id, key.project_id, "owner/repo",
                             frozenset({"author"}))
        groq = replace(codex, route_id="groq", provider="groq", client="opencode",
                       model="exact-groq", account_sha256="e"*64)
        requests = {}
        attempts = {"codex": 0, "groq": 0, "diagnostic-only": 0}
        latest = {"codex": None, "groq": None}
        codex_unavailable, qualified, fallback_unproved = object(), object(), object()
        starts = []
        for i in range(24):
            operation = f"original-{i}"
            permitted = neutral_route_order(("codex", "groq"), operation_id=operation)
            requests.setdefault(permitted[0], PoolRequest(key, "owner/repo", "a"*64, "b"*64,
                    operation, "ordinary-software", "author", "new", permitted))
        self.assertEqual(set(requests), {"codex", "groq"})

        def adapter(route):
            async def read():
                attempts[route.route_id] += 1
                value = latest[route.route_id]
                if value is codex_unavailable:
                    raise ProviderReadCooldown(0)
                return value

            def project(value, request):
                return RouteAvailability(route, request, "available", 99, 109, "f"*64,
                                         True, value is qualified, True)

            return Adapter(route, read, project, lambda *_: frozenset({"structured-report"}))

        async def read_diagnostics():
            attempts["diagnostic-only"] += 1
            return "observed"

        diagnostics = MetadataAdapter("diagnostic-only", read_diagnostics,
            lambda evidence, **_: {"metadata_state": evidence})
        discovery = NormalProviderDiscovery((adapter(codex), adapter(groq)), clock=lambda: 100,
                                            interval=.01, timeout=.005, diagnostics=(diagnostics,))

        async def until(check):
            async with asyncio.timeout(1):
                while not check():
                    await asyncio.sleep(.001)

        def choose(request, **options):
            return discovery.select(request, **(dict(owner_paused=False, pending_original=False,
                required_capabilities=frozenset({"structured-report"})) | options))

        async def normal_start():
            starts.append("started")
            self.assertIsNone(choose(requests["codex"]).route)
            await until(lambda: min(attempts.values()) >= 2)
            self.assertIsNone(choose(requests["groq"]).route)

            latest.update(codex=codex_unavailable, groq=qualified)
            await until(lambda: discovery.observed("groq") is qualified and
                        dict(discovery.status())["codex"] == "reader-throttled")
            self.assertEqual(choose(requests["codex"]).route, groq)
            old = replace(requests["codex"], intent="retry", route_ids=("codex",), original_route=codex)
            self.assertIsNone(choose(old).route)
            self.assertIsNone(choose(requests["codex"], pending_original=True).route)

            latest["codex"] = qualified
            await until(lambda: discovery.observed("codex") is qualified)
            self.assertEqual(choose(requests["codex"]).route, codex)
            self.assertEqual(choose(requests["groq"]).route, groq)
            self.assertEqual(choose(old).route, codex)

            latest.update(codex=None, groq=fallback_unproved)
            await until(lambda: discovery.observed("codex") is None and
                        discovery.observed("groq") is fallback_unproved)
            held = choose(requests["codex"])
            self.assertIsNone(held.route)
            self.assertIn(("groq", "included-only-routing-unproved"), held.rejected)
            self.assertIsNone(choose(replace(requests["codex"], route_ids=("diagnostic-only",))).route)

        await discovery.run(normal_start)
        self.assertEqual(starts, ["started"])
        self.assertIsNone(choose(requests["codex"]).route)

    async def test_broken_optional_diagnostics_do_not_hold_normal_start_or_other_rows(self):
        broken = MetadataAdapter("broken", None, lambda *_args, **_kwargs: 1 / 0)
        healthy = MetadataAdapter("healthy", None,
            lambda _, **_kwargs: {"metadata_state": "unavailable", "dispatch_hold": "unmounted"})
        discovery = NormalProviderDiscovery((), clock=lambda: 100, interval=.01, timeout=.005,
                                            diagnostics=(broken, healthy))
        failed, other = discovery.roster()["metadata_candidates"]
        self.assertEqual(failed["dispatch_hold"], "quota-diagnostics-unavailable")
        self.assertEqual(failed["reader_status"], "reader-unmounted")
        self.assertEqual(other["route_id"], "healthy")
        self.assertEqual(other["dispatch_hold"], "unmounted")
        self.assertEqual(await discovery.run(AsyncMock(return_value="normal-started")), "normal-started")

    async def test_unavailable_metadata_backs_off_and_fresh_evidence_resets_delay(self):
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        route = ProviderRoute("codex", "codex", "codex", "exact-codex", "a"*64, "b"*64,
                              "c"*64, "d"*64, key.workspace_id, key.project_id, "owner/repo",
                              frozenset({"author"}))
        read = AsyncMock(side_effect=[RuntimeError()] * 4 + [None, "fresh", RuntimeError(), None])
        adapter = Adapter(route, read, lambda evidence, request: None)
        discovery = NormalProviderDiscovery((adapter,), clock=lambda: 100, interval=30, timeout=8)
        delays = []
        evidence = []

        async def sleep(delay):
            delays.append(delay)
            evidence.append(discovery._evidence.get(route.route_id))
            if len(delays) == 8:
                raise asyncio.CancelledError

        with patch('uif.provider_discovery.asyncio.sleep', side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await discovery._poll(adapter)

        self.assertEqual(delays, [30, 60, 120, 240, 300, 30, 30, 60])
        self.assertEqual(evidence, [None] * 5 + ["fresh", None, None])

    async def test_metadata_retry_after_can_extend_backoff_and_clears_old_evidence(self):
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        route = ProviderRoute("codex", "codex", "codex", "exact-codex", "a"*64, "b"*64,
                              "c"*64, "d"*64, key.workspace_id, key.project_id, "owner/repo",
                              frozenset({"author"}))
        read = AsyncMock(side_effect=["fresh", ProviderReadCooldown(600),
                                      ProviderReadCooldown(1), "recovered"])
        adapter = Adapter(route, read, lambda evidence, request: None)
        discovery = NormalProviderDiscovery((adapter,), clock=lambda: 100, interval=30, timeout=8)
        polls = []

        async def sleep(delay):
            polls.append((delay, discovery.status(), route.route_id in discovery._evidence))
            if len(polls) == 4:
                raise asyncio.CancelledError

        with patch('uif.provider_discovery.asyncio.sleep', side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await discovery._poll(adapter)

        self.assertEqual(polls, [
            (30, (("codex", "observed"),), True),
            (600, (("codex", "reader-throttled"),), False),
            (60, (("codex", "reader-throttled"),), False),
            (30, (("codex", "observed"),), True),
        ])

    async def test_authenticated_but_stale_account_receipts_do_not_reset_polling(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uif.stokowski_cloudflare.account_admission import AccountUsageEvidence, FreeRoute
        from uif.stokowski_cloudflare.client import GLM_MODEL
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        from uif.stokowski_cloudflare.shared_admission import AccountUsageReceipt

        now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        route = FreeRoute('a' * 32, GLM_MODEL, None, 'b' * 64)
        current = AccountUsageEvidence(route, '2026-11-25', now,
                                      now + timedelta(seconds=120), 0, 0, True, True)
        stale = replace(current, covered_through=now - timedelta(seconds=91))
        expired = replace(current, valid_until=now)
        partial = replace(current, completeness_verified=False)
        exhausted = replace(current, upper=7_499_999)
        issuer = object()
        receipts = [AccountUsageReceipt(issuer, digit * 64, projection)
                    for digit, projection in zip('cdef01', (current, stale, expired, partial, exhausted, current))]
        remote = SimpleNamespace(
            routes={'OffMenu': route}, clock=lambda: now,
            workspace_id='00000000-0000-0000-0000-000000000001',
            project_id='00000000-0000-0000-0000-000000000002',
            read_usage=Mock(side_effect=receipts), verify_usage=Mock(return_value=True),
            quota_issuer=issuer,
        )
        composition = NativeComposition(remote, "/not/read")
        discovery = composition._installed_provider_discovery()
        polls = []

        async def sleep(delay):
            polls.append((delay, discovery.status()))
            if len(polls) in (2, 3, 4, 5):
                self.assertIsNone(composition._discovered_preview('OffMenu', 10))
            if len(polls) == len(receipts):
                raise asyncio.CancelledError

        with patch('uif.provider_discovery.asyncio.sleep', side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await discovery._poll(discovery.adapters[0])

        self.assertEqual(polls, [
            (delay, (('workers-ai-offmenu', state),))
            for delay, state in (
                (20, 'observed'), (20, 'evidence-missing'), (40, 'evidence-missing'),
                (80, 'evidence-missing'), (20, 'observed'), (20, 'observed'),
            )
        ])
        self.assertEqual(remote.verify_usage.call_count, len(receipts))

    async def test_normal_runtime_refresh_resumes_independently(self):
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        a = ProviderRoute("codex", "codex", "codex", "exact-codex", "a"*64, "b"*64,
                          "c"*64, "d"*64, key.workspace_id, key.project_id, "owner/repo",
                          frozenset({"author"}))
        b = replace(a, route_id="groq", provider="groq", client="opencode",
                    model="qwen/qwen3.8-27b", account_sha256="e"*64)
        request = PoolRequest(key, "owner/repo", "a"*64, "b"*64, "original",
                              "ordinary-software", "author", "new", ("codex", "groq"))
        state = {"codex": "throttled", "groq": "unknown"}
        pulses = asyncio.Queue()
        projected = []

        def adapter(route):
            async def read():
                pulses.put_nowait(route.route_id)
                if state[route.route_id] == "unavailable":
                    raise RuntimeError("unexpected fixture error")
                return state[route.route_id]

            def project(value, asked):
                projected.append(route.route_id)
                return RouteAvailability(route, asked, value, 99, 109, "f"*64,
                                         True, True, value == "available")
            return Adapter(route, read, project)

        d = NormalProviderDiscovery((adapter(a), adapter(b)),
                                    clock=lambda: 100, interval=.01, timeout=.005)

        def select(**extra):
            return d.select(request, **(dict(owner_paused=False, pending_original=False) | extra))

        async def normal_start():
            self.assertIsNone(select().route)
            while not pulses.empty():
                pulses.get_nowait()
            state.update(codex="unavailable", groq="available")
            await asyncio.wait_for(pulses.get(), .5)
            for _ in range(100):
                if select().route == b:
                    break
                await asyncio.sleep(.001)
            self.assertEqual(select().route, b)
            needs_tools = select(required_capabilities=frozenset({"tool-calls"}))
            self.assertIsNone(needs_tools.route)
            self.assertIn(("groq", "capability-unproved"), needs_tools.rejected)
            projected.clear()
            self.assertIsNone(select(pending_original=True).route)
            self.assertIsNone(select(owner_paused=True).route)
            self.assertEqual(projected, [])
            for forbidden in (replace(request, repository="different/repo"),
                              replace(request, route_ids=("unregistered",)),
                              replace(request, role="not-authorized")):
                self.assertIsNone(d.select(forbidden, owner_paused=False, pending_original=False).route)
            self.assertEqual(projected, [])
            d.clock = lambda: 110
            self.assertIsNone(select().route)
            return "stopped"

        self.assertEqual(await d.run(normal_start), "stopped")
        d.clock = lambda: 100
        self.assertIsNone(select().route)

    async def test_task_capabilities_follow_latest_route_evidence(self):
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        primary = ProviderRoute("primary", "codex", "codex", "exact-codex", "a"*64, "b"*64,
                                "c"*64, "d"*64, key.workspace_id, key.project_id, "owner/repo",
                                frozenset({"author"}))
        secondary = replace(primary, route_id="secondary", provider="groq", client="opencode",
                            model="exact-groq", account_sha256="e"*64)
        request = PoolRequest(key, "owner/repo", "a"*64, "b"*64, "original",
                              "ordinary-software", "author", "new", ("primary", "secondary"))
        tools = frozenset({"tool-calls"})
        rich = (frozenset({"structured-reports", "tool-calls"}), 109)
        report_only = (frozenset({"structured-reports"}), 109)
        unavailable = (None, 109)
        latest = {"primary": rich, "secondary": rich}
        now = 100

        def adapter(route):
            async def read():
                return latest[route.route_id]

            def project(observation, asked):
                _, until = observation
                return RouteAvailability(route, asked, "available", 99, until, "f"*64,
                                         True, True, True)

            return Adapter(route, read, project, lambda observation, _: observation[0])

        discovery = NormalProviderDiscovery(
            (adapter(primary), adapter(secondary)),
            clock=lambda: now, interval=.01, timeout=.005,
        )

        async def refreshed():
            for _ in range(100):
                if all(discovery.observed(name) is snapshot for name, snapshot in latest.items()):
                    return
                await asyncio.sleep(.001)
            self.fail("discovery did not observe the expected capability update")

        def select(required=tools):
            return discovery.select(request, owner_paused=False, pending_original=False,
                                    required_capabilities=required)

        async def normal_start():
            nonlocal now
            await refreshed()
            self.assertEqual(select().route, primary)
            latest["primary"] = report_only
            await refreshed()
            decision = select()
            self.assertEqual(decision.route, secondary)
            self.assertIn(("primary", "capability-unproved"), decision.rejected)
            self.assertEqual(select(frozenset({"structured-reports"})).route, primary)

            latest["secondary"] = unavailable
            await refreshed()
            self.assertIsNone(select().route)
            self.assertIn(("secondary", "capability-unproved"), select().rejected)
            self.assertEqual(select("tool-calls").hold, "required-capabilities-invalid")

            latest["primary"] = rich
            await refreshed()
            self.assertEqual(select().route, primary)
            now = 109
            self.assertIsNone(select().route)
            self.assertIn(("primary", "evidence-stale"), select().rejected)

        await discovery.run(normal_start)
        self.assertIsNone(select().route)

    async def test_cancel_resistant_reader_does_not_hold_other_route_or_shutdown(self):
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        old = ProviderRoute("old", "codex", "codex", "exact-codex", "a"*64, "b"*64,
                            "c"*64, "d"*64, key.workspace_id, key.project_id, "owner/repo",
                            frozenset({"author"}))
        new = replace(old, route_id="new", provider="groq", client="opencode", model="exact-groq")
        request = PoolRequest(key, "owner/repo", "a"*64, "b"*64, "original",
                              "ordinary-software", "author", "new", ("old", "new"))
        release, blocked = asyncio.Event(), asyncio.Event()
        starts = []
        async def stuck():
            starts.append(1)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                blocked.set()
                await release.wait()
            return "late"
        async def healthy():
            return "qualified"
        def row(route):
            return lambda _, req: RouteAvailability(route, req, "available", 99, 109, "f"*64,
                                                     True, True, True)
        bindings = (Adapter(old, stuck, row(old)), Adapter(new, healthy, row(new)))
        with self.assertRaises(ValueError):
            NormalProviderDiscovery(bindings, clock=lambda: 100, interval=float("inf"), timeout=1)
        d = NormalProviderDiscovery(bindings, clock=lambda: 100, interval=.01, timeout=.002)
        async def normal():
            await asyncio.wait_for(blocked.wait(), .5)
            with self.assertRaises(RuntimeError):
                await d.run(normal)
            await asyncio.sleep(.025)
            self.assertIn(("old", "reader-quarantined"), d.status())
            self.assertEqual(d.select(request, owner_paused=False, pending_original=False).route, new)
            self.assertEqual(starts, [1])
        try:
            await asyncio.wait_for(d.run(normal), .5)
            self.assertEqual(d.status(), (("old","not-observed"), ("new","not-observed")))
        finally:
            release.set()
        await asyncio.sleep(0)
        self.assertIsNone(d.select(request, owner_paused=False, pending_original=False).route)

    async def test_configure_owns_hook_for_only_normal_start(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock, patch
        from uif.stokowski_cloudflare.launcher import configure

        class Owner:
            _dispatch, _run_worker, _release_slot, _is_eligible = Mock(), AsyncMock(), Mock(), Mock()

        discovery = NormalProviderDiscovery((), clock=lambda: 100, interval=.01, timeout=.002)
        async def normal_start(owner):
            self.assertTrue(discovery._running)
            self.assertTrue(callable(owner.uif_select_provider))
            return "fixture-stopped"
        multi = type("Multi", (), {"start": normal_start})
        runner = SimpleNamespace(run_turn=Mock())
        orch = SimpleNamespace(run_turn=runner.run_turn, Orchestrator=Owner, MultiOrchestrator=multi, logger=Mock())
        config = dict(accounts=dict(zip(("UIF", "OffMenu", "ANQR", "CIX", "CQ"), (n*32 for n in "abcde"))), projects=["OffMenu"], credentials="/etc/uif-stokowski",
                      admissions={})
        with (TemporaryDirectory() as scratch,
              patch('uif.stokowski_cloudflare.launcher._protected_json', return_value=config),
              patch('uif.stokowski_cloudflare.launcher.WorkersAIClient')):
            configure(runner, orch, SimpleNamespace(), policy=Path('/etc/owner.json'),
                      database=Path(scratch)/'budget.sqlite', provider_discovery=discovery)
            self.assertFalse(hasattr(multi, "uif_select_provider"))
            self.assertEqual(await multi().start(), "fixture-stopped")
            self.assertFalse(hasattr(multi, "uif_select_provider"))
            self.assertFalse(discovery._running)

    async def test_production_composition_uses_only_authenticated_existing_reader(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uif.stokowski_cloudflare.account_admission import AccountUsageEvidence, FreeRoute
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        from uif.stokowski_cloudflare.shared_admission import AccountUsageReceipt
        from uif.stokowski_cloudflare.client import FreeAdmissionWindow, GLM_MODEL

        now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        exact = FreeRoute('a'*32, GLM_MODEL, None, 'b'*64)
        evidence = AccountUsageEvidence(exact, '2026-11-25', now, now+timedelta(seconds=120),
                                        0, 0, True, True)
        proof = AccountUsageReceipt(object(), 'd'*64, evidence)
        direct = Mock(return_value=FreeAdmissionWindow(
            now+timedelta(seconds=90), {"OffMenu": evidence}))
        port = SimpleNamespace(read_account_usage=direct, launcher_options=lambda: {})
        remote = SimpleNamespace(routes={'OffMenu': exact}, clock=lambda: now,
            workspace_id='00000000-0000-0000-0000-000000000001',
            project_id='00000000-0000-0000-0000-000000000002',
            read_usage=Mock(return_value=proof), verify_usage=Mock(return_value=True),
            quota_issuer=proof.issuer, account_ports={'OffMenu': port}, ports=port)
        c = NativeComposition(remote, "/not/read")
        opts = c.options()
        self.assertIs(opts['authority_lifecycle'], c)
        discovery = opts["provider_discovery"]
        async def normal():
            from uif.stokowski_cloudflare.budget import BudgetRefused
            with self.assertRaises(BudgetRefused):
                opts['read_account_usage'](10)
            direct.assert_not_called()
            for _ in range(100):
                if discovery.observed("workers-ai-offmenu") is not None:
                    break
                await asyncio.sleep(.001)
            self.assertEqual(opts['read_account_usage'](10).usage_snapshots, {"OffMenu": evidence})
            direct.assert_not_called()
            c._running.set((SimpleNamespace(scope=SimpleNamespace(key=SimpleNamespace(issue_id="issue"))), None))
            c.current = lambda _: SimpleNamespace(attempt_id="attempt", lease=SimpleNamespace(
                expires_at=int(now.timestamp())+180))
            c.attempt_account = lambda *_: "OffMenu"
            opts['read_account_usage'](10)
            direct.assert_called_once()
        await discovery.run(normal)
        remote.verify_usage.assert_called_once_with(proof.issuer, proof)

    async def test_workers_roster_reports_fresh_coverage_and_expires_without_another_read(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uif.stokowski_cloudflare.account_admission import (
            LIMIT_MILLINEURONS, AccountUsageEvidence, FreeRoute,
        )
        from uif.stokowski_cloudflare.client import GLM_MODEL
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        from uif.stokowski_cloudflare.shared_admission import AccountReservationSnapshot, AccountUsageReceipt

        covered = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        now = covered + timedelta(seconds=10)
        route = FreeRoute('a' * 32, GLM_MODEL, 'fixture-gateway', 'b' * 64)
        proof = AccountUsageEvidence(route, '2026-11-25', covered, covered + timedelta(minutes=2),
                                     20, 30, True, True)
        snapshot = AccountReservationSnapshot(receipt_sha256='c' * 64,
            observed_at=int(now.timestamp()), debt_millineurons=100, ceiling_breached=False)
        receipt = AccountUsageReceipt(object(), 'c' * 64, proof, reservation_snapshot=snapshot)
        remote = SimpleNamespace(
            routes={'OffMenu': route}, clock=lambda: now,
            workspace_id='00000000-0000-0000-0000-000000000001',
            project_id='00000000-0000-0000-0000-000000000002',
            read_usage=Mock(return_value=receipt), verify_usage=Mock(return_value=True),
            quota_issuer=receipt.issuer,
        )
        discovery = NativeComposition(remote, '/unread-roster-fixture')._installed_provider_discovery()

        async def normal_start():
            nonlocal now
            async with asyncio.timeout(2):
                while discovery.observed('workers-ai-offmenu') is None:
                    await asyncio.sleep(.002)
            roster = discovery.roster()
            self.assertEqual(roster['route_readers'], (('workers-ai-offmenu', 'observed'),))
            row, = roster['routes']
            self.assertEqual(row['model'], GLM_MODEL)
            self.assertEqual(row['account_sha256'], discovery.adapters[0].route.account_sha256)
            self.assertNotIn(route.account_id, repr(row))
            self.assertNotIn(route.credential_epoch_sha256, repr(row))
            self.assertEqual(row['dispatch_hold'], 'task-admission-unchecked')
            self.assertEqual(row['headroom']['amount'], LIMIT_MILLINEURONS - 1 - 150)
            self.assertEqual(row['reservations']['amount'], 100)
            observation = row['account_observation']
            self.assertEqual(observation['metadata_state'], 'current')
            self.assertEqual(observation['coverage_age_seconds'], 10)
            self.assertEqual(observation['expires_at_epoch_seconds'], covered.timestamp() + 90)
            self.assertEqual(observation['resets_at_epoch_seconds'], covered.timestamp() + 12 * 3600)
            self.assertEqual(observation['account_usage_upper_millineurons'], 20)
            self.assertEqual(observation['outside_gap_upper_millineurons'], 30)

            now = covered + timedelta(seconds=91)
            stale, = discovery.roster()['routes']
            self.assertEqual(stale['dispatch_hold'], 'quota-evidence-stale')
            self.assertIsNone(stale['headroom'])
            self.assertIsNone(stale['reservations'])
            self.assertEqual(stale['account_observation']['coverage_age_seconds'], 91)
            self.assertNotIn('account_usage_upper_millineurons', stale['account_observation'])
            remote.read_usage.assert_called_once_with(route)
            remote.verify_usage.assert_called_once_with(receipt.issuer, receipt)

        await discovery.run(normal_start)

    def test_workers_roster_counts_only_exact_current_authority_reservations(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uif.stokowski_cloudflare.account_admission import (
            LIMIT_MILLINEURONS, AccountUsageEvidence, FreeRoute,
        )
        from uif.stokowski_cloudflare.client import GLM_MODEL, MODEL_RESERVATIONS
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        from uif.stokowski_cloudflare.shared_admission import (
            AccountReservationSnapshot, AccountUsageReceipt,
        )

        covered = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        now = int(covered.timestamp()) + 15
        route = FreeRoute('a' * 32, GLM_MODEL, None, 'b' * 64)
        proof = AccountUsageEvidence(
            route, '2026-11-25', covered, covered + timedelta(minutes=2), 20, 30, True, True,
        )
        receipt = AccountUsageReceipt(object(), 'c' * 64, proof)
        valid = AccountReservationSnapshot(
            receipt_sha256=receipt.receipt_sha256, observed_at=now - 5,
            debt_millineurons=100, ceiling_breached=False,
        )
        remote = SimpleNamespace(
            routes={'OffMenu': route}, clock=lambda: datetime.fromtimestamp(now, timezone.utc),
            workspace_id='00000000-0000-0000-0000-000000000001',
            project_id='00000000-0000-0000-0000-000000000002',
            read_usage=Mock(),
        )
        discovery = NativeComposition(remote, '/unread-roster-fixture')._installed_provider_discovery()
        adapter, = discovery.adapters

        def describe(reported):
            return adapter.describe(replace(receipt, reservation_snapshot=reported), now=now)

        _, hold, headroom, debt = describe(valid)
        self.assertEqual(hold, 'task-admission-unchecked')
        self.assertEqual(debt, {'unit': 'millineurons', 'amount': 100,
                                'observed_at_epoch_seconds': now - 5})
        self.assertEqual(headroom['amount'], LIMIT_MILLINEURONS - 1 - 150)
        for invalid in (None, replace(valid, receipt_sha256='d' * 64),
                        replace(valid, observed_at=int(covered.timestamp()) - 1),
                        replace(valid, observed_at=now + 1),
                        replace(valid, debt_millineurons=True),
                        replace(valid, ceiling_breached=1)):
            _, hold, headroom, debt = describe(invalid)
            self.assertEqual(hold, 'reservation-headroom-unverified')
            self.assertIsNone(headroom)
            self.assertIsNone(debt)
        maximum = MODEL_RESERVATIONS[GLM_MODEL]
        _, hold, headroom, _ = describe(replace(valid, debt_millineurons=LIMIT_MILLINEURONS - 50 - maximum))
        self.assertEqual(hold, 'quota-ceiling')
        self.assertEqual(headroom['amount'], maximum - 1)
        _, hold, headroom, _ = describe(replace(valid, ceiling_breached=True))
        self.assertEqual(hold, 'budget-ceiling-breached')
        self.assertEqual(headroom['amount'], 0)
        remote.read_usage.assert_not_called()

    async def test_composed_worker_thread_remains_accountable_until_transport_finishes(self):
        from datetime import datetime, timedelta, timezone
        from threading import Event
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uif.stokowski_cloudflare.account_admission import AccountUsageEvidence, FreeRoute
        from uif.stokowski_cloudflare.native_composition import NativeComposition
        from uif.stokowski_cloudflare.shared_admission import AccountUsageReceipt
        from uif.stokowski_cloudflare.client import GLM_MODEL
        from uif.stokowski_cloudflare.budget import BudgetRefused

        now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        exact = FreeRoute('a'*32, GLM_MODEL, None, 'b'*64)
        valid = AccountUsageEvidence(exact, '2026-11-25', now, now+timedelta(seconds=120),
                                     0, 0, True, True)
        late = replace(valid, covered_through=now-timedelta(days=1))
        issuer = object()
        release = Event()
        count = []
        def read(_):
            count.append(1)
            if len(count) == 1:
                if not release.wait(1):
                    raise TimeoutError()
                return AccountUsageReceipt(issuer, 'd'*64, late)
            return AccountUsageReceipt(issuer, 'e'*64, valid)
        direct = Mock(side_effect=AssertionError("no duplicate advisory read"))
        port = SimpleNamespace(read_account_usage=direct, launcher_options=lambda: {})
        remote = SimpleNamespace(routes={'OffMenu': exact}, clock=lambda: now,
            workspace_id='00000000-0000-0000-0000-000000000001',
            project_id='00000000-0000-0000-0000-000000000002',
            read_usage=read, verify_usage=lambda *_: True,
            quota_issuer=issuer, account_ports={'OffMenu': port}, ports=port)
        c = NativeComposition(remote, "/not/read")
        discovery = c.options()["provider_discovery"]
        discovery.interval, discovery.timeout = .012, .003
        async def normal():
            async def until(predicate):
                for _ in range(200):
                    if predicate():
                        return
                    await asyncio.sleep(.001)
                self.fail("fixture transition was not observed")
            await until(lambda: ("workers-ai-offmenu", "reader-quarantined") in discovery.status())
            with self.assertRaises(BudgetRefused):
                c.usage(10)
            self.assertEqual(count, [1])
            release.set()
            await until(lambda: discovery.observed("workers-ai-offmenu") is not None)
            self.assertEqual(discovery.observed("workers-ai-offmenu").receipt_sha256, 'e'*64)
            self.assertEqual(c.usage(10).usage_snapshots, {"OffMenu": valid})
            direct.assert_not_called()
        try:
            await discovery.run(normal)
        finally:
            release.set()
