"""UIF validation over the original Windmill job and bounded runner receipt.

Ports the retained Stokowski Journal's lock, prepare, inspect-before-submit
and durable intent semantics from OffMenu 5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e.
The selected UIF route supplies the approved command, capacity and runner pins.
"""

import asyncio
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import time
from uuid import UUID

from .source import SourceHold, exact_path, file_digest, git, oid, verify_checkout
from .ledger import RowKey
from .stokowski import DispatchReportBinding, UIF


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def identity(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def invocation_uuid(value):
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def decode(raw):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise SourceHold("uif_stage_duplicate_json_key")
        return result

    def invalid_constant(_):
        raise SourceHold("uif_stage_nonfinite_json")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def read_evidence(path, *, uid, limit):
    """Read a stable original evidence file in the registered backend's custody."""
    path = exact_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != uid or before.st_mode & 0o022 or before.st_size > limit):
            raise SourceHold("uif_stage_evidence_custody")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
        if len(raw) != before.st_size or any(
            (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            for item in (after, current)
        ):
            raise SourceHold("uif_stage_evidence_drift")
        return raw


@dataclass(frozen=True)
class StageBinding:
    dispatch: DispatchReportBinding
    candidate: Path
    base: str
    head: str
    tree: str
    branch: str
    coding_receipt: Path
    coding_receipt_sha256: str

    def __post_init__(self):
        admission = self.dispatch.ledger
        issue = self.dispatch.issue
        if (admission is None or admission.original_operation_id != self.dispatch.operation_id
                or (self.task is None and (issue is None
                    or (issue.project_id, issue.project_slug) != (UIF.project_id, UIF.project_slug)))
                or (self.task is not None and issue is not None)
                or not all(oid(value) for value in (self.base, self.head, self.tree))
                or not all(digest(value) for value in (self.dispatch.receipt_sha256, self.coding_receipt_sha256))):
            raise SourceHold("uif_stage_dispatch_binding")
        admission.require_implementation()
        runtime = exact_path(self.dispatch.workspace).parent.parent
        if not all(exact_path(path).is_relative_to(runtime) for path in (
            self.candidate, self.coding_receipt, self.dispatch.receipt_path
        )):
            raise SourceHold("uif_stage_outside_task_runtime")
        if self.task is not None:
            from .ledger import verify_ledger_task_admission

            verify_ledger_task_admission(self.task, admission)
            if (self.dispatch.workspace != runtime / "workspaces" / self.workspace_key
                    or self.dispatch.receipt_path != runtime / "attempts" / self.workspace_key / "dispatch-intent.json"
                    or self.candidate not in (
                        self.dispatch.workspace, runtime / "contributions" / self.workspace_key / "candidate",
                    )):
                raise SourceHold("uif_stage_ledger_paths")

    @property
    def task(self):
        return getattr(self.dispatch, "task", None)

    @property
    def workspace_key(self):
        if self.task is not None:
            return self.task.key.workspace_key
        return self.dispatch.issue.identifier

    @property
    def row(self):
        admission = self.dispatch.ledger
        return RowKey(admission.store_id, admission.row_id)

    def key(self, stage):
        return sha(wire([stage, self.row.store_id, self.row.row_id, self.dispatch.operation_id]))

    def verify(self):
        if self.task is not None:
            from .dispatch import read_outcome

            outcome = read_outcome(self.dispatch)
            if (outcome.status != "succeeded" or outcome.operation_id != self.dispatch.operation_id
                    or (outcome.worker_receipt_path, outcome.worker_receipt_sha256)
                    != (self.coding_receipt, self.coding_receipt_sha256)):
                raise SourceHold("uif_stage_ledger_original_coding")
        coding = read_evidence(self.coding_receipt, uid=os.geteuid(), limit=1048576)
        if (file_digest(self.dispatch.receipt_path) != self.dispatch.receipt_sha256
                or sha(coding) != self.coding_receipt_sha256):
            raise SourceHold("uif_stage_original_receipt_drift")
        receipt = decode(coding)
        parent = receipt.get("parent_binding")
        outputs = receipt.get("output_sha256")
        if (receipt.get("origin") != "native-coding" or receipt.get("status") != "succeeded"
                or receipt.get("child_tree_gone") is not True or receipt.get("scope_violations") != []
                or receipt.get("source_head") != self.base
                or not isinstance(parent, dict) or parent.get("owner") != self.dispatch.ledger.owner
                or not isinstance(outputs, dict) or not outputs):
            raise SourceHold("uif_stage_coding_receipt_binding")
        verify_checkout(self.candidate, self.head, self.tree, self.branch)
        git(self.candidate, "merge-base", "--is-ancestor", self.base, self.head)
        for name, expected in outputs.items():
            relative = PurePosixPath(name)
            if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                    or relative.as_posix() != name or relative.parts[0] == ".git"
                    or not digest(expected) or file_digest(self.candidate / name) != expected):
                raise SourceHold("uif_stage_coding_output_binding")


