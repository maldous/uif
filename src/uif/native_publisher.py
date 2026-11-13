"""UIF native publisher v1 in the existing publisher claim/receipt namespace.

Retains the intent-before-spawn, original UUID observation, and same-subreaper
terminal proof of native_publisher_invocation.py at 5b8ca36e3a212d60db7b74bb8ccb37e3993bb01e.
The publisher runs as one dedicated process per request on Linux.
"""

from contextlib import contextmanager
import ctypes
from dataclasses import InitVar, asdict, dataclass
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time

from .publication import (
    PUBLISHER_CLEANUP_SECONDS, PUBLISHER_MAX_WORK_SECONDS, PublicationRequest,
    PublisherReceiptPaths, accepted_result, digest, fingerprint, publisher_identity, publisher_source_kind,
    record_digest, require, unique_json_object, uuid, verify_publication_origin, wire,
)
from .publication_rpc import (
    CandidateSource, CanonicalGit, Executable, GitHubPullRequests, PublicationRPC,
    PullRequestCandidate, PullRequestPin,
)
from .source import SourceHold, exact_path, oid
from .ledger import TERMINAL_LINKS
from .stokowski import LedgerAdmission, PolledIssue, UIF
from .task_identity import LedgerTaskEnvelope


LIMIT = 65_536
MODES = {
    "publish-candidate": ("candidate", True), "observe-candidate": ("candidate", False),
    "prepare-pr": ("pr", True), "observe-pr": ("pr", False),
    "qualify-readiness": ("readiness", True), "observe-readiness": ("readiness", False),
    "invoke": ("final", True), "observe": ("final", False),
}
ORIGIN_PINS = (
    "admission", "issue", "policy_sha256", "validation_operation_id", "validation_job_id",
    "validation_receipt_sha256", "publisher_operation_id", "task",
)
SOURCE_PINS = ("source_base", "source_head", "source_tree", "source_branch")


def decode(raw):
    require(0 < len(raw) <= LIMIT, "uif_publisher_json_size")
    def invalid_constant(_):
        raise SourceHold("uif_publisher_json_constant")

    try:
        return json.loads(raw, object_pairs_hook=unique_json_object, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as error:
        raise SourceHold("uif_publisher_json_shape") from error


def fields(value, expected):
    return isinstance(value, dict) and set(value) == set(expected.split())


def check_file(fd, *, uid, mode):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == uid
            and stat.S_IMODE(info.st_mode) == mode, "uif_publisher_file_custody")
    return info


def read(path, *, uid, mode=0o600):
    fd = os.open(exact_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = check_file(stream.fileno(), uid=uid, mode=mode)
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
        require(before.st_size == len(raw) and 0 < len(raw) <= LIMIT
                and (before.st_mtime_ns, before.st_ctime_ns) == (after.st_mtime_ns, after.st_ctime_ns),
                "uif_publisher_file_drift")
    return raw


def read_optional(path, *, uid):
    try:
        raw = read(path, uid=uid)
    except FileNotFoundError:
        return None
    value = decode(raw)
    require(raw == wire(value) + b"\n", "uif_publisher_receipt_encoding")
    return value


def check_directory(path, uid):
    info = exact_path(path).stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700,
            "uif_publisher_directory_custody")


def durable(path, value, *, replace=False):
    """Commit complete canonical bytes and fsync the existing receipt directory."""
    with NamedTemporaryFile(prefix=".native-", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            raw = wire(value) + b"\n"
            require(len(raw) <= LIMIT, "uif_publisher_record_size")
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, path)
            else:
                # Atomic create-only link. No incomplete record is exposed.
                os.link(temporary, path)
                temporary.unlink()
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


def process_start(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    return int(raw.rsplit(")", 1)[1].split()[19])


def inner_response(inner, status):
    return {
        **{key: value for key, value in inner.items() if key != "mode"},
        "kind": f"native-publisher-response-v{inner['version']}", "request_sha256": fingerprint(inner), "phase": "integration",
        "status": status,
    }


def reap(child):
    """Collect direct and adopted children; only ECHILD proves the tree stopped."""
    direct_status = None
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return direct_status, True
        if pid == 0:
            return direct_status, False
        if pid == child:
            direct_status = status


def subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    value = ctypes.c_int()
    require(libc.prctl(37, ctypes.byref(value), 0, 0, 0) == 0, "uif_publisher_subreaper_query")
    return libc, value.value


def run_child(fd, work):
    try:
        os.setsid()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_DFL)
        raw = wire(work())
        require(os.write(fd, raw) == len(raw), "uif_publisher_child_output")
    except BaseException:
        os._exit(1)
    os._exit(0)


