"""Exercise the Codex roster with detached metadata and no sender."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uif.codex_metadata_bridge import BoundMetadataReplies, CodexMetadataBinding, MAX_REPLY_BYTES
from uif.provider_pool import PoolRequest
from uif.quota_handoff import LinearKey
from uif.stokowski_cloudflare.launcher import configure
from uif.stokowski_cloudflare.native_composition import NativeComposition


class CodexRosterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000
        self.remote = SimpleNamespace(routes={}, clock=lambda: datetime.fromtimestamp(self.now, timezone.utc),
                                      ports=SimpleNamespace(launcher_options=lambda: {}))
        key = LinearKey(*(f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)))
        self.request = PoolRequest(key, "maldous/offmenu", "a" * 64, "b" * 64, "original",
                                   "ordinary-software", "author", "new", ("codex-app-server",))

    def discovery(self, binding=None):
        composition = NativeComposition(self.remote, "/unread-normal-fixture", codex_metadata=binding)
        discovery = composition.options()["provider_discovery"]
        discovery.interval, discovery.timeout = .02, .015
        return discovery

    def assert_held(self, discovery):
        selected = discovery.select(self.request, owner_paused=False, pending_original=False)
        self.assertIsNone(selected.route)
        self.assertEqual(selected.rejected, (("codex-app-server", "route-unregistered"),))

    async def test_normal_launcher_reports_the_unmounted_candidate_and_cleans_up_its_hooks(self):
        discovery = self.discovery()

        class Owner:
            _dispatch, _run_worker, _release_slot, _is_eligible = Mock(), AsyncMock(), Mock(), Mock()

        async def normal_start(owner):
            self.assertTrue(discovery.active)
            candidate, = owner.uif_provider_roster()["metadata_candidates"]
            self.assertEqual(candidate["provider"], "codex")
            self.assertEqual(candidate["reader_status"], "reader-unmounted")
            self.assertIsNone(candidate["account_observation"])
            self.assertEqual(candidate["dispatch_hold"], "same-session-entitlement-and-fenced-sender-required")
            self.assert_held(discovery)

        multi = type("Multi", (), {"start": normal_start})
        runner = SimpleNamespace(run_turn=Mock(side_effect=AssertionError("unexpected native send")))
        orchestration = SimpleNamespace(run_turn=runner.run_turn, Orchestrator=Owner, MultiOrchestrator=multi,
                                        logger=Mock())
        accounts = {name: char * 32 for name, char in zip(("UIF", "OffMenu", "ANQR", "CIX", "CQ"), "abcde")}
        config = {"accounts": accounts, "projects": ["OffMenu"],
                  "credentials": "/unused", "admissions": {}}
        with (TemporaryDirectory() as scratch,
              patch("uif.stokowski_cloudflare.launcher._protected_json", return_value=config),
              patch("uif.stokowski_cloudflare.launcher.WorkersAIClient")):
            configure(runner, orchestration, SimpleNamespace(), policy=Path("/unused-policy"),
                      database=Path(scratch) / "budget.sqlite", provider_discovery=discovery)
            self.assertFalse(hasattr(multi, "uif_provider_roster"))
            await multi().start()
            self.assertFalse(hasattr(multi, "uif_provider_roster"))
            self.assertFalse(hasattr(multi, "uif_select_provider"))

    async def test_current_credits_can_appear_disappear_or_go_stale_without_a_dispatch_route(self):
        port = Mock()

        def replies(credits, *, account="fixture-account"):
            window = {"usedPercent": 8, "windowDurationMins": 300, "resetsAt": 2000}
            return BoundMetadataReplies("fixture-account", "fixture-session", self.now,
                b'{"account":{"type":"chatgpt"},"requiresOpenaiAuth":true}',
                json.dumps({"accountId": account, "rateLimitsByLimitId": {"codex": {
                    "limitId": "codex", "primary": window, "secondary": window, "credits": credits,
                }}, "rateLimitResetCredits": {"availableCount": 80}}).encode())

        discovery = self.discovery(CodexMetadataBinding(port, "fixture-account", "fixture-session", "codex"))
        credit_row = {"hasCredits": True, "unlimited": False, "balance": "24.50"}
        port.read_bound.return_value = replies(credit_row)

        def candidate():
            return discovery.roster()["metadata_candidates"][0]

        async def wait_for(predicate):
            async with asyncio.timeout(2):
                while not predicate():
                    await asyncio.sleep(.002)

        async def normal_start():
            await wait_for(lambda: candidate()["metadata_state"] == "current")
            self.assertEqual(candidate()["account_observation"]["credits"], {
                "has_credits": True, "unlimited": False, "reported_balance": "24.50",
            })
            self.assertNotIn("fixture-account", repr(candidate()))
            self.assert_held(discovery)
            port.read_bound.return_value = replies({**credit_row, "balance": "non-numeric"})
            await wait_for(lambda: candidate()["account_observation"]["credits"] is None)
            port.read_bound.return_value = replies(None, account="changed-account")
            await wait_for(lambda: candidate()["reader_status"] == "evidence-missing")
            self.assertIsNone(candidate()["account_observation"])
            port.read_bound.return_value = replies({"hasCredits": False, "unlimited": False, "balance": None})
            await wait_for(lambda: candidate()["metadata_state"] == "current")
            self.assertFalse(candidate()["account_observation"]["credits"]["has_credits"])
            self.now += 91
            self.assertEqual(candidate()["metadata_state"], "stale")
            self.assertIsNone(candidate()["account_observation"])
            self.assert_held(discovery)

        await discovery.run(normal_start)
        self.assertEqual(candidate()["metadata_state"], "unavailable")
        port.read_bound.assert_called_with(methods=("account/read", "account/rateLimits/read"),
                                           refresh_token=False, maximum_reply_bytes=MAX_REPLY_BYTES,
                                           timeout_seconds=2.0)
