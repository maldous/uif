"""Private metadata adapters. No transcript, title, command line, or error reads.

The SQLite contracts below are observed implementation schemas, not supported
upstream APIs. A changed schema/version fails closed until explicitly reviewed.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Any


class SourceError(Exception):
    """A reviewed diagnostic code, never the underlying raw error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Observation:
    source: str
    observed_at: int  # UTC Unix seconds, the independent read/probe time
    health: str
    capability: str
    records: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    complete: bool = False
    error_code: str | None = None
    service_state: str | None = None
    allowance: dict[str, Any] | None = None

    @property
    def latest_record_at(self) -> int | None:
        values = [r["record_at"] for r in self.records if r.get("record_at") is not None]
        return max(values) if values else None


THREAD_SCHEMA = dict(
    id="TEXT", rollout_path="TEXT", created_at="INTEGER", updated_at="INTEGER",
    source="TEXT", model_provider="TEXT", cwd="TEXT", title="TEXT",
    sandbox_policy="TEXT", approval_mode="TEXT", tokens_used="INTEGER",
    has_user_event="INTEGER", archived="INTEGER", archived_at="INTEGER",
    git_sha="TEXT", git_branch="TEXT", git_origin_url="TEXT", cli_version="TEXT",
    first_user_message="TEXT", agent_nickname="TEXT", agent_role="TEXT",
    memory_mode="TEXT", model="TEXT", reasoning_effort="TEXT", agent_path="TEXT",
    created_at_ms="INTEGER", updated_at_ms="INTEGER", thread_source="TEXT",
    preview="TEXT", recency_at="INTEGER", recency_at_ms="INTEGER",
    history_mode="TEXT", name="TEXT", is_pinned="INTEGER", thread_section_id="TEXT",
    section_position="INTEGER", section_entered_at_ms="INTEGER", project_id="TEXT",
    originator="TEXT", daybreak_enabled="BOOLEAN", creator_user_id="TEXT",
    creator_account_id="TEXT", attached_thread_parent_id="TEXT",
)
TURN_SCHEMA = dict(
    thread_id="TEXT", turn_id="TEXT", rollout_ordinal="INTEGER", status="TEXT",
    error_json="TEXT", started_at="INTEGER", completed_at="INTEGER", duration_ms="INTEGER",
    first_user_item_id="TEXT", final_agent_item_id="TEXT", rollout_byte_offset="INTEGER",
    rollout_end_ordinal="INTEGER", rollout_end_byte_offset="INTEGER", root_turn_id="TEXT",
)
LEASE_SCHEMA = dict(id="TEXT", binding="TEXT", fingerprint="TEXT", status="TEXT", outcome="TEXT", created="REAL")
EVENT_SCHEMA = dict(sequence="INTEGER", id="TEXT", event="TEXT", at="REAL")
MIGRATION_SCHEMA = dict(version="BIGINT", description="TEXT", installed_on="TIMESTAMP", success="BOOLEAN", checksum="BLOB", execution_time="BIGINT")


def timestamp(value: Any, *, milliseconds: bool = False) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SourceError("invalid_timestamp")
    seconds = value / 1000 if milliseconds else value
    if not 0 <= seconds <= 253402300799:
        raise SourceError("invalid_timestamp")
    return int(seconds)


def identity(value: Any) -> str:
    # IDs stay private. Bound their size before storing or keyset pagination.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise SourceError("invalid_record")
    return value


@contextmanager
def readonly_database(path: str | Path, deadline_seconds: float = 2.0):
    """Read live WAL data without immutable mode, mutation, or Pool.status()."""
    connection = None
    try:
        uri = Path(path).expanduser().absolute().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=min(deadline_seconds, 1.0))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=1000")
        deadline = time.monotonic() + deadline_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        # One bounded snapshot across count and keyset pages; no writer lock.
        connection.execute("BEGIN")
        yield connection
    except (sqlite3.DatabaseError, OSError, ValueError):
        raise SourceError("source_unavailable") from None
    finally:
        if connection is not None:
            connection.close()


def require_schema(db: sqlite3.Connection, table: str, expected: dict[str, str]) -> None:
    # Table names come only from constants in this module, never configuration.
    actual = {r["name"]: r["type"].upper() for r in db.execute(f'PRAGMA table_info("{table}")')}
    if actual != expected:
        raise SourceError("schema_drift")


def require_migration(db: sqlite3.Connection, version: int) -> None:
    require_schema(db, "_sqlx_migrations", MIGRATION_SCHEMA)
    row = db.execute("SELECT MAX(version) AS version, MIN(success) AS success FROM _sqlx_migrations").fetchone()
    if row["version"] != version or row["success"] != 1:
        raise SourceError("schema_drift")


