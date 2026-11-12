"""Controller-owned admission for one original ordinary Foo job in the shared Pool."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys

from .dispatch import _digest, _encode, _unique
from .neutral_launch import NeutralLaunch
from .owner_runtime import OwnerAuthority, _owned_file
from .role_broker import ATTEMPT_ISOLATION, POLICY as ROLE_POLICY, RoleBroker, SCHEMA as ROLE_SCHEMA
from .source import SourceHold, exact_path, file_digest
from .supervisor import POLICY as SUPERVISOR_POLICY, _trusted, load_capacity

SCHEMA = "uif-shared-job-admission/1"
NAMESPACE = "uif-foo-user-job/1"
REGISTRATION = Path("/etc/uif-stokowski/shared-job-admission.json")
CAPACITY_SHA256 = "db72ff2b7b1afb01250c7f9060fdcd26b6dcca667ad2aa688784e085d84ae7e8"
MAX_WIRE = 65536
MAX_REQUEST = 2 * MAX_WIRE + 1024
BINDING_FIELDS = {
    "namespace", "workspace", "job_id", "descriptor_sha256", "request_sha256",
    "command_sha256", "mode", "timeout_seconds", "resources", "capacity_policy_sha256", "runner_sha256",
}
POOL_RESOURCES = {"cpu_millis", "memory_bytes", "io_slots", "output_bytes"}
LIMITS = POOL_RESOURCES | {"workers", "overhead_processes"}
OUTCOMES = {"succeeded": "succeeded", "failed": "failed", "timed_out": "failed", "cancelled": "cancelled"}


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _registered_file(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise SourceHold("uif_shared_job_file_registration")
    path = _trusted(reference["path"])
    if not _sha(reference["sha256"]) or file_digest(path) != reference["sha256"]:
        raise SourceHold("uif_shared_job_file_pin")
    return path


def _decode(raw, limit=MAX_WIRE):
    if len(raw) > limit:
        raise SourceHold("uif_shared_job_wire_limit")
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise SourceHold("uif_shared_job_wire_record")
    return value


def _canonical(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise SourceHold("uif_shared_job_path")
    path = exact_path(value)
    if str(path) != value:
        raise SourceHold("uif_shared_job_path")
    return path


def _below(path, root):
    return path != root and path.is_relative_to(root)


class SharedJobAdmission:
    """Bind neutral requests and stopped evidence to the controller's original lease."""

    def __init__(self, authority, capacity, policy, config, registration_sha256, launch):
        self.authority, self.capacity, self.policy = authority, capacity, policy
        self.config, self.registration_sha256, self.launch = config, registration_sha256, launch
        self.execution_root = _canonical(config["execution_root"])
        native_root = authority.runtime_root_path
        self.stopped_receipts = _canonical(config["stopped_receipts"])
        self.protected_roots = tuple(_canonical(path) for path in config["protected_roots"])
        self.roots = {name: tuple(_canonical(path) for path in config[name])
                      for name in ("read_roots", "write_roots")}
        if (self.execution_root.is_relative_to(native_root) or native_root.is_relative_to(self.execution_root)
                or self.execution_root == Path("/") or not self.execution_root.is_dir()
                or not _below(self.stopped_receipts, native_root)
                or _canonical(policy["symphony"]["pool_path"]) != authority.pool_database_path.parent
                or not all(_sha(config[name]) for name in ("descriptor_sha256", "runner_sha256"))
                or not _sha(registration_sha256) or set(config["limits"]) != LIMITS
                or any(type(value) is not int or value < (0 if name == "output_bytes" else 1)
                       for name, value in config["limits"].items())
                or type(config["timeout_seconds"]) is not int or not 1 <= config["timeout_seconds"] <= 1800
                or any(not path.is_relative_to(self.execution_root) for paths in self.roots.values()
                       for path in paths)):
            raise SourceHold("uif_shared_job_registration")
        self._receipt_custody()
        _owned_file(authority.pool_database_path)
        self.pool = capacity.Pool(authority.pool_database_path.parent, policy)

    @classmethod
    def load(cls, registration):
        path = _trusted(registration)
        raw = path.read_bytes()
        config = _decode(raw)
        expected = {
            "schema", "controller_uid", "runner_uid", "owner_registration", "capacity", "capacity_policy",
            "descriptor_sha256", "runner_sha256", "execution_root", "read_roots", "write_roots",
            "protected_roots", "runner_control", "stopped_receipts", "limits", "timeout_seconds",
        }
        if (set(config) != expected or config["schema"] != "uif-shared-job-registration/1"
                or any(type(config[name]) is not int or config[name] <= 0
                       for name in ("controller_uid", "runner_uid"))
                or config["controller_uid"] == config["runner_uid"]
                or os.getuid() != os.geteuid() or os.getuid() != config["controller_uid"]
                or os.environ.get("SUDO_UID") != str(config["runner_uid"])):
            raise SourceHold("uif_shared_job_invocation_binding")
        owner = _registered_file(config["owner_registration"])
        capacity_source = _registered_file(config["capacity"])
        policy_path = _registered_file(config["capacity_policy"])
        if config["capacity"]["sha256"] != CAPACITY_SHA256:
            raise SourceHold("uif_shared_job_neutral_capacity_pin")
        supervisor = _decode(_trusted(SUPERVISOR_POLICY).read_bytes())
        authority = OwnerAuthority.load(owner, config["owner_registration"]["sha256"])
        if (supervisor["schema"] != "uif-coding-supervisor/2"
                or supervisor["uid"] != config["controller_uid"]
                or supervisor["capacity"] != config["capacity"]
                or supervisor["owner_registration"] != config["owner_registration"]
                or _canonical(supervisor["runtime_root"]) != authority.runtime_root_path
                or _canonical(supervisor["pool_root"]) != authority.pool_database_path.parent):
            raise SourceHold("uif_shared_job_native_pool_binding")
        capacity = load_capacity({"capacity": capacity_source})
        policy_raw = policy_path.read_bytes()
        if sha256(policy_raw).hexdigest() != config["capacity_policy"]["sha256"]:
            raise SourceHold("uif_shared_job_policy_drift")
        policy = _decode(policy_raw)
        registration_sha = sha256(raw).hexdigest()
        role_path = _registered_file(supervisor["role_policy"])
        if role_path != ROLE_POLICY:
            raise SourceHold("uif_neutral_role_policy_path")
        roles = _decode(role_path.read_bytes())
        neutral = roles["roles"]["neutral"]
        expected = {
            **{name: config[name] for name in ("runner_uid", "descriptor_sha256", "runner_sha256",
                                               "execution_root", "runner_control", "read_roots", "write_roots", "protected_roots",
                                               "limits", "timeout_seconds")},
            "store_id": authority.store_id, "registration_sha256": registration_sha,
            "capacity_policy_sha256": config["capacity_policy"]["sha256"],
            "policy_fingerprint": _digest(policy),
            "memory_reserve_bytes": policy["host"]["memory_reserve_bytes"],
            "disk_reserve_bytes": policy["host"]["disk_reserve_bytes"],
        }
        if (roles["schema"] != ROLE_SCHEMA or roles["attempt_isolation"] != ATTEMPT_ISOLATION
                or roles["controller_uid"] != config["controller_uid"] or roles["controller_gid"] != os.getegid()
                or _canonical(roles["controller_root"]) != authority.runtime_root_path
                or _canonical(roles["pool_database"]) != authority.pool_database_path
                or _canonical(roles["operation_locks"]) != authority.operation_locks_path
                or any(neutral.get(name) != value for name, value in expected.items())):
            raise SourceHold("uif_neutral_role_registration")
        helper = supervisor["role_helper"]
        helper_path = _registered_file(helper)
        broker = RoleBroker(helper_path, helper["sha256"])
        launch = NeutralLaunch(authority.runtime_root_path, broker, neutral["cpu_ids"])
        return cls(authority, capacity, policy, config, registration_sha, launch)

    def _receipt_custody(self):
        for directory in (self.stopped_receipts, *self.stopped_receipts.parents):
            if not directory.is_relative_to(self.authority.runtime_root_path):
                break
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise SourceHold("uif_shared_job_receipt_custody")

    def _binding(self, binding):
        config = self.config
        if (not isinstance(binding, dict) or set(binding) != BINDING_FIELDS
                or (binding["namespace"], binding["workspace"]) != (NAMESPACE, "foo")
                or not isinstance(binding["job_id"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", binding["job_id"]) is None
                or any(not _sha(binding[field]) for field in ("request_sha256", "command_sha256"))
                or binding["descriptor_sha256"] != config["descriptor_sha256"]
                or binding["runner_sha256"] != config["runner_sha256"]
                or binding["capacity_policy_sha256"] != config["capacity_policy"]["sha256"]
                or binding["mode"] not in ("read", "write")
                or type(binding["timeout_seconds"]) is not int
                or not 1 <= binding["timeout_seconds"] <= config["timeout_seconds"]):
            raise SourceHold("uif_shared_job_binding")
        resources = binding["resources"]
        if (not isinstance(resources, dict) or set(resources) != LIMITS | self.roots.keys()
                or any(type(resources[name]) is not int
                       or not (0 if name == "output_bytes" else 1024**2 if name == "memory_bytes" else 1)
                       <= resources[name] <= config["limits"][name]
                       for name in LIMITS)):
            raise SourceHold("uif_shared_job_resources")
        for name, allowed in self.roots.items():
            values = resources[name]
            if (not isinstance(values, list) or len(values) > 32
                    or any(not isinstance(value, str) for value in values) or len(set(values)) != len(values)):
                raise SourceHold("uif_shared_job_scope")
            for value in values:
                path = _canonical(value)
                if (not any(path.is_relative_to(root) for root in allowed)
                        or any(path.is_relative_to(root) or root.is_relative_to(path)
                               for root in self.protected_roots)):
                    raise SourceHold("uif_shared_job_scope")
        if (bool(resources["write_roots"]) != (binding["mode"] == "write")
                or not (resources["read_roots"] or resources["write_roots"])):
            raise SourceHold("uif_shared_job_scope_mode")
        lease = "neutral-" + _digest([NAMESPACE, "foo", binding["job_id"]])
        pool_binding = dict(mode="foo-neutral-user", owner=self.authority.owner, epoch=self.authority.epoch,
                            write_roots=resources["write_roots"], request=binding,
                            resources={key: resources[key] for key in POOL_RESOURCES},
                            registration_sha256=self.registration_sha256, policy_fingerprint=_digest(self.policy))
        return lease, pool_binding

    def handle(self, request, *, before_admit_commit=None):
        if request.get("schema") != SCHEMA or request.get("action") not in ("admit", "start", "status", "cancel", "finish"):
            raise SourceHold("uif_shared_job_protocol")
        action = request["action"]
        expected = {"schema", "action", "binding"}
        if action == "start" or (action == "cancel" and "request" in request):
            expected.add("request")
        if action == "finish":
            expected |= {"lease_id", "receipt_sha256", "outcome", "child_tree_gone"}
        if set(request) != expected:
            raise SourceHold("uif_shared_job_request_shape")
        binding = request["binding"]
        lease, pool_binding = self._binding(binding)
        response = dict(schema=SCHEMA, binding_sha256=_digest(binding))
        with self.authority.lock(operation=lease):
            original = next((row for row in self.pool.status() if row["id"] == lease), None)
            if original is not None and original["binding"] != pool_binding:
                return dict(response, status="held", lease_id=lease)
            if action == "admit":
                if original is not None:
                    return dict(response, status="held", lease_id=lease, reused=True)
                available = sorted(os.sched_getaffinity(0))
                start = self.policy["host"]["cpu_reserve"]
                width = (self.policy["symphony"]["cpu_budget_millis"] + 999) // 1000
                cpus = available[start:start + width]
                if not cpus:
                    return dict(response, status="refused")
                if tuple(cpus) != self.launch.cpu_ids:
                    raise SourceHold("uif_neutral_registered_cpu")
                try:
                    # Runner logs and receipts consume host disk. The execution
                    # root may be a separately bounded project filesystem.
                    host = self.capacity.host_snapshot(_canonical(self.config["runner_control"]))
                    if before_admit_commit is None:
                        admitted = self.pool.admit_neutral(lease, pool_binding, host)
                    else:
                        admitted = self.pool.admit_neutral(
                            lease, pool_binding, host, before_commit=before_admit_commit)
                except self.capacity.AdmissionError as error:
                    if str(error) not in ("capacity_sessions", "capacity_cpu_millis", "capacity_memory_bytes",
                                          "capacity_io_slots", "capacity_output_bytes", "ownership_conflict"):
                        raise
                    return dict(response, status="refused")
                if (admitted["id"] != lease or admitted["reused"] is not False
                        or admitted["status"] != "running" or admitted["binding"] != pool_binding):
                    return dict(response, status="held", lease_id=lease)
                self.launch.reserve(lease, pool_binding, cpus)
                return dict(response, status="admitted", lease_id=lease, reused=False, cpu_ids=cpus)
            if original is None:
                return dict(response, status="absent")
            state = original["status"]
            if action == "status" and state in ("finished", "failed"):
                receipt_pin = self.pool.neutral_release_receipt(lease, original["fingerprint"])
                return dict(response, status="finished", lease_id=lease, receipt_sha256=receipt_pin,
                            pool_status=state, outcome=original["outcome"])
            if action == "finish":
                return self._finish(request, pool_binding, lease, response)
            if state not in ("running", "cancel_requested"):
                return dict(response, status=state, outcome=original["outcome"], lease_id=lease)
            work = request.get("request")
            if "request" in request:
                self.launch._check_run(work, binding)
            if action == "cancel" and state == "running":
                self.pool.cancel(lease)
                state = "cancel_requested"
            try:
                launch, fresh = self.launch.request(lease, pool_binding, work=work)
            except FileNotFoundError:
                if action in ("status", "cancel"):
                    return dict(response, status="held", lease_id=lease)
                raise
            response = dict(response, status=state, outcome=original["outcome"], lease_id=lease)
            if launch is None:
                return response
            operation = "observe"
            if action == "cancel" or (action == "start" and state == "cancel_requested"):
                operation = "cancel"
            elif action == "start" and fresh:
                operation = "start"
            observed = self.launch.observe(launch, operation)
            return dict(response, observation=self.launch.runner_observation(observed))

    def _finish(self, request, pool_binding, lease, response):
        receipt_pin, outcome = request["receipt_sha256"], request["outcome"]
        if (request["lease_id"] != lease or request["child_tree_gone"] is not True
                or not _sha(receipt_pin) or outcome not in OUTCOMES):
            raise SourceHold("uif_shared_job_finish_binding")
        self._receipt_custody()
        launch, _ = self.launch.request(lease, pool_binding)
        if launch is None:
            raise SourceHold("uif_neutral_original_launch_missing")
        path = self.stopped_receipts / (lease + ".json")
        self.launch.stopped(path, launch, receipt_pin, outcome)
        _owned_file(path)
        self.pool.finish_neutral(lease, True, OUTCOMES[outcome], receipt_pin)
        return dict(response, status="finished", lease_id=lease, receipt_sha256=receipt_pin)


def main():
    if len(sys.argv) != 1 or sys.stdin.isatty():
        return 2
    try:
        request = _decode(sys.stdin.buffer.read(MAX_REQUEST + 1), MAX_REQUEST)
        response = SharedJobAdmission.load(REGISTRATION).handle(request)
        sys.stdout.buffer.write(_encode(response) + b"\n")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return 2
    return 0
