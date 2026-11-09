"""Offline synthetic event, persistence, retry and privacy tests."""
import copy
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error

from tools.morgan_monitor.events import EventError, make_event, validate_bindings, validate_event
from tools.morgan_monitor.sources import Observation
from tools.morgan_monitor.store import Store, StoreError, read_snapshot
from tools.morgan_monitor.transport import HTTPSReceiver, TransportError, flush
from tools.morgan_monitor.mcp import handle, serve

NOW = 1794240000
SENTINEL = "private-id-or-secret-do-not-publish"


def observation(now=NOW, health="ok"):
    return Observation("codex_turns", now, health, "historical_registry",
                       [{"key": SENTINEL, "record_at": NOW - 700000, "state": "inProgress", "liveness": "unknown"}] if health == "ok" else [],
                       1 if health == "ok" else 0, health == "ok", None if health == "ok" else "source_unavailable")


def binding():
    return [{"sourceId": "codex_turns", "recordKey": SENTINEL, "alias": "1", "topicKeys": ["monitoring"], "blockerKeys": []}]


def receiver_config(enabled=True):
    return {"enabled": enabled, "url": "https://receiver.example/v1/ingest" if enabled else None,
            "allowedUrls": ["https://receiver.example/v1/ingest"] if enabled else [],
            "tokenEnvs": {"codex_turns": "SYNTHETIC_TOKEN"}, "timeoutSeconds": 1}


class Response:
    status = 202
    def __init__(self, event, *, mismatch=False):
        self.body = json.dumps({"accepted": True, "duplicate": False, "eventId": "wrong" if mismatch else event["eventId"], "sequence": event["sequence"]}).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.body[:limit]