class SQLiteSource:
    source = ""
    capability = "historical_registry"

    def __init__(self, path: str | None, *, page_size: int = 100, max_records: int = 1000, deadline_seconds: float = 2.0):
        if not 1 <= page_size <= 500 or not 1 <= max_records <= 10000 or not 0.05 <= deadline_seconds <= 10:
            raise ValueError("invalid source budget")
        self.path, self.page_size, self.max_records, self.deadline_seconds = path, page_size, max_records, deadline_seconds

    def collect(self, now: int) -> Observation:
        if not self.path:
            return Observation(self.source, now, "unconfigured", self.capability, error_code="not_configured")
        try:
            with readonly_database(self.path, self.deadline_seconds) as db:
                records, total = self.read(db)
            # Clock skew is uncertainty, never a fresh-looking future record.
            if any(r.get("record_at") is not None and r["record_at"] > now + 30 for r in records):
                raise SourceError("clock_skew")
            return Observation(self.source, now, "ok", self.capability, records, total, total == len(records))
        except SourceError as exc:
            return Observation(self.source, now, "unavailable", self.capability, error_code=exc.code)

    def read(self, db: sqlite3.Connection):
        raise NotImplementedError


class CodexThreads(SQLiteSource):
    source = "codex_registry"

    def read(self, db):
        require_migration(db, 62)
        require_schema(db, "threads", THREAD_SCHEMA)
        total = db.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        records, cursor = [], None
        # Explicit ms/seconds fallback for the qualified local database, never
        # guessed by magnitude or reused for another connector's units.
        expression = "COALESCE(updated_at_ms, updated_at * 1000)"
        while len(records) < min(total, self.max_records):
            params: list[Any] = []
            where = ""
            if cursor is not None:
                where = f" WHERE ({expression}, id) < (?, ?)"
                params.extend(cursor)
            params.append(min(self.page_size, self.max_records - len(records)))
            rows = db.execute(
                "SELECT id, created_at, created_at_ms, updated_at, updated_at_ms, archived "
                f"FROM threads{where} ORDER BY {expression} DESC, id DESC LIMIT ?", params,
            ).fetchall()
            if not rows:
                break
            for row in rows:
                if row["archived"] not in (0, 1):
                    raise SourceError("invalid_record")
                record_at = timestamp(row["updated_at_ms"], milliseconds=True) if row["updated_at_ms"] is not None else timestamp(row["updated_at"])
                if record_at is None:
                    raise SourceError("invalid_timestamp")
                records.append({"key": identity(row["id"]), "record_at": record_at, "state": "archived" if row["archived"] else "recorded", "liveness": "unknown"})
            last = rows[-1]
            cursor = (last["updated_at_ms"] if last["updated_at_ms"] is not None else last["updated_at"] * 1000, last["id"])
        return records, total


class CodexTurns(SQLiteSource):
    source = "codex_turns"

    def read(self, db):
        require_migration(db, 8)
        require_schema(db, "thread_turns", TURN_SCHEMA)
        total = db.execute("SELECT COUNT(*) FROM thread_turns").fetchone()[0]
        records, cursor = [], None
        expression = "COALESCE(completed_at, started_at, 0)"
        allowed = {"completed", "failed", "interrupted", "inProgress"}
        while len(records) < min(total, self.max_records):
            params, where = [], ""
            if cursor is not None:
                where = f" WHERE ({expression}, thread_id, turn_id) < (?, ?, ?)"
                params.extend(cursor)
            params.append(min(self.page_size, self.max_records - len(records)))
            rows = db.execute("SELECT thread_id, turn_id, status, started_at, completed_at, duration_ms "
                              f"FROM thread_turns{where} ORDER BY {expression} DESC, thread_id DESC, turn_id DESC LIMIT ?", params).fetchall()
            if not rows:
                break
            for row in rows:
                if row["status"] not in allowed:
                    raise SourceError("schema_drift")
                start, end = timestamp(row["started_at"]), timestamp(row["completed_at"])
                # A historical inProgress row is NEVER an active agent proof.
                records.append({"key": identity(row["thread_id"]) + "/" + identity(row["turn_id"]),
                                "record_at": end if end is not None else start, "state": row["status"], "liveness": "unknown"})
            last = rows[-1]
            cursor = (last["completed_at"] if last["completed_at"] is not None else (last["started_at"] or 0), last["thread_id"], last["turn_id"])
        return records, total


