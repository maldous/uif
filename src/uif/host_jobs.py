"""Ordinary-user Foo jobs. Read mode is a scheduling declaration.

The process supervision and resource checks derive from retained runner blob
862dcd4401f3844f927c7da3ca1285cbcb808964. The existing external capacity
authority owns reservations. Local files retain original operation evidence.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

PROTOCOL = "foo-user-exec-v1"
NAMESPACE = "uif-foo-user-job/1"
MAX_WIRE = 65536
LOG_LIMIT = 1024 * 1024
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled", "refused"}
RESOURCES = {"workers", "overhead_processes", "cpu_millis", "memory_bytes", "output_bytes", "io_slots"}
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
SHA = re.compile(r"[a-f0-9]{64}")


class Hold(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Hold(reason)


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def lease_id(binding):
    """Stable Pool identity for one new route job across descriptor revisions."""
    identity = [binding["namespace"], binding["workspace"], binding["job_id"]]
    return "neutral-" + sha(wire(identity))


def decode(raw):
    def pairs(items):
        data = dict(items)
        require(len(data) == len(items), "duplicate_field")
        return data

    require(len(raw) <= MAX_WIRE, "wire_size")
    value = json.loads(raw, object_pairs_hook=pairs)
    require(isinstance(value, dict), "wire_record")
    return value


def token(value, pattern=TOKEN):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def path(value):
    require(isinstance(value, str) and len(value) <= 4096, "path_value")
    item = Path(value)
    require(item.is_absolute() and str(item) == value and item == item.resolve(), "path_alias")
    return item


def overlap(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def _read(pathname, uid, limit=MAX_WIRE):
    fd = os.open(pathname, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= limit, "file_custody")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        require(len(raw) == before.st_size
                and (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                == (before.st_size, before.st_mtime_ns, before.st_ctime_ns), "file_drift")
        return raw


def _trusted_ancestry(item, uid):
    for parent in item.parents:
        info = parent.stat()
        require(info.st_uid == uid and not info.st_mode & 0o022, "installation_ancestry")


def _private(item, uid):
    info = item.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077,
            "runtime_custody")
    return item


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _create(target, payload):
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(wire(payload))
        stream.flush()
        os.fsync(stream.fileno())
    _sync(target.parent)


def _replace(target, payload):
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".receipt-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(wire(payload))
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, target)
            _sync(target.parent)
        finally:
            temporary.unlink(missing_ok=True)


def load_descriptor(location):
    """Read the fixed root-owned installation; no request selects these paths."""
    location = path(str(location))
    _trusted_ancestry(location, 0)
    raw = _read(location, 0)
    config = decode(raw)
    require(config.get("runner_sha256") == sha(Path(__file__).read_bytes()), "runner_pin")
    return HostJobs(config, sha(raw))


class SharedAuthority:
    """Fixed executable protocol into the integrator's existing shared budget."""

    def __init__(self, config):
        require(isinstance(config, dict) and set(config) == {"path", "sha256"}, "authority_binding")
        self.executable = path(config["path"])
        self.pin = config["sha256"]

    def call(self, action, binding, **details):
        _trusted_ancestry(self.executable, 0)
        require(sha(_read(self.executable, 0, 16 * 1024 * 1024)) == self.pin, "authority_pin")
        require(self.executable.stat().st_mode & stat.S_IXUSR, "authority_executable")
        request = dict(schema="uif-shared-job-admission/1", action=action, binding=binding, **details)
        payload = wire(request)
        require(len(payload) <= MAX_WIRE, "authority_request_size")
        with tempfile.TemporaryFile() as response:
            result = subprocess.run([str(self.executable)], input=payload, stdout=response,
                                    stderr=subprocess.DEVNULL, timeout=20, env=ENV, check=False)
            require(result.returncode == 0, "authority_uncertain")
            response.seek(0)
            value = decode(response.read(MAX_WIRE + 1))
        require(value.get("schema") == request["schema"] and value.get("binding_sha256") == sha(wire(binding)),
                "authority_readback")
        return value


