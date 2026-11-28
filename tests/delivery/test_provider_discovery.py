import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from uif.provider_discovery import Adapter, NormalProviderDiscovery
from uif.provider_pool import PoolRequest, ProviderRoute, RouteAvailability
from uif.quota_handoff import LinearKey


class TestRuntimeDiscovery(unittest.IsolatedAsyncioTestCase):
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
