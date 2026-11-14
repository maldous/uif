"""Private, single-host claim persistence, never a work selector or queue.

The stable lock, expected-byte digest, file fsync, atomic replacement and
directory fsync follow the independently reviewed OwnerAuthority primitives at
UIF PR42 e40103c55f749a3a2835f8d14ad7f10ab87a5a3d, owner_runtime.py:158-205.
This implementation deliberately imports neither that ledger nor its coordinator.
All clients must reach the same Foo-hosted authority. No distributed-lock or
host-outage recovery guarantee is implied.
"""

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import stat
import sqlite3
import tempfile
from urllib.parse import quote

MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024
HOT_REQUESTS = 256
HOT_EVENTS = 256
CONTROL_RESERVE_BYTES = 1024 * 1024
COLD_SCHEMA = "uif-quota-cold-archive/1"
TERMINAL_OPERATIONS = {"verified", "failed", "denied"}


class Hold(ValueError):
    """A bounded refusal; callers retain original operation identity."""


def require(condition, reason):
    if not condition:
        raise Hold(reason)


def wire(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode()
    except (ValueError, TypeError):
        raise Hold("invalid_json_value") from None


def digest(value):
    return sha256(wire(value)).hexdigest()


def decode(raw, limit=MAX_REQUEST_BYTES):
    require(isinstance(raw, bytes) and len(raw) <= limit, "input_size")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(Hold("nonfinite_json")))
        require(isinstance(value, dict), "object_required")
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise Hold("invalid_json") from None


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class PrivateStore:
    """One private owner, one existing stable lock, atomic whole-record updates."""

    def __init__(self, root):
        self.root = Path(root)
        require(self.root.is_absolute() and self.root == self.root.resolve(), "registry_path")
        self.uid = os.geteuid()
        self._directory()

    def _directory(self):
        info = self.root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.uid
                and stat.S_IMODE(info.st_mode) == 0o700, "registry_custody")
        return info

    def _read(self, name):
        path = self.root / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid == self.uid
                    and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                    and before.st_size <= MAX_STATE_BYTES, "registry_file_custody")
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
            after = os.fstat(fd)
            current = path.lstat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(identity(before) == identity(after) == identity(current), "registry_file_drift")
            return decode(raw, MAX_STATE_BYTES), sha256(raw).hexdigest()
        finally:
            os.close(fd)

    @contextmanager
    def _cold(self, *, create=False, readonly=False):
        """Same-authority archival storage; read-only calls never initialize it.

        Only previously committed JSON records enter these tables. The cold
        commit precedes hot pruning, so an interrupted prune leaves duplicates
        with identical contents, never a fabricated completed operation.
        """
        self._directory()
        path = self.root / "cold-archive.sqlite3"
        try:
            if create and not path.exists():
                self._initialize_cold(path)
            try:
                before = path.lstat()
            except FileNotFoundError:
                yield None
                return
            require(stat.S_ISREG(before.st_mode) and before.st_uid == self.uid
                    and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600,
                    "archive_custody")
            mode = "ro" if readonly else "rw"
            connection = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=" + mode,
                                         uri=True, timeout=0, isolation_level=None)
            try:
                if not readonly:
                    connection.execute("PRAGMA synchronous=FULL")
                binding = connection.execute("SELECT schema,registration_sha256,archive_revision FROM metadata").fetchall()
                require(len(binding) == 1 and binding[0][:2] == (COLD_SCHEMA, self._read("registration.json")[1])
                        and type(binding[0][2]) is int and binding[0][2] >= 0, "archive_registration_binding")
                current = path.lstat()
                require((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino), "archive_file_drift")
                yield connection
            finally:
                connection.close()
        except sqlite3.Error:
            raise Hold("archive_unavailable") from None

    def _initialize_cold(self, path):
        """Publish a complete empty schema, never a half-created archive file."""
        fd, name = tempfile.mkstemp(dir=self.root, prefix=".cold-archive-")
        os.close(fd)
        temporary = Path(name)
        try:
            connection = sqlite3.connect(temporary, isolation_level=None)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE metadata (schema TEXT NOT NULL, registration_sha256 TEXT NOT NULL, archive_revision INTEGER NOT NULL)")
                connection.execute("CREATE TABLE requests (identity TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result BLOB NOT NULL)")
                connection.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, record BLOB NOT NULL)")
                connection.execute("CREATE TABLE tasks (identity TEXT NOT NULL, generation INTEGER NOT NULL, record BLOB NOT NULL, PRIMARY KEY(identity,generation))")
                connection.execute("CREATE TABLE task_heads (identity TEXT PRIMARY KEY, generation INTEGER NOT NULL)")
                connection.execute("INSERT INTO metadata VALUES (?,?,0)", (COLD_SCHEMA, self._read("registration.json")[1]))
                connection.commit()
            finally:
                connection.close()
            fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            require(not path.exists() and not path.is_symlink(), "archive_already_exists")
            # The existing authority lock serializes all legitimate writers.
            # One rename avoids a crash-visible multiply-linked SQLite inode.
            os.replace(temporary, path)
            sync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)

    def request_receipt(self, identity):
        with self._cold() as connection:
            if connection is None:
                return None
            row = connection.execute("SELECT fingerprint,result FROM requests WHERE identity=?", (identity,)).fetchone()
            return None if row is None else dict(sha256=row[0], result=decode(row[1], MAX_REQUEST_BYTES))

    def archived_task(self, identity, *, readonly=False):
        with self._cold(readonly=readonly) as connection:
            if connection is None:
                return None
            row = connection.execute("SELECT t.generation,t.record FROM task_heads h JOIN tasks t ON t.identity=h.identity AND t.generation=h.generation WHERE h.identity=?", (identity,)).fetchone()
            if row is None:
                return None
            task = decode(row[1], MAX_STATE_BYTES)
            require(self._terminal(task) and task.get("generation") == row[0]
                    and ":".join(task["key"][part] for part in ("workspace_id", "project_id", "issue_id")) == identity,
                    "archive_task_binding")
            return task

    @staticmethod
    def _terminal(task):
        def known(attempt):
            return all(operation.get("status") in TERMINAL_OPERATIONS
                       for operation in attempt.get("operations", {}).values())
        return (task.get("state") in {"complete", "failed"}
                and isinstance(task.get("completion"), dict)
                and known(task) and all(known(attempt) for attempt in task.get("attempts", [])))

    @staticmethod
    def require_headroom(state, growth=MAX_REQUEST_BYTES, *, active_delta=0):
        """Reserve completion/control space before new work; never gate repair.

        This is an admission bound, not a promise under disk failure. Receipts,
        stop, quota observation and original-operation reconciliation can still
        commit while new claims/effect intents are held.
        """
        require(not state.get("archive_degraded"), "registry_archive_degraded")
        active = [task for task in state["tasks"].values() if task["state"] in {"active", "handoff-pending"}]
        uncertain = sum(operation["status"] not in TERMINAL_OPERATIONS
                        for task in active for operation in task["operations"].values())
        reserve = max(CONTROL_RESERVE_BYTES, (len(active) + active_delta) * (MAX_REQUEST_BYTES + 1024)
                      + uncertain * 512)
        require(len(wire(state)) + growth <= MAX_STATE_BYTES - reserve, "registry_admission_headroom")

    def _compact(self, committed, state):
        """Archive old committed bytes only, then return a pruned hot snapshot."""
        hot = deepcopy(state)
        requests = sorted((key for key in hot["requests"] if key in committed["requests"]
                           and hot["requests"][key] == committed["requests"][key]),
                          key=lambda key: hot["requests"][key]["result"]["revision"])
        cold_requests = {key: committed["requests"][key]
                         for key in requests[:max(0, len(hot["requests"]) - HOT_REQUESTS)]}
        retained_revisions = {event["revision"] for event in hot["events"][-HOT_EVENTS:]}
        cold_events = [event for event in committed["events"] if event["revision"] not in retained_revisions
                       and event in hot["events"]]
        cold_tasks = {key: task for key, task in committed["tasks"].items()
                      if self._terminal(task) and hot["tasks"].get(key) == task}
        if not cold_requests and not cold_events and not cold_tasks:
            return hot
        with self._cold(create=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for identity, receipt in cold_requests.items():
                    values = (receipt["sha256"], wire(receipt["result"]))
                    prior = connection.execute("SELECT fingerprint,result FROM requests WHERE identity=?", (identity,)).fetchone()
                    require(prior is None or prior == values, "archive_request_conflict")
                    connection.execute("INSERT OR IGNORE INTO requests VALUES (?,?,?)", (identity, *values))
                for event in cold_events:
                    raw = wire(event)
                    prior = connection.execute("SELECT record FROM events WHERE revision=?", (event["revision"],)).fetchone()
                    require(prior is None or prior == (raw,), "archive_event_conflict")
                    connection.execute("INSERT OR IGNORE INTO events VALUES (?,?)", (event["revision"], raw))
                for identity, task in cold_tasks.items():
                    raw, generation = wire(task), task["generation"]
                    prior = connection.execute("SELECT record FROM tasks WHERE identity=? AND generation=?", (identity, generation)).fetchone()
                    require(prior is None or prior == (raw,), "archive_task_conflict")
                    connection.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?)", (identity, generation, raw))
                    connection.execute("INSERT INTO task_heads VALUES (?,?) ON CONFLICT(identity) DO UPDATE SET generation=MAX(generation,excluded.generation)", (identity, generation))
                connection.execute("UPDATE metadata SET archive_revision=archive_revision+1")
                archive_revision = connection.execute("SELECT archive_revision FROM metadata").fetchone()[0]
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        for identity in cold_requests:
            del hot["requests"][identity]
        cold_revisions = {event["revision"] for event in cold_events}
        hot["events"] = [event for event in hot["events"] if event["revision"] not in cold_revisions]
        for identity in cold_tasks:
            del hot["tasks"][identity]
        hot["schema"] = "uif-quota-authority/2"
        hot["cold_archive"] = dict(schema=COLD_SCHEMA, registration_sha256=self._read("registration.json")[1],
                                   minimum_revision=archive_revision)
        return hot

    @contextmanager
    def task_fence(self, identity):
        """Serialize one task's effects/transfer without blocking other tasks."""
        self._directory()
        path = self.root / ("task-" + digest(identity) + ".lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid == self.uid
                    and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600, "task_lock_custody")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Hold("task_effect_busy") from None
            current = path.lstat()
            require((before.st_dev, before.st_ino) == (current.st_dev, current.st_ino), "task_lock_drift")
            yield
        finally:
            os.close(fd)

    @contextmanager
    def transaction(self, *, readonly=False):
        original_directory = self._directory()
        path = self.root / "authority.lock"
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            require(stat.S_ISREG(opened.st_mode) and opened.st_uid == self.uid
                    and opened.st_nlink == 1 and stat.S_IMODE(opened.st_mode) == 0o600,
                    "registry_lock_custody")
            # A concurrent caller retries the same request. Never block a tool
            # indefinitely while a guarded external effect is still executing.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Hold("authority_busy") from None
            current = path.lstat()
            require((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino), "registry_lock_drift")
            current_directory = self._directory()
            require((original_directory.st_dev, original_directory.st_ino) ==
                    (current_directory.st_dev, current_directory.st_ino), "registry_directory_drift")
            registration, registration_digest = self._read("registration.json")
            state, expected = self._read("state.json")
            if "cold_archive" in state:
                marker = state["cold_archive"]
                require(isinstance(marker, dict) and set(marker) == {"schema", "registration_sha256", "minimum_revision"}
                        and marker["schema"] == COLD_SCHEMA and marker["registration_sha256"] == registration_digest
                        and type(marker["minimum_revision"]) is int and marker["minimum_revision"] > 0,
                        "archive_registration_binding")
                with self._cold(readonly=readonly) as archive:
                    require(archive is not None, "required_archive_unavailable")
                    require(archive.execute("SELECT archive_revision FROM metadata").fetchone()[0] >= marker["minimum_revision"],
                            "archive_revision_rollback")
            committed = deepcopy(state)

            def save():
                nonlocal expected, committed
                require(not readonly, "readonly_transaction")
                require(self._read("registration.json")[1] == registration_digest, "registration_drift")
                require(self._read("state.json")[1] == expected, "state_drift")
                try:
                    compacted = self._compact(committed, state)
                    compacted.pop("archive_degraded", None)
                except (OSError, Hold) as error:
                    if isinstance(error, Hold) and str(error) != "archive_unavailable":
                        raise
                    # Keep every record hot if archival I/O fails. Reserved
                    # space lets control and terminal receipts proceed; this
                    # cannot promise writes after the filesystem itself fills.
                    compacted = deepcopy(state)
                    compacted["archive_degraded"] = "archive_write_unavailable"
                raw = wire(compacted)
                require(len(raw) <= MAX_STATE_BYTES, "registry_full")
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.root, prefix=".state-", delete=False) as stream:
                        temporary = Path(stream.name)
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.root / "state.json")
                    sync_directory(self.root)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                require(self._read("state.json")[1] == sha256(raw).hexdigest(), "state_readback")
                expected = sha256(raw).hexdigest()
                state.clear()
                state.update(compacted)
                committed = deepcopy(compacted)

            yield registration, state, save
        finally:
            os.close(fd)

    @classmethod
    def initialize(cls, root, registration, state):
        """Explicit one-time provisioning; never overwrite/recreate an old store."""
        path = Path(root)
        require(path.is_absolute() and path == path.resolve(), "registry_path")
        path.mkdir(mode=0o700, exist_ok=False)
        for name, raw in (("authority.lock", b""), ("registration.json", wire(registration)),
                          ("state.json", wire(state))):
            with os.fdopen(os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600), "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        sync_directory(path)
        sync_directory(path.parent)
        return cls(path)
