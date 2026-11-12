import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from uif import host_jobs as host


REPO = Path(__file__).resolve().parents[2]
MODULE = importlib.util.spec_from_file_location("foo_jobs", REPO / "config/windmill/source/foo_jobs.py")
front = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(front)


class Authority:
    def __init__(self):
        self.calls = []

    def call(self, action, binding, **details):
        self.calls.append((action, binding, details))
        if action == "admit":
            assert not details
            return dict(status="admitted", reused=False, lease_id=host.lease_id(binding),
                        cpu_ids=[min(os.sched_getaffinity(0))])
        if action in ("status", "cancel"):
            assert not details or action == "cancel" and set(details) == {"request"}
            return dict(status="running" if action == "status" else "cancel_requested",
                        lease_id=host.lease_id(binding))
        return dict(schema="uif-shared-job-admission/1", binding_sha256=host.sha(host.wire(binding)),
                    status="finished", lease_id=details["lease_id"], receipt_sha256=details["receipt_sha256"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.scope, self.control = root / "execution", root / "control"
        self.scope.mkdir(mode=0o700)
        self.control.mkdir(mode=0o700)
        self.allowed, self.other, self.protected = (self.scope / name for name in ("allowed", "other", "protected"))
        for location in (self.allowed, self.other, self.protected):
            location.mkdir()
        self.cfg = dict(
            schema="uif-foo-user-host/1", workspace="foo", protocol=host.PROTOCOL, uid=os.geteuid(),
            execution_root=str(self.scope), control_root=str(self.control),
            read_roots=[str(self.allowed)], write_roots=[str(self.allowed)], protected_roots=[str(self.protected)],
            runner_sha256="a" * 64, policy_sha256="b" * 64,
            limits=dict(workers=4, overhead_processes=4, cpu_millis=1000, memory_bytes=512 * 1024**2,
                        output_bytes=2**20, io_slots=2, disk_reserve_bytes=0, memory_reserve_bytes=0),
            authority=dict(path=str(root / "missing-authority"), sha256="c" * 64),
        )
        self.descriptor = "d" * 64
        self.jobs = host.HostJobs(self.cfg, self.descriptor)
        self.authority = Authority()
        self.front_config = dict(
            schema="uif-foo-user-route/1", workspace="foo", protocol=host.PROTOCOL, host="fixture.invalid",
            user="foo-fixture", host_key="fixture-key", credential_reference="u/admin/fixture_reference",
            descriptor_sha256=self.descriptor, runner_sha256=self.cfg["runner_sha256"],
            capacity_policy_sha256=self.cfg["policy_sha256"],
        )

    def run_request(self, *, command="printf hello", job="original", mode="read", read=None,
                    write=None, output_bytes=0):
        resources = dict(workers=2, overhead_processes=2, cpu_millis=100, memory_bytes=128 * 1024**2,
                         output_bytes=output_bytes, io_slots=1, read_roots=[str(self.allowed)] if read is None else read,
                         write_roots=[] if write is None else write)
        request = dict(protocol=host.PROTOCOL, workspace="foo", descriptor_sha256=self.descriptor, job_id=job,
                       command=command, mode=mode, timeout_seconds=5, resources=resources)
        return dict(request, action="run", request_sha256=host.sha(host.wire(request)))

    @staticmethod
    def observe(req, action="status"):
        return {k: req[k] for k in ("protocol", "workspace", "descriptor_sha256", "job_id", "request_sha256")} | {
            "action": action,
        }

    def test_refuses_wrong_route_digest_and_protected_or_cross_project_roots(self):
        req = self.run_request()
        for key, value in (("workspace", "another"), ("protocol", "other"), ("descriptor_sha256", "1" * 64),
                           ("request_sha256", "2" * 64)):
            with self.assertRaises(host.Hold):
                self.jobs.handle(dict(req, **{key: value}))
        for forbidden in (self.other, self.protected, self.control, self.scope):
            with self.assertRaisesRegex(host.Hold, "resource_scope"):
                self.jobs.handle(self.run_request(read=[str(forbidden)]))
        self.assertEqual(list(self.control.iterdir()), [])

    def test_missing_authority_keeps_original_unknown_and_never_starts(self):
        req = self.run_request()
        with patch.object(host.subprocess, "Popen", side_effect=AssertionError("unexpected child")):
            self.assertEqual(self.jobs.handle(req)["status"], "unknown")
            self.assertEqual(self.jobs.handle(req)["status"], "unknown")
        self.assertEqual(self.jobs.handle(self.observe(req))["status"], "unknown")
        self.assertEqual(self.jobs.handle(self.observe(req, "cancel"))["status"], "cancellation_requested")
        with self.assertRaisesRegex(host.Hold, "original_binding_conflict"):
            self.jobs.handle(self.run_request(command="printf different"))

    def test_failed_and_cancelled_originals_do_not_replay(self):
        self.jobs.authority = self.authority
        for outcome in ("failed", "cancelled"):
            req = self.run_request(job=outcome)
            with patch.object(self.jobs, "_broker_turn", return_value=dict(status=outcome, child_tree_gone=True,
                                                                       exit_code=1, reconcile_required=False)) as run:
                original = self.jobs.handle(req)
                self.assertEqual(self.jobs.handle(req), original)
                self.assertEqual(self.jobs.handle(self.observe(req, "cancel")), original)
                self.assertEqual(run.call_count, 1)
            self.assertEqual(original["status"], outcome)
        self.assertEqual([entry[0] for entry in self.authority.calls], ["admit", "finish", "admit", "finish"])

    def test_uncertain_cleanup_keeps_lease_and_original_receipt(self):
        req = self.run_request()
        self.jobs.authority = self.authority
        with patch.object(self.jobs, "_broker_turn", return_value=dict(status="unknown", child_tree_gone=False,
                                                                   reconcile_required=True)) as run:
            record = self.jobs.handle(req)
            self.assertEqual(self.jobs.handle(req), record)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(record["lease_id"], host.lease_id(self.jobs._binding(req)))
        self.assertEqual([entry[0] for entry in self.authority.calls], ["admit"])

    def test_lost_finish_keeps_terminal_bytes_and_prevents_replay(self):
        req = self.run_request()
        calls = []

        def call(action, binding, **details):
            calls.append(action)
            if action == "finish":
                raise TimeoutError
            return self.authority.call(action, binding, **details)

        self.jobs.authority = SimpleNamespace(call=call)
        with patch.object(self.jobs, "_broker_turn", return_value=dict(status="failed", child_tree_gone=True,
                                                                   exit_code=1, reconcile_required=False)):
            self.assertTrue(self.jobs.handle(req)["reconcile_required"])
        path = self.control / req["job_id"] / "receipt.json"
        original = path.read_bytes()
        self.assertTrue(self.jobs.handle(req)["reconcile_required"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(calls, ["admit", "finish"])

    def test_wrong_workspace_and_absent_registration_refuse_before_exchange(self):
        req = self.run_request()
        wmill = SimpleNamespace(get_variable=lambda _: None)
        with patch.dict(os.environ, {"WM_WORKSPACE": "other"}, clear=True):
            with self.assertRaisesRegex(ValueError, "workspace_binding"):
                front.main("status", req["job_id"], req["request_sha256"])
        with (patch.dict(os.environ, {"WM_WORKSPACE": "foo"}, clear=True),
              patch.dict("sys.modules", {"wmill": wmill}),
              patch.object(front, "exchange", side_effect=AssertionError("unexpected transport"))):
            with self.assertRaisesRegex(ValueError, "route_unavailable"):
                front.main("status", req["job_id"], req["request_sha256"])

    def test_incident_failure_preserves_original_failed_outcome(self):
        req = self.run_request()
        response = self.jobs._binding(req) | dict(status="failed", child_tree_gone=True,
                                                  lease_id="original-lease", exit_code=1, reconcile_required=False)
        calls = []

        def variable(name):
            calls.append(name)
            return front.wire(self.front_config).decode() if name == front.ROUTE else "synthetic-credential"

        with (patch.dict(os.environ, {"WM_WORKSPACE": "foo"}, clear=True),
              patch.dict("sys.modules", {"wmill": SimpleNamespace(get_variable=variable)}),
              patch.object(front, "exchange", return_value=response) as exchange,
              patch.object(front, "record_incident", side_effect=PermissionError)):
            result = front.main("run", req["job_id"], req["request_sha256"], req["command"], req["mode"],
                                req["timeout_seconds"], req["resources"])
        self.assertEqual((result["status"], result["job_succeeded"], result["incident"]), ("failed", False, "unconfirmed"))
        self.assertEqual(exchange.call_args.args[1], req)
        self.assertEqual(calls, [front.ROUTE, self.front_config["credential_reference"]])

    def test_wrong_receipt_identity_is_held_with_no_second_call(self):
        req = self.run_request()
        bad = self.jobs._binding(req) | {"status": "unknown", "job_id": "different"}
        fixture = SimpleNamespace(get_variable=lambda _: front.wire(self.front_config).decode())
        with (patch.dict(os.environ, {"WM_WORKSPACE": "foo"}, clear=True),
              patch.dict("sys.modules", {"wmill": fixture}),
              patch.object(front, "exchange", return_value=bad) as exchange):
            result = front.main("status", req["job_id"], req["request_sha256"])
        self.assertTrue(result["reconcile_required"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(exchange.call_count, 1)

    def helper_result(self, req, *, budget, mismatch=False, lost_start=False):
        self.jobs.authority = self.authority
        binding = self.jobs._binding(req)
        directory = self.control / req["job_id"]
        actions = []

        def terminal():
            counts = {"stdout": budget, "stderr": 0}
            if mismatch:
                counts["stdout"] += 1
            return dict(status="running", lease_id=host.lease_id(binding), observation=dict(
                state="exited-clean", containment_stopped=True, request_sha256="a" * 64,
                receipt_sha256="b" * 64, outcome="succeeded", exit_code=0,
                log_bytes=counts, logs_truncated={"stdout": True, "stderr": True},
            ))

        def call(action, original, **details):
            actions.append(action)
            if action == "start":
                self.assertEqual(original, binding)
                self.assertEqual(details, {"request": req})
                self.assertEqual((directory / "request.json").read_bytes(), host.wire(req))
                (directory / "stdout.log").write_bytes(b"x" * budget)
                (directory / "stderr.log").write_bytes(b"")
                if lost_start:
                    raise TimeoutError
                return terminal()
            if action == "status" and lost_start:
                return terminal()
            return self.authority.call(action, original, **details)

        self.jobs.authority = SimpleNamespace(call=call)
        result = self.jobs.handle(req)
        self.assertEqual(self.jobs.handle(req), result)
        self.assertEqual((directory / "stdout.log").stat().st_size, budget)
        return result, actions

    def test_broker_owns_zero_byte_logs_and_terminal_metadata(self):
        result, actions = self.helper_result(self.run_request(), budget=0)
        self.assertEqual((result["status"], result["log_bytes"]), ("succeeded", {"stdout": 0, "stderr": 0}))
        self.assertEqual(actions, ["admit", "start", "finish"])

    def test_stdout_and_stderr_still_share_one_output_reservation(self):
        req = self.run_request(output_bytes=128)
        result, actions = self.helper_result(req, budget=128)
        self.assertEqual((result["status"], sum(result["log_bytes"].values())), ("succeeded", 128))
        self.assertEqual(actions, ["admit", "start", "finish"])

    def test_over_budget_helper_metadata_holds_without_finish(self):
        result, actions = self.helper_result(self.run_request(), budget=0, mismatch=True)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(actions, ["admit", "start"])

    def test_lost_start_observes_the_same_job_without_relaunch(self):
        result, actions = self.helper_result(self.run_request(), budget=0, lost_start=True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(actions, ["admit", "start", "status", "finish"])

    def test_fixed_ssh_command_and_no_ambient_key_discovery(self):
        req = self.observe(self.run_request())
        returned = front.wire(self.jobs.handle(req))
        calls = []

        class Channel:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def exec_command(self, command):
                calls.append(("exec", command))

            def sendall(self, payload):
                calls.append(("request", payload))

            def recv_ready(self):
                return len(calls) == 4

            def recv(self, bound):
                self.asserted_bound = bound
                calls.append(("read", bound))
                return returned

            def settimeout(self, value):
                calls.append(("timeout", value))

            def shutdown_write(self):
                pass

            def recv_stderr_ready(self):
                return False

            def exit_status_ready(self):
                return True

            def recv_exit_status(self):
                return 0

        class SSH:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def connect(self, endpoint, **options):
                calls.append(("connect", endpoint, options))

            def get_host_keys(self):
                return SimpleNamespace(add=lambda host, kind, key: None)

            def set_missing_host_key_policy(self, policy):
                pass

            def get_transport(self):
                return SimpleNamespace(open_session=lambda **_: Channel())

        key = SimpleNamespace(get_name=lambda: "ssh-ed25519")
        key_type = type("FixtureKey", (), {"__new__": lambda cls, **_: key,
                                          "from_private_key": staticmethod(lambda _: key)})
        paramiko = SimpleNamespace(Ed25519Key=key_type, SSHClient=SSH, RejectPolicy=object)
        with patch.dict("sys.modules", {"paramiko": paramiko}):
            self.assertEqual(front.exchange(dict(self.front_config, host_key="Zml4dHVyZQ=="), req, "synthetic"),
                             front.decode(returned))
        self.assertEqual(calls[0][1], "fixture.invalid")
        self.assertIs(calls[0][2]["allow_agent"], False)
        self.assertIs(calls[0][2]["look_for_keys"], False)
        self.assertEqual(calls[2:4], [("exec", front.COMMAND), ("request", front.wire(req))])

    def test_forced_entrypoint_refuses_other_commands_and_tty_before_loading(self):
        spec = importlib.util.spec_from_file_location("foo_user_exec", REPO / "tools/windmill/foo_user_exec.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with (patch.object(cli.sys, "argv", [str(spec.origin)]),
              patch.object(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False)),
              patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "unbound command"}, clear=True),
              patch.object(cli, "load_descriptor", side_effect=AssertionError("unexpected descriptor read"))):
            self.assertEqual(cli.main(), 2)
            os.environ.update(SSH_ORIGINAL_COMMAND=cli.COMMAND, SSH_TTY="fixture")
            self.assertEqual(cli.main(), 2)

    def test_pinned_authority_executable_uses_only_fixed_stdio(self):
        executable = self.control.parent / "synthetic-authority"
        executable.write_text("""#!/usr/bin/python3
import hashlib, json, os, sys
assert len(sys.argv) == 1
assert set(os.environ) <= {"PATH", "LANG", "LC_CTYPE"}
value = json.load(sys.stdin)
assert value['schema'] == 'uif-shared-job-admission/1'
assert value['action'] == 'admit'
raw = json.dumps(value['binding'], sort_keys=True, separators=(',', ':')).encode()
print(json.dumps(dict(schema=value['schema'], binding_sha256=hashlib.sha256(raw).hexdigest(),
                      status='admitted', reused=False, lease_id='neutral-'+hashlib.sha256(
                          json.dumps([value['binding']['namespace'], value['binding']['workspace'],
                                      value['binding']['job_id']], separators=(',', ':')).encode()).hexdigest(),
                      cpu_ids=[min(os.sched_getaffinity(0))])))
""")
        executable.chmod(0o700)
        binding = self.jobs._binding(self.run_request())
        authority = host.SharedAuthority({"path": str(executable), "sha256": host.sha(executable.read_bytes())})
        read = host._read
        # The synthetic executable is owned by the fixture user. The
        # production descriptor always requires root-owned installation.
        with (patch.object(host, "_trusted_ancestry"),
              patch.object(host, "_read", side_effect=lambda item, uid, limit: read(item, os.getuid(), limit))):
            lease = host.lease_id(binding)
            self.assertEqual(authority.call("admit", binding)["lease_id"], lease)
            executable.write_text("drifted")
            with self.assertRaisesRegex(host.Hold, "authority_pin"):
                authority.call("admit", binding)

    def test_known_original_lease_cancel_marks_intent_once_without_release(self):
        req = self.run_request()
        self.jobs.authority = self.authority
        with patch.object(self.jobs, "_broker_turn", return_value=dict(status="unknown", child_tree_gone=False,
                                                                   reconcile_required=True)):
            original = self.jobs.handle(req)
        for _ in range(2):
            cancelled = self.jobs.handle(self.observe(req, "cancel"))
            self.assertEqual(cancelled["status"], "cancellation_requested")
        self.assertEqual([call[0] for call in self.authority.calls], ["admit", "cancel"])
        self.assertEqual(self.authority.calls[-1][1:], (self.jobs._binding(req), {"request": req}))
        self.assertEqual(self.jobs.handle(self.observe(req)), original)
        self.assertEqual(self.authority.calls[-1][0], "status")

    def test_reused_admission_is_observation_only(self):
        req = self.run_request()
        binding = self.jobs._binding(req)
        reused = SimpleNamespace(call=lambda *args, **kwargs: dict(status="admitted", reused=True,
                                                                   lease_id=host.lease_id(binding)))
        self.jobs.authority = reused
        with patch.object(self.jobs, "_broker_turn", side_effect=AssertionError("unexpected child")):
            result = self.jobs.handle(req)
            self.assertEqual((result["status"], result["lease_id"]), ("unknown", host.lease_id(binding)))
            self.assertEqual(self.jobs.handle(req), result)

    def test_released_status_recovers_only_original_acknowledgement(self):
        req = self.run_request()
        binding = self.jobs._binding(req)
        calls = []

        def call(action, original, **details):
            self.assertEqual(original, binding)
            calls.append(action)
            if action == "finish":
                raise TimeoutError
            if action == "status":
                receipt = host.decode((self.control / req["job_id"] / "receipt.json").read_bytes())
                return dict(schema="uif-shared-job-admission/1", binding_sha256=host.sha(host.wire(binding)),
                            status="finished", lease_id=host.lease_id(binding), pool_status="failed",
                            outcome="failed", receipt_sha256=host.sha(host.wire(receipt)))
            return self.authority.call(action, original, **details)

        self.jobs.authority = SimpleNamespace(call=call)
        with patch.object(self.jobs, "_broker_turn", return_value=dict(status="timed_out", child_tree_gone=True,
                                                                   exit_code=15, reconcile_required=False)):
            initial = self.jobs.handle(req)
        self.assertTrue(initial["reconcile_required"])
        directory = self.control / req["job_id"]
        retained = (directory / "receipt.json").read_bytes()
        self.assertIs(self.jobs.handle(self.observe(req))["reconcile_required"], False)
        self.assertEqual(self.jobs.handle(req), host.decode(retained))
        self.assertEqual(self.jobs.handle(self.observe(req)), host.decode(retained))
        self.assertEqual((directory / "receipt.json").read_bytes(), retained)
        self.assertEqual(calls, ["admit", "finish", "status"])

    def test_conflicting_release_status_cannot_change_original_receipt(self):
        req = self.run_request()
        calls = []

        def call(action, binding, **details):
            calls.append(action)
            if action == "finish":
                raise TimeoutError
            if action == "status":
                return dict(schema="uif-shared-job-admission/1", binding_sha256=host.sha(host.wire(binding)),
                            status="finished", lease_id=host.lease_id(binding), pool_status="failed",
                            outcome="failed", receipt_sha256="9" * 64)
            return self.authority.call(action, binding, **details)

        self.jobs.authority = SimpleNamespace(call=call)
        with patch.object(self.jobs, "_broker_turn", return_value=dict(status="failed", child_tree_gone=True,
                                                                   exit_code=1, reconcile_required=False)):
            self.jobs.handle(req)
        directory = self.control / req["job_id"]
        original = (directory / "receipt.json").read_bytes()
        self.assertTrue(self.jobs.handle(self.observe(req))["reconcile_required"])
        self.assertFalse((directory / "release.json").exists())
        self.assertEqual((directory / "receipt.json").read_bytes(), original)
        self.assertEqual(calls, ["admit", "finish", "status"])

    def test_original_run_request_bytes_are_durable_before_admit(self):
        request = self.run_request()
        path = self.control / request["job_id"] / "request.json"
        seen = []

        def call(action, binding, **details):
            self.assertEqual(action, "admit")
            self.assertEqual(path.read_bytes(), host.wire(request))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(host.decode(path.with_name("intent.json").read_bytes()), binding)
            seen.append(action)
            return {"status": "refused"}

        self.jobs.authority = SimpleNamespace(call=call)
        self.assertEqual(self.jobs.handle(request)["status"], "refused")
        self.assertEqual(self.jobs.handle(request)["status"], "refused")
        self.assertEqual(path.read_bytes(), host.wire(request))
        self.assertEqual(seen, ["admit"])

    def test_broker_no_fork_cancellation_uses_zero_log_metadata(self):
        request = self.run_request()
        binding = self.jobs._binding(request)
        calls = []

        def call(action, original, **details):
            calls.append(action)
            if action == "start":
                self.assertEqual(details, {"request": request})
                return dict(status="cancel_requested", lease_id=host.lease_id(binding), observation=dict(
                    state="refused-before-fork", containment_stopped=True,
                    request_sha256="a" * 64, receipt_sha256="b" * 64,
                    outcome="cancelled", exit_code=None, log_bytes={"stdout": 0, "stderr": 0},
                    logs_truncated={"stdout": False, "stderr": False},
                ))
            return self.authority.call(action, original, **details)

        self.jobs.authority = SimpleNamespace(call=call)
        result = self.jobs.handle(request)
        self.assertEqual((result["status"], result["exit_code"], result["child_tree_gone"]),
                         ("cancelled", None, True))
        self.assertFalse((self.control / request["job_id"] / "stdout.log").exists())
        self.assertEqual(self.jobs.handle(request), result)
        self.assertEqual(calls, ["admit", "start", "finish"])
