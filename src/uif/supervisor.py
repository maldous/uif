"""Run one already dispatched UIF coding attempt under the installed native guard.

This is a single-purpose process, invoked as an installed script by dispatch.
The original attempt remains held after a crash. Only this supervisor can use
its kernel child-reaping witness to release the capacity it admitted.
"""

import argparse
import ctypes
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time
from uuid import UUID

from uif.dispatch import (
    CAPACITY_REFUSALS, LEDGER_RECEIPT, MODEL, SCHEMA, _digest, _invocation, _lease_id, _record,
    _supervisor_identity, _sync_directory, capacity_no_start_receipt,
)
from uif.source import SourceHold, exact_path, file_digest, git, verify_checkout
from uif.stokowski import LedgerAdmission, UIF


POLICY = Path("/etc/uif/coding-supervisor.json")
TYPED_POLICY = Path("/etc/uif/typed-supervisor.json")
TYPED_HELPER = Path("/usr/local/libexec/uif-typed-role-helper")
SUDO = Path("/usr/bin/sudo")
NATIVE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
LEDGER_FIELDS = ("store_id", "row_id", "snapshot_sha256", "source_sha256",
                 "epoch", "original_operation_id")
LOG_LIMIT = 1_048_576


def _trusted(path, executable=False, *, setuid=False):
    """Require a canonical root-owned installation path, including its parents."""
    path = exact_path(path)
    for part in path.parents:
        info = part.lstat()
        if info.st_uid != 0 or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise SourceHold("uif_supervisor_installation_parent")
    info = path.lstat()
    if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
            or info.st_nlink != 1 or info.st_mode & stat.S_ISGID
            or bool(info.st_mode & stat.S_ISUID) != setuid
            or (executable and not info.st_mode & 0o100)):
        raise SourceHold("uif_supervisor_installation_file")
    return path