class Opener:
    def __init__(self, fail=0, mismatch=False):
        self.requests, self.fail, self.mismatch = [], fail, mismatch
    def open(self, request, timeout):
        self.requests.append(request)
        if self.fail:
            self.fail -= 1
            raise urllib.error.URLError(SENTINEL)
        return Response(json.loads(request.data), mismatch=self.mismatch)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "monitor.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save(self, obs=None):
        obs = obs or observation()
        return self.store.save(obs, lambda seq: make_event(obs, seq, binding()))

    def test_allowlist_strips_private_identity_and_state_never_live(self):
        event = self.save()
        self.assertNotIn(SENTINEL, json.dumps(event))
        self.assertEqual(event["sessions"][0]["state"], "unknown")
        self.assertEqual(event["sessions"][0]["assignment"], "assigned")
        self.assertEqual(event["sessions"][0]["proof"], "turn_history")
        self.assertEqual(event["coverage"], {"complete": False, "observed": 1, "total": None})
        self.assertFalse(self.store.snapshot()["executionCoverageComplete"])

    def test_unmapped_records_never_assign_slots(self):
        event = make_event(observation(), 1, [])
        self.assertEqual(event["sessions"], [])
        self.assertEqual(event["coverage"]["total"], None)

    def test_unknown_fields_and_free_text_rejected(self):
        for key in ("title", "url", "path", "error", "id", "message", "credential"):
            event = make_event(observation(), 1, binding())
            event[key] = SENTINEL
            with self.assertRaises(EventError): validate_event(event)
        value = binding()
        value[0]["topicKeys"] = [SENTINEL]
        with self.assertRaises(EventError): validate_bindings(value)
        event = make_event(observation(), 1, binding())
        event["sessions"][0]["title"] = SENTINEL
        with self.assertRaises(EventError): validate_event(event)

    def test_missing_binding_is_unavailable_not_unassigned(self):
        bindings = binding()
        bindings[0]["recordKey"] = "absent"
        self.assertEqual(make_event(observation(), 1, bindings)["sessions"], [])

    def test_repeat_poll_keeps_original_record_time_and_change_time(self):
        first = self.save()
        next_event = self.save(observation(NOW + 30))
        self.assertEqual(first["sessions"][0]["observedAt"], next_event["sessions"][0]["observedAt"])
        self.assertNotEqual(first["observedAt"], next_event["observedAt"])
        row = self.store.db.execute("SELECT last_changed_at,last_seen_at FROM registry_records").fetchone()
        self.assertEqual(tuple(row), (NOW, NOW + 30))
        self.assertEqual(self.store.snapshot()["sources"][0]["lastSuccessAt"], NOW + 30)

    def test_outage_preserves_last_success_and_records(self):
        self.save()
        event = self.save(observation(NOW + 30, "unavailable"))
        self.assertEqual(event["health"], "error")
        self.assertEqual(event["sessions"], [])
        self.assertEqual(self.store.snapshot()["sources"][0]["lastSuccessAt"], NOW)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM registry_records").fetchone()[0], 1)

    def test_monotonic_sequence_and_restart_durability(self):
        first = self.save()
        self.store.close()
        self.store = Store(self.path)
        second = self.save(observation(NOW + 30))
        self.assertEqual((first["eventId"], second["eventId"]), ("1", "2"))
        self.assertEqual(self.store.status()["pending"], 2)
        with self.assertRaises(StoreError): self.save(observation(NOW + 20))
        self.assertEqual(self.store.status()["pending"], 2)

    def test_atomic_outbox_limit_and_transaction_rollback(self):
        self.store.max_pending = 1
        self.save()
        with self.assertRaises(StoreError): self.save(observation(NOW + 30))
        row = self.store.db.execute("SELECT sequence,observed_at FROM source_state").fetchone()
        self.assertEqual(tuple(row), (1, NOW))

    def test_retry_uses_identical_event_and_idempotency_id(self):
        self.save()
        opener = Opener(fail=1)
        receiver = HTTPSReceiver(receiver_config(), environment={"SYNTHETIC_TOKEN": "synthetic-test-token-not-real-32chars"}, opener=opener)
        self.assertEqual(flush(self.store, receiver, NOW), {"sent": 0, "failed": 1})
        self.assertEqual(flush(self.store, receiver, NOW + 4), {"sent": 0, "failed": 0})
        self.assertEqual(flush(self.store, receiver, NOW + 5), {"sent": 1, "failed": 0})
        self.assertEqual(opener.requests[0].data, opener.requests[1].data)
        self.assertEqual(self.store.status()["pending"], 0)
        self.assertNotIn("synthetic-test-token-not-real-32chars", json.dumps(self.store.snapshot()))
        self.assertNotIn("synthetic-test-token-not-real-32chars", self.store.db.execute("SELECT event_json FROM outbox").fetchone()[0])

    def test_failed_head_blocks_only_same_source(self):
        self.save()
        self.save(observation(NOW + 30))
        head = self.store.pending(NOW + 40)
        self.assertEqual(len(head), 1)
        self.store.fail(head[0]["id"], NOW + 40, "delivery_failed")
        self.assertEqual(self.store.pending(NOW + 41), [])

    def test_ack_mismatch_never_discards_event(self):
        self.save()
        receiver = HTTPSReceiver(receiver_config(), environment={"SYNTHETIC_TOKEN": "synthetic-test-token-not-real-32chars"}, opener=Opener(mismatch=True))
        self.assertEqual(flush(self.store, receiver, NOW)["failed"], 1)
        self.assertEqual(self.store.status()["pending"], 1)
        self.assertEqual(self.store.db.execute("SELECT last_error_code FROM outbox").fetchone()[0], "ack_mismatch")

    def test_read_only_safe_mcp_snapshot(self):
        self.save()
        response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "monitor_metadata", "arguments": {}}}, self.path)
        self.assertFalse(response["result"]["isError"])
        self.assertNotIn(SENTINEL, json.dumps(response))
        self.assertNotIn(str(self.path), json.dumps(response))
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.path))
        self.assertEqual(handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "write", "arguments": {}}}, self.path)["error"]["code"], -32601)

    def test_mcp_request_bound(self):
        incoming, outgoing = io.StringIO("x" * 70000 + "\n"), io.StringIO()
        serve(self.path, incoming, outgoing)
        self.assertIn("Request too large", outgoing.getvalue())
        self.assertLess(len(outgoing.getvalue()), 512)

    def test_private_state_permissions(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_existing_foreign_database_never_migrated(self):
        foreign = self.path.parent / "foreign.db"
        db = sqlite3.connect(foreign)
        db.execute("CREATE TABLE private(value TEXT)")
        db.commit()
        db.close()
        os.chmod(foreign, 0o600)
        before = foreign.read_bytes()
        with self.assertRaises(StoreError): Store(foreign)
        self.assertEqual(before, foreign.read_bytes())


    def test_restored_metadata_cannot_add_private_output_fields(self):
        self.save()
        raw = json.loads(self.store.db.execute("SELECT health_json FROM source_state").fetchone()[0])
        raw["privateTitle"] = SENTINEL
        self.store.db.execute("UPDATE source_state SET health_json=?", (json.dumps(raw),))
        with self.assertRaises(StoreError): self.store.snapshot()
        response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "monitor_metadata"}}, self.path)
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn(SENTINEL, json.dumps(response))

    def test_backoff_has_upper_bound(self):
        self.save()
        row = self.store.pending(NOW)[0]
        for attempt in range(100):
            self.store.fail(row["id"], NOW, "delivery_failed")
        stored = self.store.db.execute("SELECT next_attempt_at,attempts FROM outbox").fetchone()
        self.assertEqual(tuple(stored), (NOW + 300, 100))

    def test_duplicate_ack_is_harmless(self):
        self.save()
        row = self.store.pending(NOW)[0]
        self.store.acknowledge(row["id"], NOW)
        self.store.acknowledge(row["id"], NOW + 300)
        self.assertEqual(self.store.db.execute("SELECT acknowledged_at FROM outbox").fetchone()[0], NOW)


    def test_source_delivery_pacing_survives_restart(self):
        self.save()
        self.save(observation(NOW + 1))
        head = self.store.pending(NOW)[0]
        self.store.acknowledge(head["id"], NOW)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.pending(NOW + 1), [])
        ready = self.store.pending(NOW + 2)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["sequence"], 2)