def stop_children():
    # This dedicated supervisor had no children before the invocation. pidfds
    # keep timeout cleanup scoped even when an adopted child exits concurrently.
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    for item in path.read_text().split():
        pid = int(item)
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            current = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if int(current[1]) == os.getpid():
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass
        finally:
            os.close(fd)


def supervise(work, inner, *, timeout):
    try:
        os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        pass
    else:
        raise SourceHold("uif_publisher_preexisting_children")
    libc, previous = subreaper()
    require(libc.prctl(36, 1, 0, 0, 0) == 0 and subreaper()[1] == 1,
            "uif_publisher_subreaper")
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    handlers = {}
    cancelled = False
    child = start = direct_status = response = evidence = None
    gone = False
    outcome = "supervisor_error"
    output = bytearray()

    def cancel(*_):
        nonlocal cancelled
        cancelled = True

    try:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, cancel)
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            run_child(write_fd, work)
        os.close(write_fd)
        write_fd = None
        start = process_start(child)
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while True:
                status, gone = reap(child)
                if status is not None:
                    direct_status = status
                if cancelled or time.monotonic() >= deadline:
                    outcome = "cancelled" if cancelled else "timed_out"
                    break
                if gone and not selector.get_map():
                    outcome = "succeeded" if direct_status == 0 else "publisher_error"
                    break
                if not selector.get_map():
                    time.sleep(0.02)
                    continue
                for _, _ in selector.select(0.02):
                    chunk = os.read(read_fd, 4096)
                    if not chunk:
                        selector.unregister(read_fd)
                    output.extend(chunk)
                if len(output) > 4096:
                    outcome = "output_limit"
                    break
    finally:
        try:
            if child is not None and child > 0 and not gone:
                deadline = time.monotonic() + PUBLISHER_CLEANUP_SECONDS
                while not gone and time.monotonic() < deadline:
                    stop_children()
                    status, gone = reap(child)
                    if status is not None:
                        direct_status = status
                    if not gone:
                        time.sleep(0.02)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            libc.prctl(36, previous, 0, 0, 0)

    if outcome == "succeeded":
        try:
            value = decode(bytes(output))
            require(fields(value, "status evidence") and value["status"] in ("verified", "ready", "denied", "unknown")
                    and (value["evidence"] is not None or value["status"] != "verified")
                    and (value["evidence"] is None or value["status"] in ("verified", "unknown"))
                    and (value["status"] != "ready" or inner["mode"] == "observe"),
                    "uif_publisher_child_readback")
            response = inner_response(inner, value["status"])
            evidence = value["evidence"]
        except SourceHold:
            outcome = "invalid_response"
    terminal = {
        "outcome": outcome, "child_started": child is not None, "child_pid": child,
        "child_starttime": start, "subreaper_verified": True,
        "direct_child_reaped": direct_status is not None, "waitpid_echild": gone,
        "child_tree_gone": gone and direct_status is not None,
        "response": response, "response_sha256": fingerprint(response) if response is not None else None,
        "evidence": evidence,
    }
    return terminal


@dataclass(frozen=True)
class PreparedPublication:
    request: PublicationRequest
    pr: PullRequestPin
    test_merge_head: str
    review_facts_sha256: str
    require_atomic_base: bool
    pr_preparation_invocation_id: str
    pr_preparation_receipt_sha256: str

    @classmethod
    def parse(cls, value, *, readiness=False, snapshot_path=None):
        schema = "uif-prepared-publisher-readiness" if readiness else "uif-prepared-native-publication"
        require(fields(value, "schema request pull_request test_merge_head review_facts_sha256 require_atomic_base "
                       "pr_preparation_invocation_id pr_preparation_receipt_sha256")
                and value["schema"] in (publisher_source_kind(schema, 1), publisher_source_kind(schema, 2))
                and oid(value["test_merge_head"]) and digest(value["review_facts_sha256"])
                and type(value["require_atomic_base"]) is bool
                and uuid(value["pr_preparation_invocation_id"])
                and digest(value["pr_preparation_receipt_sha256"]), "uif_publisher_prepared_binding")
        try:
            binding = dict(value["request"])
            if readiness:
                require("readiness_receipt_sha256" not in binding, "uif_publisher_readiness_circular_pin")
                # Validation reuses the frozen request type. This in-memory
                # value never enters the grant, wire, or stored receipt.
                binding["readiness_receipt_sha256"] = "0" * 64
            request = PublicationRequest.from_record(binding, snapshot_path=snapshot_path)
            require(value["schema"] == publisher_source_kind(schema, request.version),
                    "uif_publisher_prepared_binding")
            require(value["pr_preparation_invocation_id"] not in (request.invocation_id, request.readiness_invocation_id),
                    "uif_publisher_pr_preparation_identity")
            return cls(request, PullRequestPin(**value["pull_request"]),
                       value["test_merge_head"], value["review_facts_sha256"], value["require_atomic_base"],
                       value["pr_preparation_invocation_id"], value["pr_preparation_receipt_sha256"])
        except (KeyError, TypeError) as error:
            raise SourceHold("uif_publisher_prepared_binding") from error