class HostJobs:
    def __init__(self, config, descriptor_sha256):
        fields = {"schema", "workspace", "protocol", "uid", "execution_root", "control_root",
                  "read_roots", "write_roots", "protected_roots", "runner_sha256",
                  "policy_sha256", "limits", "authority"}
        require(set(config) == fields and config["schema"] == "uif-foo-user-host/1"
                and config["workspace"] == "foo" and config["protocol"] == PROTOCOL
                and type(config["uid"]) is int and config["uid"] > 0
                and os.getuid() == os.geteuid() == config["uid"]
                and all(token(pin, SHA) for pin in (descriptor_sha256, config["policy_sha256"], config["runner_sha256"])),
                "descriptor_binding")
        self.uid = config["uid"]
        self.descriptor = descriptor_sha256
        self.config = config
        self.execution = path(config["execution_root"])
        self.control = _private(path(config["control_root"]), self.uid)
        require(not overlap(self.control, self.execution) and self.execution.is_dir(), "execution_custody")
        self.protected = [self.control, *(path(item) for item in config["protected_roots"])]
        self.roots = {kind: [path(item) for item in config[kind]] for kind in ("read_roots", "write_roots")}
        require(all(item.is_relative_to(self.execution) and not any(overlap(item, q) for q in self.protected)
                    for items in self.roots.values() for item in items), "descriptor_root_scope")
        limits = config["limits"]
        require(isinstance(limits, dict) and set(limits) == RESOURCES | {"disk_reserve_bytes", "memory_reserve_bytes"}
                and all(type(value) is int and value >= 0 for value in limits.values()), "descriptor_limits")
        self.authority = SharedAuthority(config["authority"])

    def _binding(self, req):
        common = {"protocol", "workspace", "descriptor_sha256", "action", "job_id", "request_sha256"}
        require(req.get("protocol") == PROTOCOL and req.get("workspace") == "foo"
                and req.get("descriptor_sha256") == self.descriptor
                and token(req.get("job_id")) and token(req.get("request_sha256"), SHA)
                and req.get("action") in ("run", "status", "cancel"), "request_identity")
        if req["action"] != "run":
            require(set(req) == common, "request_shape")
            return None
        require(set(req) == common | {"command", "mode", "timeout_seconds", "resources"}, "request_shape")
        command, mode, timeout, resources = (req[k] for k in ("command", "mode", "timeout_seconds", "resources"))
        require(isinstance(command, str) and command.strip() and "\0" not in command
                and len(command.encode()) <= 32000 and mode in ("read", "write")
                and type(timeout) is int and 1 <= timeout <= 1800, "run_parameters")
        require(isinstance(resources, dict) and set(resources) == RESOURCES | {"read_roots", "write_roots"},
                "resource_shape")
        limits = self.config["limits"]
        for key in RESOURCES:
            minimum = 0 if key == "output_bytes" else 1024**2 if key == "memory_bytes" else 1
            require(type(resources[key]) is int and minimum <= resources[key] <= limits[key], "resource_range")
        for key, allowed in self.roots.items():
            items = resources[key]
            require(isinstance(items, list) and len(items) <= 32 and all(isinstance(v, str) for v in items)
                    and len(set(items)) == len(items), "resource_paths")
            for raw in items:
                item = path(raw)
                require(any(item.is_relative_to(root) for root in allowed)
                        and not any(overlap(item, root) for root in self.protected), "resource_scope")
        require(bool(resources["write_roots"]) == (mode == "write")
                and bool(resources["read_roots"] or resources["write_roots"]), "resource_mode")
        body = {k: req[k] for k in sorted(set(req) - {"action", "request_sha256"})}
        require(sha(wire(body)) == req["request_sha256"], "request_digest")
        binding = dict(namespace=NAMESPACE, workspace="foo", job_id=req["job_id"],
                       descriptor_sha256=self.descriptor, request_sha256=req["request_sha256"],
                       command_sha256=sha(command.encode()), mode=mode, timeout_seconds=timeout,
                       resources=resources, capacity_policy_sha256=self.config["policy_sha256"],
                       runner_sha256=self.config["runner_sha256"])
        require(len(wire(dict(schema="uif-shared-job-admission/1", action="start",
                              binding=binding, request=req))) <= MAX_WIRE, "authority_request_size")
        return binding

    def handle(self, req):
        binding = self._binding(req)
        directory = self.control / req["job_id"]
        basic = {k: req[k] for k in ("workspace", "descriptor_sha256", "job_id", "request_sha256")}
        basic["namespace"] = NAMESPACE
        try:
            if req["action"] == "run":
                directory.mkdir(mode=0o700)
                _sync(self.control)
                _create(directory / "request.json", req)
                _create(directory / "intent.json", binding)
                return self._run(directory, binding)
        except FileExistsError:
            pass
        if not os.path.lexists(directory):
            return dict(basic, status="not_found")
        _private(path(str(directory)), self.uid)
        try:
            original = decode(_read(directory / "intent.json", self.uid))
        except FileNotFoundError:
            return dict(basic, status="unknown", reconcile_required=True)
        require(all(original.get(k) == v for k, v in basic.items())
                and (binding is None or binding == original), "original_binding_conflict")
        result = self._receipt(directory, original)
        if req["action"] == "status":
            return self._status(directory, original, basic, result)
        if req["action"] == "cancel" and result["status"] not in TERMINAL:
            marker = directory / "cancel.json"
            try:
                _create(marker, basic)
            except FileExistsError:
                require(decode(_read(marker, self.uid)) == basic, "cancel_binding")
            observed = self._status(directory, original, basic, result)
            if observed["status"] in TERMINAL:
                return observed
            return dict(basic, status="cancellation_requested", reconcile_required=True)
        return self._project_release(directory, result, original)

    @contextmanager
    def _receipt_lock(self, directory):
        fd = os.open(directory / "receipt.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == self.uid and info.st_nlink == 1
                    and not info.st_mode & 0o077, "receipt_lock_custody")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def _receipt(self, directory, binding):
        try:
            raw = _read(directory / "receipt.json", self.uid)
            result = decode(raw)
        except FileNotFoundError:
            return dict(binding, status="unknown", reconcile_required=True, child_tree_gone=False)
        require(raw == wire(result) and all(result.get(k) == v for k, v in binding.items())
                and result.get("lease_id") in (None, lease_id(binding)), "receipt_binding")
        return result

    def _project_release(self, directory, receipt, binding):
        if receipt["status"] == "running" and time.time() - receipt.get("heartbeat_at", 0) > 15:
            return dict(receipt, status="unknown", reconcile_required=True)
        if receipt["status"] in TERMINAL and receipt.get("lease_id"):
            try:
                self._released(decode(_read(directory / "release.json", self.uid)), receipt, binding)
            except (OSError, ValueError):
                return dict(receipt, reconcile_required=True)
        return receipt

    @staticmethod
    def _held(observed, lease):
        return observed.get("lease_id") == lease and observed.get("status") in ("running", "cancel_requested")

    def _cancel(self, directory, binding, lease):
        work = self._original_request(directory, binding)
        acknowledgement = self.authority.call("cancel", binding, request=work)
        require(acknowledgement.get("status") == "cancel_requested"
                and acknowledgement.get("lease_id") == lease, "cancel_uncertain")

    def _observed_terminal(self, directory, observed, binding):
        observation = observed.get("observation")
        return self._terminal(directory, observation, binding) if isinstance(observation, dict) else None

    def _status(self, directory, binding, basic, current):
        lease = lease_id(binding)
        try:
            with self._receipt_lock(directory):
                current = self._receipt(directory, binding)
                if current["status"] == "refused" or os.path.lexists(directory / "release.json"):
                    return self._project_release(directory, current, binding)
                observed = self.authority.call("status", binding)
                if observed.get("status") == "finished":
                    self._recover_release(directory, current, binding, observed)
                    return current
                if not self._held(observed, lease):
                    return dict(current, status="unknown" if current["status"] == "running" else current["status"],
                                reconcile_required=True)
                terminal = self._observed_terminal(directory, observed, binding)
                if terminal is not None:
                    # Keep exact settled bytes across finish retries. Only
                    # the controller can independently authorize the release.
                    if current["status"] in TERMINAL:
                        require(current.get("lease_id") == lease
                                and all(current.get(k) == v for k, v in terminal.items()), "terminal_receipt_conflict")
                    else:
                        current = dict(current, **terminal, lease_id=lease)
                        _replace(directory / "receipt.json", current)
                    return self._finish(directory, current, binding)
                marker = directory / "cancel.json"
                if current["status"] not in TERMINAL and os.path.lexists(marker):
                    require(decode(_read(marker, self.uid)) == basic, "cancel_binding")
                    self._cancel(directory, binding, lease)
                return self._project_release(directory, dict(current, lease_id=lease), binding)
        except (OSError, ValueError, subprocess.SubprocessError):
            return dict(current, status="unknown" if current["status"] == "running" else current["status"],
                        reconcile_required=True)

    def _recover_release(self, directory, receipt, binding, observed):
        require(receipt["status"] in TERMINAL and receipt.get("child_tree_gone") is True
                and receipt.get("lease_id") == lease_id(binding), "release_unsettled_receipt")
        self._released(observed, receipt, binding)
        outcome = "failed" if receipt["status"] == "timed_out" else receipt["status"]
        pool_status = "finished" if outcome == "succeeded" else "failed"
        require((observed.get("pool_status"), observed.get("outcome")) == (pool_status, outcome),
                "release_outcome")
        _create(directory / "release.json", observed)

    def _finish(self, directory, record, binding):
        try:
            finished = self.authority.call("finish", binding, lease_id=record["lease_id"],
                                           receipt_sha256=sha(wire(record)), outcome=record["status"],
                                           child_tree_gone=True)
            self._released(finished, record, binding)
            _create(directory / "release.json", finished)
        except (OSError, ValueError, subprocess.SubprocessError):
            return dict(record, reconcile_required=True)
        return record

    @staticmethod
    def _released(release, record, binding):
        require(release.get("schema") == "uif-shared-job-admission/1"
                and release.get("binding_sha256") == sha(wire(binding))
                and release.get("status") == "finished" and release.get("lease_id") == record["lease_id"]
                and release.get("receipt_sha256") == sha(wire(record)), "finish_uncertain")

    def _original_request(self, directory, binding):
        raw = _read(directory / "request.json", self.uid)
        work = decode(raw)
        require(raw == wire(work) and work.get("action") == "run"
                and self._binding(work) == binding, "original_request_binding")
        return work

    def _terminal(self, directory, observation, binding):
        state = observation.get("state")
        if state not in ("exited-clean", "refused-before-fork"):
            return None
        outcome, exit_code = observation.get("outcome"), observation.get("exit_code")
        counts, truncated = observation.get("log_bytes"), observation.get("logs_truncated")
        streams = {"stdout", "stderr"}
        require(observation.get("containment_stopped") is True
                and token(observation.get("request_sha256"), SHA)
                and token(observation.get("receipt_sha256"), SHA)
                and outcome in ("succeeded", "failed", "timed_out", "cancelled")
                and isinstance(counts, dict) and set(counts) == streams
                and all(type(value) is int and 0 <= value <= LOG_LIMIT for value in counts.values())
                and sum(counts.values()) <= binding["resources"]["output_bytes"]
                and isinstance(truncated, dict) and set(truncated) == streams
                and all(type(value) is bool for value in truncated.values()), "broker_terminal_binding")
        if state == "refused-before-fork":
            require(outcome in ("failed", "cancelled") and exit_code is None
                    and not any(counts.values()) and not any(truncated.values())
                    and not any(os.path.lexists(directory / (name + ".log")) for name in streams), "broker_no_fork")
        else:
            require(type(exit_code) is int and (outcome != "succeeded" or exit_code == 0), "broker_exit")
            for name in streams:
                log = directory / (name + ".log")
                info = log.stat(follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == self.uid and info.st_nlink == 1
                        and not info.st_mode & 0o022 and info.st_size == counts[name], "broker_log_readback")
        return dict(status=outcome, child_tree_gone=True, reconcile_required=False, exit_code=exit_code,
                    log_bytes=counts, logs_truncated=truncated)

    def _broker_turn(self, directory, binding, lease, heartbeat):
        """Start once, then use only same-original observations."""
        request = self._original_request(directory, binding)
        action = "cancel" if (directory / "cancel.json").exists() else "start"
        deadline = time.monotonic() + request["timeout_seconds"] + 20
        next_heartbeat = time.monotonic() + 2
        next_cancel = time.monotonic() + (2 if action == "cancel" else 0)
        cancel_retries = 2
        observed_uncertain = False
        try:
            reply = self.authority.call(action, binding, request=request)
        except (OSError, ValueError, subprocess.SubprocessError):
            reply = self.authority.call("status", binding)
            observed_uncertain = True
        launch_pin = None
        while True:
            require(reply.get("lease_id") == lease and reply.get("status") in ("running", "cancel_requested"),
                    "broker_original_lease")
            observation = reply.get("observation")
            if not isinstance(observation, dict):
                break
            pin = observation.get("request_sha256")
            require(token(pin, SHA) and launch_pin in (None, pin), "broker_original_launch")
            launch_pin = pin
            terminal = self._terminal(directory, observation, binding)
            if terminal is not None:
                return terminal
            state = observation.get("state")
            if state == "uncertain" and not observed_uncertain:
                observed_uncertain = True
                reply = self.authority.call("status", binding)
                continue
            if state not in ("prepared", "running") or time.monotonic() >= deadline:
                break
            marker = directory / "cancel.json"
            if cancel_retries and time.monotonic() >= next_cancel and os.path.lexists(marker):
                cancel_retries -= 1
                next_cancel = time.monotonic() + 2
                try:
                    original = {key: binding[key] for key in
                                ("workspace", "descriptor_sha256", "job_id", "request_sha256", "namespace")}
                    with self._receipt_lock(directory):
                        require(decode(_read(marker, self.uid)) == original, "cancel_binding")
                        if self._receipt(directory, binding)["status"] not in TERMINAL:
                            self._cancel(directory, binding, lease)
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            if time.monotonic() >= next_heartbeat:
                heartbeat()
                next_heartbeat = time.monotonic() + 2
            time.sleep(0.1)
            reply = self.authority.call("status", binding)
        return dict(status="unknown", child_tree_gone=False, reconcile_required=True)

    def _run(self, directory, binding):
        receipt = directory / "receipt.json"
        record = dict(binding, status="unknown", reconcile_required=True, child_tree_gone=False)
        _create(receipt, record)
        try:
            expected_lease = lease_id(binding)
            admitted = self.authority.call("admit", binding)
            if admitted.get("status") == "refused":
                record.update(status="refused", reconcile_required=False)
                _replace(receipt, record)
                return record
            lease, cpus = admitted.get("lease_id"), admitted.get("cpu_ids")
            require(lease == expected_lease, "admission_lease_binding")
            record.update(lease_id=lease)
            require(admitted.get("status") == "admitted" and admitted.get("reused") is False,
                    "admission_uncertain")
            require(isinstance(cpus, list) and cpus and all(type(cpu) is int for cpu in cpus)
                    and len(set(cpus)) == len(cpus) and set(cpus) <= os.sched_getaffinity(0),
                    "authority_cpu_binding")
            def heartbeat():
                try:
                    with self._receipt_lock(directory):
                        current = self._receipt(directory, binding)
                        if current["status"] not in TERMINAL:
                            record["heartbeat_at"] = time.time()
                            _replace(receipt, record)
                except BlockingIOError:
                    pass

            record.update(status="running")
            heartbeat()
            outcome = self._broker_turn(directory, binding, lease, heartbeat)
        except (OSError, ValueError, subprocess.SubprocessError):
            outcome = dict(status="unknown", reconcile_required=True, child_tree_gone=False)
        try:
            with self._receipt_lock(directory):
                current = self._receipt(directory, binding)
                if current["status"] in TERMINAL:
                    return self._project_release(directory, current, binding)
                record.update(outcome)
                _replace(receipt, record)
                if record["child_tree_gone"]:
                    return self._finish(directory, record, binding)
                return record
        except (OSError, ValueError, subprocess.SubprocessError):
            return dict(record, status="unknown", reconcile_required=True, child_tree_gone=False)