class TransportTests(unittest.TestCase):
    def test_http_protocol_failures_are_fixed_transport_errors(self):
        for failure in (http.client.IncompleteRead(SENTINEL.encode(), 20),
                        http.client.BadStatusLine(SENTINEL),
                        http.client.LineTooLong(SENTINEL)):
            for stage in ("open", "read"):
                with self.subTest(failure=type(failure).__name__, stage=stage):
                    class BrokenResponse:
                        status = 202
                        def __enter__(self): return self
                        def __exit__(self, *args): return False
                        def read(self, limit): raise failure
                    class BrokenOpener:
                        def open(self, request, timeout):
                            if stage == "open": raise failure
                            return BrokenResponse()
                    receiver = HTTPSReceiver(receiver_config(), environment={"SYNTHETIC_TOKEN": "synthetic-test-token-not-real-32chars"}, opener=BrokenOpener())
                    with self.assertRaisesRegex(TransportError, "^delivery_failed$"):
                        receiver.send(make_event(observation(), 1, binding()))

    def test_defaults_make_no_network_calls(self):
        opener = Opener()
        receiver = HTTPSReceiver(receiver_config(False), opener=opener)
        with self.assertRaisesRegex(TransportError, "transport_unconfigured"):
            receiver.send(make_event(observation(), 1, binding()))
        self.assertEqual(opener.requests, [])

    def test_destination_requires_exact_https_allowlist(self):
        for bad in ("http://receiver.example/v1/ingest", "https://receiver.example/other", "https://user:pass@receiver.example/v1/ingest", "https://receiver.example/v1/ingest?token=bad", "https://receiver.example:444/v1/ingest", "https://other.example/v1/ingest"):
            config = receiver_config()
            config["url"] = bad
            with self.assertRaises(TransportError): HTTPSReceiver(config)

    def test_missing_auth_cannot_send(self):
        opener = Opener()
        receiver = HTTPSReceiver(receiver_config(), environment={}, opener=opener)
        with self.assertRaisesRegex(TransportError, "auth_unavailable"):
            receiver.send(make_event(observation(), 1, binding()))
        self.assertEqual(opener.requests, [])

    def test_redirect_not_followed(self):
        from tools.morgan_monitor.transport import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://evil.example/"))

    def test_old_event_remains_old_on_delivery_after_outage(self):
        opener = Opener()
        receiver = HTTPSReceiver(receiver_config(), environment={"SYNTHETIC_TOKEN": "synthetic-test-token-not-real-32chars"}, opener=opener)
        event = make_event(observation(NOW - 1000), 1, binding())
        receiver.send(event)
        self.assertEqual(json.loads(opener.requests[0].data)["observedAt"], event["observedAt"])