def verify_origin(grant):
    if grant.task is not None:
        verify_publication_origin(grant.admission, grant.issue, grant.task)
    else:
        grant.admission.require_implementation()
        require(isinstance(grant.issue, PolledIssue) and grant.issue.project_id == UIF.project_id
                and grant.issue.project_slug == UIF.project_slug and uuid(grant.issue.issue_id)
                and grant.issue.identifier not in TERMINAL_LINKS, "uif_publisher_origin_pins")
    require(uuid(grant.invocation_id) and uuid(grant.validation_job_id)
            and all(digest(value) for value in (
                grant.policy_sha256, grant.validation_operation_id,
                grant.validation_receipt_sha256, grant.publisher_operation_id,
            )), "uif_publisher_origin_pins")


def origin_record(grant):
    record = asdict(grant)
    if grant.task is not None:
        del record["issue"]
        record.update(publisher_identity(None, grant.task))
    return record


def parse_candidate_grant(cls, value, schema, candidate_type, snapshot_path):
    require(isinstance(value, dict), "uif_publisher_source_grant")
    try:
        binding = dict(value)
        tag = binding.pop("schema")
        binding["admission"] = LedgerAdmission(**binding["admission"])
        binding["candidate"] = candidate_type(**binding["candidate"])
        if tag == publisher_source_kind(schema, 1):
            require("task" not in binding, "uif_publisher_source_grant")
            binding["issue"] = PolledIssue(**binding["issue"])
            return cls(**binding)
        require(tag == publisher_source_kind(schema, 2) and "issue" not in binding
                and snapshot_path is not None, "uif_publisher_source_grant_schema")
        task = LedgerTaskEnvelope.from_record(binding.pop("task"), snapshot_path=snapshot_path)
        require(binding.pop("workspace_key") == task.key.workspace_key, "uif_publication_workspace_key")
        result = cls(issue=None, task=task, **binding)
        require(wire({"schema": tag, **result.to_record()}) == wire(value), "uif_publisher_source_grant")
        return result
    except (KeyError, TypeError) as error:
        raise SourceHold("uif_publisher_source_grant") from error


def source_envelope(grant, action, observe_mode, observe):
    version = 2 if grant.task is not None else 1
    return {
        "version": version, "mode": observe_mode if observe else action, "invocation_id": grant.invocation_id,
        "request": {
            "version": version, "mode": action, "operation_id": grant.publisher_operation_id,
            **publisher_identity(grant.issue, grant.task),
            "owner": grant.admission.owner, "binding_sha256": fingerprint(grant.to_record()),
        },
    }


@dataclass(frozen=True)
class PreparedCandidate:
    admission: LedgerAdmission
    issue: PolledIssue | None
    candidate: CandidateSource
    policy_sha256: str
    validation_operation_id: str
    validation_job_id: str
    validation_receipt_sha256: str
    publisher_operation_id: str
    invocation_id: str

    task: InitVar[LedgerTaskEnvelope | None] = None

    def __post_init__(self, task):
        object.__setattr__(self, "task", task)
        verify_origin(self)

    def to_record(self):
        return origin_record(self)

    @classmethod
    def parse(cls, value, *, snapshot_path=None):
        return parse_candidate_grant(cls, value, "uif-prepared-candidate-publication", CandidateSource, snapshot_path)

    def envelope(self, *, observe):
        return source_envelope(self, "publish-candidate", "observe-candidate", observe)


@dataclass(frozen=True)
class PreparedPullRequest:
    """Sealed original PR preparation, before the independent review stage."""

    admission: LedgerAdmission
    issue: PolledIssue | None
    candidate: PullRequestCandidate
    policy_sha256: str
    validation_operation_id: str
    validation_job_id: str
    validation_receipt_sha256: str
    publisher_operation_id: str
    invocation_id: str
    candidate_invocation_id: str
    candidate_receipt_sha256: str

    task: InitVar[LedgerTaskEnvelope | None] = None

    def __post_init__(self, task):
        object.__setattr__(self, "task", task)
        verify_origin(self)
        require(uuid(self.candidate_invocation_id) and self.candidate_invocation_id != self.invocation_id
                and digest(self.candidate_receipt_sha256), "uif_publisher_pr_candidate_receipt")

    def to_record(self):
        return origin_record(self)

    @classmethod
    def parse(cls, value, *, snapshot_path=None):
        return parse_candidate_grant(cls, value, "uif-prepared-pull-request", PullRequestCandidate, snapshot_path)

    def envelope(self, *, observe):
        return source_envelope(self, "prepare-pr", "observe-pr", observe)


