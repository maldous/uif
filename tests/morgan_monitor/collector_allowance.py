"""Synthetic allowance metadata/protocol fixtures. No real account or daemon I/O."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest

from tools.morgan_monitor.allowance import (
    DEFAULT_ALLOWANCE_CONFIG, FooAllowance, LocalAllowanceRPC, NOTIFICATIONS,
    classify_allowance, validate_allowance_config,
)
from tools.morgan_monitor.events import EventError, make_event, validate_event
from tools.morgan_monitor.sources import SourceError
from tools.morgan_monitor.store import Store

NOW = 1794240000
PRIVATE = "SYNTHETIC-PRIVATE-ACCOUNT-DO-NOT-PUBLISH"


def account():
    return {"account": {"type": "chatgpt", "email": PRIVATE, "planType": "pro", "workspaceName": PRIVATE}, "requiresOpenaiAuth": True,
            "workspaceRouting": {"accountRoutingOverride": "NO_CONSTRAINT", "backendOrigin": "https://synthetic.example", "chatgptAccountId": PRIVATE}}


def payload():
    core = {"limitId": "codex", "limitName": PRIVATE, "normalModelSlug": None,
            "primary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": NOW + 86400},
            "secondary": None, "credits": {"hasCredits": False, "unlimited": False, "balance": PRIVATE},
            "individualLimit": None, "spendControlReached": False, "planType": "pro", "rateLimitReachedType": "rate_limit_reached"}
    return {"ordinaryUsageAllowed": False, "accountId": PRIVATE, "rateLimits": copy.deepcopy(core),
            "rateLimitsByLimitId": {"codex": core}, "rateLimitResetCredits": None, "rateLimitUpsell": None}


class ClassifierTests(unittest.TestCase):
    def test_explicit_block_plus_quota_and_no_credits(self):
        proof = classify_allowance(account(), payload(), NOW)
        self.assertEqual(proof, {"state": "exhausted", "canRunAgents": False})
        self.assertNotIn(PRIVATE, json.dumps(proof))

    def test_null_permission_is_not_inferred_from_100_percent(self):
        data = payload()
        data["ordinaryUsageAllowed"] = None
        self.assertEqual(classify_allowance(account(), data, NOW), {"state": "unavailable", "canRunAgents": None})

    def test_recovery_requires_explicit_backend_permission(self):
        data = payload()
        core = data["rateLimitsByLimitId"]["codex"]
        core["primary"]["usedPercent"] = 0
        core["rateLimitReachedType"] = None
        data["ordinaryUsageAllowed"] = None
        self.assertEqual(classify_allowance(account(), data, NOW)["state"], "unavailable")
        data["ordinaryUsageAllowed"] = True
        self.assertEqual(classify_allowance(account(), data, NOW), {"state": "valid", "canRunAgents": True})

    def test_reset_clock_passage_never_proves_recovery(self):
        self.assertEqual(classify_allowance(account(), payload(), NOW + 90000)["state"], "unavailable")

    def test_credits_missing_binding_and_conflicting_proof_fail_closed(self):
        mutations = [
            lambda data: data.update(accountId=None),
            lambda data: data["rateLimitsByLimitId"]["codex"]["credits"].update(hasCredits=True),
            lambda data: data["rateLimitsByLimitId"]["codex"]["credits"].update(unlimited=True),
            lambda data: data["rateLimitsByLimitId"]["codex"].update(credits=None),
            lambda data: data["rateLimitsByLimitId"]["codex"].update(spendControlReached=True),
            lambda data: data["rateLimitsByLimitId"]["codex"].update(rateLimitReachedType=None),
            lambda data: data.update(ordinaryUsageAllowed=True),
        ]
        for mutate in mutations:
            data = payload()
            mutate(data)
            self.assertEqual(classify_allowance(account(), data, NOW)["state"], "unavailable")

    def test_present_bucket_map_does_not_fall_back_to_unrelated_legacy(self):
        data = payload()
        data["rateLimitsByLimitId"] = {"other": data["rateLimits"]}
        self.assertEqual(classify_allowance(account(), data, NOW)["state"], "unavailable")

    def test_api_key_and_signed_out_accounts_not_assumed_chatgpt(self):
        for value in (None, {"type": "apiKey"}, {"type": "amazonBedrock"}):
            self.assertEqual(classify_allowance({"account": value, "requiresOpenaiAuth": False}, payload(), NOW)["state"], "unavailable")

    def test_protocol_drift_rejected(self):
        data = payload()
        data["newSecretField"] = PRIVATE
        with self.assertRaisesRegex(SourceError, "protocol_drift"):
            classify_allowance(account(), data, NOW)
        data = payload()
        data["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = True
        with self.assertRaises(SourceError): classify_allowance(account(), data, NOW)

    def test_usage_account_must_match_selected_workspace(self):
        for routing in (None, {"accountRoutingOverride": "NO_CONSTRAINT", "backendOrigin": "https://synthetic.example", "chatgptAccountId": "different-synthetic"}):
            selected = account()
            selected["workspaceRouting"] = routing
            self.assertEqual(classify_allowance(selected, payload(), NOW)["state"], "unavailable")

    def test_malformed_field_types_fail_closed_without_crashing_collector(self):
        class FakeReader:
            def __init__(self, config): pass
            def read(self):
                data = payload()
                data["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = {"private": PRIVATE}
                return account(), data
        obs = FooAllowance(dict(DEFAULT_ALLOWANCE_CONFIG, socketPath="synthetic"), reader_factory=FakeReader, clock=lambda: NOW).collect(NOW)
        self.assertEqual(obs.health, "unavailable")
        self.assertEqual(obs.error_code, "protocol_drift")
        self.assertNotIn(PRIVATE, str(obs))

    def test_default_adapter_never_connects(self):
        def forbidden(config): raise AssertionError("default must not connect")
        result = FooAllowance(DEFAULT_ALLOWANCE_CONFIG, reader_factory=forbidden).collect(NOW)
        self.assertEqual(result.health, "unconfigured")
        self.assertIsNone(result.allowance)

    def test_completed_read_time_and_public_safe_persistence(self):
        class FakeReader:
            def __init__(self, config): pass
            def read(self): return account(), payload()
        config = dict(DEFAULT_ALLOWANCE_CONFIG, socketPath="synthetic")
        obs = FooAllowance(config, reader_factory=FakeReader, clock=lambda: NOW + 5).collect(NOW)
        self.assertEqual(obs.observed_at, NOW + 5)
        self.assertEqual(obs.allowance["observed_at"], NOW + 5)
        event = make_event(obs, 1, [])
        self.assertEqual(event["observedAt"], event["fooAllowance"]["observedAt"])
        self.assertNotIn(PRIVATE, json.dumps(event))
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "monitor.sqlite3")
            try:
                store.save(obs, lambda seq: make_event(obs, seq, []))
                snapshot = store.snapshot()
                self.assertEqual(snapshot["sources"][0]["fooAllowance"]["state"], "exhausted")
                self.assertNotIn(PRIVATE, json.dumps(snapshot))
            finally: store.close()

    def test_source_failure_does_not_emit_an_allowance_proof(self):
        class FakeReader:
            def __init__(self, config): pass
            def read(self): raise SourceError("identity_mismatch")
        obs = FooAllowance(dict(DEFAULT_ALLOWANCE_CONFIG, socketPath="synthetic"), reader_factory=FakeReader, clock=lambda: NOW).collect(NOW)
        self.assertEqual(obs.health, "unavailable")
        self.assertNotIn("fooAllowance", make_event(obs, 1, []))

    def test_exact_public_shape_and_boolean_pairs(self):
        from tools.morgan_monitor.sources import Observation
        obs = Observation("foo_allowance", NOW, "ok", "foo_allowance", allowance={"state": "exhausted", "canRunAgents": False, "observed_at": NOW})
        for mutate in (lambda event: event["fooAllowance"].update(accountId=PRIVATE),
                       lambda event: event["fooAllowance"].update(canRunAgents=0),
                       lambda event: event.update(sourceId="codex_registry"),
                       lambda event: event.update(health="error")):
            event = make_event(obs, 1, [])
            mutate(event)
            with self.assertRaises(EventError): validate_event(event)

    def test_config_requires_complete_pinned_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(validate_allowance_config(DEFAULT_ALLOWANCE_CONFIG, base), DEFAULT_ALLOWANCE_CONFIG)
            for field, value in (("socketPath", "synthetic.sock"), ("expectedVersion", "future"), ("expectedUid", 123)):
                config = dict(DEFAULT_ALLOWANCE_CONFIG)
                config[field] = value
                with self.assertRaises(ValueError): validate_allowance_config(config, base)


class SyntheticServer:
    """Temporary same-process Unix socket, containing only synthetic metadata."""
    def __init__(self, root, *, fragment=False, ping=False, failure=None):
        self.root, self.fragment, self.ping, self.failure = root, fragment, ping, failure
        self.path = root / "synthetic.sock"
        self.requests, self.errors = [], []
        self.stop = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.listener.listen(1)
        self.listener.settimeout(.1)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(2)

    def config(self):
        exe = os.readlink("/proc/self/exe")
        return {"socketPath": str(self.path), "expectedExecutable": exe,
                "expectedExecutableSha256": hashlib.sha256(Path(exe).read_bytes()).hexdigest(),
                "expectedCodexHome": str(self.root), "expectedUid": os.getuid(), "expectedVersion": "0.167.0", "timeoutSeconds": 2}

    @staticmethod
    def exact(conn, count):
        data = b""
        while len(data) < count:
            part = conn.recv(count - len(data))
            if not part: raise OSError("synthetic disconnect")
            data += part
        return data

    def client_frame(self, conn):
        a, b = self.exact(conn, 2)
        if not b & 128: raise AssertionError("client frame must be masked")
        length = b & 127
        if length == 126: length = struct.unpack("!H", self.exact(conn, 2))[0]
        elif length == 127: length = struct.unpack("!Q", self.exact(conn, 8))[0]
        mask = self.exact(conn, 4)
        data = self.exact(conn, length)
        return a & 15, bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))

    @staticmethod
    def server_frame(conn, data, *, final=True, opcode=1):
        head = (128 if final else 0) | opcode
        frame = bytes([head, len(data)]) if len(data) < 126 else bytes([head, 126]) + struct.pack("!H", len(data))
        conn.sendall(frame + data)

    def run(self):
        try:
            while not self.stop.is_set():
                try: conn, _ = self.listener.accept(); break
                except socket.timeout: continue
            else: return
            with conn:
                conn.settimeout(1)
                header = b""
                while not header.endswith(b"\r\n\r\n"): header += self.exact(conn, 1)
                fields = dict(line.split(": ", 1) for line in header.decode().split("\r\n")[1:] if line)
                if not header.startswith(b"GET / HTTP/1.1\r\n"): raise AssertionError("wrong handshake target")
                accept = base64.b64encode(hashlib.sha1((fields["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
                for _ in range(5):
                    opcode, data = self.client_frame(conn)
                    if opcode != 1: raise AssertionError("unexpected client frame")
                    request = json.loads(data)
                    self.requests.append(request)
                    method = request["method"]
                    if method == "initialized": continue
                    if method == "initialize":
                        self.assert_initialization(request)
                        result = {"codexHome": str(self.root), "platformFamily": "unix", "platformOs": "linux", "userAgent": "synthetic/0.167.0"}
                        if self.failure == "wrong_home": result["codexHome"] = str(self.root / "other")
                    elif method == "account/read":
                        if request["params"] != {"refreshToken": False}: raise AssertionError("refresh prohibited")
                        result = account()
                        if self.failure == "account_changed" and request["id"] == 4: result["account"]["email"] = "changed-synthetic"
                    elif method == "account/rateLimits/read":
                        if request["params"] != {"excludeResetCreditDetails": True}: raise AssertionError("unqualified capability")
                        result = payload()
                    else: raise AssertionError("unapproved RPC")
                    if self.failure == "rpc_error":
                        result_frame = {"id": request["id"], "error": {"message": PRIVATE}}
                    elif self.failure == "notification":
                        result_frame = {"method": "thread/started", "params": {"title": PRIVATE}}
                    else: result_frame = {"id": request["id"], "result": result}
                    encoded = json.dumps(result_frame).encode()
                    if self.failure == "oversized":
                        conn.sendall(bytes([0x81, 0x7f]) + struct.pack("!Q", 65537))
                        return
                    if self.failure == "slow":
                        time.sleep(.2)
                    if self.ping:
                        self.server_frame(conn, b"synthetic-ping", opcode=9)
                        opcode, pong = self.client_frame(conn)
                        if opcode != 10 or pong != b"synthetic-ping": raise AssertionError("ping/pong mismatch")
                    if self.fragment:
                        self.server_frame(conn, encoded[:8], final=False)
                        self.server_frame(conn, encoded[8:], opcode=0)
                    else: self.server_frame(conn, encoded)
        except OSError: pass
        except Exception as exc: self.errors.append(type(exc).__name__)

    @staticmethod
    def assert_initialization(request):
        caps = request["params"]["capabilities"]
        if caps != {"experimentalApi": False, "optOutNotificationMethods": list(NOTIFICATIONS)}:
            raise AssertionError("unqualified initialization")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()
            self.assertEqual(server.errors, [])
        self.temp.cleanup()

    def server(self, **kwargs):
        server = SyntheticServer(self.root, **kwargs)
        self.servers.append(server)
        return server

    def test_existing_peer_rpc_allowlist_and_no_auth_refresh(self):
        server = self.server()
        actual_account, actual_payload = LocalAllowanceRPC(server.config()).read()
        self.assertEqual(classify_allowance(actual_account, actual_payload, NOW)["state"], "exhausted")
        self.assertEqual([value["method"] for value in server.requests], ["initialize", "initialized", "account/read", "account/rateLimits/read", "account/read"])
        self.assertFalse(any("login" in value["method"] for value in server.requests))
        self.assertNotIn("thread/read", [value["method"] for value in server.requests])

    def test_fragmented_frames_and_ping_are_bounded_protocol_only(self):
        server = self.server(fragment=True, ping=True)
        self.assertEqual(LocalAllowanceRPC(server.config()).read(), (account(), payload()))

    def test_wrong_executable_hash_fails_before_connect(self):
        server = self.server()
        config = server.config()
        config["expectedExecutableSha256"] = "0" * 64
        with self.assertRaisesRegex(SourceError, "identity_mismatch"): LocalAllowanceRPC(config).read()
        self.assertEqual(server.requests, [])

    def test_untrusted_socket_mode_fails_before_connect(self):
        server = self.server()
        os.chmod(server.path, 0o660)
        with self.assertRaisesRegex(SourceError, "identity_mismatch"): LocalAllowanceRPC(server.config()).read()
        self.assertEqual(server.requests, [])

    def test_private_identity_mismatch_suppresses_output(self):
        server = self.server(failure="wrong_home")
        obs = FooAllowance(server.config(), clock=lambda: NOW).collect(NOW)
        self.assertEqual(obs.health, "unavailable")
        self.assertEqual(obs.error_code, "identity_mismatch")
        self.assertNotIn(str(self.root), str(obs))

    def test_account_switch_fails_closed(self):
        server = self.server(failure="account_changed")
        obs = FooAllowance(server.config(), clock=lambda: NOW).collect(NOW)
        self.assertEqual(obs.error_code, "identity_mismatch")
        self.assertIsNone(obs.allowance)

    def test_raw_rpc_error_never_exposed(self):
        server = self.server(failure="rpc_error")
        obs = FooAllowance(server.config(), clock=lambda: NOW).collect(NOW)
        self.assertEqual(obs.error_code, "metadata_unavailable")
        self.assertNotIn(PRIVATE, str(obs))

    def test_unsolicited_notification_terminates_metadata_session(self):
        server = self.server(failure="notification")
        obs = FooAllowance(server.config(), clock=lambda: NOW).collect(NOW)
        self.assertEqual(obs.error_code, "metadata_unavailable")
        self.assertNotIn(PRIVATE, str(obs))

    def test_oversized_frame_is_rejected_without_reading_body(self):
        server = self.server(failure="oversized")
        with self.assertRaisesRegex(SourceError, "protocol_drift"):
            LocalAllowanceRPC(server.config()).read()

    def test_total_timeout_is_bounded(self):
        server = self.server(failure="slow")
        config = server.config()
        config["timeoutSeconds"] = .1
        start = time.monotonic()
        with self.assertRaisesRegex(SourceError, "source_unavailable"):
            LocalAllowanceRPC(config).read()
        self.assertLess(time.monotonic() - start, .5)

    def test_nonregular_executable_rejected_without_blocking(self):
        server = self.server()
        fifo = self.root / "synthetic-fifo"
        os.mkfifo(fifo)
        config = server.config()
        config["expectedExecutable"] = str(fifo)
        start = time.monotonic()
        with self.assertRaisesRegex(SourceError, "identity_mismatch"):
            LocalAllowanceRPC(config).read()
        self.assertLess(time.monotonic() - start, .5)
        self.assertEqual(server.requests, [])