class SymphonyLeases(SQLiteSource):
    source = "symphony_registry"

    def read(self, db):
        require_schema(db, "leases", LEASE_SCHEMA)
        require_schema(db, "events", EVENT_SCHEMA)
        total = db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
        records, cursor = [], None
        allowed = {"running", "cancel_requested", "uncertain", "finished", "failed"}
        while len(records) < min(total, self.max_records):
            params, where = [], ""
            if cursor is not None:
                where = " WHERE (created, id) < (?, ?)"
                params.extend(cursor)
            params.append(min(self.page_size, self.max_records - len(records)))
            rows = db.execute("SELECT id, status, created FROM leases" + where + " ORDER BY created DESC, id DESC LIMIT ?", params).fetchall()
            if not rows:
                break
            for row in rows:
                if row["status"] not in allowed:
                    raise SourceError("schema_drift")
                # Lifecycle events are transitions, never periodic heartbeats.
                event = db.execute("SELECT sequence, at FROM events WHERE id=? ORDER BY sequence DESC LIMIT 1", (row["id"],)).fetchone()
                if event is not None and (type(event["sequence"]) is not int or event["sequence"] < 1):
                    raise SourceError("invalid_record")
                records.append({"key": identity(row["id"]), "record_at": timestamp(event["at"] if event else row["created"]),
                                "source_event_sequence": event["sequence"] if event else None, "state": row["status"], "liveness": "unknown"})
            last = rows[-1]
            cursor = (last["created"], last["id"])
        return records, total


class SystemdService:
    source, capability = "controller_service", "service_process"
    PROPERTIES = ("LoadState", "ActiveState", "SubState", "MainPID")

    def __init__(self, unit: str | None, *, user: bool = False, timeout: float = 3.0, runner=subprocess.run):
        if unit is not None and not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}\.service", unit):
            raise ValueError("invalid unit")
        if type(user) is not bool or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 10:
            raise ValueError("invalid service budget")
        self.unit, self.user, self.timeout, self.runner = unit, user, timeout, runner

    def collect(self, now: int) -> Observation:
        if not self.unit:
            return Observation(self.source, now, "unconfigured", self.capability, error_code="not_configured")
        command = ["/usr/bin/systemctl", "show", "--no-pager", "--property=" + ",".join(self.PROPERTIES)]
        if self.user:
            command.append("--user")
        command.append(self.unit)
        try:
            result = self.runner(command, capture_output=True, text=True, timeout=self.timeout, check=False)
            if result.returncode or len(result.stdout) > 4096:
                raise SourceError("source_unavailable")
            values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if set(values) != set(self.PROPERTIES) or values["LoadState"] != "loaded":
                raise SourceError("source_unavailable")
            pid = int(values["MainPID"])
            active = values["ActiveState"]
            if active == "active" and values["SubState"] == "running" and pid > 0:
                state = "running"
            elif active in {"inactive", "failed"} and pid == 0:
                state = "stopped"
            else:
                state = "unknown"
            return Observation(self.source, now, "ok", self.capability, complete=True, service_state=state)
        except (SourceError, OSError, ValueError, subprocess.SubprocessError):
            return Observation(self.source, now, "unavailable", self.capability, error_code="source_unavailable")


class ProcessIdentity:
    """An explicit private PID + kernel start-tick binding; no process discovery.

    This proves a matching process exists, not useful execution or connectivity.
    Caller is responsible for a current authorized session/owner mapping.
    """
    source, capability = "process_identity", "service_process"

    def __init__(self, pid: int | None = None, start_ticks: int | None = None, *, proc_root: Path = Path("/proc")):
        if (pid is None) != (start_ticks is None):
            raise ValueError("incomplete process identity")
        if pid is not None and (type(pid) is not int or type(start_ticks) is not int or pid < 1 or start_ticks < 1):
            raise ValueError("invalid process identity")
        self.pid, self.start_ticks, self.proc_root = pid, start_ticks, proc_root

    def collect(self, now: int) -> Observation:
        if self.pid is None:
            return Observation(self.source, now, "unconfigured", self.capability, error_code="not_configured")
        try:
            # The comm field may contain spaces/parentheses; use its last ')'.
            with (self.proc_root / str(self.pid) / "stat").open("r", encoding="utf-8") as handle:
                stat = handle.read(8193)
            if len(stat) > 8192:
                raise ValueError("oversize stat")
            right = stat.rfind(")")
            fields = stat[right + 2:].split()
            if right < 0 or len(fields) < 20:
                raise ValueError("invalid stat")
            matches = int(fields[19]) == self.start_ticks
            state = "running" if matches and fields[0] not in {"Z", "X"} else "stopped"
            return Observation(self.source, now, "ok", self.capability, complete=True, service_state=state)
        except FileNotFoundError:
            return Observation(self.source, now, "ok", self.capability, complete=True, service_state="stopped")
        except (OSError, ValueError, IndexError):
            return Observation(self.source, now, "unavailable", self.capability, error_code="source_unavailable")


class UnconfiguredCloud:
    source = "cloud_feed"

    def collect(self, now: int) -> Observation:
        return Observation(self.source, now, "unconfigured", "none", error_code="not_configured")
