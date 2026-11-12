"""Fixed per-attempt UIF launch helper and controller adapter.

The installed entrypoint requires root custody, an exact sudo caller and a
qualified cgroup v2 subtree. Source tests do not invoke that entrypoint as root.
"""

from contextlib import ExitStack, nullcontext
from dataclasses import asdict, dataclass
import ctypes
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import select
import selectors
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from uuid import UUID


POLICY = Path("/etc/uif/role-launch.json")
SUDO = "/usr/bin/sudo"
SCHEMA = "uif-role-launch/1"
LEDGER_SCHEMA = "uif-role-launch/2"
LEDGER_REVIEW_ORIGIN = "native-readonly-review-v2"
ATTEMPT_ISOLATION = "uif-role-attempt-namespaces/1"
CANCEL_BEFORE_START = "cancel-before-start"
MODES = {"coding": "workspace-write", "review": "read-only"}
STATES = {"absent", "prepared", "running", "exited-clean", "refused-before-fork", "uncertain"}
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
MAX_RECORD = 262144
LOG_LIMIT = 1_048_576
NEUTRAL_SCHEMA = "uif-neutral-role-launch/1"
NEUTRAL_NAMESPACE = "uif-foo-user-job/1"
NEUTRAL_BINDING = {"namespace", "workspace", "job_id", "descriptor_sha256", "request_sha256",
                   "command_sha256", "mode", "timeout_seconds", "resources", "capacity_policy_sha256",
                   "runner_sha256"}
NEUTRAL_LIMITS = {"workers", "overhead_processes", "cpu_millis", "memory_bytes", "output_bytes", "io_slots"}
NEUTRAL_OUTCOMES = {"succeeded", "failed", "timed_out", "cancelled"}


class LaunchHold(ValueError):
    """The exact launch must be held or reconciled."""


class _CloneRefused(LaunchHold):
    """clone3 returned an error, so this call created no child."""



def _startup_error(error, phase):
    # Never persist exception messages containing paths, command bytes or data.
    value = {"phase": phase, "kind": type(error).__name__}
    if isinstance(error, OSError):
        value["errno"] = error.errno
    elif isinstance(error, LaunchHold) and (match := re.fullmatch(
            r"neutral_launch_mount_syscall:(428|429|437|442):([0-9]{1,4})", str(error))):
        value.update(reason_code="neutral_launch_mount_syscall",
                     syscall=int(match[1]), errno=int(match[2]))
    elif (isinstance(error, LaunchHold) and len(str(error)) <= 160
          and re.fullmatch(r"[a-z0-9_]+(?::[0-9]+)?", str(error))):
        value["reason"] = str(error)
    return value