@dataclass(frozen=True)
class ValidationSpec:
    binding: StageBinding
    operation_key: str
    windmill_job_id: str
    runner_job_id: str
    args_json: bytes
    runner_sha256: str
    capacity_policy_sha256: str
    first_submission: bool = False

    def __post_init__(self):
        args = decode(self.args_json)
        if (not invocation_uuid(self.windmill_job_id)
                or not isinstance(self.runner_job_id, str)
                or re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.runner_job_id) is None
                or not all(digest(v) for v in (self.operation_key, self.runner_sha256, self.capacity_policy_sha256))
                or (self.binding.task is not None and self.operation_key != self.binding.key("validation"))
                or type(self.first_submission) is not bool
                or not isinstance(args, dict) or wire(args) != self.args_json
                or args.get("job_id") != self.runner_job_id or args.get("action") != "run"
                or not identity(args.get("command"))):
            raise SourceHold("uif_validation_runner_binding")

    @property
    def command_sha256(self):
        return sha(decode(self.args_json)["command"].encode())


@dataclass(frozen=True)
class StageResult:
    status: str
    receipt_sha256: str | None = None


@dataclass(frozen=True)
class ValidationEvidence:
    binding: StageBinding
    operation_key: str
    windmill_job_id: str
    runner_job_id: str
    receipt_sha256: str
    receipt: bytes


@dataclass(frozen=True)
class _Observation:
    status: str
    receipt: bytes | None = None


