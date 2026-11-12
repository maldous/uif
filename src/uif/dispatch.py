"""Guard the UIF coding supervisor and read the original attempt's evidence.

The supervisor is the existing capacity consumer (Pool.admit/finish). This
adapter owns the preceding dispatch intent and never retries a supervisor
whose creation or outcome is unknown.
"""

import asyncio
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
from uuid import UUID, uuid4

from .source import SourceHold, exact_path, file_digest, verify_checkout
from .stokowski import DispatchReportBinding, LedgerAdmission, UIF


MODEL = "gpt-6.1-sol"
SCHEMA = "uif-dispatch-intent/1"
LEDGER_RECEIPT = "uif-coding-receipt/2"
CAPACITY_NO_START = "uif-coding-capacity-no-start/1"
CAPACITY_REFUSALS = frozenset({
    "capacity_sessions", "capacity_cpu_millis", "capacity_memory_bytes", "capacity_io_slots", "capacity_output_bytes",
})
SAFE_ENV = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR",
            "CODEX_HOME", "CODEX_SQLITE_HOME"}
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimePins:
    """Pins returned by the current owner after context/provider qualification."""

    admission_sha256: str
    provider_sha256: str


@dataclass(frozen=True)
class DispatchOutcome:
    status: str
    operation_id: str
    lease_id: str
    worker_receipt_path: Path
    worker_receipt_sha256: str | None = None
    session_id: str | None = None
    resolved_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def _encode(value):
    # Same canonical digest as retained symphony_capacity and symphony_tasks.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return sha256(_encode(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceHold("uif_dispatch_duplicate_record_key")
        result[key] = value
    return result


