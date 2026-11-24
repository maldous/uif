"""Collector-owned SQLite persistence; never opens upstream DBs for writing."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

from .sources import Observation, SourceError, readonly_database

SCHEMA_VERSION = 1


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


class StoreError(Exception):
    pass


class Store:
    def __init__(self, path: str | Path, *, max_pending: int = 10000):
        self.path = Path(path).expanduser().absolute()
        self.max_pending = max_pending
        if not 10 <= max_pending <= 100000:
            raise StoreError("invalid_outbox_budget")
        # Only a new dedicated private database may be initialized. Never turn
        # an arbitrary existing SQLite database into a monitor state database.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._verify_existing()
        else:
            os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=3000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS monitor_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO monitor_meta VALUES ('schema_version','1');
            CREATE TABLE IF NOT EXISTS source_state (
                source_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                observed_at INTEGER NOT NULL, last_success_at INTEGER,
                health_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS registry_records (
                source_id TEXT NOT NULL, record_key TEXT NOT NULL,
                payload_json TEXT NOT NULL, content_hash TEXT NOT NULL,
                first_seen_at INTEGER NOT NULL, last_changed_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                PRIMARY KEY(source_id, record_key)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, event_json TEXT NOT NULL,
                created_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at INTEGER NOT NULL DEFAULT 0, acknowledged_at INTEGER,
                last_error_code TEXT,
                UNIQUE(source_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(acknowledged_at,id);
            CREATE INDEX IF NOT EXISTS outbox_source_ack ON outbox(source_id,acknowledged_at);
            COMMIT;
        """)

    def _verify_existing(self):
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise StoreError("state_not_private")
        try:
            with readonly_database(self.path) as db:
                version = db.execute("SELECT value FROM monitor_meta WHERE key='schema_version'").fetchone()
                if version is None or version[0] != str(SCHEMA_VERSION):
                    raise StoreError("state_schema_mismatch")
        except (SourceError, sqlite3.DatabaseError):
            raise StoreError("state_schema_mismatch") from None

    def close(self):
        self.db.close()

    def save(self, observation: Observation, event_factory) -> dict[str, Any]:
        """Commit health, records, per-source sequence and outbox in one transaction.

        An outbox full condition rolls back rather than discarding telemetry.
        Record timestamps and last_changed_at are unchanged on repeat reads.
        """
        db = self.db
        try:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT COUNT(*) FROM outbox WHERE acknowledged_at IS NULL").fetchone()[0]
            if pending >= self.max_pending:
                raise StoreError("outbox_full")
            old = db.execute("SELECT sequence,observed_at,last_success_at FROM source_state WHERE source_id=?", (observation.source,)).fetchone()
            if old is not None and observation.observed_at <= old["observed_at"]:
                raise StoreError("non_monotonic_observation")
            sequence = 1 if old is None else old["sequence"] + 1
            last_success = observation.observed_at if observation.health == "ok" else (old["last_success_at"] if old is not None else None)
            event = event_factory(sequence)
            if event["sourceId"] != observation.source or event["sequence"] != sequence:
                raise StoreError("event_identity_mismatch")
            encoded = canonical(event)
            if len(encoded.encode()) > 65536:
                raise StoreError("event_too_large")
            health = {
                "sourceId": observation.source, "health": observation.health,
                "observedAt": observation.observed_at, "lastSuccessAt": last_success,
                "capability": observation.capability, "latestRecordAt": observation.latest_record_at,
                "registryCoverage": {"returned": len(observation.records), "total": observation.total,
                                     "complete": observation.complete},
                "errorCode": observation.error_code, "serviceState": observation.service_state,
            }
            if observation.allowance is not None and observation.health == "ok":
                health["fooAllowance"] = {"state": observation.allowance["state"],
                                          "canRunAgents": observation.allowance["canRunAgents"],
                                          "observedAt": observation.allowance["observed_at"]}
            # A failed read must not replace the prior last-good registry data.
            # Current health's coverage is explicitly zero/incomplete on failure.
            db.execute("INSERT INTO source_state VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                       "sequence=excluded.sequence,observed_at=excluded.observed_at,last_success_at=excluded.last_success_at,health_json=excluded.health_json",
                       (observation.source, sequence, observation.observed_at, last_success, canonical(health)))
            for record in observation.records:
                body = canonical(record)
                digest = hashlib.sha256(body.encode()).hexdigest()
                db.execute("INSERT INTO registry_records VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id,record_key) DO UPDATE SET "
                           "payload_json=excluded.payload_json,content_hash=excluded.content_hash,last_seen_at=excluded.last_seen_at,"
                           "last_changed_at=CASE WHEN registry_records.content_hash=excluded.content_hash THEN registry_records.last_changed_at ELSE excluded.last_changed_at END",
                           (observation.source, record["key"], body, digest, observation.observed_at, observation.observed_at, observation.observed_at))
            db.execute("INSERT INTO outbox(source_id,sequence,event_json,created_at) VALUES(?,?,?,?)",
                       (observation.source, sequence, encoded, observation.observed_at))
            db.execute("COMMIT")
            return event
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise

    def pending(self, now: int, limit: int = 50, *, source_ids=None) -> list[dict[str, Any]]:
        # Per-source head-of-line order prevents later sequence delivery before
        # a pending earlier event; one source outage need not block other sources.
        # Durable 2-second integer timestamp gap guarantees at least one real
        # second between new events, including across collector restarts. This
        # avoids the receiver's known per-source rate limit without failed sends.
        if source_ids is not None and not source_ids:
            return []
        condition = "" if source_ids is None else " AND o.source_id IN (" + ",".join("?" for _ in source_ids) + ")"
        parameters = [now, now - 2] + ([] if source_ids is None else list(source_ids)) + [min(max(limit, 1), 100)]
        rows = self.db.execute("""
            SELECT o.* FROM outbox o WHERE o.acknowledged_at IS NULL AND o.next_attempt_at<=?
            AND NOT EXISTS(SELECT 1 FROM outbox p WHERE p.source_id=o.source_id
                           AND p.acknowledged_at IS NULL AND p.sequence<o.sequence)
            AND NOT EXISTS(SELECT 1 FROM outbox a WHERE a.source_id=o.source_id
                           AND a.acknowledged_at>?)
        """ + condition + " ORDER BY o.id LIMIT ?", parameters).fetchall()
        return [dict(r) for r in rows]

    def acknowledge(self, event_id: int, now: int):
        self.db.execute("UPDATE outbox SET acknowledged_at=?,last_error_code=NULL WHERE id=? AND acknowledged_at IS NULL", (now, event_id))

    def fail(self, event_id: int, now: int, code: str, *, base_seconds: int = 5, max_seconds: int = 300):
        if code not in {"transport_unconfigured", "delivery_failed", "ack_mismatch", "auth_unavailable", "invalid_destination"}:
            raise StoreError("invalid_error_code")
        row = self.db.execute("SELECT attempts FROM outbox WHERE id=? AND acknowledged_at IS NULL", (event_id,)).fetchone()
        if row is None:
            return
        attempts = row[0] + 1
        delay = min(max_seconds, base_seconds * (2 ** min(attempts - 1, 12)))
        self.db.execute("UPDATE outbox SET attempts=?,next_attempt_at=?,last_error_code=? WHERE id=?", (attempts, now + delay, code, event_id))

    def status(self) -> dict[str, Any]:
        return safe_status(self.db)

    def snapshot(self) -> dict[str, Any]:
        return safe_snapshot(self.db)


