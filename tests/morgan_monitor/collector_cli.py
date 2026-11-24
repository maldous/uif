"""Offline synthetic CLI/config/loop tests; never real source or receiver I/O."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.morgan_monitor.collector import Collector, ConfigurationError, load_config, validate_config
from tools.morgan_monitor.__main__ import main
from tools.morgan_monitor.store import read_snapshot

NOW = 1794240000
ROOT = Path(__file__).resolve().parents[2]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = json.loads((ROOT / "tools/morgan_monitor/config.example.json").read_text())
        self.path = self.root / "config.json"
        self.path.write_text(json.dumps(self.config))

    def tearDown(self):
        self.temp.cleanup()

    def test_sample_config_no_source_no_auth_no_network(self):
        value = load_config(self.path)
        self.assertFalse(value["receiver"]["enabled"])
        self.assertEqual(value["receiver"]["tokenEnvs"], {})
        self.assertIsNone(value["sources"]["codexRegistry"]["path"])
        self.assertFalse((self.root / "private-monitor-state").exists())

    def test_state_source_collision_rejected(self):
        self.config["sources"]["codexRegistry"]["path"] = self.config["statePath"]
        with self.assertRaises(ConfigurationError): validate_config(self.config, base=self.root)

    def test_duplicate_json_keys_rejected(self):
        self.path.write_text('{"schemaVersion":1,"schemaVersion":2}')
        with self.assertRaises(ConfigurationError): load_config(self.path)

    def test_unknown_and_secret_config_fields_rejected(self):
        for key in ("token", "password", "privateTitle"):
            config = copy.deepcopy(self.config)
            config[key] = "SYNTHETIC-SECRET"
            with self.assertRaises(ConfigurationError): validate_config(config, base=self.root)

    def test_cli_check_only_does_not_create_state(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check-config", "--config", str(self.path)]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"valid": True, "transportConfigured": False, "cloudFeedConfigured": False})
        self.assertFalse((self.root / "private-monitor-state").exists())

    def test_collector_unconfigured_sources_are_explicit(self):
        collector = Collector(load_config(self.path))
        try:
            result = collector.collect_once(now=NOW)
            self.assertEqual(len(result["snapshot"]["sources"]), 7)
            self.assertTrue(all(source["health"] == "unconfigured" for source in result["snapshot"]["sources"]))
            self.assertEqual(result["delivery"], {"sent": 0, "failed": 0})
            self.assertEqual(result["snapshot"]["outbox"]["pending"], 7)
            self.assertFalse(result["snapshot"]["executionCoverageComplete"])
            self.assertNotIn(str(self.root), json.dumps(result))
        finally:
            collector.close()

    def test_cli_snapshot_and_status_read_only(self):
        collector = Collector(load_config(self.path))
        collector.collect_once(now=NOW)
        collector.close()
        state = self.root / "private-monitor-state/monitor.sqlite3"
        before = state.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["outbox-status", "--state", str(state)]), 0)
        self.assertEqual(json.loads(output.getvalue())["pending"], 7)
        self.assertEqual(before, state.read_bytes())

    def test_cli_errors_never_include_path_or_raw_details(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(main(["snapshot", "--state", str(self.root / "SYNTHETIC-SECRET")]), 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "state_unavailable"})
        self.assertNotIn("SYNTHETIC-SECRET", output.getvalue())

    def test_loop_can_stop_without_external_mutation(self):
        collector = Collector(load_config(self.path))
        stopped, results = [False], []
        def sleep(seconds): stopped[0] = True
        try:
            collector.run(stop_requested=lambda: stopped[0], sleep=sleep, on_result=results.append)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["snapshot"]["outbox"]["pending"], 7)
        finally:
            collector.close()

    def test_missing_auth_is_never_dumped_as_config(self):
        self.config["receiver"] = {"enabled": True, "url": "https://receiver.example/v1/ingest", "allowedUrls": ["https://receiver.example/v1/ingest"], "tokenEnvs": {}, "timeoutSeconds": 1}
        self.path.write_text(json.dumps(self.config))
        collector = Collector(load_config(self.path))
        try:
            result = collector.collect_once(now=NOW)
            self.assertEqual(result["delivery"]["failed"], 7)
            self.assertEqual(result["snapshot"]["outbox"]["pending"], 7)
        finally:
            collector.close()

    def _fill_synthetic_outbox(self, collector):
        from tools.morgan_monitor.sources import Observation
        from tools.morgan_monitor.events import make_event
        collector.adapters = [adapter for adapter in collector.adapters if adapter.source != "foo_allowance"]
        collector.store.max_pending = 10
        for offset in range(10):
            observation = Observation("codex_turns", NOW + offset, "unconfigured", "historical_registry", error_code="not_configured")
            collector.store.save(observation, lambda sequence, observation=observation: make_event(observation, sequence, []))
        return [row[0] for row in collector.store.db.execute("SELECT event_json FROM outbox ORDER BY id")]

    def test_full_outbox_recovers_before_collecting(self):
        class SyntheticReceiver:
            config = {"enabled": True}
            def __init__(self): self.sent = []
            def send(self, event): self.sent.append(event)
        receiver = SyntheticReceiver()
        collector = Collector(load_config(self.path), receiver=receiver)
        try:
            retained = self._fill_synthetic_outbox(collector)
            self.assertEqual(collector.store.status()["pending"], 10)
            # Durable pacing admits one event/source each two integer seconds.
            # A recovered receiver drains even while a complete pass cannot fit.
            for offset in range(5):
                result = collector.collect_once(now=NOW + 100 + 2 * offset)
                self.assertTrue(result["collectionDeferred"])
                self.assertEqual(result["delivery"], {"sent": 1, "failed": 0})
            result = collector.collect_once(now=NOW + 110)
            self.assertFalse(result["collectionDeferred"])
            self.assertEqual(result["delivery"], {"sent": 6, "failed": 0})
            from tools.morgan_monitor.transport import flush
            for offset in range(1, 6):
                flush(collector.store, receiver, NOW + 110 + 2 * offset)
            self.assertEqual(collector.store.status()["pending"], 0)
            old_sent = [event for event in receiver.sent if event["sourceId"] == "codex_turns"][:10]
            self.assertEqual(old_sent, [json.loads(value) for value in retained])
            self.assertEqual(collector.store.db.execute("SELECT sequence FROM source_state WHERE source_id='codex_turns'").fetchone()[0], 11)
        finally:
            collector.close()

    def test_full_outbox_waits_for_retry_without_freshening(self):
        class SyntheticReceiver:
            config = {"enabled": True}
            def __init__(self): self.sent = []
            def send(self, event): self.sent.append(event)
        receiver = SyntheticReceiver()
        collector = Collector(load_config(self.path), receiver=receiver)
        try:
            retained = self._fill_synthetic_outbox(collector)
            head = collector.store.pending(NOW + 20)[0]
            collector.store.fail(head["id"], NOW + 20, "delivery_failed")
            before = [tuple(row) for row in collector.store.db.execute("SELECT source_id,sequence,observed_at FROM source_state")]
            result = collector.collect_once(now=NOW + 24)
            self.assertTrue(result["collectionDeferred"])
            self.assertEqual(result["delivery"], {"sent": 0, "failed": 0})
            self.assertEqual(collector.store.status()["pending"], 10)
            self.assertEqual(before, [tuple(row) for row in collector.store.db.execute("SELECT source_id,sequence,observed_at FROM source_state")])
            self.assertEqual(retained, [row[0] for row in collector.store.db.execute("SELECT event_json FROM outbox ORDER BY id")])
            recovered = collector.collect_once(now=NOW + 25)
            self.assertTrue(recovered["collectionDeferred"])
            self.assertEqual(recovered["delivery"], {"sent": 1, "failed": 0})
            self.assertEqual(recovered["snapshot"]["outbox"]["pending"], 9)
        finally:
            collector.close()

    def test_slow_recovery_defers_whole_pass_without_source_starvation(self):
        from tools.morgan_monitor.transport import TransportError
        class RateLimitedSyntheticReceiver:
            config = {"enabled": True}
            def __init__(self): self.accepted_this_poll = False
            def send(self, event):
                if self.accepted_this_poll:
                    raise TransportError("delivery_failed")
                self.accepted_this_poll = True
        receiver = RateLimitedSyntheticReceiver()
        collector = Collector(load_config(self.path), receiver=receiver)
        try:
            self._fill_synthetic_outbox(collector)
            for offset in range(5):
                receiver.accepted_this_poll = False
                result = collector.collect_once(now=NOW + 100 + 10 * offset)
                self.assertTrue(result["collectionDeferred"])
                self.assertEqual(collector.store.db.execute("SELECT COUNT(*) FROM source_state").fetchone()[0], 1)
                self.assertEqual(collector.store.db.execute("SELECT observed_at FROM source_state").fetchone()[0], NOW + 9)
            receiver.accepted_this_poll = False
            resumed = collector.collect_once(now=NOW + 150)
            self.assertFalse(resumed["collectionDeferred"])
            self.assertEqual(collector.store.db.execute("SELECT COUNT(*) FROM source_state").fetchone()[0], 6)
            self.assertEqual(collector.store.status()["pending"], 10)
        finally:
            collector.close()

    def test_independent_delivery_catches_up_with_one_second_receiver_limit(self):
        from unittest.mock import patch
        from tools.morgan_monitor.events import make_event
        from tools.morgan_monitor.sources import CodexTurns, Observation
        from tools.morgan_monitor.transport import TransportError
        clock = [float(NOW)]
        class RateLimitedSyntheticReceiver:
            config = {"enabled": True}
            def __init__(self): self.sent, self.last, self.rate_errors = [], {}, 0
            def send(self, event):
                if clock[0] - self.last.get(event["sourceId"], -float("inf")) < 1:
                    self.rate_errors += 1
                    raise TransportError("delivery_failed")
                self.last[event["sourceId"]] = clock[0]
                self.sent.append(event)
        receiver = RateLimitedSyntheticReceiver()
        collector = Collector(load_config(self.path), adapters=[CodexTurns(None)], receiver=receiver)
        initial_events, results = [], []
        try:
            for offset in range(3):
                obs = Observation("codex_turns", NOW - 90 + 30 * offset, "unconfigured", "historical_registry", error_code="not_configured")
                initial_events.append(collector.store.save(obs, lambda seq, obs=obs: make_event(obs, seq, [])))
            def sleep(seconds): clock[0] += seconds
            with patch("time.time", side_effect=lambda: clock[0]), patch("time.monotonic", side_effect=lambda: clock[0]):
                collector.run(stop_requested=lambda: clock[0] >= NOW + 90, sleep=sleep, on_result=results.append)
            self.assertEqual(len(results), 3)
            self.assertEqual(results[0]["snapshot"]["outbox"]["pending"], 3)
            self.assertEqual(results[1]["snapshot"]["outbox"]["pending"], 0)
            self.assertEqual(collector.store.status()["pending"], 0)
            self.assertEqual(receiver.rate_errors, 0)
            self.assertEqual(receiver.sent[:3], initial_events)
            self.assertEqual([event["sequence"] for event in receiver.sent], list(range(1, 7)))
        finally:
            collector.close()

    def test_slow_failed_delivery_overrun_does_not_reprobe_same_second(self):
        from unittest.mock import patch
        from tools.morgan_monitor.events import make_event
        from tools.morgan_monitor.sources import CodexThreads, CodexTurns, Observation
        from tools.morgan_monitor.transport import TransportError
        clock = [float(NOW)]
        class SlowFailingSyntheticReceiver:
            config = {"enabled": True}
            def send(self, event):
                clock[0] += 10
                raise TransportError("delivery_failed")
        collector = Collector(load_config(self.path), adapters=[CodexThreads(None), CodexTurns(None)], receiver=SlowFailingSyntheticReceiver())
        results = []
        try:
            for source in ("codex_registry", "codex_turns"):
                obs = Observation(source, NOW - 60, "unconfigured", "historical_registry", error_code="not_configured")
                collector.store.save(obs, lambda seq, obs=obs: make_event(obs, seq, []))
            def sleep(seconds): clock[0] += seconds
            with patch("time.time", side_effect=lambda: clock[0]), patch("time.monotonic", side_effect=lambda: clock[0]):
                collector.run(stop_requested=lambda: len(results) >= 2, sleep=sleep, on_result=results.append)
            self.assertEqual(len(results), 2)
            first = {source["sourceId"]: source["observedAt"] for source in results[0]["snapshot"]["sources"]}
            second = {source["sourceId"]: source["observedAt"] for source in results[1]["snapshot"]["sources"]}
            self.assertEqual(first, {"codex_registry": NOW + 60, "codex_turns": NOW + 60})
            for source in first:
                self.assertGreaterEqual(second[source] - first[source], self.config["intervalSeconds"])
            self.assertEqual(collector.store.status()["pending"], 6)
        finally:
            collector.close()

    def test_allowance_only_selection_never_reads_or_emits_other_sources(self):
        from unittest.mock import patch
        self.config["selectedSources"] = ["foo_allowance"]
        self.path.write_text(json.dumps(self.config))
        collector = Collector(load_config(self.path))
        try:
            self.assertEqual([adapter.source for adapter in collector.adapters], ["foo_allowance"])
            with patch("tools.morgan_monitor.sources.SQLiteSource.collect", side_effect=AssertionError("unselected source read")), patch("tools.morgan_monitor.sources.SystemdService.collect", side_effect=AssertionError("unselected service read")), patch("tools.morgan_monitor.sources.ProcessIdentity.collect", side_effect=AssertionError("unselected process read")):
                result = collector.collect_once(now=NOW)
            self.assertEqual(result["selectedSources"], ["foo_allowance"])
            self.assertEqual([source["sourceId"] for source in result["snapshot"]["sources"]], ["foo_allowance"])
            self.assertEqual(result["snapshot"]["outbox"]["pending"], 1)
        finally:
            collector.close()

    def test_allowance_only_delivery_does_not_accumulate_missing_token_streams(self):
        class SyntheticReceiver:
            config = {"enabled": True}
            def __init__(self): self.sources = []
            def send(self, event):
                if event["sourceId"] != "foo_allowance": raise AssertionError("unregistered source")
                self.sources.append(event["sourceId"])
        self.config["selectedSources"] = ["foo_allowance"]
        self.config["maxPending"] = 10
        self.path.write_text(json.dumps(self.config))
        receiver = SyntheticReceiver()
        collector = Collector(load_config(self.path), receiver=receiver)
        try:
            for offset in range(30):
                result = collector.collect_once(now=NOW + offset * 30)
                self.assertFalse(result["collectionDeferred"])
                self.assertEqual(result["snapshot"]["outbox"]["pending"], 0)
            self.assertEqual(receiver.sources, ["foo_allowance"] * 30)
        finally:
            collector.close()

    def test_selection_change_preserves_unacknowledged_old_sources(self):
        collector = Collector(load_config(self.path))
        collector.collect_once(now=NOW)
        retained = [tuple(row) for row in collector.store.db.execute("SELECT source_id,event_json,acknowledged_at,attempts FROM outbox WHERE source_id!='foo_allowance' ORDER BY id")]
        collector.close()
        class SyntheticReceiver:
            config = {"enabled": True}
            def send(self, event):
                if event["sourceId"] != "foo_allowance": raise AssertionError("unselected delivery")
        self.config["selectedSources"] = ["foo_allowance"]
        self.path.write_text(json.dumps(self.config))
        collector = Collector(load_config(self.path), receiver=SyntheticReceiver())
        try:
            collector.collect_once(now=NOW + 30)
            after = [tuple(row) for row in collector.store.db.execute("SELECT source_id,event_json,acknowledged_at,attempts FROM outbox WHERE source_id!='foo_allowance' ORDER BY id")]
            self.assertEqual(after, retained)
            self.assertEqual(len(after), 6)
        finally:
            collector.close()

    def test_source_selection_validation_and_legacy_default(self):
        from tools.morgan_monitor.events import SOURCE_CAPABILITIES
        legacy = copy.deepcopy(self.config)
        legacy.pop("selectedSources", None)
        self.assertEqual(validate_config(legacy, base=self.root)["selectedSources"], list(SOURCE_CAPABILITIES))
        for selection in ([], ["foo_allowance", "foo_allowance"], ["secret-source"], "foo_allowance", [None]):
            invalid = copy.deepcopy(self.config)
            invalid["selectedSources"] = selection
            with self.assertRaises(ConfigurationError): validate_config(invalid, base=self.root)
