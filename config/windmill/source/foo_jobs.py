"""New ordinary-user route for workspace foo. No historical job fallback."""
# requirements:
# paramiko==5.1.0
# wmill==1.850.0
import base64
import hashlib
import io
import json
import math
import os
import re
import time
from typing import NamedTuple
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

WORKSPACE = "foo"
PROTOCOL = "foo-user-exec-v1"
NAMESPACE = "uif-foo-user-job/1"
ROUTE = "u/admin/foo_user_route"
COMMAND = "/usr/local/libexec/foo-user-exec"
MAX_WIRE = 65536
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
PIN = re.compile(r"[a-f0-9]{64}")
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled", "refused"}
RECEIPT_FIELDS = {
    "namespace", "workspace", "descriptor_sha256", "job_id", "request_sha256",
    "command_sha256", "mode", "timeout_seconds", "resources", "capacity_policy_sha256",
    "runner_sha256", "status", "reconcile_required", "child_tree_gone", "lease_id",
    "exit_code", "reason", "log_bytes", "logs_truncated", "heartbeat_at",
}


class _ProofScope(NamedTuple):
    permissioned_as: str
    route_sha256: str
    descriptor_sha256: str
    not_before: int
    expires: int
    requests: tuple[tuple[str, str, tuple[str, ...]], ...]


# Set only in the separately owner-reviewed, proof-only script source.
# This is a source literal, never a Windmill argument, variable or resource.
_PROOF_SCOPE: _ProofScope | None = None


class _ProofWindowClosed(ValueError):
    pass


def _check_proof_window(scope):
    if scope is not None and not 0 < scope.not_before <= time.time() < scope.expires:
        raise _ProofWindowClosed("proof_window")


def wire(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def decode(raw):
    def unique(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), "duplicate_field")
        return result

    require(len(raw) <= MAX_WIRE, "response_size")
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), "response_shape")
    return value


def route(value):
    fields = {"schema", "workspace", "protocol", "host", "user", "host_key", "credential_reference",
              "descriptor_sha256", "runner_sha256", "capacity_policy_sha256"}
    require(isinstance(value, dict) and set(value) == fields
            and value["schema"] == "uif-foo-user-route/1" and value["workspace"] == WORKSPACE
            and value["protocol"] == PROTOCOL and matches(ID, value["host"]) and matches(ID, value["user"])
            and value["user"] != "root"
            and isinstance(value["host_key"], str) and len(value["host_key"]) <= 512
            and isinstance(value["credential_reference"], str)
            and re.fullmatch(r"u/[a-zA-Z0-9_-]+/[a-zA-Z0-9_/-]+", value["credential_reference"])
            and all(matches(PIN, value[key]) for key in ("descriptor_sha256", "runner_sha256", "capacity_policy_sha256")),
            "route_binding")
    return value


def resource_shape(resources):
    limits = {"workers": 1, "overhead_processes": 1, "cpu_millis": 1, "memory_bytes": 1024**2,
              "output_bytes": 0, "io_slots": 1}
    require(isinstance(resources, dict) and set(resources) == limits.keys() | {"read_roots", "write_roots"}
            and all(type(resources[k]) is int and low <= resources[k] <= 2**63 - 1 for k, low in limits.items()),
            "resource_shape")
    for kind in ("read_roots", "write_roots"):
        paths = resources[kind]
        require(isinstance(paths, list) and len(paths) <= 32
                and all(isinstance(item, str) and item.startswith("/") and 1 <= len(item) <= 4096
                        and "\0" not in item for item in paths), "resource_paths")


def request(cfg, action, job_id, request_sha256, command, mode, timeout_seconds, resources):
    require(action in ("run", "status", "cancel") and matches(ID, job_id)
            and matches(PIN, request_sha256), "request_identity")
    body = dict(protocol=PROTOCOL, workspace=WORKSPACE, descriptor_sha256=cfg["descriptor_sha256"],
                job_id=job_id)
    if action == "run":
        require(isinstance(command, str) and command.strip() and "\0" not in command
                and len(command.encode()) <= 32000 and mode in ("read", "write")
                and type(timeout_seconds) is int and 1 <= timeout_seconds <= 1800
                and isinstance(resources, dict), "run_parameters")
        resource_shape(resources)
        require(bool(resources["write_roots"]) == (mode == "write"), "resource_mode")
        body.update(command=command, mode=mode, timeout_seconds=timeout_seconds, resources=resources)
        require(sha(wire(body)) == request_sha256, "request_digest")
    else:
        require(command is None and mode is None and timeout_seconds is None and resources is None,
                "observation_parameters")
    body.update(action=action, request_sha256=request_sha256)
    require(len(wire(body)) <= MAX_WIRE, "request_size")
    return body