def _installed(runtime, installation, *, typed=False):
    policy_path = _trusted(TYPED_POLICY if typed else POLICY)
    expected = installation.get("supervisor_policy_sha256")
    if file_digest(policy_path) != expected:
        raise SourceHold("uif_supervisor_policy_pin")
    raw = policy_path.read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise SourceHold("uif_supervisor_policy_pin")
    cfg = json.loads(raw)
    required = {"schema", "runtime_root", "coordinator_runtime", "pool_root",
                "uid", "capacity", "role_helper", "role_policy", "owner_registration"}
    if (set(cfg) != required or cfg["schema"] != "uif-coding-supervisor/2"
            or type(cfg["uid"]) is not int or cfg["uid"] <= 0
            or os.getuid() != cfg["uid"] or os.geteuid() != cfg["uid"]):
        raise SourceHold("uif_supervisor_policy")
    if typed:
        ordinary = json.loads(_trusted(POLICY).read_bytes())
        if (set(ordinary) != required
                or any(cfg[field] != ordinary[field] for field in required - {"role_helper", "role_policy"})):
            raise SourceHold("uif_supervisor_shared_binding")
    root = exact_path(cfg["runtime_root"])
    coordinator = exact_path(cfg["coordinator_runtime"])
    pool = exact_path(cfg["pool_root"])
    if (runtime == root or not runtime.is_relative_to(root)
            or not coordinator.is_relative_to(root) or runtime == coordinator
            or not pool.is_relative_to(root)):
        raise SourceHold("uif_supervisor_runtime_binding")
    for directory in (runtime, coordinator, pool):
        _owned_dir(directory, root)

    for name in ("capacity", "role_policy", "owner_registration"):
        ref = cfg[name]
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise SourceHold("uif_supervisor_installed_binding")
        path = _trusted(ref["path"])
        if file_digest(path) != _pin(ref["sha256"]):
            raise SourceHold("uif_supervisor_installed_digest")

    from uif.owner_runtime import OwnerAuthority
    from uif import role_broker

    role_policy = cfg["role_policy"]
    selected_role_policy = role_broker.TYPED_POLICY if typed else role_broker.POLICY
    if exact_path(role_policy["path"]) != selected_role_policy:
        raise SourceHold("uif_supervisor_role_policy_path")
    role_raw = selected_role_policy.read_bytes()
    if sha256(role_raw).hexdigest() != role_policy["sha256"]:
        raise SourceHold("uif_supervisor_installed_digest")
    roles = json.loads(role_raw)
    if typed:
        ordinary_role = ordinary["role_policy"]
        ordinary_path = _trusted(ordinary_role["path"])
        ordinary_raw = ordinary_path.read_bytes()
        if ordinary_path != role_broker.POLICY or sha256(ordinary_raw).hexdigest() != ordinary_role["sha256"]:
            raise SourceHold("uif_supervisor_shared_binding")
        ordinary_roles = json.loads(ordinary_raw)
        native_assets = {"guard", "dropper", "runtime_files"}
        if ({key: value for key, value in roles.items() if key != "roles"}
                != {key: value for key, value in ordinary_roles.items() if key != "roles"}
                or set(roles["roles"]) != set(ordinary_roles["roles"])
                or roles["roles"].get("neutral") != ordinary_roles["roles"].get("neutral")
                or any({key: value for key, value in roles["roles"][role].items() if key not in native_assets}
                       != {key: value for key, value in ordinary_roles["roles"][role].items()
                           if key not in native_assets} for role in role_broker.MODES)):
            raise SourceHold("uif_supervisor_shared_binding")
    isolation = "uif-role-attempt-namespaces/1"
    if roles.get("attempt_isolation") != isolation or getattr(role_broker, "ATTEMPT_ISOLATION", None) != isolation:
        raise SourceHold("uif_supervisor_attempt_isolation_unqualified")
    owner = cfg["owner_registration"]
    authority = OwnerAuthority.load(owner["path"], owner["sha256"])
    control = exact_path(roles["controller_root"])
    if (roles["schema"] != "uif-role-launch/1" or roles["controller_uid"] != cfg["uid"]
            or roles["controller_gid"] != os.getegid()
            or authority.runtime_root_path != root or authority.coordinator_runtime_path != coordinator
            or authority.pool_database_path != pool / "state.sqlite"
            or roles["pool_database"] != str(authority.pool_database_path)
            or roles["operation_locks"] != str(authority.operation_locks_path)
            or control != root or set(roles["roles"]) not in ({"coding", "review"}, {"coding", "review", "neutral"})):
        raise SourceHold("uif_supervisor_role_binding")
    helper = cfg["role_helper"]
    if not isinstance(helper, dict) or set(helper) != {"path", "sha256"}:
        raise SourceHold("uif_supervisor_role_binding")
    if typed and exact_path(helper["path"]) != TYPED_HELPER:
        raise SourceHold("uif_supervisor_typed_helper_path")
    cfg.update(runtime_root=root, coordinator_runtime=coordinator, pool_root=pool,
               capacity=exact_path(cfg["capacity"]["path"]), controller_root=control,
               role_broker=role_broker.RoleBroker(helper["path"], _pin(helper["sha256"])), role_limits=roles["roles"],
               authority=authority)
    return cfg


def _owned_dir(path, root):
    for item in (path, *path.parents):
        if not item.is_relative_to(root):
            break
        info = item.lstat()
        if (info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022):
            raise SourceHold("uif_supervisor_directory_custody")


def load_capacity(cfg):
    # Load only the installed product-independent Pool implementation whose
    # exact root-owned file was selected by the pinned policy.
    spec = importlib.util.spec_from_file_location("_uif_capacity", cfg["capacity"])
    if spec is None:
        raise SourceHold("uif_supervisor_capacity_binding")
    module = importlib.util.module_from_spec(spec)
    # Read verified source directly. Do not load or create a bytecode cache.
    exec(compile(cfg["capacity"].read_bytes(), str(cfg["capacity"]), "exec"), module.__dict__)
    return module