def _record(path, expected=None):
    path = exact_path(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o022 or before.st_uid != os.getuid()):
            raise SourceHold("uif_dispatch_record_custody")
        data = stream.read(4 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    digest = sha256(data).hexdigest()
    if (len(data) > 4 * 1024 * 1024 or
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (expected is not None and digest != expected)):
        raise SourceHold("uif_dispatch_record_drift")
    value = json.loads(data, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise SourceHold("uif_dispatch_record_type")
    return value, digest


def _sync_directory(path):
    fd = os.open(exact_path(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise SourceHold("uif_dispatch_directory_custody")
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_intent(path, record):
    # Exclusive directory creation matches CurrentWork's retained-attempt hold,
    # including a crash between mkdir and creation of the intent itself.
    data = _encode(record)
    directory = path.parent
    _sync_directory(directory.parent.parent)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    _sync_directory(directory.parent.parent)
    _sync_directory(directory.parent)
    directory.mkdir(mode=0o700)
    _sync_directory(directory.parent)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(directory)
    return sha256(data).hexdigest()


def _supervisor_identity(pid):
    start_ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    return dict(pid=pid, start_ticks=start_ticks,
                boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def _seal_supervisor_start(directory, pid, operation, intent_sha256, *, invocation_id=None):
    record = dict(**_supervisor_identity(pid), operation_id=operation, dispatch_sha256=intent_sha256)
    if invocation_id is not None:
        record["invocation_id"] = invocation_id
    path = directory / "controller-supervisor.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600), "wb") as stream:
        stream.write(_encode(record))
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(directory)


def _pool_row(database, lease, *, require_database=False):
    """Observe the retained Pool namespace without opening or initializing it for writes."""
    database = exact_path(database)
    try:
        info = database.stat()
    except FileNotFoundError:
        if require_database:
            raise
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_mode & 0o022 or info.st_uid != os.getuid()):
        raise SourceHold("uif_dispatch_pool_custody")
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        return db.execute(
            "SELECT binding, fingerprint, status, outcome FROM leases WHERE id=?", (lease,)
        ).fetchone()
    finally:
        db.close()


def _lease_id(runtime, manifest, identifier, *, ledger_only=False):
    # Keep the retained symphony_tasks v1 lease key for issue-backed work.
    field = "ledger_task" if ledger_only else "issue"
    return "rt-" + _digest(dict(runtime=str(runtime), manifest_sha256=_digest(manifest),
                                **{field: identifier}))


def _invocation(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _watch_pid(callback, pid, active):
    if callback is not None:
        try:
            callback(pid, active)
        except Exception:
            LOG.warning("UIF supervisor PID observer failed")


async def _reap(process):
    while process.poll() is None:
        await asyncio.sleep(0.05)


def capacity_no_start_receipt(intent, dispatch_sha, handoff_sha, supervisor_start_sha, reason):
    return dict(schema=CAPACITY_NO_START, status="capacity-refused", reason=reason,
                child_started=False, child_tree_gone=True, accepted_delivery=False,
                task=intent["task"], operation_id=intent["operation_id"], lease=intent["lease_id"],
                worker_binding_sha256=_digest(intent["worker_binding"]),
                dispatch_sha256=dispatch_sha, handoff_sha256=handoff_sha,
                supervisor_start_sha256=supervisor_start_sha)


def _read_capacity_no_start(binding, intent, result, directory):
    if (binding.task is None or not isinstance(result.get("reason"), str)
            or result["reason"] not in CAPACITY_REFUSALS):
        return False
    handoff, handoff_sha = _record(directory / "controller-supervisor.json")
    start, start_sha = _record(directory / "supervisor-start.json")
    worker_intent, _ = _record(directory / "intent.json")
    identity = {name: handoff[name] for name in ("pid", "start_ticks", "boot_id")}
    return (set(handoff) == {*identity, "operation_id", "dispatch_sha256", "invocation_id"}
            and all(type(identity[name]) is int and identity[name] > 0 for name in ("pid", "start_ticks"))
            and _invocation(identity["boot_id"])
            and _invocation(handoff["invocation_id"])
            and handoff["operation_id"] == binding.operation_id
            and handoff["dispatch_sha256"] == binding.receipt_sha256
            and start == dict(**identity, operation_id=binding.operation_id,
                              dispatch_sha256=binding.receipt_sha256, lease=intent["lease_id"])
            and worker_intent == dict(lease=intent["lease_id"], binding=intent["worker_binding"])
            and result == capacity_no_start_receipt(intent, binding.receipt_sha256, handoff_sha,
                                                    start_sha, result["reason"])
            and result["child_started"] is False and result["child_tree_gone"] is True
            and result["accepted_delivery"] is False
            and _pool_row(intent["pool_database"], intent["lease_id"], require_database=True) is None)


def read_outcome(binding: DispatchReportBinding) -> DispatchOutcome:
    """Resolve the exact registered intent against the original worker and Pool receipts.

    Supervisor exit, later workspace state and report text are not evidence
    of terminal coding. Missing/inconsistent worker or lease evidence is held.
    """
    if not isinstance(binding, DispatchReportBinding) or not isinstance(binding.ledger, LedgerAdmission):
        raise SourceHold("uif_dispatch_report_binding")
    task = getattr(binding, "task", None)
    identifier = binding.issue.identifier if task is None else task.key.workspace_key
    workspace = exact_path(binding.workspace)
    path = exact_path(binding.receipt_path)
    if (workspace.parent.name != "workspaces" or workspace.name != identifier
            or path != workspace.parent.parent / "attempts" / workspace.name / "dispatch-intent.json"):
        raise SourceHold("uif_dispatch_report_path")
    intent, _ = _record(path, binding.receipt_sha256)
    if task is None:
        identity_matches = intent.get("schema") == SCHEMA and intent.get("issue") == asdict(binding.issue)
    else:
        from .stokowski import LEDGER_DISPATCH_INTENT, LEDGER_DISPATCH_INTENT_FIELDS

        identity_matches = (set(intent) == LEDGER_DISPATCH_INTENT_FIELDS
                            and intent.get("schema") == LEDGER_DISPATCH_INTENT
                            and intent.get("task") == task.to_record()
                            and intent.get("operation_id") == binding.operation_id)
    if (not identity_matches or intent.get("admission") != asdict(binding.ledger)
            or binding.operation_id != binding.ledger.original_operation_id
            or intent.get("attempt") != binding.attempt_number or intent.get("workspace") != str(workspace)):
        raise SourceHold("uif_dispatch_report_identity")
    lease = intent["lease_id"]
    receipt_path = path.parent / "receipt.json"
    unknown = DispatchOutcome("uncertain", binding.operation_id, lease, receipt_path)
    try:
        result, digest = _record(receipt_path)
        if result.get("schema") == CAPACITY_NO_START:
            if _read_capacity_no_start(binding, intent, result, path.parent):
                return DispatchOutcome("capacity-refused", binding.operation_id, lease, receipt_path, digest)
            return unknown
        worker_intent, _ = _record(path.parent / "intent.json")
        pool = _pool_row(intent["pool_database"], lease)
        worker_binding = intent["worker_binding"]
        status = result.get("status")
        if task is None:
            receipt_matches = result.get("issue") == identifier and result.get("attempt") == identifier
        else:
            handoff, handoff_sha = _record(path.parent / "controller-supervisor.json")
            invocation = handoff.get("invocation_id")
            receipt_matches = (
                _invocation(invocation) and handoff.get("operation_id") == binding.operation_id
                and handoff.get("dispatch_sha256") == binding.receipt_sha256
                and "issue" not in result and result.get("origin") == "native-coding"
                and result.get("schema") == LEDGER_RECEIPT and result.get("task") == task.to_record()
                and result.get("workspace_key") == identifier and "attempt" in result
                and type(result["attempt"]) is type(binding.attempt_number)
                and result["attempt"] == binding.attempt_number
                and result.get("original_operation_id") == binding.operation_id
                and result.get("invocation_id") == invocation
                and result.get("dispatch_sha256") == binding.receipt_sha256
                and result.get("admission_sha256") == intent["pins"]["admission_sha256"]
                and result.get("handoff_sha256") == handoff_sha
            )
        terminal_pool = {"succeeded": ("finished", "succeeded"),
                         "failed": ("failed", "failed"),
                         "cancelled": ("failed", "cancelled")}
        if (status not in terminal_pool or result.get("child_tree_gone") is not True
                or worker_intent != dict(lease=lease, binding=worker_binding)
                or not receipt_matches or result.get("lease") != lease
                or result.get("parent_binding") != worker_binding
                or result.get("source_head") != worker_binding["head"]
                or result.get("binding_sha256") != _digest(worker_binding)
                or result.get("parent_runtime") != str(workspace.parent.parent)
                or result.get("attempt_path") != str(path.parent)
                or result.get("accepted_delivery") is not False
                or (result.get("provider_route"), result.get("requested_model"), result.get("requested_effort"))
                != ("codex-chatgpt", MODEL, "medium")
                or pool is None or json.loads(pool[0], object_pairs_hook=_unique) != worker_binding
                or pool[1] != _digest(worker_binding) or pool[2:] != terminal_pool[status]):
            return unknown
        if status == "succeeded" and (result.get("turn_completed") is not True
                or result.get("source_identity_changed") is not False or result.get("scope_violations") != []):
            return unknown
        usage = result.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        tokens = tuple(usage.get(key) for key in ("input_tokens", "output_tokens"))
        tokens = tuple(v if type(v) is int and v >= 0 else 0 for v in tokens)
        model = result.get("resolved_model") if result.get("resolution_status") == "observed" else None
        session = result.get("session_id")
        return DispatchOutcome(status, binding.operation_id, lease, receipt_path, digest,
                               session if isinstance(session, str) else None,
                               model if isinstance(model, str) and model.strip() else None, *tokens)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return unknown


class DispatchBackend:
    """Coordinator hook for the pinned retained coding supervisor CLI.

    dispatch_fence(issue, admission) enters the existing owner/epoch fence
    and yields record_intent(path, sha256). This writer must durably compare
    and record the original claim's intent while the fence remains held.
    The owner keeps the sealed runtime inputs fixed and the claim unreusable
    until that intent is reconciled. runtime_pins(issue, admission) comes from
    the current owner qualifier after installation, cancellation, provider
    and retained-process/attempt checks; it is not authority by itself.
    The qualified supervisor owns the actual
    Pool.admit call, process tree and Pool.finish; it must reject reused leases.
    """

    def __init__(self, *, boundary, polled_issue, runtime_pins, dispatch_fence,
                 register_dispatch, supervisor, supervisor_sha256, verify_ledger_dispatch=None):
        self.boundary = boundary
        self.polled_issue = polled_issue
        self.runtime_pins = runtime_pins
        self.dispatch_fence = dispatch_fence
        self.register_dispatch = register_dispatch
        self.supervisor = exact_path(supervisor)
        self.supervisor_sha256 = supervisor_sha256
        self.verify_ledger_dispatch = verify_ledger_dispatch
        self._reapers = set()

    @classmethod
    def for_ledger(cls, authority, *, supervisor, supervisor_sha256):
        from .stokowski import SourceBoundary

        return cls(
            boundary=SourceBoundary(runtime_root=authority.runtime_root_path,
                                    coordinator_runtime=authority.coordinator_runtime_path,
                                    qualify_context=authority.admission.ledger_context,
                                    selected_source=authority.selected_ledger_source),
            polled_issue=None, runtime_pins=authority.admission.runtime_pins,
            dispatch_fence=authority.admission.dispatch_fence, register_dispatch=None,
            supervisor=supervisor, supervisor_sha256=supervisor_sha256,
            verify_ledger_dispatch=authority.admission.ledger.verify_dispatch,
        )

    def _inputs(self, runtime, selected, polled, admission, prompt, timeout, *, task=None, pins=None):
        if task is None:
            identifier = polled.identifier
            pins = self.runtime_pins(polled, admission)
        else:
            from .ledger import ledger_task_target, verify_ledger_task_admission, verify_ledger_task_input

            verify_ledger_task_admission(task, admission)
            identifier = task.key.workspace_key
            if pins is None:
                pins = self.runtime_pins(None, admission, task=task)
        if (not isinstance(pins, RuntimePins)
                or any(not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v)
                       for v in (pins.admission_sha256, pins.provider_sha256))):
            raise SourceHold("uif_dispatch_runtime_pins")
        current, _ = _record(runtime / "current-admission.json", pins.admission_sha256)
        if task is None:
            current_matches = (current.get("issue"), current.get("issue_id")) == (identifier, polled.issue_id)
        else:
            current_matches = (current.get("ledger") == task.to_record()
                               and not {"issue", "issue_id"} & current.keys())
        if (type(current.get("version")) is not int or current["version"] != (1 if task is None else 2)
                or current.get("owner") != admission.owner or not current_matches
                or current.get("source") != str(selected.checkout)):
            raise SourceHold("uif_dispatch_current_admission")
        names = {"manifest_sha256": runtime / "manifest.json",
                 "request_sha256": runtime / "requests" / (identifier + ".json"),
                 "policy_sha256": runtime / "capacity-policy.json",
                 "installation_sha256": runtime / "installation.json"}
        for key, path in names.items():
            if current.get(key) != file_digest(path):
                raise SourceHold("uif_dispatch_runtime_input_drift")
        inputs = current.get("inputs")
        if not isinstance(inputs, dict) or any(file_digest(path) != digest for path, digest in inputs.items()):
            raise SourceHold("uif_dispatch_declared_input_drift")
        manifest, _ = _record(names["manifest_sha256"], current["manifest_sha256"])
        request, _ = _record(names["request_sha256"], current["request_sha256"])
        policy, _ = _record(names["policy_sha256"], current["policy_sha256"])
        provider, _ = _record(runtime / "provider-proof.json", pins.provider_sha256)
        expected_request = dict(prompt=prompt, timeout_seconds=timeout)
        if task is not None:
            target = ledger_task_target(task.key, task.source_sha256)
            verify_ledger_task_input(request, target)
            expected_request["ledger_task"] = target
        if (type(request.get("timeout_seconds")) is not int or not 1 <= timeout <= 600
                or not isinstance(prompt, str) or not prompt.strip()
                or request != expected_request):
            raise SourceHold("uif_dispatch_request_drift")
        tasks = manifest.get("tasks")
        if not isinstance(tasks, dict) or set(tasks) != {identifier} or not isinstance(tasks[identifier], dict):
            raise SourceHold("uif_dispatch_manifest_binding")
        specification = tasks[identifier]
        if task is not None:
            verify_ledger_task_input(specification, target)
        if (manifest.get("project_id") != UIF.project_id
                or (manifest.get("base_revision"), manifest.get("branch")) != (selected.head, selected.branch)
                or specification.get("owner") != admission.owner or "stage" in specification
                or (task is None and specification.get("issue_id") != polled.issue_id)):
            raise SourceHold("uif_dispatch_manifest_binding")
        checked, now = provider.get("checked_at"), time.time()
        if (provider.get("route") != "codex-chatgpt" or provider.get("available") is not True
                or type(checked) not in (int, float) or not math.isfinite(checked)
                or checked > now + 5 or now - checked > policy["symphony"]["provider_proof_ttl_seconds"]
                or type(provider.get("session_ceiling")) is not int or provider["session_ceiling"] < 1):
            raise SourceHold("uif_dispatch_provider_unavailable")
        database = exact_path(policy["symphony"]["pool_path"]) / "state.sqlite"
        worker_binding = dict(branch=selected.branch, head=selected.head, owner=specification["owner"],
                              write_roots=specification["allowed_paths"], resources=specification["resources"],
                              task_sha256=_digest(dict(runtime=str(runtime), manifest=manifest, request=request)))
        return dict(pins=asdict(pins), manifest_sha256=current["manifest_sha256"],
                    request_sha256=current["request_sha256"],
                    lease_id=_lease_id(runtime, manifest, identifier, ledger_only=task is not None),
                    pool_database=str(database), worker_binding=worker_binding)

    async def owned_turn(self, runner_type, claude_cfg, hooks_cfg, prompt, workspace_path,
                         issue, attempt, on_event=None, on_pid=None, env=None):
        """Replacement for the coordinator's implementation run_turn alias."""
        if (runner_type != "codex" or claude_cfg.model != MODEL
                or type(claude_cfg.turn_timeout_ms) is not int
                or any(getattr(hooks_cfg, k, None) for k in ("before_run", "after_run", "on_stage_enter"))):
            raise SourceHold("uif_dispatch_worker_route")
        timeout = min(600, claude_cfg.turn_timeout_ms // 1000)
        polled = self.polled_issue(issue.identifier)
        workspace = exact_path(workspace_path)
        number = attempt.attempt
        if ((polled.project_id, polled.project_slug) != (UIF.project_id, UIF.project_slug)
                or polled.identifier != issue.identifier or polled.issue_id != issue.id
                or polled.identifier in ("UIF-137", "UIF-106")
                or attempt.issue_id != issue.id or attempt.issue_identifier != issue.identifier
                or exact_path(attempt.workspace_path) != workspace
                or (number is not None and (type(number) is not int or number < 0))):
            raise SourceHold("uif_dispatch_attempt_binding")
        def bind():
            return self.boundary._bind(polled, require_ledger=True)

        def register(path, digest, admission, operation):
            self.register_dispatch(issue, attempt, workspace, admission=admission, operation_id=operation,
                                   receipt_path=path, receipt_sha256=digest)

        _, outcome = await self._run(bind, polled, None, number, workspace, prompt, timeout, on_pid,
                                     register, lambda: setattr(attempt, "turn_count", attempt.turn_count + 1))
        attempt.status = "succeeded" if outcome.status == "succeeded" else "failed"
        attempt.session_id = outcome.session_id
        attempt.model = outcome.resolved_model
        attempt.input_tokens = outcome.input_tokens
        attempt.output_tokens = outcome.output_tokens
        attempt.total_tokens = outcome.input_tokens + outcome.output_tokens
        attempt.error = None if outcome.status == "succeeded" else outcome.status
        attempt.result_text = ("Coding attempt ended; independent acceptance is pending."
                               if outcome.status != "uncertain"
                               else "Original coding attempt requires reconciliation.")
        return attempt

    async def owned_ledger_turn(self, task, *, workspace, prompt, timeout, attempt_number=None, on_pid=None):
        """Run one already claimed ledger implementation under the original owner fence."""
        from .task_identity import LedgerTaskEnvelope

        if (not isinstance(task, LedgerTaskEnvelope) or not callable(self.verify_ledger_dispatch)
                or type(timeout) is not int
                or (attempt_number is not None and (type(attempt_number) is not int or attempt_number < 0))):
            raise SourceHold("uif_dispatch_attempt_binding")

        def bind():
            return self.boundary._bind_ledger_task(task)

        def register(path, digest, admission, operation):
            self.verify_ledger_dispatch(admission, None, operation_id=operation, receipt_path=path,
                                        receipt_sha256=digest, task=task)

        return await self._run(bind, None, task, attempt_number, exact_path(workspace), prompt, timeout,
                               on_pid, register, None)

    async def _run(self, bind, polled, task, number, workspace, prompt, timeout, on_pid, register, on_launch):
        identifier = polled.identifier if task is None else task.key.workspace_key
        initial = bind()
        runtime, selected, admission = initial
        operation = admission.original_operation_id
        fence_args = {} if task is None else {"task": task}
        with self.dispatch_fence(polled, admission, **fence_args) as record_intent:
            if bind() != initial:
                raise SourceHold("uif_dispatch_claim_drift")
            if (workspace != runtime / "workspaces" / identifier
                    or any(os.path.lexists(path / "cancel.json") for path in
                           (runtime, self.boundary.coordinator_runtime))):
                raise SourceHold("uif_dispatch_workspace_or_cancelled")
            verify_checkout(selected.checkout, selected.head, selected.tree, selected.branch)
            verify_checkout(workspace, selected.head, selected.tree, selected.branch)
            path = exact_path(runtime / "attempts" / identifier / "dispatch-intent.json")
            if os.path.lexists(path.parent):
                raise SourceHold("uif_dispatch_original_attempt_retained")
            inputs = self._inputs(runtime, selected, polled, admission, prompt, timeout, **fence_args)
            if _pool_row(inputs["pool_database"], inputs["lease_id"]) is not None:
                raise SourceHold("uif_dispatch_original_lease_retained")
            if file_digest(self.supervisor) != self.supervisor_sha256:
                raise SourceHold("uif_dispatch_supervisor_drift")
            if task is None:
                identity = dict(schema=SCHEMA, issue=asdict(polled))
            else:
                from .stokowski import LEDGER_DISPATCH_INTENT

                identity = dict(schema=LEDGER_DISPATCH_INTENT, task=task.to_record(), operation_id=operation)
            record = dict(**identity, admission=asdict(admission),
                          attempt=number, workspace=str(workspace), source=selected.repository,
                          source_head=selected.head, source_tree=selected.tree, source_branch=selected.branch,
                          acquisition_sha256=selected.acquisition_sha256, accepted_sha256=selected.accepted_sha256,
                          supervisor_sha256=self.supervisor_sha256, requested_model=MODEL,
                          requested_effort="medium", **inputs)
            try:
                receipt_sha256 = _write_intent(path, record)
            except FileExistsError as error:
                raise SourceHold("uif_dispatch_original_attempt_retained") from error
            record_intent(path, receipt_sha256)
            register(path, receipt_sha256, admission, operation)
            binding = DispatchReportBinding(polled, number, workspace, operation, path, receipt_sha256, admission,
                                            **fence_args)
            # The ledger intake qualifier closes after intent creation. Read the
            # same pinned inputs under the operation fence and verify the intent.
            recheck_args = {} if task is None else {"task": task, "pins": RuntimePins(**inputs["pins"])}
            if (file_digest(path) != receipt_sha256 or file_digest(self.supervisor) != self.supervisor_sha256
                    or file_digest(selected.accepted_evidence) != selected.accepted_sha256
                    or file_digest(selected.acquisition) != selected.acquisition_sha256
                    or self._inputs(runtime, selected, polled, admission, prompt, timeout, **recheck_args) != inputs
                    or _pool_row(inputs["pool_database"], inputs["lease_id"]) is not None
                    or any(os.path.lexists(p / "cancel.json") for p in
                           (runtime, self.boundary.coordinator_runtime))):
                raise SourceHold("uif_dispatch_prelaunch_drift")
            verify_checkout(selected.checkout, selected.head, selected.tree, selected.branch)
            verify_checkout(workspace, selected.head, selected.tree, selected.branch)
            if task is not None:
                register(path, receipt_sha256, admission, operation)
            child_env = {key: value for key, value in os.environ.items() if key in SAFE_ENV}
            child_env.update(LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false",
                             OTEL_SDK_DISABLED="true", GIT_CONFIG_COUNT="0")
            invocation_id = None if task is None else str(uuid4())
            selector = ["--issue", identifier] if task is None else [
                "--ledger-workspace", identifier, "--invocation-id", invocation_id]
            try:
                process = subprocess.Popen(
                    [sys.executable, str(self.supervisor), "--runtime", str(runtime), *selector],
                    cwd=self.supervisor.parent, env=child_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
            except OSError as error:
                raise SourceHold("uif_dispatch_spawn_requires_reconciliation") from error
            # Reap even if the owner fence fails while exiting. Cancelling the
            # turn leaves supervisor/lease disposition to the existing drain.
            waiter = asyncio.create_task(_reap(process))
            self._reapers.add(waiter)

            def reaped(task):
                self._reapers.discard(task)
                if not task.cancelled() and task.exception() is None:
                    _watch_pid(on_pid, process.pid, False)

            waiter.add_done_callback(reaped)
            try:
                _seal_supervisor_start(path.parent, process.pid, operation, receipt_sha256,
                                       invocation_id=invocation_id)
            except (OSError, ValueError) as error:
                raise SourceHold("uif_dispatch_supervisor_handoff_unknown") from error
            if on_launch is not None:
                on_launch()
            _watch_pid(on_pid, process.pid, True)
        await asyncio.shield(waiter)
        return binding, read_outcome(binding)