def _proof_request(scope, action, job_id, request_sha256, command, mode, timeout_seconds, resources):
    require(scope.permissioned_as and os.environ.get("WM_PERMISSIONED_AS") == scope.permissioned_as,
            "proof_actor")
    _check_proof_window(scope)
    require(matches(PIN, scope.route_sha256) and matches(PIN, scope.descriptor_sha256)
            and len(scope.requests) == 2 and scope.requests[0][0] != scope.requests[1][0]
            and all(matches(ID, job) and matches(PIN, digest) and actions
                    and set(actions) <= {"run", "status", "cancel"}
                    for job, digest, actions in scope.requests), "proof_binding")
    require(any(job_id == job and request_sha256 == digest and action in actions
                for job, digest, actions in scope.requests), "proof_request")
    return request({"descriptor_sha256": scope.descriptor_sha256}, action, job_id, request_sha256,
                   command, mode, timeout_seconds, resources)


def exchange(cfg, req, secret, *, proof_scope=None):
    import paramiko

    key = paramiko.Ed25519Key(data=base64.b64decode(cfg["host_key"], validate=True))
    private = paramiko.Ed25519Key.from_private_key(io.StringIO(secret))
    with paramiko.SSHClient() as ssh:
        ssh.get_host_keys().add(cfg["host"], key.get_name(), key)
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        _check_proof_window(proof_scope)
        ssh.connect(cfg["host"], username=cfg["user"], pkey=private, allow_agent=False,
                    look_for_keys=False, timeout=10, banner_timeout=10, auth_timeout=10)
        channel = ssh.get_transport().open_session(timeout=10)
        with channel:
            channel.settimeout(10)
            _check_proof_window(proof_scope)
            channel.exec_command(COMMAND)
            payload = wire(req)
            _check_proof_window(proof_scope)
            channel.sendall(payload)
            channel.shutdown_write()
            deadline = time.monotonic() + (req["timeout_seconds"] + 30 if req["action"] == "run" else 20)
            raw = bytearray()
            while time.monotonic() < deadline:
                if channel.recv_stderr_ready():
                    channel.recv_stderr(8192)
                    raise ValueError("host_stderr")
                if channel.recv_ready():
                    raw.extend(channel.recv(MAX_WIRE + 1 - len(raw)))
                    require(len(raw) <= MAX_WIRE, "response_size")
                elif channel.exit_status_ready():
                    require(channel.recv_exit_status() == 0, "host_refused")
                    return decode(bytes(raw))
                else:
                    time.sleep(0.025)
    raise ValueError("host_outcome_uncertain")