def _sha(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _token(value):
    return isinstance(value, str) and re.fullmatch("[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value) is not None


def _wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return sha256(_wire(value)).hexdigest()


def slot_for_lease(lease_id: str) -> str:
    """Locate one immutable reservation in the controller's registered Pool.

    The Pool lease is retained across observations. Request digests bind what
    was reserved, but cannot select another slot for the same lease.
    """
    if not _token(lease_id):
        raise LaunchHold("role_launch_lease_identity")
    return _digest({"schema": "uif-role-slot/2", "lease_id": lease_id})


@dataclass(frozen=True)
class LaunchKey:
    role: str
    store_id: str
    row_id: str
    row_source_sha256: str
    issue_id: str
    issue_identifier: str
    owner: str
    epoch: str
    operation_id: str
    attempt_id: str
    intent_sha256: str
    lease_id: str

    def __post_init__(self):
        if (self.role not in MODES or not all(_token(getattr(self, field)) for field in (
                "store_id", "row_id", "owner", "epoch", "operation_id", "attempt_id", "lease_id"))
                or not _sha(self.row_source_sha256) or not _sha(self.intent_sha256)
                or not re.fullmatch(r"UIF-[1-9][0-9]{0,11}", self.issue_identifier)
                or str(UUID(self.issue_id)) != self.issue_id):
            raise LaunchHold("role_launch_key")

    @property
    def workspace_key(self):
        return self.issue_identifier


@dataclass(frozen=True)
class _LedgerRoleKey:
    task: dict
    workspace_key: str
    invocation_id: str
    intent_sha256: str
    lease_id: str

    def __post_init__(self):
        from uif.ledger import SNAPSHOT_SHA256
        from uif.task_identity import LedgerTaskKey, SCHEMA as TASK_SCHEMA

        fields = {"schema", "store_id", "row_id", "snapshot_sha256", "source_sha256",
                  "owner", "epoch", "original_operation_id"}
        try:
            invocation_matches = isinstance(self.invocation_id, str) and str(UUID(self.invocation_id)) == self.invocation_id
        except ValueError:
            invocation_matches = False
        if (not isinstance(self.task, dict) or set(self.task) != fields or self.task["schema"] != TASK_SCHEMA
                or any(not isinstance(self.task[field], str) or not self.task[field].strip()
                       or self.task[field] != self.task[field].strip()
                       for field in ("owner", "epoch", "original_operation_id"))
                or self.task["snapshot_sha256"] != SNAPSHOT_SHA256
                or not _sha(self.task["source_sha256"]) or not _sha(self.intent_sha256)
                or LedgerTaskKey(self.store_id, self.task["row_id"]).workspace_key != self.workspace_key
                or not invocation_matches
                or not _token(self.lease_id)):
            raise LaunchHold("role_launch_ledger_key")

    @property
    def store_id(self):
        return self.task["store_id"]

    @property
    def operation_id(self):
        return self.task["original_operation_id"]


@dataclass(frozen=True)
class LedgerLaunchKey(_LedgerRoleKey):
    attempt: int | None
    handoff_sha256: str
    role = "coding"

    def __post_init__(self):
        super().__post_init__()
        if (not _sha(self.handoff_sha256)
                or (self.attempt is not None and (type(self.attempt) is not int or self.attempt < 0))):
            raise LaunchHold("role_launch_ledger_coding_key")


@dataclass(frozen=True)
class LedgerReviewLaunchKey(_LedgerRoleKey):
    role = "review"


@dataclass(frozen=True)
class LaunchRequest:
    key: LaunchKey | LedgerLaunchKey | LedgerReviewLaunchKey
    slot: str
    admission_sha256: str
    request_sha256: str
    projection_sha256: str
    policy_sha256: str

    def __post_init__(self):
        if (not isinstance(self.key, (LaunchKey, LedgerLaunchKey, LedgerReviewLaunchKey)) or not _sha(self.slot)
                or self.slot != slot_for_lease(self.key.lease_id) or not all(_sha(value) for value in (
                    self.admission_sha256, self.request_sha256, self.projection_sha256, self.policy_sha256))):
            raise LaunchHold("role_launch_request")

    @property
    def digest(self):
        return _digest(self.to_record())

    def to_record(self):
        record = asdict(self)
        if isinstance(self.key, _LedgerRoleKey):
            record["schema"] = LEDGER_SCHEMA
            record["role"] = self.key.role
        return record


@dataclass(frozen=True)
class _NeutralKey:
    lease_id: str
    role: str = "neutral"


@dataclass(frozen=True)
class NeutralLaunchRequest:
    schema: str
    binding: dict
    slot: str
    intent_sha256: str
    request_file_sha256: str
    pool_binding_sha256: str
    cpu_ids: tuple[int, ...]

    def __post_init__(self):
        if (self.schema != NEUTRAL_SCHEMA or not isinstance(self.binding, dict)
                or set(self.binding) != NEUTRAL_BINDING
                or (self.binding["namespace"], self.binding["workspace"]) != (NEUTRAL_NAMESPACE, "foo")
                or not isinstance(self.binding["job_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.binding["job_id"])
                or any(not _sha(self.binding[field]) for field in ("descriptor_sha256", "request_sha256",
                           "command_sha256", "capacity_policy_sha256", "runner_sha256"))
                or any(not _sha(pin) for pin in (self.intent_sha256, self.request_file_sha256,
                                                 self.pool_binding_sha256))
                or not isinstance(self.cpu_ids, (tuple, list)) or not self.cpu_ids
                or any(type(cpu) is not int or cpu < 0 for cpu in self.cpu_ids)
                or len(self.cpu_ids) != len(set(self.cpu_ids))
                or self.slot != slot_for_lease(self.key.lease_id)):
            raise LaunchHold("neutral_launch_request")
        object.__setattr__(self, "cpu_ids", tuple(self.cpu_ids))

    @property
    def key(self):
        return _NeutralKey("neutral-" + _digest([NEUTRAL_NAMESPACE, "foo", self.binding["job_id"]]))

    @property
    def digest(self):
        return _digest(asdict(self))


@dataclass(frozen=True)
class LaunchObservation:
    state: str
    request_sha256: str
    receipt_sha256: str | None = None
    exit_code: int | None = None
    output_sha256: str | None = None

    @property
    def release_safe(self):
        return self.state in {"exited-clean", "refused-before-fork"} and _sha(self.receipt_sha256)


@dataclass(frozen=True)
class NeutralObservation(LaunchObservation):
    outcome: str | None = None
    stopped_evidence: dict | None = None
    log_bytes: dict | None = None
    logs_truncated: dict | None = None

    @property
    def containment_stopped(self):
        return super().release_safe

    @property
    def release_safe(self):
        return self.containment_stopped and self.stopped_evidence is not None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LaunchHold("role_launch_duplicate_field")
        result[key] = value
    return result


def _json(data):
    if len(data) > MAX_RECORD:
        raise LaunchHold("role_launch_record_size")
    value = json.loads(data, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise LaunchHold("role_launch_record")
    return value


def _path(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise LaunchHold("role_launch_path")
    return path


def _regular(fd, uid, forbidden):
    info = os.fstat(fd)
    if (info.st_uid != uid or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1 or info.st_mode & forbidden):
        raise LaunchHold("role_launch_file_custody")
    return info


def _read(path, uid, forbidden=0o077, *, durable=False):
    with os.fdopen(os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        _regular(stream.fileno(), uid, forbidden)
        data = stream.read(MAX_RECORD + 1)
        if durable:
            os.fsync(stream.fileno())
        return data


def _record(path, uid, expected=None, forbidden=0o077):
    data = _read(path, uid, forbidden)
    if expected is not None and sha256(data).hexdigest() != expected:
        raise LaunchHold("role_launch_digest")
    return _json(data)


def _dir(path, uid, forbidden):
    info = _path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & forbidden:
        raise LaunchHold("role_launch_directory")
    return path


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _create(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_EXCL | os.O_CREAT, 0o600), "wb") as out:
        out.write(_wire(value))
        out.flush()
        os.fsync(out.fileno())
    _sync(path.parent)


def _installed(path, digest, *, executable):
    path = _path(path)
    for parent in path.parents:
        _dir(parent, 0, 0o022)
    h = sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = _regular(stream.fileno(), 0, 0o6022)
        if executable and not info.st_mode & stat.S_IXUSR:
            raise LaunchHold("role_launch_executable")
        while data := stream.read(1024 * 1024):
            h.update(data)
    if not _sha(digest) or h.hexdigest() != digest:
        raise LaunchHold("role_launch_installed_pin")
    return path


class RoleBroker:
    """Controller-side calls to the installed, pinned one-attempt helper."""

    def __init__(self, helper, helper_sha256):
        self.helper = _installed(helper, helper_sha256, executable=True)
        self.helper_sha256 = helper_sha256

    def start(self, request):
        return self._call("start", request)

    def observe(self, request):
        return self._call("observe", request)

    def cancel(self, request):
        return self._call("cancel", request)

    def _call(self, action, request):
        if not isinstance(request, (LaunchRequest, NeutralLaunchRequest)):
            raise LaunchHold("role_launch_request")
        response_type = NeutralObservation if isinstance(request, NeutralLaunchRequest) else LaunchObservation
        try:
            helper = _installed(self.helper, self.helper_sha256, executable=True)
            result = subprocess.run([SUDO, "-n", "--", str(helper), action, request.slot],
                                    env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, close_fds=True, timeout=12, check=True)
            value = _json(result.stdout)
            if (value.pop("schema") != SCHEMA or value.get("request_sha256") != request.digest
                    or value.get("state") not in STATES):
                raise LaunchHold("role_launch_response_binding")
            observation = response_type(**value)
            stopped = observation.release_safe
            if isinstance(observation, NeutralObservation):
                stopped = observation.containment_stopped
                if stopped and observation.outcome not in NEUTRAL_OUTCOMES:
                    raise LaunchHold("neutral_launch_outcome")
                evidence = observation.stopped_evidence
                if evidence is not None and (not stopped or not isinstance(evidence, dict)
                        or evidence.get("lease_id") != request.key.lease_id
                        or evidence.get("binding_sha256") != _digest(request.binding)
                        or evidence.get("trusted_terminal_sha256") != observation.receipt_sha256
                        or not _sha(evidence.get("runner_receipt_sha256"))):
                    raise LaunchHold("neutral_launch_evidence_binding")
            if (observation.state == "exited-clean" and type(observation.exit_code) is not int
                    or observation.state == "refused-before-fork" and observation.exit_code is not None
                    or (observation.output_sha256 is not None and not _sha(observation.output_sha256))
                    or observation.state in {"exited-clean", "refused-before-fork"}
                    and not stopped):
                raise LaunchHold("role_launch_terminal_response")
            return observation
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return response_type("uncertain", request.digest)


def _request(directory, uid):
    data = _record(directory / "launch.json", uid)
    if data.get("schema") == NEUTRAL_SCHEMA:
        request = NeutralLaunchRequest(**data)
    elif data.get("schema") == LEDGER_SCHEMA:
        del data["schema"]
        role = data.pop("role", None)
        key_type = {"coding": LedgerLaunchKey, "review": LedgerReviewLaunchKey}.get(role)
        if key_type is None:
            raise LaunchHold("role_launch_ledger_key")
        data["key"] = key_type(**data["key"])
        request = LaunchRequest(**data)
    else:
        data["key"] = LaunchKey(**data["key"])
        request = LaunchRequest(**data)
    if request.slot != directory.name:
        raise LaunchHold("role_launch_slot")
    return request


def _check_original(cfg, directory, request):
    """Check the controller's original admission, intent and running lease."""
    if isinstance(request, NeutralLaunchRequest):
        return _check_neutral(cfg, directory, request)
    uid, key = cfg["controller_uid"], request.key
    admission = _record(directory / "admission.json", uid, request.admission_sha256)
    ledger = admission["ledger"]
    if isinstance(key, _LedgerRoleKey):
        admission_matches = (type(admission.get("version")) is int and admission["version"] == 2
                             and not {"issue", "issue_id"} & admission.keys()
                             and ledger == key.task and admission["owner"] == key.task["owner"])
    else:
        expected = (key.store_id, key.row_id, key.row_source_sha256, key.epoch, key.operation_id)
        admission_matches = (
            tuple(ledger[name] for name in ("store_id", "row_id", "source_sha256", "epoch",
                                           "original_operation_id")) == expected
            and (admission["owner"], admission["issue_id"], admission["issue"])
            == (key.owner, key.issue_id, key.issue_identifier)
        )
    if not admission_matches:
        raise LaunchHold("role_launch_admission")
    intent = _record(directory / "intent.json", uid, key.intent_sha256)
    if isinstance(key, LedgerLaunchKey):
        from uif.stokowski import LEDGER_DISPATCH_INTENT, LEDGER_DISPATCH_INTENT_FIELDS

        handoff = _record(directory / "controller-supervisor.json", uid, key.handoff_sha256)
        workspace = _path(intent["workspace"])
        if (set(intent) != LEDGER_DISPATCH_INTENT_FIELDS or intent["schema"] != LEDGER_DISPATCH_INTENT
                or intent["task"] != key.task or intent["operation_id"] != key.operation_id
                or type(intent["attempt"]) is not type(key.attempt) or intent["attempt"] != key.attempt
                or workspace.name != key.workspace_key or workspace.parent.name != "workspaces"
                or intent["lease_id"] != key.lease_id or admission["policy_sha256"] != request.policy_sha256
                or intent["request_sha256"] != request.request_sha256
                or intent["pins"]["admission_sha256"] != request.admission_sha256
                or handoff.get("invocation_id") != key.invocation_id or handoff.get("operation_id") != key.operation_id
                or handoff.get("dispatch_sha256") != key.intent_sha256):
            raise LaunchHold("role_launch_ledger_intent")
        binding = intent["worker_binding"]
    elif isinstance(key, LedgerReviewLaunchKey):
        review = intent["request"]
        if (set(intent) != {"lease", "binding", "request", "phase", "history"}
                or intent["phase"] != "spawn-pending"
                or intent["lease"] != key.lease_id
                or set(review) != {"kind", "policy", "task", "workspace_key", "invocation_id", "lease"}
                or review["kind"] != LEDGER_REVIEW_ORIGIN or review["task"] != key.task
                or review["workspace_key"] != key.workspace_key or review["invocation_id"] != key.invocation_id
                or review["lease"] != key.lease_id or set(review["policy"]) != {"path", "sha256"}
                or review["policy"]["sha256"] != request.policy_sha256):
            raise LaunchHold("role_launch_ledger_review_intent")
        binding = intent["binding"]
        policy = _record(directory / "review-policy.json", uid, request.policy_sha256)
        runtime = _path(policy["current_runtime"])
        source = _path(policy["source"]["root"])
        if (any(policy.get(name) != value for name, value in {
                "schema": LEDGER_REVIEW_ORIGIN, "task": key.task, "workspace_key": key.workspace_key,
                "invocation_id": key.invocation_id, "lease": key.lease_id, "require_stopped_child": True,
                "owner": key.task["owner"], "reviewer_owner": binding["owner"], "resources": binding["resources"],
                }.items()) or "issue" in policy or binding["owner"] == key.task["owner"]
                or source not in (runtime / "workspaces" / key.workspace_key,
                                  runtime / "contributions" / key.workspace_key / "candidate")
                or not _path(review["policy"]["path"]).is_relative_to(runtime)
                or binding["mode"] != "native-review-read-only" or binding["write_roots"] != []
                or binding["read_roots"] != [str(source)]
                or any(binding[name] != policy["source"][field] for name, field in (
                    ("head", "head"), ("branch", "branch"), ("source_sha256", "files_sha256")))):
            raise LaunchHold("role_launch_review_scope")
    elif key.role == "coding":
        attempt = intent["attempt"]
        if (type(attempt) not in (int, type(None)) or ("initial" if attempt is None else str(attempt)) != key.attempt_id
                or intent["schema"] != "uif-dispatch-intent/1" or intent["issue"]["issue_id"] != key.issue_id
                or intent["admission"]["original_operation_id"] != key.operation_id
                or intent["lease_id"] != key.lease_id or admission["policy_sha256"] != request.policy_sha256
                or intent["request_sha256"] != request.request_sha256):
            raise LaunchHold("role_launch_coding_intent")
        binding = intent["worker_binding"]
    else:
        if intent["lease"] != key.lease_id or intent["request"]["policy"]["sha256"] != request.policy_sha256:
            raise LaunchHold("role_launch_review_intent")
        binding = intent["binding"]
        if binding["mode"] != "native-review-read-only" or binding["write_roots"] != []:
            raise LaunchHold("role_launch_review_scope")
    # The owner projects the original review ID or coding attempt under its
    # existing lock. This witness is controller-owned and grants no Pool authority.
    warrant = _record(directory / "fence.json", uid)
    if warrant != {"schema": SCHEMA, "request_sha256": request.digest, "lease": key.lease_id,
                   "binding_sha256": _digest(binding)}:
        raise LaunchHold("role_launch_fence")
    lock_root = _dir(_path(cfg["operation_locks"]), uid, 0o077)
    lock_path = lock_root / (_digest([key.store_id, key.operation_id]) + ".lock")
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _regular(fd, uid, 0o077)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise LaunchHold("role_launch_operation_fence_not_held")
    finally:
        os.close(fd)
    database = _path(cfg["pool_database"])
    _dir(database.parent, uid, 0o077)
    fd = os.open(database, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _regular(fd, uid, 0o077)
    finally:
        os.close(fd)
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        row = db.execute("SELECT fingerprint, status FROM leases WHERE id=?", (key.lease_id,)).fetchone()
    finally:
        db.close()
    if row != (_digest(binding), "running"):
        raise LaunchHold("role_launch_lease")
    work = _record(directory / "request.json", uid, request.request_sha256)
    if isinstance(key, LedgerLaunchKey):
        target = {name: key.task[name] for name in ("store_id", "row_id", "snapshot_sha256", "source_sha256")}
        if work.pop("ledger_task", None) != target:
            raise LaunchHold("role_launch_ledger_work")
    if (set(work) != {"prompt", "timeout_seconds"} or not isinstance(work["prompt"], str)
            or not work["prompt"].strip() or type(work["timeout_seconds"]) is not int
            or not 1 <= work["timeout_seconds"] <= 600):
        raise LaunchHold("role_launch_work")
    resources = binding["resources"]
    limits = {key: resources[key] for key in ("cpu_millis", "memory_bytes")}
    if any(type(limit) is not int or limit <= 0 for limit in limits.values()):
        raise LaunchHold("role_launch_resource_binding")
    return work, limits


def _read_registered_runner(cfg, request, filename):
    role = cfg["roles"]["neutral"]
    fd = _open_projection_root(_path(role["runner_control"]) / request.binding["job_id"],
                               custody_root=role["runner_control"], custody_uid=role["runner_uid"])
    try:
        if os.fstat(fd).st_mode & 0o077:
            raise LaunchHold("neutral_launch_runner_custody")
        entry = _open_beneath(fd, filename, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(entry, "rb") as stream:
            _regular(stream.fileno(), role["runner_uid"], 0o077)
            return stream.read(MAX_RECORD + 1)
    finally:
        os.close(fd)


def _neutral_limits(binding, role):
    resources = binding["resources"]
    if (not isinstance(resources, dict) or set(resources) != NEUTRAL_LIMITS | {"read_roots", "write_roots"}
            or binding["mode"] not in {"read", "write"} or type(binding["timeout_seconds"]) is not int
            or not 1 <= binding["timeout_seconds"] <= min(1800, role["timeout_seconds"])):
        raise LaunchHold("neutral_launch_resources")
    for name in NEUTRAL_LIMITS:
        floor = 0 if name == "output_bytes" else 1048576 if name == "memory_bytes" else 1
        if type(resources[name]) is not int or not floor <= resources[name] <= role["limits"][name]:
            raise LaunchHold("neutral_launch_resource_limit")
    execution = _path(role["execution_root"])
    for name in ("read_roots", "write_roots"):
        paths = resources[name]
        allowed = [_path(path) for path in role[name]]
        if (not isinstance(paths, list) or len(paths) > 32
                or any(not isinstance(path, str) for path in paths) or len(paths) != len(set(paths))):
            raise LaunchHold("neutral_launch_roots")
        for value in paths:
            path = _path(value)
            if (str(path) != value or not path.is_relative_to(execution)
                    or not any(path.is_relative_to(root) for root in allowed)):
                raise LaunchHold("neutral_launch_root_scope")
    if (bool(resources["write_roots"]) != (binding["mode"] == "write")
            or not (resources["read_roots"] or resources["write_roots"])):
        raise LaunchHold("neutral_launch_mode")
    return {name: resources[name] for name in NEUTRAL_LIMITS}


def _check_neutral(cfg, directory, request, *, cancelling=False):
    role = cfg["roles"]["neutral"]
    binding = request.binding
    lease = request.key.lease_id
    for field in ("descriptor_sha256", "runner_sha256", "capacity_policy_sha256"):
        if binding[field] != role[field]:
            raise LaunchHold("neutral_launch_registration_pin")
    if tuple(role["cpu_ids"]) != request.cpu_ids or not set(request.cpu_ids) <= os.sched_getaffinity(0):
        raise LaunchHold("neutral_launch_cpu_eligibility")
    limits = _neutral_limits(binding, role)
    intent = _read_registered_runner(cfg, request, "intent.json")
    if sha256(intent).hexdigest() != request.intent_sha256 or _json(intent) != binding:
        raise LaunchHold("neutral_launch_original_intent")
    uid = cfg["controller_uid"]
    sealed = _read(directory / "request.json", uid)
    if sha256(sealed).hexdigest() != request.request_file_sha256:
        raise LaunchHold("role_launch_digest")
    try:
        original = _read_registered_runner(cfg, request, "request.json")
    except FileNotFoundError as exc:
        raise LaunchHold("neutral_launch_original_request") from exc
    if original != sealed:
        raise LaunchHold("neutral_launch_original_request")
    record = _json(sealed)
    body = dict(protocol="foo-user-exec-v1", workspace="foo", job_id=binding["job_id"],
                descriptor_sha256=binding["descriptor_sha256"], mode=binding["mode"],
                timeout_seconds=binding["timeout_seconds"], resources=binding["resources"])
    command = record.get("command")
    if (not isinstance(command, str) or not command.strip() or "\0" in command
            or len(command.encode()) > 32000 or sha256(command.encode()).hexdigest() != binding["command_sha256"]):
        raise LaunchHold("neutral_launch_command")
    body["command"] = command
    if (record != {**body, "action": "run", "request_sha256": binding["request_sha256"]}
            or _digest(body) != binding["request_sha256"]):
        raise LaunchHold("neutral_launch_original_request")
    if _record(directory / "fence.json", uid) != {
            "schema": SCHEMA, "request_sha256": request.digest, "lease": lease,
            "binding_sha256": request.pool_binding_sha256}:
        raise LaunchHold("neutral_launch_fence")
    lock_root = _dir(_path(cfg["operation_locks"]), uid, 0o077)
    lock_path = lock_root / (_digest([role["store_id"], lease]) + ".lock")
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _regular(fd, uid, 0o077)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise LaunchHold("neutral_launch_operation_fence_not_held")
    finally:
        os.close(fd)
    database = _path(cfg["pool_database"])
    _dir(database.parent, uid, 0o077)
    fd = os.open(database, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _regular(fd, uid, 0o077)
    finally:
        os.close(fd)
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        row = db.execute("SELECT binding, fingerprint, status FROM leases WHERE id=?", (lease,)).fetchone()
    finally:
        db.close()
    allowed_states = {"running", "cancel_requested"} if cancelling else {"running"}
    if row is None or row[1] != request.pool_binding_sha256 or row[2] not in allowed_states:
        raise LaunchHold("neutral_launch_lease")
    pool_binding = _json(row[0].encode())
    if (_digest(pool_binding) != request.pool_binding_sha256 or pool_binding.get("mode") != "foo-neutral-user"
            or pool_binding.get("request") != binding or pool_binding.get("write_roots") != binding["resources"]["write_roots"]
            or pool_binding.get("registration_sha256") != role["registration_sha256"]
            or pool_binding.get("policy_fingerprint") != role["policy_fingerprint"]
            or pool_binding.get("resources") != {name: limits[name] for name in
                                                ("cpu_millis", "memory_bytes", "output_bytes", "io_slots")}):
        raise LaunchHold("neutral_launch_pool_binding")
    return record, limits


def _new_role_dir(path, uid, gid):
    path.mkdir(mode=0o700)
    os.chown(path, uid, gid, follow_symlinks=False)


class _OpenHow(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("flags", "mode", "resolve")]


def _linux():
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise LaunchHold("role_launch_linux_x86_64_required")
    return ctypes.PyDLL(None, use_errno=True)


def _open_beneath(directory, name, flags):
    # RESOLVE_NO_XDEV includes same-device bind mounts. Fail closed on old
    # kernels and never fall back to st_dev or ordinary openat.
    if not name or name in {".", ".."} or "/" in name:
        raise LaunchHold("role_launch_invalid_component")
    how = _OpenHow(flags | os.O_CLOEXEC, 0, 0x01 | 0x04 | 0x08)
    fd = _linux().syscall(437, directory, os.fsencode(name), ctypes.byref(how), ctypes.sizeof(how))
    if fd < 0:
        error = ctypes.get_errno()
        if error == 2:
            raise FileNotFoundError(error, os.strerror(error))
        raise LaunchHold(f"role_launch_projection_openat2:{error}")
    return fd


def _open_projection_root(path, *, custody_root, custody_uid):
    """Open the trusted anchor, then check every relative component."""
    anchor = _path(custody_root)
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(anchor):
        raise LaunchHold("role_launch_projection_outside_custody")
    # The independently selected anchor can itself be a mount. Do not apply
    # NO_XDEV on the way to it, but pin the inspected directory before walking.
    before = anchor.lstat()
    current = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(current)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISDIR(opened.st_mode) or opened.st_uid != custody_uid
                or opened.st_mode & 0o022):
            raise LaunchHold("role_launch_projection_anchor_custody")
        for name in path.relative_to(anchor).parts:
            child = _open_beneath(current, name, os.O_RDONLY | os.O_DIRECTORY)
            os.close(current)
            current = child
    except BaseException:
        os.close(current)
        raise
    return current


def _project(source_fd, destination, source_uid, uid, gid, max_bytes=268435456):
    """Hash or copy a bounded tree using only no-follow descriptors."""
    entries = []
    total = 0
    root = os.dup(source_fd)
    try:
        device = os.fstat(root).st_dev

        def visit(directory, prefix, target, depth):
            nonlocal total
            owner = os.fstat(directory)
            if owner.st_uid != source_uid or owner.st_mode & 0o022 or owner.st_dev != device or depth > 64:
                raise LaunchHold("role_launch_projection_custody")
            for name in sorted(os.listdir(directory)):
                if len(entries) >= 100000:
                    raise LaunchHold("role_launch_projection_entry_budget")
                relative = prefix + name
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                out = target / name if target is not None else None
                if stat.S_ISDIR(info.st_mode):
                    child = _open_beneath(directory, name, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        if out is not None:
                            _new_role_dir(out, uid, gid)
                        entries.append([relative, "dir"])
                        visit(child, relative + "/", out, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    fd = _open_beneath(directory, name, os.O_RDONLY | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as inp:
                        opened = _regular(inp.fileno(), source_uid, 0o022)
                        if opened.st_dev != device or opened.st_size > max_bytes - total:
                            raise LaunchHold("role_launch_projection_byte_budget")
                        digest = sha256()
                        flags = os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW
                        output = os.fdopen(os.open(out, flags, 0o600), "wb") if out is not None else None
                        try:
                            while data := inp.read(1024 * 1024):
                                total += len(data)
                                if total > max_bytes:
                                    raise LaunchHold("role_launch_projection_byte_budget")
                                digest.update(data)
                                if output is not None:
                                    output.write(data)
                            after = os.fstat(inp.fileno())
                            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                                raise LaunchHold("role_launch_projection_drift")
                            if output is not None:
                                output.flush()
                                os.fchmod(output.fileno(), 0o700 if opened.st_mode & 0o100 else 0o600)
                                os.fchown(output.fileno(), uid, gid)
                                os.fsync(output.fileno())
                        finally:
                            if output is not None:
                                output.close()
                    entries.append([relative, "exec" if opened.st_mode & 0o100 else "file", digest.hexdigest()])
                else:
                    raise LaunchHold("role_launch_projection_type")

        visit(root, "", destination, 0)
    finally:
        os.close(root)
    if not entries:
        raise LaunchHold("role_launch_empty_projection")
    return _digest(sorted(entries))


def projection_digest(path, max_bytes=268435456, *, custody_root):
    """Hash a controller tree below an independently qualified custody root."""
    fd = _open_projection_root(path, custody_root=custody_root, custody_uid=os.getuid())
    try:
        return _project(fd, None, os.getuid(), None, None, max_bytes)
    finally:
        os.close(fd)


def _return_outputs(cfg, role, directory, workspace_fd, attempt_fd):
    """Stage role-owned output as untrusted data after verified stop."""
    target = directory / "returned"
    target.mkdir(mode=0o700)  # Root-only until the complete return is durable.
    controller_uid, controller_gid = cfg["controller_uid"], cfg["controller_gid"]
    staged = target / "workspace"
    _new_role_dir(staged, controller_uid, controller_gid)
    workspace_digest = _project(workspace_fd, staged, role["uid"], controller_uid, controller_gid,
                                role["max_projection_bytes"])
    result = {"workspace_sha256": workspace_digest, "artifacts": {}}
    fd = os.dup(attempt_fd)
    try:
        info = os.fstat(fd)
        if info.st_uid != role["uid"] or info.st_mode & 0o022:
            raise LaunchHold("role_launch_output_custody")
        remaining = role["max_projection_bytes"]
        for name in ("stdout.jsonl", "stderr.log", "final-message.txt"):
            try:
                source = _open_beneath(fd, name, os.O_RDONLY | os.O_NONBLOCK)
            except FileNotFoundError:
                if name == "final-message.txt":
                    continue
                raise
            with os.fdopen(source, "rb") as stream:
                meta = _regular(stream.fileno(), role["uid"], 0o077)
                if meta.st_size > remaining:
                    raise LaunchHold("role_launch_output_budget")
                data = stream.read(remaining + 1)
                after = os.fstat(stream.fileno())
            if len(data) > remaining or (meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise LaunchHold("role_launch_output_drift")
            remaining -= len(data)
            out = os.open(target / name, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(out, "wb") as output:
                output.write(data)
                output.flush()
                os.fchown(output.fileno(), controller_uid, controller_gid)
                os.fsync(output.fileno())
            result["artifacts"][name] = sha256(data).hexdigest()
    finally:
        os.close(fd)
    digest = _digest(result)
    _create(target / "manifest.json", result)
    os.chown(target / "manifest.json", controller_uid, controller_gid, follow_symlinks=False)
    os.chown(target, controller_uid, controller_gid, follow_symlinks=False)
    _sync(directory)
    return digest


def _prepare(role, cfg, directory, request):
    role_root = _dir(_path(role["root"]), 0, 0o022)
    uid, gid = role["uid"], role["gid"]
    slot = role_root / request.slot
    slot.mkdir(mode=0o700)
    name = request.key.workspace_key
    workspace = slot / "workspaces" / name
    if request.key.role == "review":
        name = "review-" + name + "-" + request.slot[:16]
    attempt = slot / "attempts" / name
    for container, path in ((workspace.parent, workspace), (attempt.parent, attempt)):
        container.mkdir(mode=0o711)
        container.chmod(0o711)
        _new_role_dir(path, uid, gid)
    projection = _open_projection_root(directory / "projection", custody_root=cfg["controller_root"],
                                       custody_uid=cfg["controller_uid"])
    try:
        if os.fstat(projection).st_mode & 0o077:
            raise LaunchHold("role_launch_projection_custody")
        digest = _project(projection, workspace, cfg["controller_uid"], uid, gid,
                          role["max_projection_bytes"])
    finally:
        os.close(projection)
    if digest != request.projection_sha256:
        raise LaunchHold("role_launch_projection_digest")
    home = attempt / "codex-home-broker"
    _new_role_dir(home, uid, gid)
    auth = _path(role["auth_reference"])
    if auth.is_relative_to(role_root) or auth.is_relative_to(_path(cfg["controller_root"])):
        raise LaunchHold("role_launch_auth_boundary")
    for parent in auth.parents:
        _dir(parent, 0, 0o022)
    meta = auth.lstat()
    if meta.st_uid != uid or not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_mode & 0o077:
        raise LaunchHold("role_launch_auth_metadata")
    (home / "auth.json").symlink_to(auth)
    return slot, workspace, attempt, home


def _boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _cgroup(cfg, slot):
    return _dir(_path(cfg["cgroup_root"]), 0, 0o022) / ("uif-" + slot)


def _cgroup_id(path):
    info = _dir(path, 0, 0o022).stat()
    return [info.st_dev, info.st_ino]


def _populated(path):
    data = dict(line.split() for line in (path / "cgroup.events").read_text().splitlines())
    if data["populated"] not in {"0", "1"}:
        raise LaunchHold("role_launch_cgroup_events")
    return data["populated"] == "1"


def _kill(path):
    (path / "cgroup.kill").write_text("1")


def _wait_empty(path, limit=4):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not _populated(path):
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                return True
        time.sleep(0.02)
    return False


def _log(path, role):
    fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, role["uid"], role["gid"])
        return os.fdopen(fd, "wb", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _start_ticks(pid):
    stat_line = Path(f"/proc/{pid}/stat").read_text()
    return int(stat_line.rsplit(")", 1)[1].split()[19])


class _CloneArgs(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "flags", "pidfd", "child_tid", "parent_tid", "exit_signal", "stack", "stack_size",
        "tls", "set_tid", "set_tid_size", "cgroup")]


def _clone_into(cgroup, *, project_work=False):
    # CLONE_NEWNS | CLONE_NEWPID | CLONE_INTO_CGROUP. The fixed helper is
    # single-threaded; there is no CLONE_VM and no child can run outside cg.
    fd = os.open(cgroup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        flags = 0x00020000 | 0x20000000 | 0x200000000
        if project_work:
            flags |= 0x40000000  # CLONE_NEWNET
        args = _CloneArgs(flags=flags,
                          exit_signal=17, cgroup=fd)
        call = _linux().syscall
        call.restype = ctypes.c_long
        pid = call(435, ctypes.byref(args), ctypes.sizeof(args))
        if pid < 0:
            raise _CloneRefused(f"role_launch_clone3:{ctypes.get_errno()}")
        return pid
    finally:
        os.close(fd)


def _mount(source, target, filesystem, flags, data=None):
    arg = lambda value: None if value is None else os.fsencode(value)
    call = _linux().mount
    call.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p)
    if call(arg(source), arg(target), arg(filesystem), flags, arg(data)) != 0:
        raise LaunchHold(f"role_launch_mount:{ctypes.get_errno()}")


def _target(value):
    path = Path(value)
    if (not isinstance(value, str) or not path.is_absolute() or str(path) != value
            or path.anchor != "/" or path == Path("/") or ".." in path.parts
            or any(ord(c) < 32 for c in value)):
        raise LaunchHold("role_launch_namespace_target")
    return path


def _namespace_files(cfg, role, dropper, guard, workspace, attempt, role_name):
    entries = [(dropper, dropper, False, True), (guard, guard, False, True)]
    for entry in role["runtime_files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "target"}:
            raise LaunchHold("role_launch_runtime_pin")
        source = _installed(entry["path"], entry["sha256"], executable=False)
        entries.append((source, _target(entry["target"]), False, True))
    auth = _path(role["auth_reference"])
    entries.append((auth, auth, False, True))
    entries.extend(((workspace, workspace, True, role_name == "review"),
                    (attempt, attempt, True, False)))
    protected = tuple(_path(cfg[name]) for name in ("controller_root", "cgroup_root"))
    role_roots = tuple(_path(item["execution_root"] if name == "neutral" else item["root"])
                       for name, item in cfg["roles"].items())
    kernel = (Path("/proc"), Path("/sys"), Path("/dev"))
    seen = list(kernel)
    for source, target, directory, _readonly in entries:
        if (any(target.is_relative_to(item) or item.is_relative_to(target)
                for item in (*seen, *protected))
                or any(source.is_relative_to(root) for root in (*protected, *kernel))
                or (not directory and any(target.is_relative_to(root) or source.is_relative_to(root)
                                         for root in role_roots))):
            raise LaunchHold("role_launch_namespace_overlap")
        seen.append(target)
    return entries


def _overlap(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def _neutral_scope_fd(path, anchor, uid):
    if path == anchor:
        return _open_projection_root(path, custody_root=anchor, custody_uid=uid)
    parent = _open_projection_root(path.parent, custody_root=anchor, custody_uid=uid)
    try:
        return _open_beneath(parent, path.name, os.O_PATH)
    finally:
        os.close(parent)


def _prepare_project_output(role, request, work):
    writable = work["resources"]["write_roots"]
    if not writable:
        return
    job = request.binding["job_id"]
    parent, = (_path(value) for value in role["write_roots"])
    if (not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", job)
            or writable != [str(parent / job)]):
        raise LaunchHold("neutral_project_output_binding")
    parent_fd = _open_projection_root(parent, custody_root=role["execution_root"],
                                      custody_uid=role["execution_uid"])
    try:
        metadata = os.fstat(parent_fd)
        if (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)) != (0, 0, 0o711):
            raise LaunchHold("neutral_project_output_custody")
        os.mkdir(job, mode=0o700, dir_fd=parent_fd)
        job_fd = _open_beneath(parent_fd, job, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name in ("empty-home", "scratch"):
                os.mkdir(name, mode=0o700, dir_fd=job_fd)
                child_fd = _open_beneath(job_fd, name, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fchown(child_fd, role["uid"], role["gid"])
                finally:
                    os.close(child_fd)
            os.fchown(job_fd, role["uid"], role["gid"])
        finally:
            os.close(job_fd)
    finally:
        os.close(parent_fd)


def _neutral_plan(cfg, role, work, stack):
    runtime = []
    blocked = [Path("/proc"), Path("/sys"), Path("/dev"), _path(cfg["controller_root"]),
               _path(cfg["cgroup_root"]), _path(role["runner_control"]),
               *(_path(item["root"]) for name, item in cfg["roles"].items() if name != "neutral"),
               *(_path(item) for item in role["protected_roots"])]
    paths = {}
    fixed = [(name, role[name]) for name in ("setpriv", "bash")]
    for name, entry in [*fixed, *((None, entry) for entry in role["runtime_files"])]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "target"}:
            raise LaunchHold("neutral_launch_runtime_pin")
        source = _installed(entry["path"], entry["sha256"], executable=name is not None)
        target = _target(entry["target"])
        if any(_overlap(source, path) or _overlap(target, path) for path in blocked):
            raise LaunchHold("neutral_launch_runtime_boundary")
        if any(_overlap(target, previous) for _, previous, _, _ in runtime):
            raise LaunchHold("neutral_launch_runtime_overlap")
        if name is not None:
            paths[name] = str(target)
        runtime.append((source, target, False, True))
    if paths["bash"] != "/bin/bash":
        raise LaunchHold("neutral_launch_bash_target")
    resources = work["resources"]
    # Never let a task mount supply the privileged loader's configuration
    # or system binaries. Runtime files at those names are pinned above.
    blocked.extend(Path(path) for path in ("/etc", "/usr", "/lib", "/lib64", "/bin", "/sbin"))
    execution = _path(role["execution_root"])
    scopes = {}
    for kind in ("read_roots", "write_roots"):
        for value in resources[kind]:
            path = _path(value)
            if (not path.is_relative_to(execution)
                    or any(_overlap(path, root) for root in (*blocked, *(entry[1] for entry in runtime)))):
                raise LaunchHold("neutral_launch_mount_boundary")
            scopes[path] = kind == "read_roots"
    selected = []
    for path, readonly in sorted(scopes.items(), key=lambda item: (len(item[0].parts), str(item[0]))):
        if any(path.is_relative_to(parent) and (readonly or not parent_ro)
               for _, parent, _, parent_ro in selected):
            continue
        fd = _neutral_scope_fd(path, execution, role["execution_uid"])
        stack.callback(os.close, fd)
        mode = os.fstat(fd).st_mode
        if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
            raise LaunchHold("neutral_launch_mount_type")
        # Clone the selected mount while its FD still belongs to this
        # namespace. After CLONE_NEWNS the inherited ordinary O_PATH mount
        # is foreign; a detached anonymous mount FD remains attachable.
        mount_fd = _mount_call(428, fd, b"", 0x1000 | 1 | os.O_CLOEXEC)
        stack.callback(os.close, mount_fd)
        selected.append((mount_fd, path, stat.S_ISDIR(mode), readonly))
    command = [paths["setpriv"], f"--reuid={role['uid']}", f"--regid={role['gid']}", "--clear-groups",
               "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", "--",
               paths["bash"], "--noprofile", "--norc", "-c", work["command"]]
    env = dict(ENV, UIF_COMPUTATION_SUBPROCESS_CEILING=str(resources["workers"]), OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    return runtime, selected, command, str(execution), env


class _MountAttr(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("set", "clear", "propagation", "userns_fd")]


def _mount_call(number, *args):
    result = _linux().syscall(number, *args)
    if result < 0:
        raise LaunchHold(f"neutral_launch_mount_syscall:{number}:{ctypes.get_errno()}")
    return result


def _neutral_mounts(root, entries):
    # Clone only the selected mount node, never the submount tree. Apply
    # attributes while detached and attach by FD even for a nested write
    # override under an approved read root. No privileged path follows a
    # symlink in the ordinary user's tree.
    anchor = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    installed = []
    try:
        for fd, target, directory, readonly in entries:
            relative = target.relative_to("/")
            if not any(target.is_relative_to(parent) for parent in installed):
                mountpoint = root / relative
                mountpoint.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                for parent in mountpoint.parents:
                    if parent == root:
                        break
                    parent.chmod(0o755)
                if directory:
                    mountpoint.mkdir(mode=0o755)
                    mountpoint.chmod(0o755)
                else:
                    os.close(os.open(mountpoint, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            how = _OpenHow(os.O_PATH | os.O_CLOEXEC, 0, 0x04 | 0x08)
            target_fd = _mount_call(437, anchor, os.fsencode(relative), ctypes.byref(how), ctypes.sizeof(how))
            try:
                if stat.S_ISDIR(os.fstat(target_fd).st_mode) != directory:
                    raise LaunchHold("neutral_launch_mountpoint_type")
                # fd is the parent-created detached mount, not a path FD
                # tied to the parent's ordinary mount namespace. Ownership
                # remains with _namespace_init / the parent's ExitStack.
                attributes = _MountAttr(set=2 | 4 | int(readonly))
                _mount_call(442, fd, b"", 0x1000, ctypes.byref(attributes), ctypes.sizeof(attributes))
                _mount_call(429, fd, b"", target_fd, b"", 0x04 | 0x40)
            finally:
                os.close(target_fd)
            installed.append(target)
    finally:
        os.close(anchor)


def _neutral_monitor(child, cg, readers, witness, work, role):
    deadline = time.monotonic() + work["timeout_seconds"]
    kept = {name: bytearray() for name in readers}
    counts = dict.fromkeys(readers, 0)
    remaining = work["resources"]["output_bytes"]
    outcome = None
    child.stdin.close()
    with selectors.DefaultSelector() as selector:
        for name, pipe in readers.items():
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map() or child.poll() is None:
            if outcome is None:
                if (witness / "cancel.json").exists():
                    outcome = "cancelled"
                elif time.monotonic() >= deadline:
                    outcome = "timed_out"
                else:
                    fs = os.statvfs(role["runner_control"])
                    mem = Path("/proc/meminfo").read_text().splitlines()
                    available = next(int(line.split()[1]) * 1024 for line in mem if line.startswith("MemAvailable:"))
                    if (available < role["memory_reserve_bytes"]
                            or fs.f_bavail * fs.f_frsize < role["disk_reserve_bytes"]):
                        outcome = "failed"
                if outcome is not None:
                    _kill(cg)
                    child.wait(timeout=4)
                    deadline = time.monotonic() + 4
            elif time.monotonic() >= deadline:
                raise LaunchHold("neutral_launch_log_pipe_not_closed")
            for event, _ in selector.select(0.05):
                try:
                    data = os.read(event.fd, 16384)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(event.fileobj)
                    continue
                name = event.data
                counts[name] += len(data)
                portion = data[:min(remaining, LOG_LIMIT - len(kept[name]))]
                kept[name].extend(portion)
                remaining -= len(portion)
    code = child.poll()
    return (outcome or ("succeeded" if code == 0 else "failed"), kept,
            {name: counts[name] > len(value) for name, value in kept.items()})


def _neutral_logs(role, request, logs):
    directory = _open_projection_root(_path(role["runner_control"]) / request.binding["job_id"],
                                      custody_root=role["runner_control"], custody_uid=role["runner_uid"])
    try:
        if os.fstat(directory).st_mode & 0o077:
            raise LaunchHold("neutral_launch_runner_custody")
        for name, data in logs.items():
            fd = os.open(name + ".log", os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fchown(stream.fileno(), role["runner_uid"], role["runner_gid"])
                os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)


def _project_devices(root):
    device_root = root / "dev"
    device_root.mkdir(mode=0o755)
    # This dedicated mount enables just the null device used by Git.
    _mount("tmpfs", device_root, "tmpfs", 2 | 8, "mode=0755,size=4096,nr_inodes=4")
    null = device_root / "null"
    os.mknod(null, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    null.chmod(0o666)


def _private_root(root, entries, ordinary_mounts=(), *, project_work=False):
    # No recursive bind of a host tree. MS_PRIVATE prevents propagation in
    # either direction. Child mounts are visible only in this mount namespace.
    _mount(None, "/", None, 16384 | 262144)
    _mount("tmpfs", root, "tmpfs", 2 | 4, "mode=0755,size=16777216")
    for source, target, directory, readonly in entries:
        mountpoint = root / target.relative_to("/")
        mountpoint.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        # Helper umask is private; placeholders must be searchable by the role.
        for parent in mountpoint.parents:
            if parent == root:
                break
            parent.chmod(0o755)
        if directory:
            mountpoint.mkdir(mode=0o755)
        else:
            fd = os.open(mountpoint, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        _mount(source, mountpoint, None, 4096)
        _mount(None, mountpoint, None, 4096 | 32 | 2 | 4 | int(readonly))
    if ordinary_mounts:
        _neutral_mounts(root, ordinary_mounts)
    if project_work:
        _project_devices(root)
    proc = root / "proc"
    proc.mkdir(mode=0o700)
    _mount("proc", proc, "proc", 2 | 4 | 8)
    os.chroot(root)
    os.chdir("/")
    if os.getpid() != 1:
        raise LaunchHold("role_launch_namespace_init_identity")


def _close_except(*kept):
    low = 3
    for fd in sorted(kept):
        os.closerange(low, fd)
        low = fd + 1
    os.closerange(low, os.sysconf("SC_OPEN_MAX"))


def _namespace_init(root, entries, command, stdin, stdout, stderr, ready, gate, *,
                    ordinary_mounts=(), cwd="/", child_env=ENV, cpu_ids=(), project_work=False):
    for fd, source in enumerate((stdin, stdout, stderr)):
        os.dup2(source, fd, inheritable=True)
    mount_fds = [item[0] for item in ordinary_mounts]
    _close_except(ready, gate, *mount_fds)
    if cpu_ids:
        os.sched_setaffinity(0, cpu_ids)
    if project_work:
        _private_root(root, entries, ordinary_mounts, project_work=True)
    elif ordinary_mounts:
        _private_root(root, entries, ordinary_mounts)
    else:
        _private_root(root, entries)
    for fd in mount_fds:
        os.close(fd)
    os.chdir(cwd)
    os.write(ready, b"R")
    os.close(ready)
    if os.read(gate, 1) != b"G":
        os._exit(126)
    os.close(gate)
    pid = os.fork()
    if pid == 0:
        try:
            os.execve(command[0], command, child_env)
        finally:
            os._exit(126)
    for fd in (0, 1, 2):
        os.close(fd)
    while True:
        child, status = os.waitpid(-1, 0)
        if child == pid:
            code = os.waitstatus_to_exitcode(status)
            # Exiting namespace PID 1 makes the kernel terminate descendants.
            os._exit(code if code >= 0 else 128 - code)


class _NamespaceProcess:
    def __init__(self, pid, stdin):
        self.pid = pid
        self.stdin = stdin
        self.returncode = None

    def poll(self):
        if self.returncode is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout):
        end = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= end:
                raise subprocess.TimeoutExpired("namespace-init", timeout)
            time.sleep(0.02)
        return self.returncode


def _namespace_ids(pid):
    result = {}
    for name in ("mnt", "pid"):
        info = Path(f"/proc/{pid}/ns/{name}").stat()
        result[name] = [info.st_dev, info.st_ino]
    return result


def _network_namespace_id(pid):
    info = Path(f"/proc/{pid}/ns/net").stat()
    return info.st_dev, info.st_ino


def _valid_namespaces(value):
    return (isinstance(value, dict) and set(value) == {"mnt", "pid"}
            and all(isinstance(identity, list) and len(identity) == 2
                    and all(type(part) is int and part > 0 for part in identity)
                    for identity in value.values()))


def _spawn_isolated(cg, cg_id, root, entries, command, logs, witness, request, start_by, check_fence, *,
                    ordinary_mounts=(), cwd="/", child_env=ENV, cpu_ids=(), project_work=False, startup=None):
    startup = {} if startup is None else startup
    stdin_r, stdin_w = os.pipe2(os.O_CLOEXEC)
    ready_r, ready_w = os.pipe2(os.O_CLOEXEC)
    gate_r, gate_w = os.pipe2(os.O_CLOEXEC)
    owned = {stdin_r, stdin_w, ready_r, ready_w, gate_r, gate_w}
    try:
        startup["phase"] = "clone"
        try:
            pid = _clone_into(cg, project_work=True) if project_work else _clone_into(cg)
        except _CloneRefused:
            startup["clone_refused"] = True
            raise
        if pid == 0:
            try:
                fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC) if ordinary_mounts else stdin_r
                _namespace_init(root, entries, command, fd, logs[0].fileno(), logs[1].fileno(),
                                ready_w, gate_r, ordinary_mounts=ordinary_mounts, cwd=cwd,
                                child_env=child_env, cpu_ids=cpu_ids, project_work=project_work)
            except BaseException as error:
                try:
                    os.write(ready_w, b"E" + _wire(_startup_error(error, "namespace-init"))[:1024])
                except OSError:
                    pass
            finally:
                os._exit(126)
        startup.update(pid=pid, phase="namespace-readiness")
        _create(witness / "spawned.json", {"request": request.digest, "boot": _boot(),
                "cgroup": cg_id, "pid": pid, "start_ticks": _start_ticks(pid)})
        for fd in (stdin_r, ready_w, gate_r):
            os.close(fd)
            owned.remove(fd)
        remaining = max(0, start_by - time.monotonic())
        ready, _, _ = select.select([ready_r], [], [], remaining)
        marker = os.read(ready_r, 1) if ready else b""
        if marker == b"E":
            startup["namespace_error"] = _json(os.read(ready_r, 1024))
        if (not ready or marker != b"R" or time.monotonic() >= start_by
                or (witness / "cancel.json").exists()):
            raise LaunchHold("role_launch_namespace_readiness")
        startup["phase"] = "namespace-identity"
        namespaces = _namespace_ids(pid)
        host = _namespace_ids(os.getpid())
        members = (cg / "cgroup.procs").read_text().split()
        if (not _valid_namespaces(namespaces) or any(namespaces[n] == host[n] for n in namespaces)
                or _cgroup_id(cg) != cg_id or str(pid) not in members):
            raise LaunchHold("role_launch_namespace_or_cgroup_identity")
        if project_work and _network_namespace_id(pid) == _network_namespace_id(os.getpid()):
            raise LaunchHold("neutral_project_network_namespace_identity")
        started = {"pid": pid, "start_ticks": _start_ticks(pid), "boot": _boot(), "cgroup": cg_id,
                   "namespaces": namespaces, "request": request.digest, "isolation": ATTEMPT_ISOLATION}
        _create(witness / "started.json", started)
        check_fence()
        if time.monotonic() >= start_by or (witness / "cancel.json").exists():
            raise LaunchHold("role_launch_namespace_gate")
        startup["phase"] = "command-gate"
        os.write(gate_w, b"G")
        startup["command_released"] = True
        _create(witness / "released.json", {"request": request.digest, "started_sha256": _digest(started)})
        stream = os.fdopen(stdin_w, "wb", buffering=0)
        owned.remove(stdin_w)
        return _NamespaceProcess(pid, stream), started
    finally:
        for fd in owned:
            os.close(fd)


def _monitor(child, cg, logs, work):
    deadline = time.monotonic() + work["timeout_seconds"]
    prompt = memoryview(work["prompt"].encode())
    pipe = child.stdin
    try:
        os.set_blocking(pipe.fileno(), False)
        while child.poll() is None:
            if (time.monotonic() >= deadline
                    or sum(os.fstat(log.fileno()).st_size for log in logs) > LOG_LIMIT):
                _kill(cg)
                child.wait(timeout=4)
                break
            if not pipe.closed:
                try:
                    if prompt:
                        prompt = prompt[os.write(pipe.fileno(), prompt[:65536]):]
                    else:
                        pipe.close()
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    pipe.close()
            time.sleep(0.02)
    finally:
        pipe.close()


def _watch(cfg, directory, witness, request, role, work, limits, start_by, *,
           release_gate=None, project_work=False):
    child = None
    cg_id = None
    spawn_window = False
    startup = {"phase": "prepare", "clone_refused": False, "command_released": False}
    projection_fds = []
    cg = _cgroup(cfg, request.slot)
    try:
        _create(witness / "watcher.json", {"request": request.digest, "boot": _boot(),
                "pid": os.getpid(), "start_ticks": _start_ticks(os.getpid())})
        # The watcher is single-purpose and remains outside the role cgroup.
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise LaunchHold("role_launch_subreaper")
        cg.mkdir()
        cg_id = _cgroup_id(cg)
        _create(witness / "cgroup.json", {"boot": _boot(), "cgroup": cg_id})
        neutral = isinstance(request, NeutralLaunchRequest)
        processes = limits["workers"] + limits["overhead_processes"] if neutral else role["max_processes"]
        for name, value in (("cpu.max", f"{limits['cpu_millis'] * 100} 100000"),
                            ("memory.max", str(limits["memory_bytes"])),
                            ("pids.max", str(processes + 1))):
            fd = os.open(cg / name, os.O_WRONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "w") as stream:
                stream.write(value)
            if (cg / name).read_text().strip() != value:
                raise LaunchHold("role_launch_cgroup_limit")
        root = witness / "namespace-root"
        root.mkdir(mode=0o700)
        def check_fence():
            if _check_original(cfg, directory, request) != (work, limits):
                raise LaunchHold("role_launch_namespace_fence")

        if neutral:
            with ExitStack() as stack:
                # The helper can time out before the watcher starts. Take the
                # watcher's own drain fence before preparing any namespace and
                # retain it through release of the command and its witness.
                with release_gate() if release_gate is not None else nullcontext():
                    if project_work:
                        check_fence()
                        if time.monotonic() >= start_by or (witness / "cancel.json").exists():
                            raise LaunchHold("role_launch_pre_spawn_hold")
                        _prepare_project_output(role, request, work)
                    entries, mounts, command, cwd, env = _neutral_plan(cfg, role, work, stack)
                    readers, writers = {}, []
                    for name in ("stdout", "stderr"):
                        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
                        readers[name] = stack.enter_context(os.fdopen(read_fd, "rb", buffering=0))
                        writers.append(stack.enter_context(os.fdopen(write_fd, "wb", buffering=0)))
                    check_fence()
                    if time.monotonic() >= start_by or (witness / "cancel.json").exists():
                        raise LaunchHold("role_launch_pre_spawn_hold")
                    spawn_window = True
                    child, started = _spawn_isolated(
                        cg, cg_id, root, entries, command, writers, witness, request, start_by, check_fence,
                        ordinary_mounts=mounts, cwd=cwd, child_env=env, cpu_ids=request.cpu_ids,
                        project_work=project_work, startup=startup)
                for stream in writers:
                    stream.close()
                outcome, captured, truncated = _neutral_monitor(child, cg, readers, witness, work, role)
        else:
            slot, workspace, attempt, home = _prepare(role, cfg, directory, request)
            if (_check_original(cfg, directory, request) != (work, limits)
                    or time.monotonic() >= start_by or (witness / "cancel.json").exists()):
                raise LaunchHold("role_launch_pre_spawn_hold")
            dropper = _installed(role["dropper"]["path"], role["dropper"]["sha256"], executable=True)
            guard = _installed(role["guard"]["path"], role["guard"]["sha256"], executable=True)
            entries = _namespace_files(cfg, role, dropper, guard, workspace, attempt, request.key.role)
            command = [str(dropper), MODES[request.key.role], str(workspace),
                       str(attempt / "final-message.txt"), str(home), str(attempt / "codex-state")]
            for path in (workspace, attempt):
                projection_fds.append(_open_projection_root(path, custody_root=role["root"], custody_uid=0))
            # Keep the host slot root-only permanently. Only the two individual
            # binds inside the private namespace give the role a view of its slot.
            with _log(attempt / "stdout.jsonl", role) as out, _log(attempt / "stderr.log", role) as err:
                spawn_window = True
                child, started = _spawn_isolated(cg, cg_id, root, entries, command, (out, err),
                                                  witness, request, start_by, check_fence, startup=startup)
                _monitor(child, cg, (out, err), work)
        if _populated(cg):
            _kill(cg)
        if (type(child.returncode) is int and _cgroup_id(cg) == cg_id
                and _wait_empty(cg) and _cgroup_id(cg) == cg_id):
            terminal = {"state": "exited-clean", "request": request.digest, **started,
                        "exit_code": child.returncode, "output_sha256": None}
            if neutral:
                _neutral_logs(role, request, captured)
                terminal.update(outcome=outcome, log_bytes={name: len(data) for name, data in captured.items()},
                                logs_truncated=truncated)
            else:
                try:
                    terminal["output_sha256"] = _return_outputs(cfg, role, directory, *projection_fds)
                except (OSError, ValueError):
                    pass
            _create(witness / "terminal.json", terminal)
    except Exception as error:
        # Publish the reason before cleanup: a later cleanup error must not
        # erase the original failure. Existing old reservations are untouched.
        try:
            _create(witness / "startup-failure.json", {"request": request.digest, "boot": _boot(),
                    "isolation": ATTEMPT_ISOLATION, "cgroup": cg_id,
                    "error": _startup_error(error, startup["phase"]), **startup})
        except (OSError, ValueError):
            # A failed diagnostic write must never skip containment cleanup.
            pass
        no_child = not spawn_window or startup["clone_refused"]
        if (no_child and cg_id is not None and _cgroup_id(cg) == cg_id
                and not _populated(cg) and _wait_empty(cg) and _cgroup_id(cg) == cg_id):
            terminal = {"state": "refused-before-fork", "request": request.digest, "boot": _boot(),
                        "cgroup": cg_id, "pid": None, "exit_code": None, "isolation": ATTEMPT_ISOLATION}
            if isinstance(request, NeutralLaunchRequest):
                terminal["outcome"] = "cancelled" if (witness / "cancel.json").exists() else "failed"
            _create(witness / "terminal.json", terminal)
        elif spawn_window and cg_id is not None:
            # Best effort containment, but an exception leaves the lease held.
            try:
                _kill(cg)
                if child is not None:
                    child.wait(timeout=4)
                elif startup.get("pid") is not None:
                    # This watcher is still the original child's parent.
                    # waitpid cannot signal or reap an unrelated reused PID.
                    process = _NamespaceProcess(startup["pid"], None)
                    process.wait(timeout=4)
                    _create(witness / "startup-reaped.json", {
                        "request": request.digest, "boot": _boot(), "cgroup": cg_id,
                        "pid": process.pid, "exit_code": process.returncode})
                _wait_empty(cg)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

    finally:
        for fd in projection_fds:
            os.close(fd)


def _observe(cfg, witness, request):
    unknown = LaunchObservation("uncertain", request.digest)
    if not os.path.lexists(witness):
        return LaunchObservation("absent", request.digest)
    _dir(witness, 0, 0o077)
    try:
        origin = _record(witness / "reservation.json", 0)
    except FileNotFoundError:
        # mkdir already won the slot, but the owner may be interrupted before
        # recording its decision. This is not evidence that launch is absent.
        return unknown
    if origin == _reservation(request, decision=CANCEL_BEFORE_START):
        try:
            raw = _read(witness / "terminal.json", 0, durable=True)
        except FileNotFoundError:
            return unknown
        if (_json(raw) == _cancel_terminal(request)
                and not os.path.lexists(_cgroup(cfg, request.slot))
                and not any(os.path.lexists(witness / name) for name in (
                    "cgroup.json", "started.json", "released.json"))):
            # A racing observer may see the create-only terminal file before
            # the winner fsyncs the directory. Finish that durability barrier
            # before returning a release-safe observation.
            _sync(witness)
            _sync(witness.parent)
            return LaunchObservation("refused-before-fork", request.digest, sha256(raw).hexdigest())
        return unknown
    if origin != _reservation(request):
        return unknown
    terminal = witness / "terminal.json"
    if terminal.exists():
        raw = _read(terminal, 0)
        value = _json(raw)
        if (value.get("request") != request.digest or value.get("boot") != _boot()
                or value.get("isolation") != ATTEMPT_ISOLATION):
            return unknown
        cg = _cgroup(cfg, request.slot)
        if (_cgroup_id(cg) != value.get("cgroup") or _populated(cg)
                or _record(witness / "cgroup.json", 0) != {"boot": _boot(), "cgroup": value["cgroup"]}):
            return unknown
        if value.get("state") == "refused-before-fork" and (
                "exit_code" in value and "pid" in value and (value["exit_code"], value["pid"]) == (None, None)) and not (witness / "started.json").exists():
            return LaunchObservation(value["state"], request.digest, sha256(raw).hexdigest())
        if value.get("state") == "exited-clean":
            start = _record(witness / "started.json", 0)
            if (type(value.get("exit_code")) is int and type(value.get("pid")) is int and value["pid"] > 0
                    and type(value.get("start_ticks")) is int and value["start_ticks"] > 0
                    and _valid_namespaces(value.get("namespaces"))
                    and _record(witness / "released.json", 0) == {
                        "request": request.digest, "started_sha256": _digest(start)}
                    and start == {field: value[field] for field in (
                        "pid", "start_ticks", "boot", "cgroup", "namespaces", "request", "isolation")}):
                output = value.get("output_sha256")
                if output is not None and not _sha(output):
                    return unknown
                return LaunchObservation(value["state"], request.digest, sha256(raw).hexdigest(),
                                         value["exit_code"], output)
        return unknown
    if (witness / "started.json").exists():
        start = _record(witness / "started.json", 0)
        cg = _cgroup(cfg, request.slot)
        if (witness / "released.json").exists():
            release = _record(witness / "released.json", 0)
            pid = start.get("pid")
            if (type(pid) is int and pid > 0 and start.get("boot") == _boot()
                    and start.get("request") == request.digest
                    and start.get("isolation") == ATTEMPT_ISOLATION
                    and _valid_namespaces(start.get("namespaces"))
                    and release == {"request": request.digest, "started_sha256": _digest(start)}
                    and _cgroup_id(cg) == start["cgroup"] and _populated(cg)
                    and str(pid) in (cg / "cgroup.procs").read_text().split()
                    and _start_ticks(pid) == start.get("start_ticks")
                    and _namespace_ids(pid) == start["namespaces"]):
                return LaunchObservation("running", request.digest)
            return unknown
    if time.time() - (witness / "reservation.json").stat().st_mtime > 9:
        return unknown
    return LaunchObservation("prepared", request.digest)


def _reservation(request, *, decision=None):
    value = {"schema": SCHEMA, "request": request.digest, "boot": _boot(), "isolation": ATTEMPT_ISOLATION}
    if decision is not None:
        value["decision"] = decision
    return value


def _publish_reservation(witness, value):
    """Publish the exclusive directory and its durable binding together."""
    stage = Path(tempfile.mkdtemp(prefix=".role-helper-stage-", dir=witness.parent))
    record = stage / "reservation.json"
    published = False
    try:
        _create(record, value)
        parent = os.open(witness.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            # Linux x86_64 renameat2, RENAME_NOREPLACE. A normal rename may
            # replace an existing empty reservation and is not a fallback.
            call = _linux().syscall
            if call(316, parent, os.fsencode(stage.name), parent, os.fsencode(witness.name), 1) != 0:
                error = ctypes.get_errno()
                if error == errno.EEXIST:
                    raise FileExistsError(error, os.strerror(error), witness)
                raise LaunchHold(f"role_launch_reservation_renameat2:{error}")
            published = True
        finally:
            os.close(parent)
        _sync(witness.parent)
    finally:
        if not published:
            record.unlink(missing_ok=True)
            stage.rmdir()


def _cancel_terminal(request):
    return {"state": "refused-before-fork", "decision": CANCEL_BEFORE_START,
            "request": request.digest, "boot": _boot(), "isolation": ATTEMPT_ISOLATION,
            "cgroup": None, "pid": None, "exit_code": None}


def _cancel_existing(cfg, witness, request):
    observation = _observe(cfg, witness, request)
    if observation.state not in {"prepared", "running", "uncertain"}:
        return observation
    try:
        origin = _record(witness / "reservation.json", 0)
    except FileNotFoundError:
        return observation
    if origin != _reservation(request):
        return observation
    try:
        _create(witness / "cancel.json", {"request": request.digest})
    except FileExistsError:
        pass
    _sync(witness.parent)
    cg = _cgroup(cfg, request.slot)
    if (witness / "cgroup.json").exists() and _record(witness / "cgroup.json", 0) == {
            "boot": _boot(), "cgroup": _cgroup_id(cg)}:
        _kill(cg)
    return _observe(cfg, witness, request)


def _handle_original(cfg, action, directory, request, *, release_gate=None, project_work=False):
    slot = request.slot
    witness = directory / ".role-helper"
    if action == "observe":
        return _observe(cfg, witness, request)
    on_reserved = _cancel_existing if action == "cancel" else _observe
    if os.path.lexists(witness):
        return on_reserved(cfg, witness, request)

    if action == "cancel" and isinstance(request, NeutralLaunchRequest):
        work, limits = _check_neutral(cfg, directory, request, cancelling=True)
    else:
        work, limits = _check_original(cfg, directory, request)
    if os.path.lexists(_cgroup(cfg, slot)):
        return LaunchObservation("uncertain", request.digest)
    decision = CANCEL_BEFORE_START if action == "cancel" else None
    try:
        _publish_reservation(witness, _reservation(request, decision=decision))
    except FileExistsError:
        return on_reserved(cfg, witness, request)
    if action == "cancel":
        _create(witness / "terminal.json", _cancel_terminal(request))
        return _observe(cfg, witness, request)
    end = time.monotonic() + 8
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            fd = os.open("/dev/null", os.O_RDWR | os.O_CLOEXEC)
            for channel in (0, 1, 2):
                os.dup2(fd, channel)
            os.close(fd)
            os.closerange(3, os.sysconf("SC_OPEN_MAX"))
            _watch(cfg, directory, witness, request, cfg["roles"][request.key.role], work, limits, end,
                   release_gate=release_gate, project_work=project_work)
        finally:
            os._exit(0)
    while time.monotonic() < end:
        observation = _observe(cfg, witness, request)
        if observation.state != "prepared":
            return observation
        time.sleep(0.02)
    return LaunchObservation("uncertain", request.digest)


def _neutral_observation(cfg, directory, request, result):
    value = asdict(result)
    if not result.release_safe:
        return NeutralObservation(**value)
    witness = directory / ".role-helper"
    terminal = _record(witness / "terminal.json", 0, result.receipt_sha256)
    outcome = "cancelled" if terminal.get("decision") == CANCEL_BEFORE_START else terminal.get("outcome")
    if outcome not in NEUTRAL_OUTCOMES:
        raise LaunchHold("neutral_launch_terminal_outcome")
    counts = terminal.get("log_bytes", {"stdout": 0, "stderr": 0})
    truncated = terminal.get("logs_truncated", {"stdout": False, "stderr": False})
    if (set(counts) != {"stdout", "stderr"} or set(truncated) != set(counts)
            or any(type(value) is not int or value < 0 for value in counts.values())
            or any(type(value) is not bool for value in truncated.values())):
        raise LaunchHold("neutral_launch_log_witness")
    intent_bytes = _read_registered_runner(cfg, request, "intent.json")
    intent, intent_sha = _json(intent_bytes), sha256(intent_bytes).hexdigest()
    if intent != request.binding or intent_sha != request.intent_sha256:
        raise LaunchHold("neutral_launch_original_intent")
    try:
        runner = _json(_read_registered_runner(cfg, request, "receipt.json"))
    except FileNotFoundError:
        runner = None
    evidence = None
    if (runner is not None and runner.get("lease_id") == request.key.lease_id
            and runner.get("status") == outcome and runner.get("child_tree_gone") is True
            and runner.get("reconcile_required") is False
            and runner.get("exit_code") == result.exit_code
            and (result.state == "refused-before-fork" or (
                runner.get("log_bytes") == counts and runner.get("logs_truncated") == truncated))
            and all(runner.get(key) == binding for key, binding in intent.items())):
        evidence = {"namespace": NEUTRAL_NAMESPACE, "workspace": "foo", "job_id": intent["job_id"],
                    "lease_id": request.key.lease_id, "binding_sha256": _digest(intent),
                    "runner_intent": intent, "runner_intent_sha256": intent_sha,
                    "runner_receipt": runner, "runner_receipt_sha256": _digest(runner),
                    "trusted_terminal": terminal, "trusted_terminal_sha256": result.receipt_sha256,
                    "reservation": _record(witness / "reservation.json", 0)}
        if terminal.get("cgroup") is not None:
            evidence["cgroup"] = _record(witness / "cgroup.json", 0)
        if result.state == "exited-clean":
            evidence["started"] = _record(witness / "started.json", 0)
            evidence["released"] = _record(witness / "released.json", 0)
        if len(_wire(evidence)) > MAX_RECORD - 4096:
            raise LaunchHold("neutral_launch_evidence_size")
    return NeutralObservation(**value, outcome=outcome, stopped_evidence=evidence,
                              log_bytes=counts, logs_truncated=truncated)


def _handle(cfg, action, slot):
    directory = _dir(_path(cfg["controller_root"]) / slot, cfg["controller_uid"], 0o077)
    request = _request(directory, cfg["controller_uid"])
    if isinstance(request, NeutralLaunchRequest) and "neutral" not in cfg["roles"]:
        raise LaunchHold("neutral_launch_role_not_installed")
    result = _handle_original(cfg, action, directory, request)
    if isinstance(request, NeutralLaunchRequest):
        return _neutral_observation(cfg, directory, request, result)
    return result


def _policy():
    for parent in POLICY.parents:
        _dir(parent, 0, 0o022)
    cfg = _record(POLICY, 0, forbidden=0o022)
    if (cfg["schema"] != SCHEMA or cfg.get("attempt_isolation") != ATTEMPT_ISOLATION or any(type(cfg[field]) is not int or cfg[field] <= 0
                                      for field in ("controller_uid", "controller_gid"))):
        raise LaunchHold("role_launch_policy")
    if os.environ.get("SUDO_UID") != str(cfg["controller_uid"]):
        raise LaunchHold("role_launch_caller")
    control = _dir(_path(cfg["controller_root"]), cfg["controller_uid"], 0o077)
    if control.stat().st_gid != cfg["controller_gid"]:
        raise LaunchHold("role_launch_controller_group")
    cgroup_root = _dir(_path(cfg["cgroup_root"]), 0, 0o022)
    for parent in cgroup_root.parents:
        _dir(parent, 0, 0o022)
    for parent in control.parents:
        info = parent.lstat()
        if (info.st_uid not in {0, cfg["controller_uid"]}
                or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022):
            raise LaunchHold("role_launch_controller_ancestry")
    if not all(_path(cfg[field]).is_relative_to(control) for field in ("pool_database", "operation_locks")):
        raise LaunchHold("role_launch_pool_boundary")
    roles = cfg["roles"]
    if set(roles) not in (set(MODES), set(MODES) | {"neutral"}):
        raise LaunchHold("role_launch_roles")
    uids, gids = {cfg["controller_uid"]}, {cfg["controller_gid"]}
    roots = [control]
    for name, role in roles.items():
        uid, gid = role["uid"], role["gid"]
        root = _path(role["execution_root"] if name == "neutral" else role["root"])
        if name == "neutral":
            _dir(root, role["execution_uid"], 0o022)
            runner = _dir(_path(role["runner_control"]), role["runner_uid"], 0o077)
            if (_overlap(runner, root) or _overlap(runner, control)
                    or runner.stat().st_gid != role["runner_gid"]
                    or any(type(role[field]) is not int or role[field] <= 0
                           for field in ("execution_uid", "runner_uid", "runner_gid", "timeout_seconds"))
                    or role["runner_uid"] in {item["uid"] for item in roles.values()}
                    or role["runner_gid"] in {item["gid"] for item in roles.values()}
                    or any(type(role[field]) is not int or role[field] < 0
                           for field in ("memory_reserve_bytes", "disk_reserve_bytes"))
                    or any(not _sha(role[pin]) for pin in ("registration_sha256", "policy_fingerprint",
                               "descriptor_sha256", "runner_sha256", "capacity_policy_sha256"))):
                raise LaunchHold("neutral_launch_registration")
        else:
            _dir(root, 0, 0o022)
        for parent in root.parents:
            owner = parent.lstat().st_uid
            if name != "neutral" or owner != role["execution_uid"]:
                owner = 0
            _dir(parent, owner, 0o022)
        if (type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0 or uid in uids or gid in gids
                or not isinstance(role.get("runtime_files"), list) or not 1 <= len(role["runtime_files"]) <= 256
                or (name != "neutral" and (type(role["max_processes"]) is not int or role["max_processes"] <= 0
                    or type(role["max_projection_bytes"]) is not int
                    or not 1 <= role["max_projection_bytes"] <= 268435456))
                or any(root.is_relative_to(other) or other.is_relative_to(root) for other in roots)):
            raise LaunchHold("role_launch_role_boundary")
        uids.add(uid)
        gids.add(gid)
        roots.append(root)
    return cfg


def main():
    args = sys.argv[1:]
    if (not sys.flags.isolated or os.getuid() != 0 or os.geteuid() != 0 or len(args) != 2
            or args[0] not in {"start", "observe", "cancel"} or not _sha(args[1])):
        return 2
    os.umask(0o077)
    try:
        result = _handle(_policy(), *args)
        print(_wire({"schema": SCHEMA, **asdict(result)}).decode())
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