def safe_status(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT COUNT(*) AS total,SUM(CASE WHEN acknowledged_at IS NULL THEN 1 ELSE 0 END) AS pending,"
                     "MIN(CASE WHEN acknowledged_at IS NULL THEN created_at END) AS oldest_pending_at,MAX(attempts) AS max_attempts FROM outbox").fetchone()
    return {"total": row[0], "pending": row[1] or 0, "oldestPendingAt": row[2], "maxAttempts": row[3] or 0}


def safe_snapshot(db: sqlite3.Connection) -> dict[str, Any]:
    sources = [_project_health(json.loads(row[0])) for row in db.execute("SELECT health_json FROM source_state ORDER BY source_id LIMIT 16")]
    return {"schemaVersion": 1, "sources": sources, "outbox": safe_status(db),
            "executionCoverageComplete": False, "cloudFeedConfigured": False}


def read_snapshot(path: str | Path) -> dict[str, Any]:
    try:
        with readonly_database(path) as db:
            version = db.execute("SELECT value FROM monitor_meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != "1":
                raise StoreError("state_schema_mismatch")
            return safe_snapshot(db)
    except (SourceError, sqlite3.DatabaseError, ValueError, TypeError):
        raise StoreError("state_unavailable") from None


def _project_health(value):
    from .events import SOURCE_CAPABILITIES
    keys = {"sourceId", "health", "observedAt", "lastSuccessAt", "capability", "latestRecordAt", "registryCoverage", "errorCode", "serviceState"}
    if not isinstance(value, dict) or not keys <= set(value) or set(value) - keys - {"fooAllowance"}:
        raise StoreError("state_invalid")
    if (not isinstance(value["sourceId"], str) or value["sourceId"] not in SOURCE_CAPABILITIES
            or value["health"] not in {"ok", "unavailable", "unconfigured"}
            or value["capability"] not in {"historical_registry", "service_process", "none", "foo_allowance"}
            or value["errorCode"] not in {None, "not_configured", "source_unavailable", "schema_drift", "invalid_timestamp", "invalid_record", "clock_skew", "identity_mismatch", "protocol_drift", "metadata_unavailable"}
            or value["serviceState"] not in {None, "running", "stopped", "unknown"}):
        raise StoreError("state_invalid")
    for key in ("observedAt", "lastSuccessAt", "latestRecordAt"):
        if value[key] is not None and (type(value[key]) is not int or not 0 <= value[key] <= 253402300799):
            raise StoreError("state_invalid")
    coverage = value["registryCoverage"]
    if (not isinstance(coverage, dict) or set(coverage) != {"returned", "total", "complete"}
            or type(coverage["returned"]) is not int or not 0 <= coverage["returned"] <= 10000
            or type(coverage["total"]) is not int or coverage["total"] < coverage["returned"]
            or type(coverage["complete"]) is not bool):
        raise StoreError("state_invalid")
    result = {key: value[key] for key in sorted(keys)}
    if "fooAllowance" in value:
        proof = value["fooAllowance"]
        if (value["sourceId"] != "foo_allowance" or value["health"] != "ok" or not isinstance(proof, dict)
                or set(proof) != {"state", "canRunAgents", "observedAt"}
                or (proof["state"], proof["canRunAgents"]) not in (("valid", True), ("exhausted", False), ("unavailable", None))
                or (proof["canRunAgents"] is not None and type(proof["canRunAgents"]) is not bool)
                or type(proof["observedAt"]) is not int or not 946684800 <= proof["observedAt"] <= value["observedAt"]):
            raise StoreError("state_invalid")
        result["fooAllowance"] = {key: proof[key] for key in ("state", "canRunAgents", "observedAt")}
    return result