def receipt(cfg, req, result):
    basic = dict(namespace=NAMESPACE, workspace=WORKSPACE, descriptor_sha256=cfg["descriptor_sha256"],
                 job_id=req["job_id"], request_sha256=req["request_sha256"])
    require(isinstance(result, dict) and not set(result) - RECEIPT_FIELDS
            and all(result.get(k) == value for k, value in basic.items())
            and result.get("status") in TERMINAL | {"unknown", "running", "not_found", "cancellation_requested"},
            "receipt_binding")
    if "command_sha256" in result:
        resource_shape(result.get("resources"))
        require(matches(PIN, result["command_sha256"]) and result.get("runner_sha256") == cfg["runner_sha256"]
                and result.get("capacity_policy_sha256") == cfg["capacity_policy_sha256"], "receipt_runner")
        if req["action"] == "run":
            require(all(result.get(k) == req[k] for k in ("mode", "timeout_seconds", "resources"))
                    and result["command_sha256"] == sha(req["command"].encode()), "receipt_request")
    for key in ("reconcile_required", "child_tree_gone"):
        require(key not in result or type(result[key]) is bool, "receipt_flag")
    if "exit_code" in result:
        require(result["exit_code"] is None or type(result["exit_code"]) is int, "receipt_exit")
    if "heartbeat_at" in result:
        observed = result["heartbeat_at"]
        require(type(observed) in (int, float) and math.isfinite(observed) and observed > 0, "receipt_heartbeat")
    if "reason" in result:
        require(result["reason"] == "resource_limit", "receipt_reason")
    for key, kind in (("log_bytes", int), ("logs_truncated", bool)):
        if key in result:
            logs = result[key]
            require(isinstance(logs, dict) and set(logs) == {"stdout", "stderr"}
                    and all(type(v) is kind and 0 <= v <= 1024 * 1024 for v in logs.values()), "receipt_logs")
    if "lease_id" in result:
        require(matches(ID, result["lease_id"]), "receipt_lease")
    if result["status"] == "refused":
        require("lease_id" not in result and result.get("child_tree_gone") is False
                and result.get("reconcile_required") is False, "receipt_refusal")
    if result["status"] in TERMINAL - {"refused"}:
        require(result.get("child_tree_gone") is True and type(result.get("reconcile_required")) is bool
                and matches(ID, result.get("lease_id")) and "command_sha256" in result
                and (result["status"] != "succeeded" or type(result.get("exit_code")) is int
                     and result["exit_code"] == 0), "receipt_terminal")
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def record_incident(result):
    url = os.environ.get("BASE_INTERNAL_URL", "")
    parsed = urlsplit(url)
    require(parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
            and not parsed.query and not parsed.fragment and parsed.path in ("", "/"), "incident_origin")
    token = os.environ.get("WM_TOKEN")
    require(bool(token), "incident_identity")
    identity = {k: result[k] for k in ("namespace", "workspace", "job_id", "request_sha256", "descriptor_sha256")}
    value = dict(identity, status=result["status"])
    name = sha(wire(identity))
    payload = wire(dict(path=f"u/admin/foo_user_incidents/{name}", resource_type="any", value=value,
                        description="Sanitized original Foo job outcome"))
    req = Request(url.rstrip("/") + "/api/w/foo/resources/create", data=payload,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with build_opener(_NoRedirect()).open(req, timeout=5):
        pass


def main(action: str, job_id: str, request_sha256: str, command: str = None, mode: str = None,
         timeout_seconds: int = None, resources: dict = None) -> dict:
    require(os.environ.get("WM_WORKSPACE") == WORKSPACE, "workspace_binding")
    scope = _PROOF_SCOPE
    if scope is not None:
        req = _proof_request(scope, action, job_id, request_sha256, command, mode, timeout_seconds, resources)
    import wmill

    registration = wmill.get_variable(ROUTE)
    require(isinstance(registration, str), "route_unavailable")
    cfg = route(decode(registration.encode()))
    if scope is None:
        req = request(cfg, action, job_id, request_sha256, command, mode, timeout_seconds, resources)
    else:
        require(sha(registration.encode()) == scope.route_sha256
                and cfg["descriptor_sha256"] == scope.descriptor_sha256, "proof_route")
        _check_proof_window(scope)
    try:
        secret = wmill.get_variable(cfg["credential_reference"])
        _check_proof_window(scope)
        result = receipt(cfg, req, exchange(cfg, req, secret, proof_scope=scope))
    except _ProofWindowClosed:
        raise
    except Exception:
        # Only the exact original request may be inspected after a lost response.
        return dict(namespace=NAMESPACE, workspace=WORKSPACE, descriptor_sha256=cfg["descriptor_sha256"],
                    job_id=job_id, request_sha256=request_sha256, status="unknown",
                    reconcile_required=True, job_succeeded=False)
    incident = None
    if result["status"] in {"failed", "timed_out", "cancelled"}:
        if scope is None:
            try:
                record_incident(result)
            except Exception:
                incident = "unconfirmed"
        else:
            incident = "suppressed:qualification"
    return dict(result, job_succeeded=result["status"] == "succeeded"
                and result.get("reconcile_required") is False, incident=incident)
