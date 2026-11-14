"""Private, single-host claim persistence, never a work selector or queue.

The stable lock, expected-byte digest, file fsync, atomic replacement and
directory fsync follow the independently reviewed OwnerAuthority primitives at
UIF PR42 e40103c55f749a3a2835f8d14ad7f10ab87a5a3d, owner_runtime.py:158-205.
This implementation deliberately imports neither that ledger nor its coordinator.
All clients must reach the same Foo-hosted authority. No distributed-lock or
host-outage recovery guarantee is implied.
"""

from contextlib import contextmanager
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024


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
    def transaction(self):
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

            def save():
                nonlocal expected
                require(self._read("registration.json")[1] == registration_digest, "registration_drift")
                require(self._read("state.json")[1] == expected, "state_drift")
                raw = wire(state)
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
