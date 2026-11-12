"""Controller custody for an original neutral launch and its independent stop."""
from dataclasses import asdict
from hashlib import sha256
import os
import stat

from .controller_launch import _write_new
from .dispatch import _digest, _encode, _record, _sync_directory
from .role_broker import (
    ATTEMPT_ISOLATION, CANCEL_BEFORE_START, MAX_RECORD, NEUTRAL_SCHEMA, SCHEMA,
    NeutralLaunchRequest, NeutralObservation, slot_for_lease,
)
from .source import SourceHold, exact_path
from .supervisor import _exclusive

ADMISSION = "uif-neutral-launch-admission/1"
STOPPED = "uif-shared-job-stopped-evidence/2"


class NeutralLaunch:
    def __init__(self, root, broker, cpu_ids):
        self.root, self.broker = exact_path(root), broker
        if (not isinstance(cpu_ids, list) or not cpu_ids
                or any(type(cpu) is not int or cpu < 0 for cpu in cpu_ids)
                or len(set(cpu_ids)) != len(cpu_ids)):
            raise SourceHold("uif_neutral_registered_cpu")
        self.cpu_ids = tuple(cpu_ids)

    def _directory(self, lease):
        directory = exact_path(self.root / slot_for_lease(lease))
        for path in (self.root, directory):
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise SourceHold("uif_neutral_controller_custody")
        return directory

    def reserve(self, lease, pool_binding, cpu_ids):
        if tuple(cpu_ids) != self.cpu_ids:
            raise SourceHold("uif_neutral_admitted_cpu")
        directory = self.root / slot_for_lease(lease)
        directory.mkdir(mode=0o700)
        _sync_directory(self.root)
        _exclusive(self._directory(lease) / "admission.json",
                   dict(schema=ADMISSION, lease=lease, binding_sha256=_digest(pool_binding), cpu_ids=cpu_ids))

    @staticmethod
    def _check_run(work, binding):
        if not isinstance(work, dict) or len(_encode(work)) > MAX_RECORD:
            raise SourceHold("uif_neutral_run_request")
        command = work.get("command")
        if (not isinstance(command, str) or not command.strip()
                or "\0" in command or len(command.encode()) > 32000):
            raise SourceHold("uif_neutral_run_command")
        body = {key: binding[key] for key in ("workspace", "job_id", "descriptor_sha256",
                                               "mode", "timeout_seconds", "resources")}
        body.update(protocol="foo-user-exec-v1", command=command)
        if (work != {**body, "action": "run", "request_sha256": binding["request_sha256"]}
                or _digest(body) != binding["request_sha256"]
                or sha256(command.encode()).hexdigest() != binding["command_sha256"]):
            raise SourceHold("uif_neutral_original_run")

    @staticmethod
    def _fence(request):
        return dict(schema=SCHEMA, request_sha256=request.digest, lease=request.key.lease_id,
                    binding_sha256=request.pool_binding_sha256)

    def request(self, lease, pool_binding, *, work=None):
        """Read the original, or seal exactly one launch under the caller's fence."""
        binding = pool_binding["request"]
        if work is not None:
            self._check_run(work, binding)
        directory = self._directory(lease)
        admission, _ = _record(directory / "admission.json")
        expected = dict(schema=ADMISSION, lease=lease, binding_sha256=_digest(pool_binding),
                        cpu_ids=list(self.cpu_ids))
        if admission != expected:
            raise SourceHold("uif_neutral_original_admission")
        path = directory / "launch.json"
        if os.path.lexists(path):
            value, _ = _record(path)
            request = NeutralLaunchRequest(**value)
            if (request.binding != binding or request.key.lease_id != lease or request.slot != directory.name
                    or request.pool_binding_sha256 != expected["binding_sha256"]
                    or request.intent_sha256 != _digest(binding) or request.cpu_ids != self.cpu_ids
                    or _record(directory / "fence.json")[0] != self._fence(request)):
                raise SourceHold("uif_neutral_sealed_launch")
            sealed, _ = _record(directory / "request.json", request.request_file_sha256)
            self._check_run(sealed, binding)
            if work is not None and sealed != work:
                raise SourceHold("uif_neutral_original_run")
            return request, False
        if work is None:
            return None, False
        _write_new(directory / "request.json", _encode(work))
        _sync_directory(directory)
        _, request_pin = _record(directory / "request.json")
        request = NeutralLaunchRequest(
            NEUTRAL_SCHEMA, binding, directory.name, _digest(binding), request_pin,
            expected["binding_sha256"], self.cpu_ids)
        _exclusive(directory / "fence.json", self._fence(request))
        _exclusive(path, asdict(request))
        return request, True

    def observe(self, request, action):
        observation = getattr(self.broker, action)(request)
        if not isinstance(observation, NeutralObservation) or observation.request_sha256 != request.digest:
            raise SourceHold("uif_neutral_observation_binding")
        return observation

    @staticmethod
    def runner_observation(observation):
        value = asdict(observation)
        del value["stopped_evidence"]
        value["containment_stopped"] = observation.containment_stopped
        return value

    @staticmethod
    def _verified_receipt(request, observation, receipt_pin, outcome):
        if (observation.request_sha256 != request.digest or not observation.release_safe
                or observation.outcome != outcome or not isinstance(observation.stopped_evidence, dict)):
            raise SourceHold("uif_neutral_stop_unverified")
        evidence = observation.stopped_evidence
        binding = request.binding
        lease = request.key.lease_id
        terminal, reservation, receipt = (evidence[name] for name in
                                          ("trusted_terminal", "reservation", "runner_receipt"))
        if (not all(isinstance(value, dict) for value in (terminal, reservation, receipt))
                or (evidence["namespace"], evidence["workspace"], evidence["job_id"], evidence["lease_id"])
                   != (binding["namespace"], "foo", binding["job_id"], lease)
                or evidence["binding_sha256"] != _digest(binding)
                or evidence["runner_intent"] != binding
                or evidence["runner_intent_sha256"] != request.intent_sha256
                or evidence["runner_receipt_sha256"] != receipt_pin or _digest(receipt) != receipt_pin
                or any(receipt.get(name) != value for name, value in binding.items())
                or receipt.get("lease_id") != lease or receipt.get("status") != outcome
                or receipt.get("exit_code") != observation.exit_code
                or receipt.get("child_tree_gone") is not True or receipt.get("reconcile_required") is not False
                or evidence["trusted_terminal_sha256"] != observation.receipt_sha256
                or _digest(terminal) != observation.receipt_sha256
                or terminal.get("state") != observation.state or terminal.get("request") != request.digest
                or terminal.get("exit_code") != observation.exit_code
                or terminal.get("isolation") != ATTEMPT_ISOLATION
                or not isinstance(terminal.get("boot"), str) or not terminal["boot"]):
            raise SourceHold("uif_neutral_stopped_receipt")
        expected_origin = dict(schema=SCHEMA, request=request.digest, boot=terminal["boot"],
                               isolation=ATTEMPT_ISOLATION)
        if terminal.get("decision") == CANCEL_BEFORE_START:
            expected_origin["decision"] = CANCEL_BEFORE_START
            if (observation.state != "refused-before-fork" or outcome != "cancelled"
                    or terminal.get("cgroup") is not None):
                raise SourceHold("uif_neutral_no_fork_witness")
        elif terminal.get("cgroup") is None or terminal.get("outcome") != outcome:
            raise SourceHold("uif_neutral_terminal_witness")
        if reservation != expected_origin:
            raise SourceHold("uif_neutral_reservation_witness")
        expected_cgroup = terminal.get("cgroup")
        if expected_cgroup is None:
            if "cgroup" in evidence:
                raise SourceHold("uif_neutral_no_fork_witness")
        elif (not isinstance(expected_cgroup, list) or len(expected_cgroup) != 2
              or any(type(part) is not int or part <= 0 for part in expected_cgroup)
              or evidence.get("cgroup") != {"boot": terminal["boot"], "cgroup": expected_cgroup}):
            raise SourceHold("uif_neutral_cgroup_witness")
        if observation.state == "exited-clean":
            started = {key: terminal[key] for key in ("pid", "start_ticks", "boot", "cgroup",
                                                     "namespaces", "request", "isolation")}
            if (evidence.get("started") != started
                    or evidence.get("released") != {"request": request.digest, "started_sha256": _digest(started)}
                    or receipt.get("log_bytes") != observation.log_bytes
                    or receipt.get("logs_truncated") != observation.logs_truncated
                    or terminal.get("log_bytes") != observation.log_bytes
                    or terminal.get("logs_truncated") != observation.logs_truncated):
                raise SourceHold("uif_neutral_reaped_witness")
        elif "started" in evidence or "released" in evidence or terminal.get("pid") is not None:
            raise SourceHold("uif_neutral_no_fork_witness")
        return receipt

    def stopped(self, path, request, receipt_pin, outcome):
        """Publish and read back original independently qualified evidence."""
        expected = dict(schema=STOPPED, lease_id=request.key.lease_id, binding_sha256=_digest(request.binding))
        if not os.path.lexists(path):
            observation = self.observe(request, "observe")
            self._verified_receipt(request, observation, receipt_pin, outcome)
            _exclusive(path, {**expected, "observation": asdict(observation)})
        record, _ = _record(path)
        value = record.pop("observation", None)
        if record != expected or not isinstance(value, dict):
            raise SourceHold("uif_neutral_stopped_envelope")
        self._verified_receipt(request, NeutralObservation(**value), receipt_pin, outcome)