def _exclusive(path, value):
    data = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _pin(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise SourceHold("uif_supervisor_missing_pin")
    return value


def _inputs(runtime, issue, *, task=None):
    if task is None and (not re.fullmatch(r"UIF-[1-9][0-9]*", issue) or issue in {"UIF-106", "UIF-137"}):
        raise SourceHold("uif_supervisor_issue")
    directory = exact_path(runtime / "attempts" / issue)
    _owned_dir(directory, runtime)
    dispatch, dispatch_sha = _record(directory / "dispatch-intent.json")
    ledger = LedgerAdmission(**dispatch["admission"])
    ledger.require_implementation()
    if task is None:
        identity = dispatch["issue"]
        issue_id = identity["issue_id"]
        identity_matches = (dispatch["schema"] == SCHEMA and identity["identifier"] == issue
                            and (identity["project_id"], identity["project_slug"]) == (UIF.project_id, UIF.project_slug)
                            and str(UUID(issue_id)) == issue_id)
    else:
        from uif.ledger import ledger_task_target, verify_ledger_task_admission, verify_ledger_task_input
        from uif.stokowski import LEDGER_DISPATCH_INTENT, LEDGER_DISPATCH_INTENT_FIELDS

        verify_ledger_task_admission(task, ledger)
        identity_matches = (set(dispatch) == LEDGER_DISPATCH_INTENT_FIELDS
                            and dispatch["schema"] == LEDGER_DISPATCH_INTENT
                            and dispatch["task"] == task.to_record() and task.key.workspace_key == issue
                            and dispatch["operation_id"] == task.original_operation_id
                            and (dispatch["attempt"] is None
                                 or (type(dispatch["attempt"]) is int and dispatch["attempt"] >= 0)))
    if (not identity_matches or dispatch["source"] != UIF.repository
            or (dispatch["requested_model"], dispatch["requested_effort"]) != (MODEL, "medium")
            or file_digest(Path(__file__)) != _pin(dispatch["supervisor_sha256"])):
        raise SourceHold("uif_supervisor_dispatch_binding")
    workspace = exact_path(runtime / "workspaces" / issue)
    if dispatch["workspace"] != str(workspace):
        raise SourceHold("uif_supervisor_workspace_binding")
    current, _ = _record(runtime / "current-admission.json", _pin(dispatch["pins"]["admission_sha256"]))
    claim = current.get("ledger")
    if task is None:
        claim_matches = ((current["issue"], current["issue_id"]) == (issue, issue_id)
                         and isinstance(claim, dict)
                         and all(claim.get(field) == getattr(ledger, field) for field in LEDGER_FIELDS))
    else:
        claim_matches = (claim == task.to_record() and not {"issue", "issue_id"} & current.keys())
    if (type(current.get("version")) is not int or current["version"] != (1 if task is None else 2)
            or current["owner"] != ledger.owner or not claim_matches):
        raise SourceHold("uif_supervisor_claim_binding")
    for name, expected in current["inputs"].items():
        if file_digest(name) != _pin(expected):
            raise SourceHold("uif_supervisor_declared_input")
    manifest, _ = _record(runtime / "manifest.json", _pin(current["manifest_sha256"]))
    request, _ = _record(runtime / "requests" / (issue + ".json"), _pin(current["request_sha256"]))
    policy, _ = _record(runtime / "capacity-policy.json", _pin(current["policy_sha256"]))
    installation, _ = _record(runtime / "installation.json", _pin(current["installation_sha256"]))
    provider, _ = _record(runtime / "provider-proof.json", _pin(dispatch["pins"]["provider_sha256"]))
    specification = manifest["tasks"][issue]
    head, tree, branch = (dispatch[name] for name in ("source_head", "source_tree", "source_branch"))
    binding = dict(branch=branch, head=head, owner=ledger.owner, write_roots=specification["allowed_paths"],
                   resources=specification["resources"], task_sha256=_digest(
                       dict(runtime=str(runtime), manifest=manifest, request=request)))
    lease = _lease_id(runtime, manifest, issue, ledger_only=task is not None)
    request_fields = {"prompt", "timeout_seconds"}
    if task is not None:
        target = ledger_task_target(task.key, task.source_sha256)
        verify_ledger_task_input(specification, target)
        verify_ledger_task_input(request, target)
        request_fields.add("ledger_task")
    if (manifest["project_id"] != UIF.project_id or set(manifest["tasks"]) != {issue}
            or (manifest["base_revision"], manifest["branch"]) != (head, branch)
            or specification["owner"] != ledger.owner or "stage" in specification
            or (task is None and specification["issue_id"] != issue_id)
            or (dispatch["manifest_sha256"], dispatch["request_sha256"])
            != (current["manifest_sha256"], current["request_sha256"])
            or dispatch["worker_binding"] != binding or dispatch["lease_id"] != lease
            or set(request) != request_fields or not isinstance(request["prompt"], str)
            or not request["prompt"].strip() or type(request["timeout_seconds"]) is not int
            or not 1 <= request["timeout_seconds"] <= 600):
        raise SourceHold("uif_supervisor_runtime_inputs")
    verify_checkout(exact_path(current["source"]), head, tree, branch)
    verify_checkout(workspace, head, tree, branch)
    return dispatch, dispatch_sha, directory, workspace, binding, lease, request, policy, installation, provider


def _subreaper():
    # This process must be dedicated to exactly one coding attempt. When a
    # descendant forks and leaves the process group, the kernel reparents it
    # here once its parents exit. Use kernel child state to prove cleanup.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise SourceHold("uif_supervisor_subreaper")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise SourceHold("uif_supervisor_pidfd_unavailable")


def _children():
    return [int(pid) for pid in Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()]


def _proc_stat(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), int(fields[19]), int(fields[21])


def _signal_child(pid, signum):
    # A pidfd pins the task. Check current parentage after opening it, so a
    # recycled PID or a sibling from another attempt cannot be signalled.
    try:
        fd = os.pidfd_open(pid)
        try:
            if _proc_stat(pid)[0] == os.getpid():
                signal.pidfd_send_signal(fd, signum)
        finally:
            os.close(fd)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        # A subsequent kernel wait decides whether cleanup actually succeeded.
        return


def _cleanup(child, seconds=3):
    deadline = time.monotonic() + seconds
    signum = signal.SIGTERM
    while True:
        if child is not None:
            child.poll()
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid and child is not None and pid == child.pid:
            # Popen did not reap this child. Do not project the exit as success.
            child.returncode = 1
        now = time.monotonic()
        if now >= deadline:
            return False
        if deadline - now < seconds / 2:
            signum = signal.SIGKILL
        for pid in _children():
            _signal_child(pid, signum)
        time.sleep(0.02)


def _usage():
    """Bound resource use across the process tree, including separate sessions."""
    processes = {os.getpid()}
    total_rss = 0
    pending = {}
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            try:
                parent, _, rss = _proc_stat(int(path.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            pending.setdefault(parent, []).append((int(path.name), max(0, rss)))
    queue = [os.getpid()]
    for parent in queue:
        for pid, rss in pending.get(parent, []):
            if pid not in processes:
                processes.add(pid)
                queue.append(pid)
                total_rss += rss
    return len(processes) - 1, total_rss * os.sysconf("SC_PAGE_SIZE")


def _log(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600), "wb", buffering=0)


def _cancelled(runtime, cfg):
    return any(os.path.lexists(path / "cancel.json") for path in (runtime, cfg["coordinator_runtime"]))


def _command(cfg, workspace, directory, home, mode):
    return [str(SUDO), "-n", "--", str(cfg["dropper"]), mode, str(workspace),
            str(directory / "final-message.txt"), str(home), str(directory / "codex-state")]


def _supervise(command, workspace, directory, request, stop, resource_check):
    child = None
    result = dict(status="uncertain", child_tree_gone=False, exit_code=None)
    try:
        if stop():
            return dict(status="cancelled", child_tree_gone=True, child_started=False, exit_code=None)
        with _log(directory / "stdout.jsonl") as out, _log(directory / "stderr.log") as err:
            if stop():
                return dict(status="cancelled", child_tree_gone=True, child_started=False, exit_code=None)
            child = subprocess.Popen(command, cwd=workspace, env=NATIVE_ENV, close_fds=True,
                                     stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
            result["child_started"] = True
            os.set_blocking(child.stdin.fileno(), False)
            prompt = memoryview(request["prompt"].encode())
            deadline = time.monotonic() + request["timeout_seconds"]
            while child.poll() is None:
                if stop():
                    result["status"] = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    result.update(status="failed", reason="timed_out")
                    break
                reason = resource_check()
                if reason:
                    result.update(status="failed", reason=reason)
                    break
                if out.tell() + err.tell() > LOG_LIMIT:
                    result.update(status="failed", reason="log_budget")
                    break
                if not child.stdin.closed:
                    try:
                        if prompt:
                            prompt = prompt[os.write(child.stdin.fileno(), prompt[:65536]):]
                        else:
                            child.stdin.close()
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        child.stdin.close()
                time.sleep(0.02)
            else:
                result["status"] = "succeeded" if child.returncode == 0 else "failed"
            if out.tell() + err.tell() > LOG_LIMIT and result["status"] == "succeeded":
                result.update(status="failed", reason="log_budget")
    except Exception as error:
        result["error_type"] = type(error).__name__
    finally:
        if child is not None:
            child.stdin.close()
            try:
                result["child_tree_gone"] = _cleanup(child)
                result["exit_code"] = child.returncode
            except (OSError, ValueError, IndexError):
                pass
    if not result["child_tree_gone"]:
        result["status"] = "uncertain"
    return result


def _events(path):
    result = dict(resolved_model=None, resolution_status="unobserved", turn_completed=False,
                  session_id=None, usage={})
    try:
        fd = os.open(exact_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return result
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise SourceHold("uif_supervisor_event_custody")
        data = stream.read(LOG_LIMIT + 1)
    if len(data) > LOG_LIMIT:
        return result
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            result["session_id"] = event["thread_id"]
        if event.get("type") == "turn.completed":
            model = event.get("model")
            usage = event.get("usage")
            result["turn_completed"] = True
            result["usage"] = {k: usage[k] for k in ("input_tokens", "output_tokens")
                               if isinstance(usage, dict) and type(usage.get(k)) is int and usage[k] >= 0}
            result["resolved_model"] = model if isinstance(model, str) and model.strip() else None
            result["resolution_status"] = "observed" if result["resolved_model"] else "unobserved"
    return result


def _check_output(workspace, directory, dispatch, binding):
    changed = git(workspace, "status", "--porcelain=v2", "-z", "--no-renames",
                  "--untracked-files=all", "--ignored=matching")
    paths = []
    for entry in changed.rstrip("\0").split("\0") if changed else ():
        if entry.startswith("1 "):
            paths.append(entry.split(" ", 8)[8])
        elif entry.startswith(("? ", "! ")):
            paths.append(entry[2:])
        else:
            raise SourceHold("uif_supervisor_unmerged_or_unknown_output")
    violations = [p for p in paths if not any(p == root or p.startswith(root + "/")
                                             for root in binding["write_roots"])]
    identity_changed = (
        git(workspace, "rev-parse", "HEAD") != dispatch["source_head"]
        or git(workspace, "rev-parse", "HEAD^{tree}") != dispatch["source_tree"]
        or git(workspace, "symbolic-ref", "-q", "HEAD") != "refs/heads/" + dispatch["source_branch"])
    outputs = {}
    size = 0
    allowed_paths = [name for name in paths if name not in violations]
    for name in [*allowed_paths, str(directory / "final-message.txt")]:
        path = workspace / name
        if not path.exists() and not path.is_symlink():
            continue
        if path != path.resolve() or not path.is_file() or path.stat().st_nlink != 1:
            violations.append(name)
            continue
        size += path.stat().st_size
        if size > binding["resources"]["output_bytes"]:
            violations.append("output_budget")
            break
        if not Path(name).is_absolute():
            outputs[name] = file_digest(path)
    return dict(scope_violations=violations, source_identity_changed=identity_changed, output_sha256=outputs)


def prepare_guarded_runtime(runtime, installation, *, role_launch=False, typed=False):
    cfg = _installed(exact_path(runtime), installation, typed=typed)
    if "role_broker" in cfg and role_launch is not True:
        raise SourceHold("uif_supervisor_role_launch_required")
    _subreaper()
    return cfg


def run_guarded_turn(cfg, *, runtime, issue, workspace, directory, request, mode, stop, resource_check):
    """Run one guarded attempt. The caller owns claim, intent and admission."""
    if "role_broker" in cfg:
        raise SourceHold("uif_supervisor_role_launch_required")
    runtime, workspace, directory = map(exact_path, (runtime, workspace, directory))
    root = cfg["runtime_root"]
    if (not re.fullmatch(r"UIF-[1-9][0-9]*", issue) or not runtime.is_relative_to(root)
            or directory.parent != runtime / "attempts"
            or set(request) != {"prompt", "timeout_seconds"}
            or not isinstance(request["prompt"], str) or not request["prompt"].strip()
            or type(request["timeout_seconds"]) is not int
            or not 1 <= request["timeout_seconds"] <= 600):
        raise SourceHold("uif_supervisor_guarded_request")
    standard = workspace == runtime / "workspaces" / issue
    if mode == "workspace-write":
        allowed = standard and directory.name == issue
    elif mode == "read-only":
        candidate = (workspace.is_relative_to(root)
                     and workspace.relative_to(root).parts[-3:] == ("contributions", issue, "candidate"))
        allowed = (standard or candidate) and re.fullmatch(
            r"review-" + re.escape(issue) + r"(?:-[a-zA-Z0-9_-]+)?", directory.name)
    else:
        allowed = False
    if not allowed:
        raise SourceHold("uif_supervisor_guarded_path_or_mode")
    for path in (workspace, directory):
        _owned_dir(path, root)
    _subreaper()
    home = Path(tempfile.mkdtemp(prefix="codex-home-", dir=directory))
    auth_link = home / "auth.json"
    auth_link.symlink_to(cfg["auth_reference"])
    interrupted = False

    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = _supervise(_command(cfg, workspace, directory, home, mode), workspace, directory,
                            request, lambda: interrupted or stop() or _cancelled(runtime, cfg),
                            resource_check)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if result["child_tree_gone"]:
        try:
            auth_link.unlink()
            result.update(_events(directory / "stdout.jsonl"))
        except (OSError, ValueError) as error:
            result.update(status="failed", error_type=type(error).__name__)
    return result


def run_registered_role_turn(cfg, launch, *, directory, workspace, write_roots, output_bytes, stop):
    from uif.controller_launch import run_role_launch

    result = run_role_launch(cfg, launch, directory=directory, workspace=workspace,
                             write_roots=write_roots, output_bytes=output_bytes, stop=stop)
    if result["status"] == "succeeded":
        try:
            result.update(_events(directory / "stdout.jsonl"))
        except (OSError, ValueError) as error:
            result.update(status="failed", error_type=type(error).__name__)
    return result


def finish_attempt(pool, lease, receipt_path, result):
    """Keep the original receipt before releasing positively reaped capacity."""
    _exclusive(receipt_path, result)
    if result["child_tree_gone"] is True and result["status"] in {"succeeded", "failed", "cancelled"}:
        pool.finish(lease, True, result["status"])


def execute(runtime, issue=None, *, ledger_workspace=None, invocation_id=None):
    runtime = exact_path(runtime)
    task = None
    cfg = None
    if ledger_workspace is not None:
        from uif.task_identity import LedgerTaskEnvelope

        if issue is not None or not re.fullmatch(r"ledger-[0-9a-f]{64}", ledger_workspace) or not _invocation(invocation_id):
            raise SourceHold("uif_supervisor_ledger_invocation")
        issue = ledger_workspace
        directory = exact_path(runtime / "attempts" / issue)
        _owned_dir(directory, runtime)
        dispatch, original_sha = _record(directory / "dispatch-intent.json")
        claim, _ = _record(runtime / "current-admission.json", _pin(dispatch["pins"]["admission_sha256"]))
        installation, _ = _record(runtime / "installation.json", _pin(claim["installation_sha256"]))
        cfg = prepare_guarded_runtime(runtime, installation, role_launch=True, typed=True)
        if "role_broker" not in cfg:
            raise SourceHold("uif_supervisor_role_launch_required")
        task = LedgerTaskEnvelope.from_record(dispatch["task"], snapshot_path=cfg["authority"].snapshot_path)
    elif issue is None or invocation_id is not None:
        raise SourceHold("uif_supervisor_ledger_invocation")
    (dispatch, dispatch_sha, directory, workspace, binding, lease, request, policy,
     installation, provider) = _inputs(runtime, issue, task=task)
    if task is None:
        cfg = prepare_guarded_runtime(runtime, installation, role_launch=True)
    elif dispatch_sha != original_sha:
        raise SourceHold("uif_supervisor_dispatch_binding")
    if (cfg["pool_root"] != exact_path(policy["symphony"]["pool_path"])
            or dispatch["pool_database"] != str(cfg["pool_root"] / "state.sqlite")):
        raise SourceHold("uif_supervisor_pool_binding")
    if "role_broker" in cfg:
        from uif.stokowski import PolledIssue

        authority = cfg["authority"]
        admission = LedgerAdmission(**dispatch["admission"])
        with authority.lock(operation=admission.original_operation_id, wait=5):
            polled = PolledIssue(**dispatch["issue"]) if task is None else None
            fence_args = {} if task is None else {"task": task}
            entry, current_runtime = authority._active(admission, polled, **fence_args)
            recorded, handoff_sha = _record(directory / "controller-supervisor.json")
            expected = dict(**_supervisor_identity(os.getpid()), operation_id=admission.original_operation_id,
                            dispatch_sha256=dispatch_sha)
            if task is not None:
                expected["invocation_id"] = invocation_id
                selected = authority._source(entry)
                current, _ = _record(runtime / "current-admission.json", dispatch["pins"]["admission_sha256"])
                if (str(selected.checkout) != current["source"] or selected.repository != dispatch["source"]
                        or selected.accepted_branch != UIF.accepted_branch or selected.head != dispatch["source_head"]
                        or selected.tree != dispatch["source_tree"] or selected.branch != dispatch["source_branch"]
                        or selected.accepted_sha256 != dispatch["accepted_sha256"]
                        or selected.acquisition_sha256 != dispatch["acquisition_sha256"]
                        or file_digest(selected.accepted_evidence) != selected.accepted_sha256
                        or file_digest(selected.acquisition) != selected.acquisition_sha256):
                    raise SourceHold("uif_supervisor_ledger_source")
            if (current_runtime != runtime or recorded != expected
                    or entry["claim"]["sha256"] != dispatch["pins"]["admission_sha256"]
                    or file_digest(directory / "dispatch-intent.json") != dispatch_sha):
                raise SourceHold("uif_supervisor_controller_handoff")
            return _execute_attempt(cfg, runtime, issue, dispatch, dispatch_sha, directory, workspace,
                                    binding, lease, request, policy, provider, task=task,
                                    invocation_id=invocation_id, handoff_sha=handoff_sha)
    return _execute_attempt(cfg, runtime, issue, dispatch, dispatch_sha, directory, workspace,
                            binding, lease, request, policy, provider)


def _execute_attempt(cfg, runtime, issue, dispatch, dispatch_sha, directory, workspace,
                     binding, lease, request, policy, provider, *, task=None, invocation_id=None, handoff_sha=None):
    capacity = load_capacity(cfg)
    stopping = False

    def stop_handler(signum, frame):
        nonlocal stopping
        stopping = True

    def stop():
        return stopping or _cancelled(runtime, cfg)

    handlers = {sig: signal.signal(sig, stop_handler) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if stop() or os.path.lexists(directory / "receipt.json"):
            raise SourceHold("uif_supervisor_not_startable")
        # Exact shape is consumed by the original-attempt report adapter.
        _exclusive(directory / "intent.json", dict(lease=lease, binding=binding))
        _exclusive(directory / "supervisor-start.json", dict(
            pid=os.getpid(), start_ticks=_proc_stat(os.getpid())[1],
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            dispatch_sha256=dispatch_sha, operation_id=dispatch["admission"]["original_operation_id"],
            lease=lease))
        pool = capacity.Pool(cfg["pool_root"], policy)
        host = capacity.host_snapshot(runtime)
        try:
            admitted = pool.admit(lease, binding, provider, host)
        except capacity.AdmissionError as error:
            if task is None or str(error) not in CAPACITY_REFUSALS:
                raise
            result = capacity_no_start_receipt(
                dispatch, dispatch_sha, handoff_sha, file_digest(directory / "supervisor-start.json"), str(error),
            )
            _exclusive(directory / "receipt.json", result)
            return result
        if admitted.get("reused") is not False:
            raise SourceHold("uif_supervisor_existing_lease")
        result = dict(status="failed", child_tree_gone=True, child_started=False, exit_code=None)
        try:
            available = sorted(os.sched_getaffinity(0))
            start = policy["host"]["cpu_reserve"]
            width = (policy["symphony"]["cpu_budget_millis"] + 999) // 1000
            os.sched_setaffinity(0, available[start:start + width])
            process_limit = policy["windmill"]["computation_subprocesses_per_job"]
            if type(process_limit) is not int or process_limit < 1:
                raise SourceHold("uif_supervisor_process_limit")
            if file_digest(directory / "dispatch-intent.json") != dispatch_sha:
                raise SourceHold("uif_supervisor_dispatch_changed")
            verify_checkout(workspace, binding["head"], dispatch["source_tree"], binding["branch"])

            def resource_check():
                count, rss = _usage()
                if count > process_limit:
                    return "process_budget"
                if rss > binding["resources"]["memory_bytes"]:
                    return "memory_budget"
                return capacity.host_pressure(workspace, policy)

            # Mark uncertainty before entering anything that may start a child.
            result = dict(status="uncertain", child_tree_gone=False, exit_code=None)
            if "role_broker" in cfg:
                from uif.controller_launch import prepare_role_launch
                from uif.role_broker import LaunchKey, LedgerLaunchKey

                if cfg["role_limits"]["coding"]["max_processes"] > process_limit:
                    raise SourceHold("uif_supervisor_role_process_limit")
                admission = dispatch["admission"]
                if task is None:
                    key = LaunchKey(
                        "coding", admission["store_id"], admission["row_id"], admission["source_sha256"],
                        dispatch["issue"]["issue_id"], issue, admission["owner"], admission["epoch"],
                        admission["original_operation_id"],
                        "initial" if dispatch["attempt"] is None else str(dispatch["attempt"]), dispatch_sha, lease,
                    )
                else:
                    key = LedgerLaunchKey(task.to_record(), issue, invocation_id, dispatch_sha, lease,
                                          dispatch["attempt"], handoff_sha)
                launch = prepare_role_launch(
                    cfg, key=key, admission_path=runtime / "current-admission.json",
                    intent_path=directory / "dispatch-intent.json", request_path=runtime / "requests" / (issue + ".json"),
                    workspace=workspace, policy_sha256=file_digest(runtime / "capacity-policy.json"), pool_binding=binding,
                )
                if launch.admission_sha256 != dispatch["pins"]["admission_sha256"]:
                    raise SourceHold("uif_supervisor_controller_handoff")
                result = run_registered_role_turn(
                    cfg, launch, directory=directory, workspace=workspace, write_roots=binding["write_roots"],
                    output_bytes=binding["resources"]["output_bytes"],
                    stop=lambda: stop() or capacity.host_pressure(workspace, policy) is not None,
                )
            else:
                result = run_guarded_turn(cfg, runtime=runtime, issue=issue, workspace=workspace,
                                          directory=directory, request=request, mode="workspace-write",
                                          stop=stop, resource_check=resource_check)
            if result["status"] == "succeeded":
                result.update(_check_output(workspace, directory, dispatch, binding))
                if (result["scope_violations"] or result["source_identity_changed"]
                        or not result["turn_completed"] or not result["session_id"]):
                    result["status"] = "failed"
        except Exception as error:
            result["error_type"] = type(error).__name__
            if result["child_tree_gone"] and result["status"] != "cancelled":
                result["status"] = "failed"
        if task is None:
            result.update(issue=issue, attempt=issue)
        else:
            result.update(schema=LEDGER_RECEIPT, task=task.to_record(), workspace_key=issue,
                          attempt=dispatch["attempt"], invocation_id=invocation_id, handoff_sha256=handoff_sha,
                          admission_sha256=dispatch["pins"]["admission_sha256"])
        result.update(lease=lease, parent_binding=binding, binding_sha256=_digest(binding),
                      source_head=binding["head"], parent_runtime=str(runtime),
                      attempt_path=str(directory), accepted_delivery=False, origin="native-coding",
                      declared_resources=binding["resources"], provider_route="codex-chatgpt",
                      requested_model=MODEL, requested_effort="medium", dispatch_sha256=dispatch_sha,
                      original_operation_id=dispatch["admission"]["original_operation_id"])
        finish_attempt(pool, lease, directory / "receipt.json", result)
        return result
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--issue")
    identity.add_argument("--ledger-workspace")
    parser.add_argument("--invocation-id")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = execute(args.runtime, args.issue, ledger_workspace=args.ledger_workspace,
                         invocation_id=args.invocation_id)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError):
        # Caller reads the exact original files. CLI stdout contains no input,
        # credential, or exception bodies and cannot authorize a retry.
        return 2
    return 0 if result["status"] == "succeeded" else 2


if __name__ == "__main__":
    raise SystemExit(main())