class StageJournal:
    """Use the task runtime's operation journal, without allocating work."""

    def __init__(self, root):
        self.root = exact_path(root)
        self._private(self.root.parent)
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.path = self.root / "operations.sqlite"
        self._private(self.root)
        fd = os.open(self.path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY,spec TEXT NOT NULL,state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY,key TEXT NOT NULL,event TEXT NOT NULL,at REAL NOT NULL)")

    @staticmethod
    def _private(path):
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
            raise SourceHold("uif_stage_journal_custody")

    @contextmanager
    def _db(self):
        self._private(self.path)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            yield db

    @contextmanager
    def lock(self, key):
        fd = os.open(self.root / (key + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SourceHold("uif_stage_tick_already_owned") from None
            yield
        finally:
            os.close(fd)

    def read(self, key, pin):
        self._private(self.path)
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            row = db.execute("SELECT spec,state FROM operations WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        if row[0] != pin:
            raise SourceHold("uif_stage_original_operation_rebind")
        return decode(row[1])

    def save(self, key, pin, status, receipt=None):
        state = dict(status=status, receipt=receipt.decode() if receipt is not None else None,
                     receipt_sha256=sha(receipt) if receipt is not None else None)
        with self._db() as db:
            result = db.execute(
                "INSERT INTO operations VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET state=excluded.state "
                "WHERE operations.spec=excluded.spec", (key, pin, wire(state).decode())
            )
            if result.rowcount != 1:
                raise SourceHold("uif_stage_original_operation_rebind")
            db.execute("INSERT INTO events(key,event,at) VALUES (?,?,?)", (key, status, time.time()))
        return StageResult(status, state["receipt_sha256"])


_TERMINAL = {"succeeded", "failed", "rework", "quarantined"}


def stage_spec_record(spec):
    """Serialize the existing issue spec or the tagged ledger spec."""
    record = asdict(spec)
    if spec.binding.task is not None:
        validation = record if isinstance(spec, ValidationSpec) else record["validation"]
        validation["binding"]["dispatch"] = spec.binding.dispatch.to_record()
    return record


class _Stage:
    def __init__(self, journal, fence, backend):
        self.journal = journal
        self.fence = fence
        self.backend = backend

    def _pin(self, spec):
        record = dict(stage=self.stage, spec=stage_spec_record(spec), backend=self.backend.registration)
        if spec.binding.task is not None:
            record.update(schema="uif-stage/2", workspace_key=spec.binding.workspace_key)
            if self.stage == "review":
                record["invocation_id"] = spec.invocation_id
        return json.dumps(record, sort_keys=True, allow_nan=False,
                          default=lambda value: value.decode() if isinstance(value, bytes) else str(value))

    def _journal_key(self, spec):
        if self.journal.root != spec.binding.dispatch.workspace.parent.parent / "operations":
            raise SourceHold("uif_stage_journal_runtime_binding")
        return spec.binding.key(self.stage)

    def _accepted(self, spec):
        row = self.journal.read(self._journal_key(spec), self._pin(spec))
        if not row or row["status"] != "succeeded":
            raise SourceHold("uif_stage_acceptance_pending")
        raw = row["receipt"].encode() if isinstance(row["receipt"], str) else None
        if raw is None or sha(raw) != row["receipt_sha256"]:
            raise SourceHold("uif_stage_receipt_digest")
        return raw, row["receipt_sha256"]

    def _qualify(self, spec):
        spec.binding.verify()

    async def tick(self, spec):
        key, pin = self._journal_key(spec), self._pin(spec)
        # The owner supplies its existing async admission fence for the full
        # selection, including authorization for an initial stage effect.
        async with self.fence(self.stage, spec):
            with self.journal.lock(key):
                self._qualify(spec)
                row = self.journal.read(key, pin)
                if row is None:
                    self.journal.save(key, pin, "prepared")
                    status = "prepared"
                else:
                    status = row["status"]
                if status in _TERMINAL:
                    if status == "succeeded":
                        self._accepted(spec)
                    return StageResult(status, row["receipt_sha256"])
                try:
                    observed = await self.backend.inspect(spec)
                except SourceHold:
                    self.journal.save(key, pin, "quarantined")
                    raise
                except Exception:
                    return self.journal.save(key, pin, "unknown")
                self._qualify(spec)
                if observed is not None:
                    return self.journal.save(key, pin, observed.status, observed.receipt)
                if status != "prepared" or not spec.first_submission:
                    return self.journal.save(key, pin, "unknown")
                self.journal.save(key, pin, "submit-intent")
                try:
                    await self.backend.submit(spec)
                except SourceHold:
                    raise
                except asyncio.CancelledError:
                    self.journal.save(key, pin, "unknown")
                    raise
                except Exception:
                    # A later tick may only inspect this original job.
                    return self.journal.save(key, pin, "unknown")
                return self.journal.save(key, pin, "submitted")


class WindmillValidationBackend:
    """Use a registered Windmill API client and, if supplied, the original Foo receipt."""

    def __init__(self, client, *, workspace, script_path, runner_receipts=None, runner_uid=None,
                 inspect_version=None):
        if (re.fullmatch(r"[a-zA-Z0-9_-]+", workspace) is None
                or re.fullmatch(r"(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+", script_path) is None
                or (runner_receipts is not None and (type(runner_uid) is not int or runner_uid < 0))):
            raise SourceHold("uif_validation_route_binding")
        self.client = client
        self.workspace = workspace
        self.script_path = script_path
        self.runner_receipts = exact_path(runner_receipts) if runner_receipts is not None else None
        self.runner_uid = runner_uid
        self.inspect_version = inspect_version
        self.registration = dict(workspace=workspace, script_path=script_path, runner_receipts=self.runner_receipts,
                                 runner_uid=runner_uid, inspect_version=inspect_version)

    def _receipt(self, spec, raw):
        receipt = decode(raw)
        if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in {
            "job_id": spec.runner_job_id, "command_sha256": spec.command_sha256,
            "runner_sha256": spec.runner_sha256, "capacity_policy_sha256": spec.capacity_policy_sha256,
        }.items()):
            return _Observation("quarantined")
        status = receipt.get("status")
        if receipt.get("child_tree_gone") is not True or receipt.get("reconcile_required") is not False:
            return _Observation("unknown")
        if status == "succeeded" and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0:
            return _Observation("succeeded", raw)
        if status in ("failed", "timed_out", "cancelled"):
            return _Observation("failed", raw)
        return _Observation("unknown")

    def _original(self, spec):
        if self.runner_receipts is None:
            return None
        path = self.runner_receipts / spec.runner_job_id / "receipt.json"
        try:
            raw = read_evidence(path, uid=self.runner_uid, limit=65536)
        except FileNotFoundError:
            if spec.binding.task is not None and os.path.lexists(path.parent):
                return _Observation("unknown")
            return None
        observed = self._receipt(spec, raw)
        # Preserve evidence of a prior effect without qualifying the receipt
        # until the original Windmill invocation also binds it.
        if spec.binding.task is not None and observed.status != "quarantined":
            return _Observation("unknown")
        return observed

    async def inspect(self, spec):
        response = await self.client.get(f"/w/{self.workspace}/jobs_u/get/{spec.windmill_job_id}",
                                         follow_redirects=False)
        if response.status_code == 404:
            return self._original(spec)
        response.raise_for_status()
        raw = response.content
        if len(raw) > 1048576:
            return _Observation("unknown")
        job = decode(raw)
        if not isinstance(job, dict):
            return _Observation("quarantined")
        compact = "inspect_version" in job
        if (compact != (self.inspect_version is not None)
                or job.get("id") != spec.windmill_job_id or job.get("workspace_id") != self.workspace
                or job.get("script_path") != self.script_path
                or (compact and ("args" in job or self.inspect_version is None
                                 or type(job.get("inspect_version")) is not type(self.inspect_version)
                                 or job.get("inspect_version") != self.inspect_version
                                 or job.get("args_sha256") != sha(spec.args_json)))
                or (not compact and ("args_sha256" in job or wire(job.get("args")) != spec.args_json))):
            return _Observation("quarantined")
        if job.get("type") == "QueuedJob" and type(job.get("running")) is bool and job.get("success") is None:
            return self._original(spec) or _Observation("running" if job["running"] else "queued")
        if job.get("type") != "CompletedJob" or job.get("running", False) is not False or type(job.get("success")) is not bool:
            return _Observation("unknown")
        result = job.get("result")
        if isinstance(result, str):
            try:
                result = decode(result)
            except ValueError:
                result = None
        if not isinstance(result, dict):
            return self._original(spec) or _Observation("unknown")
        if "receipt" in result:
            receipt = result["receipt"]
            if not isinstance(receipt, dict):
                return self._original(spec) or _Observation("unknown")
        elif (self.workspace, self.script_path) == ("foo", "u/admin/foo_jobs") and "namespace" in result:
            request_sha256 = decode(spec.args_json).get("request_sha256")
            if (result["namespace"] != "uif-foo-user-job/1" or result.get("workspace") != self.workspace
                    or not digest(request_sha256) or result.get("request_sha256") != request_sha256):
                return _Observation("quarantined")
            receipt = result
        else:
            return self._original(spec) or _Observation("unknown")
        if result.get("job_id") != spec.runner_job_id:
            return _Observation("quarantined")
        observed = self._receipt(spec, wire(receipt))
        if observed.status == "succeeded":
            if (result.get("job_succeeded") is not True
                    or ((receipt is result or spec.binding.task is not None) and job["success"] is not True)):
                return _Observation("unknown")
        return observed

    async def submit(self, spec):
        response = await self.client.post(f"/w/{self.workspace}/jobs/run/p/{self.script_path}",
                                          params={"job_id": spec.windmill_job_id},
                                          json=decode(spec.args_json), follow_redirects=False)
        response.raise_for_status()
        if response.text.strip().strip('"') != spec.windmill_job_id:
            raise SourceHold("uif_validation_submit_identity")


class ValidationAdapter(_Stage):
    stage = "validation"

    async def accepted(self, spec):
        async with self.fence(self.stage, spec):
            self._qualify(spec)
            raw, receipt_sha256 = self._accepted(spec)
            return ValidationEvidence(spec.binding, spec.operation_key, spec.windmill_job_id,
                                      spec.runner_job_id, receipt_sha256, raw)
