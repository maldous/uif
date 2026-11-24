"""Offline SYNTHETIC fixtures only. No real user's databases or services."""
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from tools.morgan_monitor.sources import (
    CodexThreads, CodexTurns, SymphonyLeases, SystemdService, ProcessIdentity,
    THREAD_SCHEMA, TURN_SCHEMA, LEASE_SCHEMA, EVENT_SCHEMA, MIGRATION_SCHEMA,
    SourceError, readonly_database,
)

NOW = 1794240000
SECRET = "PRIVATE-TITLE-PASSWORD-token=https://private.example/secret"


def schema(db, name, fields):
    db.execute('CREATE TABLE "' + name + '" (' + ','.join('"' + key + '" ' + value for key, value in fields.items()) + ')')


def fixture(path, kind):
    db = sqlite3.connect(path)
    if kind in {"threads", "turns"}:
        schema(db, "_sqlx_migrations", MIGRATION_SCHEMA)
        db.execute("INSERT INTO _sqlx_migrations(version,success) VALUES(?,1)", (62 if kind == "threads" else 8,))
    if kind == "threads":
        schema(db, "threads", THREAD_SCHEMA)
        for i in range(7):
            db.execute("INSERT INTO threads(id,updated_at,updated_at_ms,created_at,created_at_ms,archived,title,first_user_message,rollout_path) VALUES(?,?,?,?,?,?,?,?,?)",
                       (f"private-{i}", NOW - 700000, (NOW - 700000) * 1000 + i, NOW - 800000, None, 0, SECRET, SECRET, SECRET))
    elif kind == "turns":
        schema(db, "thread_turns", TURN_SCHEMA)
        for i, status in enumerate(["inProgress", "inProgress", "completed", "failed", "interrupted"]):
            db.execute("INSERT INTO thread_turns(thread_id,turn_id,status,started_at,completed_at,error_json) VALUES(?,?,?,?,?,?)",
                       (f"private-{i}", f"private-turn-{i}", status, NOW - 700000 - i, None if status == "inProgress" else NOW - 600000 - i, SECRET))
        schema(db, "thread_items", {"item_json": "TEXT"})
        db.execute("INSERT INTO thread_items VALUES(?)", (SECRET,))
    else:
        schema(db, "leases", LEASE_SCHEMA)
        schema(db, "events", EVENT_SCHEMA)
        for i, status in enumerate(["running", "cancel_requested", "uncertain", "finished", "failed"]):
            db.execute("INSERT INTO leases VALUES(?,?,?,?,?,?)", (f"lease-{i}", SECRET, SECRET, status, SECRET, NOW - 700000 - i))
            db.execute("INSERT INTO events VALUES(?,?,?,?)", (i + 1, f"lease-{i}", "admitted", NOW - 600000 - i))
    db.commit()
    db.close()


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_threads_keyset_pagination_and_bounded_coverage(self):
        path = self.root / "threads.db"
        fixture(path, "threads")
        observation = CodexThreads(str(path), page_size=2, max_records=4).collect(NOW)
        self.assertEqual(observation.health, "ok")
        self.assertEqual(observation.total, 7)
        self.assertFalse(observation.complete)
        self.assertEqual([r["key"] for r in observation.records], [f"private-{i}" for i in (6, 5, 4, 3)])
        complete = CodexThreads(str(path), page_size=2, max_records=7).collect(NOW)
        self.assertTrue(complete.complete)
        self.assertNotIn(SECRET, str(complete))
        self.assertTrue(all(r["liveness"] == "unknown" for r in complete.records))

    def test_explicit_millisecond_fallback(self):
        path = self.root / "threads.db"
        fixture(path, "threads")
        db = sqlite3.connect(path)
        db.execute("UPDATE threads SET updated_at_ms=NULL WHERE id='private-6'")
        db.commit()
        db.close()
        records = CodexThreads(str(path)).collect(NOW).records
        self.assertEqual(next(r["record_at"] for r in records if r["key"] == "private-6"), NOW - 700000)

    def test_stale_in_progress_is_not_liveness(self):
        path = self.root / "turns.db"
        fixture(path, "turns")
        observation = CodexTurns(str(path), page_size=1).collect(NOW)
        self.assertTrue(observation.complete)
        self.assertEqual(len(observation.records), 5)
        self.assertEqual(sum(r["state"] == "inProgress" for r in observation.records), 2)
        self.assertTrue(all(r["liveness"] == "unknown" for r in observation.records))
        self.assertLess(observation.latest_record_at, NOW - 500000)
        self.assertNotIn(SECRET, str(observation))

    def test_symphony_history_not_live_and_no_pool_import(self):
        path = self.root / "leases.db"
        fixture(path, "leases")
        before = path.read_bytes()
        observation = SymphonyLeases(str(path), page_size=2).collect(NOW)
        self.assertTrue(observation.complete)
        self.assertEqual(observation.total, 5)
        self.assertTrue(all(r["liveness"] == "unknown" for r in observation.records))
        self.assertNotIn(SECRET, str(observation))
        self.assertEqual(before, path.read_bytes())
        self.assertNotIn("symphony_capacity", __import__("sys").modules)

    def test_added_column_is_schema_drift(self):
        path = self.root / "turns.db"
        fixture(path, "turns")
        db = sqlite3.connect(path)
        db.execute("ALTER TABLE thread_turns ADD COLUMN unexpected TEXT")
        db.commit()
        db.close()
        observation = CodexTurns(str(path)).collect(NOW)
        self.assertEqual(observation.error_code, "schema_drift")
        self.assertEqual(observation.records, [])
        self.assertFalse(observation.complete)

    def test_migration_change_fails_closed(self):
        path = self.root / "threads.db"
        fixture(path, "threads")
        db = sqlite3.connect(path)
        db.execute("UPDATE _sqlx_migrations SET version=63")
        db.commit()
        db.close()
        self.assertEqual(CodexThreads(str(path)).collect(NOW).error_code, "schema_drift")

    def test_status_drift_fails_closed(self):
        path = self.root / "turns.db"
        fixture(path, "turns")
        db = sqlite3.connect(path)
        db.execute("UPDATE thread_turns SET status=?", (SECRET,))
        db.commit()
        db.close()
        result = CodexTurns(str(path)).collect(NOW)
        self.assertEqual(result.error_code, "schema_drift")
        self.assertNotIn(SECRET, str(result))

    def test_missing_source_never_created(self):
        path = self.root / "missing.db"
        result = CodexThreads(str(path)).collect(NOW)
        self.assertEqual(result.health, "unavailable")
        self.assertEqual(result.error_code, "source_unavailable")
        self.assertFalse(path.exists())
        self.assertEqual(CodexThreads(None).collect(NOW).health, "unconfigured")

    def test_query_only_and_no_source_write(self):
        path = self.root / "threads.db"
        fixture(path, "threads")
        before = path.read_bytes()
        with readonly_database(path) as db:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM threads")
        self.assertEqual(before, path.read_bytes())

    def test_live_wal_is_visible_without_immutable(self):
        path = self.root / "threads.db"
        fixture(path, "threads")
        writer = sqlite3.connect(path)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE threads SET updated_at_ms=? WHERE id='private-1'", ((NOW - 20) * 1000,))
        writer.commit()
        result = CodexThreads(str(path)).collect(NOW)
        self.assertEqual(result.latest_record_at, NOW - 20)
        writer.close()

    def test_systemd_process_state_not_useful_execution(self):
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\n", "")
        result = SystemdService("synthetic-controller.service", runner=runner).collect(NOW)
        self.assertEqual(result.health, "ok")
        self.assertEqual(result.service_state, "running")
        self.assertEqual(result.records, [])
        self.assertEqual(commands[0][1], "show")
        self.assertNotIn("restart", commands[0])

    def test_systemd_stopped_and_probe_outage_separate(self):
        def stopped(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\n", SECRET)
        result = SystemdService("synthetic.service", runner=stopped).collect(NOW)
        self.assertEqual((result.health, result.service_state), ("ok", "stopped"))
        def failed(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 1, output=SECRET)
        result = SystemdService("synthetic.service", runner=failed).collect(NOW)
        self.assertEqual(result.health, "unavailable")
        self.assertNotIn(SECRET, str(result))

    def test_pid_start_identity_and_reuse(self):
        path = self.root / "42"
        path.mkdir()
        fields = ["S"] + ["0"] * 18 + ["999"] + ["0"] * 4
        (path / "stat").write_text("42 (synthetic (process)) " + " ".join(fields))
        probe = ProcessIdentity(42, 999, proc_root=self.root)
        self.assertEqual(probe.collect(NOW).service_state, "running")
        self.assertEqual(ProcessIdentity(42, 998, proc_root=self.root).collect(NOW).service_state, "stopped")
        (path / "stat").unlink()
        self.assertEqual(probe.collect(NOW).service_state, "stopped")
        self.assertEqual(ProcessIdentity().collect(NOW).health, "unconfigured")

    def test_future_source_timestamp_does_not_refresh_health(self):
        path = self.root / "future.db"
        fixture(path, "threads")
        db = sqlite3.connect(path)
        db.execute("UPDATE threads SET updated_at_ms=?", ((NOW + 31) * 1000,))
        db.commit()
        db.close()
        observation = CodexThreads(str(path)).collect(NOW)
        self.assertEqual(observation.error_code, "clock_skew")
        self.assertEqual(observation.records, [])

    def test_read_deadline_cancels_long_query(self):
        path = self.root / "deadline.db"
        fixture(path, "threads")
        with self.assertRaisesRegex(SourceError, "source_unavailable"):
            with readonly_database(path, 0.05) as db:
                db.execute("WITH RECURSIVE counter(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM counter WHERE n<10000000) SELECT SUM(n) FROM counter").fetchone()

    def test_payload_columns_never_selected(self):
        from unittest.mock import patch
        path = self.root / "turns.db"
        fixture(path, "turns")
        original = sqlite3.connect
        statements = []
        def connect(*args, **kwargs):
            db = original(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        with patch("tools.morgan_monitor.sources.sqlite3.connect", side_effect=connect):
            self.assertEqual(CodexTurns(str(path)).collect(NOW).health, "ok")
        queries = [q.lower() for q in statements if q.lstrip().lower().startswith("select")]
        self.assertTrue(queries)
        for forbidden in ("error_json", "item_json", "thread_items", "select *"):
            self.assertFalse(any(forbidden in query for query in queries))