def readiness_envelope(prepared, *, observe):
    request = prepared.request
    return {
        "version": request.version, "mode": "observe-readiness" if observe else "qualify-readiness",
        "invocation_id": request.readiness_invocation_id,
        "request": {**request.envelope(observe=False)["request"], "mode": "observe"},
    }


def copy_candidate_bundle(source, target, *, uid, expected):
    fd = os.open(exact_path(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    hasher = sha256()
    with os.fdopen(fd, "rb") as stream, target.open("xb") as output:
        before = check_file(stream.fileno(), uid=uid, mode=0o600)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            output.write(chunk)
        after = os.fstat(stream.fileno())
        require(output.tell() == before.st_size and before.st_size > 0
                and (before.st_mtime_ns, before.st_ctime_ns) == (after.st_mtime_ns, after.st_ctime_ns)
                and hasher.hexdigest() == expected, "uif_candidate_bundle_digest")


def claim_binding(request, binding_sha256, valid_until):
    admitted = request.admission
    return {
        "binding_sha256": binding_sha256, "publisher_operation_id": request.publisher_operation_id,
        "original_operation_id": admitted.original_operation_id, "owner": admitted.owner,
        "epoch": admitted.epoch, "store_id": admitted.store_id, "row_id": admitted.row_id,
        "valid_until": valid_until,
        **(publisher_identity(None, request.task) if request.task is not None else {}),
    }


def verify_record(record, inner, invocation, closure):
    require(fields(record, "version kind intent terminal")
            and type(record["version"]) is int and record["version"] == inner["version"]
            and record["kind"] == f"native-publisher-invocation-record-v{inner['version']}",
            "uif_publisher_original_record")
    intent = record["intent"]
    require(fields(intent, "operation_id invocation_id request request_sha256 closure_sha256 phase supervisor_pid supervisor_starttime")
            and intent["operation_id"] == inner["operation_id"] and intent["invocation_id"] == invocation
            and fingerprint(intent["request"]) == intent["request_sha256"] == fingerprint(inner)
            and intent["closure_sha256"] == closure and intent["phase"] == "integration"
            and type(intent["supervisor_pid"]) is int and intent["supervisor_pid"] > 0
            and type(intent["supervisor_starttime"]) is int and intent["supervisor_starttime"] > 0,
            "uif_publisher_original_intent")


def verify_child_stopped(terminal):
    require(isinstance(terminal, dict) and all(terminal.get(key) is True for key in (
                "child_started", "subreaper_verified", "direct_child_reaped", "waitpid_echild", "child_tree_gone",
            ))
            and type(terminal.get("child_pid")) is int and terminal["child_pid"] > 0
            and type(terminal.get("child_starttime")) is int and terminal["child_starttime"] > 0,
            "uif_publisher_stopped_child")


def verify_stopped(terminal, inner, status):
    verify_child_stopped(terminal)
    require(terminal.get("outcome") == "succeeded"
            and terminal.get("response_sha256") == fingerprint(terminal.get("response"))
            == fingerprint(inner_response(inner, status)), "uif_publisher_stopped_receipt")


def verify_recovery_stop(terminal, intent, inner, phase):
    if phase == "final" and isinstance(terminal, dict) and terminal.get("outcome") == "timed_out":
        verify_child_stopped(terminal)
        require(terminal["child_pid"] != intent["supervisor_pid"]
                and terminal["child_starttime"] >= intent["supervisor_starttime"]
                and all(key in terminal and terminal[key] is None for key in (
                    "response", "response_sha256", "evidence",
                )), "uif_publisher_stopped_timeout")
        return
    verify_stopped(terminal, inner, "unknown")


@dataclass(frozen=True)
class NativePublisher:
    """Registered host implementation. Paths and identities come from its owner."""

    paths: PublisherReceiptPaths
    closure_sha256: str
    repository_id: int
    uid: int
    rpc: PublicationRPC
    timeout: int = 180
    candidate_exclusions_sha256: str | None = None
    snapshot_path: Path | None = None

    def __post_init__(self):
        require(type(self.uid) is int and self.uid > 0 and self.uid == os.getuid() == os.geteuid()
                and digest(self.closure_sha256)
                and type(self.repository_id) is int and self.repository_id > 0
                and type(self.timeout) is int and 0 < self.timeout <= PUBLISHER_MAX_WORK_SECONDS,
                "uif_publisher_host_registration")

    @contextmanager
    def owner_lock(self):
        fd = os.open(exact_path(self.paths.claim_root / "repository-owner.lock"), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            check_file(fd, uid=self.uid, mode=0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SourceHold("uif_publisher_owner_busy") from error
            yield
        finally:
            os.close(fd)

    def stage(self, directory, phase, *, observe):
        filename = "binding.json" if phase == "final" else f"{phase}-binding.json"
        raw = read(directory / filename, uid=self.uid)
        value = decode(raw)
        if phase in ("candidate", "pr"):
            cls = PreparedCandidate if phase == "candidate" else PreparedPullRequest
            prepared = request = cls.parse(value, snapshot_path=self.snapshot_path)
            envelope = request.envelope(observe=observe)
        else:
            prepared = PreparedPublication.parse(value, readiness=phase == "readiness", snapshot_path=self.snapshot_path)
            request = prepared.request
            require(prepared.pr.repository_id == self.repository_id, "uif_publisher_repository_pin")
            envelope = (readiness_envelope(prepared, observe=observe) if phase == "readiness"
                        else request.envelope(observe=observe))
        return prepared, request, envelope, sha256(raw).hexdigest()

    def handle(self, raw):
        envelope = decode(raw)
        require(fields(envelope, "version mode invocation_id request")
                and type(envelope["version"]) is int and envelope["version"] in (1, 2)
                and isinstance(envelope["mode"], str) and envelope["mode"] in MODES
                and uuid(envelope["invocation_id"]), "uif_publisher_envelope")
        inner = envelope["request"]
        require(isinstance(inner, dict) and digest(inner.get("operation_id")), "uif_publisher_operation")
        phase, execute = MODES[envelope["mode"]]
        check_directory(self.paths.claim_root, self.uid)
        directory = self.paths.claim_root / ("transaction-" + inner["operation_id"])
        # The original observation may repair local evidence under the same
        # owner lock. Only an initial execute can enter the mutation RPC.
        with self.owner_lock():
            check_directory(directory, self.uid)
            prepared, request, expected, binding_sha = self.stage(directory, phase, observe=not execute)
            require(wire(envelope) == wire(expected), "uif_publisher_grant_drift")
            return self.transaction(directory, envelope, prepared, request, binding_sha,
                                    execute=execute, phase=phase, reconcile=True)

    def transaction(self, directory, envelope, prepared, request, binding_sha, *, execute, phase, reconcile=False):
        inner = envelope["request"]
        invocation = envelope["invocation_id"]
        path = directory / f"native-invocation-{invocation}.json"
        marker_path = directory / f"native-{phase}-intent.json"
        marker = read_optional(marker_path, uid=self.uid)
        record = read_optional(path, uid=self.uid)
        if marker is not None:
            require(fields(marker, "binding_sha256 original") and marker["binding_sha256"] == binding_sha,
                    "uif_publisher_invocation_rebind")
            original = marker["original"]
            verify_record(original, inner, invocation, self.closure_sha256)
            require(original["terminal"] is None, "uif_publisher_original_marker")
            if record is not None:
                verify_record(record, inner, invocation, self.closure_sha256)
                require(record["intent"] == original["intent"], "uif_publisher_original_intent_drift")
                if reconcile:
                    record = self.reconcile(path, envelope, prepared, record, phase)
            return self.result(envelope, prepared, record if record is not None else original, phase=phase)
        require(record is None, "uif_publisher_original_marker_missing")
        if not execute:
            return self.result(envelope, prepared, None, phase=phase)

        claim = decode(read(self.paths.claim_root / "active-owner.json", uid=self.uid))
        valid_until = claim.get("valid_until") if isinstance(claim, dict) else None
        require(type(valid_until) is int and valid_until > time.time()
                and claim == claim_binding(request, binding_sha, valid_until), "uif_publisher_active_claim")
        success_path = None
        if phase == "candidate":
            require(digest(self.candidate_exclusions_sha256)
                    and prepared.candidate.exclusions_sha256 == self.candidate_exclusions_sha256,
                    "uif_candidate_exclusion_policy")
            success_path = directory / "native-candidate-published.json"
        elif phase == "pr":
            self.verify_candidate(directory, prepared)
            success_path = directory / "native-pr-prepared.json"
        else:
            require(not prepared.require_atomic_base, "uif_publisher_atomic_base_unsupported")
            self.verify_prepared_pr(directory, prepared)
            if phase == "final":
                self.verify_readiness(directory, prepared)
                check_directory(self.paths.accepted_root, self.uid)
                check_directory(self.paths.accepted_root / "by-operation", self.uid)
                success_path = Path(self.paths.accepted(request))
        if success_path is not None:
            require(read_optional(success_path, uid=self.uid) is None, "uif_publisher_accepted_original_exists")
        record = {
            "version": inner["version"], "kind": f"native-publisher-invocation-record-v{inner['version']}",
            "terminal": None,
            "intent": {
                "operation_id": request.publisher_operation_id, "invocation_id": invocation,
                "request": inner, "request_sha256": fingerprint(inner), "phase": "integration",
                "closure_sha256": self.closure_sha256, "supervisor_pid": os.getpid(),
                "supervisor_starttime": process_start(os.getpid()),
            },
        }
        durable(marker_path, {"binding_sha256": binding_sha, "original": record})
        durable(path, record)

        def work():
            if phase == "candidate":
                with TemporaryDirectory(prefix="uif-sealed-candidate-") as temporary:
                    bundle = Path(temporary) / "candidate.bundle"
                    try:
                        copy_candidate_bundle(directory / "candidate.bundle", bundle, uid=self.uid,
                                              expected=prepared.candidate.bundle_sha256)
                    except (OSError, SourceHold):
                        return {"status": "denied", "evidence": None}
                    result = self.rpc.publish_candidate(prepared.candidate, bundle, valid_until=valid_until)
                    return {"status": result.status, "evidence": result.merge_head}
            if phase == "pr":
                status, pin = self.rpc.prepare_pull_request(prepared.candidate, self.repository_id,
                                                            valid_until=valid_until)
                return {"status": status, "evidence": asdict(pin) if pin is not None else None}
            pins = {"test_merge_head": prepared.test_merge_head,
                    "review_facts_sha256": prepared.review_facts_sha256, "valid_until": valid_until}
            if phase == "readiness":
                status = self.rpc.readiness(request, prepared.pr, **pins)
                return {"status": status, "evidence": None}
            result = self.rpc.publish(request, prepared.pr, **pins)
            head = result.merge_head if result.status == "verified" else result.effect_hint
            return {"status": result.status, "evidence": self.merge_evidence(prepared, head) if head else None}

        terminal = supervise(work, inner, timeout=self.timeout)
        record["terminal"] = terminal
        durable(path, record, replace=True)
        return self.result(envelope, prepared, record, phase=phase)

    def reconcile(self, path, envelope, prepared, record, phase):
        inner = envelope["request"]
        terminal = record["terminal"]
        if phase == "readiness" or not terminal or terminal.get("child_tree_gone") is not True:
            return record
        stopped_timeout = phase == "final" and terminal.get("outcome") == "timed_out"
        if not stopped_timeout and terminal.get("response") != inner_response(inner, "unknown"):
            return record
        verify_recovery_stop(terminal, record["intent"], inner, phase)
        hint = terminal.get("evidence")
        try:
            if phase == "candidate":
                evidence = self.rpc.observe_candidate(prepared.candidate)
            elif phase == "pr":
                pin = self.rpc.observe_prepared_pr(prepared.candidate, self.repository_id,
                                                   self.pr_evidence(hint) if hint is not None else None)
                evidence = asdict(pin)
            else:
                expected = self.merge_head(prepared, hint) if hint is not None else None
                evidence = self.merge_evidence(prepared, self.rpc.observe_merge(prepared.request, prepared.pr, expected))
        except (SourceHold, UnicodeError):
            return record

        response = inner_response(inner, "verified")
        # The v1 projection describes successful verification. The original
        # process outcome and stop witness remain bound in its proof below.
        observed = {**terminal, "outcome": "succeeded",
                    "response": response, "response_sha256": fingerprint(response),
                    "evidence": evidence, "reconciliation": {
                        "original_terminal": terminal, "original_receipt_sha256": record_digest(record),
                    }}
        updated = {**record, "terminal": observed}
        self.verify_reconciliation(updated, inner, phase)
        durable(path, updated, replace=True)
        return updated

    def verify_reconciliation(self, record, inner, phase):
        terminal = record["terminal"]
        proof = terminal.get("reconciliation")
        if proof is None:
            return
        require(fields(proof, "original_terminal original_receipt_sha256"),
                "uif_publisher_reconciliation_proof")
        original = proof["original_terminal"]
        verify_recovery_stop(original, record["intent"], inner, phase)
        changed_fields = {"response", "response_sha256", "evidence", "reconciliation"}
        if original["outcome"] == "timed_out":
            changed_fields.add("outcome")
        require("reconciliation" not in original
                and proof["original_receipt_sha256"] == record_digest({**record, "terminal": original})
                and {key: val for key, val in terminal.items() if key not in changed_fields}
                == {key: val for key, val in original.items() if key not in changed_fields},
                "uif_publisher_reconciliation_proof")
        hint = original.get("evidence")
        require(phase in ("candidate", "pr", "final")
                and (hint is None or hint == terminal["evidence"]), "uif_publisher_reconciliation_drift")

    def pr_evidence(self, evidence):
        require(fields(evidence, "repository_id number pull_request_id"), "uif_publisher_pr_evidence")
        pin = PullRequestPin(**evidence)
        require(pin.repository_id == self.repository_id, "uif_publisher_repository_pin")
        return pin

    def merge_evidence(self, prepared, head):
        require(oid(head), "uif_publisher_verified_merge_pin")
        return {"pull_request": asdict(prepared.pr), "merge_head": head}

    def merge_head(self, prepared, evidence):
        require(fields(evidence, "pull_request merge_head")
                and self.pr_evidence(evidence["pull_request"]) == prepared.pr
                and oid(evidence["merge_head"]), "uif_publisher_merge_evidence")
        return evidence["merge_head"]

    def materialize(self, path, expected):
        fact = read_optional(path, uid=self.uid)
        if fact is None:
            durable(path, expected)
            fact = read_optional(path, uid=self.uid)
        require(fact == expected, "uif_publisher_outcome_facet_drift")
        return fact

    def predecessor(self, directory, phase):
        prepared, request, envelope, binding_sha = self.stage(directory, phase, observe=True)
        result = self.transaction(directory, envelope, prepared, request, binding_sha,
                                  execute=False, phase=phase)
        return prepared, result

    def verify_candidate(self, directory, prepared):
        candidate, result = self.predecessor(directory, "candidate")
        fact = result.get("published_candidate")
        require(candidate.invocation_id == prepared.candidate_invocation_id
                and all(getattr(candidate, key) == getattr(prepared, key) for key in ORIGIN_PINS)
                and all(getattr(candidate.candidate, key) == getattr(prepared.candidate, key) for key in SOURCE_PINS)
                and fact is not None and fact["receipt_sha256"] == prepared.candidate_receipt_sha256,
                "uif_publisher_original_candidate_receipt")

    def verify_prepared_pr(self, directory, prepared):
        request = prepared.request
        pr_request, result = self.predecessor(directory, "pr")
        require(pr_request.invocation_id == prepared.pr_preparation_invocation_id
                and all(getattr(pr_request, key) == getattr(request, key) for key in ORIGIN_PINS)
                and all(getattr(pr_request.candidate, key) == getattr(request, key) for key in SOURCE_PINS),
                "uif_publisher_original_pr_binding")
        require(result.get("prepared_pull_request") == {
            "pull_request": asdict(prepared.pr), "receipt_sha256": prepared.pr_preparation_receipt_sha256,
            **(publisher_identity(None, request.task) if request.task is not None else {}),
        }, "uif_publisher_original_pr_receipt")

    def verify_readiness(self, directory, prepared):
        ready, result = self.predecessor(directory, "readiness")
        request = prepared.request
        expected = asdict(prepared)
        expected["request"]["readiness_receipt_sha256"] = "0" * 64
        require(asdict(ready) == expected and ready.request.task == request.task,
                "uif_publisher_original_readiness_binding")
        require(result["status"] == "complete" and result["receipt"]["sha256"] == request.readiness_receipt_sha256,
                "uif_publisher_readiness_pin")
        verify_stopped(result["receipt"]["body"]["terminal"],
                       readiness_envelope(ready, observe=True)["request"], "ready")

    def candidate_fact(self, prepared, record):
        return {**{key: getattr(prepared.candidate, key) for key in SOURCE_PINS},
                "receipt_sha256": record_digest(record),
                **(publisher_identity(None, prepared.task) if prepared.task is not None else {})}

    def fact(self, prepared, record, merge_head):
        require(oid(merge_head), "uif_publisher_verified_merge_pin")
        request = prepared.request
        return {
            "schema": publisher_source_kind("uif-accepted-project-source", request.version),
            "repository": UIF.repository,
            **(publisher_identity(None, request.task) if request.task is not None else {}),
            "branch": UIF.accepted_branch, "head": merge_head, "tree": request.source_tree,
            "parents": [request.source_base, request.source_head],
            "base": request.source_base, "candidate_head": request.source_head,
            "pr_url": prepared.pr.url, "operation_id": request.publisher_operation_id,
            "unit_sha256": request.unit_sha256, "packet_sha256": request.packet_sha256,
            "policy_sha256": request.policy_sha256,
            "helper_receipt": {"path": self.paths.invocation(request), "sha256": record_digest(record)},
        }

    def result(self, envelope, prepared, record, *, phase):
        inner = envelope["request"]
        terminal = record["terminal"] if record else None
        if record is None:
            status = "not-found"
        else:
            status = "complete" if terminal and terminal.get("child_tree_gone") is True else "unknown"
        kind = phase if phase in ("candidate", "pr") else "invocation"
        value = {
            "version": inner["version"], "kind": f"native-publisher-{kind}-v{inner['version']}",
            "operation_id": inner["operation_id"],
            "invocation_id": envelope["invocation_id"], "request_sha256": fingerprint(inner),
            "status": status, "response": terminal["response"] if terminal else None,
            "receipt": {"body": record, "sha256": record_digest(record)} if record else None,
        }
        if phase == "readiness" and status == "complete" and value["response"] == inner_response(inner, "ready"):
            verify_stopped(terminal, inner, "ready")
        if status == "complete" and value["response"] == inner_response(inner, "verified"):
            verify_stopped(terminal, inner, "verified")
            self.verify_reconciliation(record, inner, phase)
            evidence = terminal.get("evidence")
            directory = self.paths.claim_root / ("transaction-" + inner["operation_id"])
            if phase == "candidate":
                require(evidence == prepared.candidate.source_head, "uif_candidate_verified_head")
                value["published_candidate"] = self.materialize(
                    directory / "native-candidate-published.json", self.candidate_fact(prepared, record))
            elif phase == "pr":
                expected = {"pull_request": asdict(self.pr_evidence(evidence)),
                            "receipt_sha256": record_digest(record),
                            **(publisher_identity(None, prepared.task) if prepared.task is not None else {})}
                value["prepared_pull_request"] = self.materialize(directory / "native-pr-prepared.json", expected)
            else:
                request = prepared.request
                path = Path(self.paths.accepted(request))
                check_directory(self.paths.accepted_root, self.uid)
                check_directory(path.parent, self.uid)
                fact = self.materialize(path, self.fact(prepared, record, self.merge_head(prepared, evidence)))
                value["accepted_source"] = {
                    "reference": {"path": str(path), "sha256": record_digest(fact)}, "fact": fact,
                }
                accepted_result(value, request, envelope, self)
        return value


def registered(config_path):
    """Load a root-owned installation binding; the wire supplies no selectors."""
    value = decode(read(config_path, uid=0, mode=0o644))
    config_fields = ("schema claim_root accepted_root closure_sha256 repository_id publisher_uid timeout git gh "
                     "askpass candidate_askpass gh_config_dir candidate_exclusions_sha256")
    version = value.get("schema") if isinstance(value, dict) else None
    snapshot = None
    if version == "uif-native-publisher-config/2":
        require(fields(value, config_fields + " snapshot_path")
                and isinstance(value["snapshot_path"], str), "uif_publisher_config")
        snapshot = exact_path(value["snapshot_path"])
    else:
        require(version == "uif-native-publisher-config/1" and fields(value, config_fields),
                "uif_publisher_config")
    require(digest(value["candidate_exclusions_sha256"]), "uif_publisher_config")

    def executable(item):
        require(fields(item, "path sha256"), "uif_publisher_executable_config")
        return Executable(Path(item["path"]), item["sha256"])

    git = CanonicalGit(executable(value["git"]), executable(value["askpass"]) if value["askpass"] else None,
                       candidate_askpass=executable(value["candidate_askpass"]) if value["candidate_askpass"] else None)
    github = GitHubPullRequests(executable(value["gh"]), Path(value["gh_config_dir"]))
    return NativePublisher(PublisherReceiptPaths(Path(value["accepted_root"]), Path(value["claim_root"])),
                           value["closure_sha256"], value["repository_id"], value["publisher_uid"],
                           PublicationRPC(github, git), value["timeout"],
                           candidate_exclusions_sha256=value["candidate_exclusions_sha256"], snapshot_path=snapshot)


def main(argv):
    try:
        require(len(argv) == 1, "uif_publisher_arguments")
        publisher = registered(Path(argv[0]))
        raw = sys.stdin.buffer.read(16_385)
        require(len(raw) <= 16_384, "uif_publisher_envelope_size")
        sys.stdout.buffer.write(wire(publisher.handle(raw)) + b"\n")
    except SourceHold as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, TypeError):
        return 1
    return 0
