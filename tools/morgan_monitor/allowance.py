"""Optional read-only allowance probe for a qualified existing local app-server.

No process launch, auth/login/reset/refresh request, credential file, transcript
method, generic RPC, network listener or persistent connection is provided.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time
from typing import Any

from .sources import Observation, SourceError

QUALIFIED_VERSION = "0.167.0"
MAX_MESSAGE = 65536
NOTIFICATIONS = tuple(json.loads(Path(__file__).with_name("allowance_notifications.json").read_text()))
DEFAULT_ALLOWANCE_CONFIG = {
    "socketPath": None, "expectedExecutable": None, "expectedExecutableSha256": None,
    "expectedCodexHome": None, "expectedUid": None,
    "expectedVersion": QUALIFIED_VERSION, "timeoutSeconds": 5,
}


def validate_allowance_config(value, base: Path):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_ALLOWANCE_CONFIG):
        raise ValueError("invalid allowance config")
    result = dict(value)
    if result["expectedVersion"] != QUALIFIED_VERSION:
        raise ValueError("unqualified version")
    timeout = result["timeoutSeconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 10:
        raise ValueError("invalid allowance budget")
    identity_fields = ("socketPath", "expectedExecutable", "expectedExecutableSha256", "expectedCodexHome", "expectedUid")
    if result["socketPath"] is None:
        if any(result[key] is not None for key in identity_fields):
            raise ValueError("incomplete allowance identity")
        return result
    for key in ("socketPath", "expectedExecutable", "expectedCodexHome"):
        if not isinstance(result[key], str) or not result[key] or len(result[key]) > 4096:
            raise ValueError("invalid allowance identity")
        result[key] = str((base / Path(result[key]).expanduser()).resolve())
    if type(result["expectedUid"]) is not int or result["expectedUid"] < 0:
        raise ValueError("invalid allowance identity")
    if not isinstance(result["expectedExecutableSha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", result["expectedExecutableSha256"]):
        raise ValueError("invalid allowance identity")
    return result


def _exact(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise SourceError("protocol_drift")


def _nullable_bool(value):
    if value is not None and type(value) is not bool:
        raise SourceError("protocol_drift")


def classify_allowance(account: Any, payload: Any, now: int) -> dict[str, Any]:
    """Conservative, account-bound ordinary Codex eligibility, not all providers.

    Null does not mean zero/false/recovery. Never use reset-time passage alone.
    No percentage, account identifier, balance, model name or text is returned.
    """
    _exact(account, {"requiresOpenaiAuth"}, {"account", "workspaceRouting"})
    if type(account["requiresOpenaiAuth"]) is not bool:
        raise SourceError("protocol_drift")
    routing = account.get("workspaceRouting")
    if routing is not None:
        _exact(routing, {"accountRoutingOverride", "backendOrigin", "chatgptAccountId"})
        if (not isinstance(routing["accountRoutingOverride"], str) or routing["accountRoutingOverride"] not in {"NO_CONSTRAINT", "us", "us_cr"}
                or not isinstance(routing["backendOrigin"], str) or not 1 <= len(routing["backendOrigin"]) <= 2048
                or not isinstance(routing["chatgptAccountId"], str) or not 1 <= len(routing["chatgptAccountId"]) <= 256):
            raise SourceError("protocol_drift")
    selected = account.get("account")
    if not isinstance(selected, dict) or selected.get("type") != "chatgpt" or account["requiresOpenaiAuth"] is not True:
        return {"state": "unavailable", "canRunAgents": None}
    _exact(selected, {"type", "email", "planType"}, {"workspaceName"})
    for key in ("email", "planType", "workspaceName"):
        value = selected.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 1024):
            raise SourceError("protocol_drift")
    _exact(payload, {"rateLimits"}, {"ordinaryUsageAllowed", "rateLimitsByLimitId", "rateLimitResetCredits", "accountId", "rateLimitUpsell"})
    ordinary = payload.get("ordinaryUsageAllowed")
    _nullable_bool(ordinary)
    account_id = payload.get("accountId")
    if account_id is not None and (not isinstance(account_id, str) or not 1 <= len(account_id) <= 256):
        raise SourceError("protocol_drift")
    buckets = payload.get("rateLimitsByLimitId")
    if buckets is not None and (not isinstance(buckets, dict) or len(buckets) > 32):
        raise SourceError("protocol_drift")
    # A present multi-bucket response cannot silently fall back to a different
    # legacy/model-specific bucket when the core Codex bucket is absent.
    core = buckets.get("codex") if buckets is not None else payload["rateLimits"]
    if core is None:
        return {"state": "unavailable", "canRunAgents": None}
    _exact(core, set(), {"limitId", "limitName", "normalModelSlug", "primary", "secondary", "credits", "individualLimit", "spendControlReached", "planType", "rateLimitReachedType"})
    if core.get("limitId") != "codex":
        return {"state": "unavailable", "canRunAgents": None}
    reached = core.get("rateLimitReachedType")
    if (reached is not None and not isinstance(reached, str)) or reached not in {None, "rate_limit_reached", "workspace_owner_credits_depleted", "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached"}:
        raise SourceError("protocol_drift")
    _nullable_bool(core.get("spendControlReached"))
    exhausted_window = False
    for name in ("primary", "secondary"):
        window = core.get(name)
        if window is None:
            continue
        _exact(window, {"usedPercent"}, {"windowDurationMins", "resetsAt"})
        if type(window["usedPercent"]) is not int or not 0 <= window["usedPercent"] <= 100:
            raise SourceError("protocol_drift")
        for field in ("windowDurationMins", "resetsAt"):
            val = window.get(field)
            if val is not None and (type(val) is not int or val <= 0):
                raise SourceError("protocol_drift")
        # A dated, still-current window supports exhaustion; crossing a reset
        # without a fresh explicit eligibility read never proves recovery.
        exhausted_window |= window["usedPercent"] == 100 and window.get("resetsAt") is not None and window["resetsAt"] > now
    credits = core.get("credits")
    no_credits = False
    if credits is not None:
        _exact(credits, {"hasCredits", "unlimited"}, {"balance"})
        if type(credits["hasCredits"]) is not bool or type(credits["unlimited"]) is not bool:
            raise SourceError("protocol_drift")
        no_credits = credits["hasCredits"] is False and credits["unlimited"] is False
    # Presence of the account binding plus the backend permission is required.
    # Percentages, error messages, idle sessions, or clock passage never suffice.
    if not account_id or routing is None or routing["chatgptAccountId"] != account_id or ordinary is None:
        return {"state": "unavailable", "canRunAgents": None}
    if ordinary is True:
        # Conflicting denial evidence is uncertainty, not an optimistic recovery.
        if reached is None and core.get("spendControlReached") is not True:
            return {"state": "valid", "canRunAgents": True}
        return {"state": "unavailable", "canRunAgents": None}
    if reached == "rate_limit_reached" and exhausted_window and no_credits and core.get("spendControlReached") is not True:
        return {"state": "exhausted", "canRunAgents": False}
    return {"state": "unavailable", "canRunAgents": None}


class LocalAllowanceRPC:
    """Bounded RFC6455 client to an existing same-UID Unix control socket."""
    def __init__(self, config, *, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.deadline = 0.0
        self.sock = None
        self.total_bytes = 0

    def _remaining(self):
        seconds = self.deadline - self.clock()
        if seconds <= 0:
            raise SourceError("source_unavailable")
        return seconds

    def _read_exact(self, count):
        if count > MAX_MESSAGE:
            raise SourceError("protocol_drift")
        data = bytearray()
        while len(data) < count:
            self.sock.settimeout(self._remaining())
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise SourceError("source_unavailable")
            data.extend(chunk)
            self.total_bytes += len(chunk)
            if self.total_bytes > 4 * MAX_MESSAGE:
                raise SourceError("protocol_drift")
        return bytes(data)

    def _send_frame(self, data: bytes, opcode=1):
        if len(data) > MAX_MESSAGE:
            raise SourceError("protocol_drift")
        # RFC6455 nonce/mask is ephemeral protocol material, not an auth token.
        mask = os.urandom(4)
        length = len(data)
        header = bytes([0x80 | opcode, 0x80 | length]) if length < 126 else bytes([0x80 | opcode, 0xfe]) + struct.pack("!H", length)
        self.sock.settimeout(self._remaining())
        self.sock.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)))

    def _receive(self):
        message = bytearray()
        started = False
        for _ in range(32):
            first, second = self._read_exact(2)
            final, opcode, length = bool(first & 0x80), first & 15, second & 127
            if first & 0x70 or second & 0x80:
                raise SourceError("protocol_drift")
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if opcode in (8, 9, 10):
                if not final or length > 125:
                    raise SourceError("protocol_drift")
                data = self._read_exact(length)
                if opcode == 8:
                    raise SourceError("source_unavailable")
                if opcode == 9:
                    self._send_frame(data, opcode=10)
                continue
            if (not started and opcode != 1) or (started and opcode != 0) or len(message) + length > MAX_MESSAGE:
                raise SourceError("protocol_drift")
            started = True
            message.extend(self._read_exact(length))
            if final:
                value = json.loads(message.decode("utf-8"))
                if not isinstance(value, dict):
                    raise SourceError("protocol_drift")
                return value
        raise SourceError("protocol_drift")

    def _request(self, number, method, params):
        if method not in {"initialize", "account/read", "account/rateLimits/read"}:
            raise SourceError("protocol_drift")
        self._send_frame(json.dumps({"id": number, "method": method, "params": params}, separators=(",", ":")).encode())
        result = self._receive()
        # No thread subscription is requested. All known notifications are
        # suppressed; any unsolicited message/request terminates this read.
        if result.get("id") != number or "error" in result or "result" not in result:
            raise SourceError("metadata_unavailable")
        return result["result"]

    def _peer(self):
        pid, uid, _ = struct.unpack("3i", self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != self.config["expectedUid"] or uid != os.getuid():
            raise SourceError("identity_mismatch")
        root = Path("/proc") / str(pid)
        if os.readlink(root / "exe") != self.config["expectedExecutable"]:
            raise SourceError("identity_mismatch")
        data = (root / "stat").read_text()
        parts = data[data.rfind(")") + 2:].split()
        if len(parts) < 20 or int(parts[19]) <= 0:
            raise SourceError("identity_mismatch")
        return pid, uid, int(parts[19])

    def read(self):
        self.deadline = self.clock() + self.config["timeoutSeconds"]
        try:
            path = Path(self.config["socketPath"])
            metadata = path.stat()
            if not stat.S_ISSOCK(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != self.config["expectedUid"]:
                raise SourceError("identity_mismatch")
            digest, size = hashlib.sha256(), 0
            descriptor = os.open(self.config["expectedExecutable"], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as executable:
                metadata = os.fstat(executable.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 512 * 1024 * 1024:
                    raise SourceError("identity_mismatch")
                while chunk := executable.read(1024 * 1024):
                    self._remaining()
                    size += len(chunk)
                    if size > 512 * 1024 * 1024:
                        raise SourceError("identity_mismatch")
                    digest.update(chunk)
            if digest.hexdigest() != self.config["expectedExecutableSha256"]:
                raise SourceError("identity_mismatch")
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self._remaining())
            self.sock.connect(str(path))
            peer = self._peer()
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            self.sock.sendall(request.encode("ascii"))
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                if len(header) >= 8192:
                    raise SourceError("protocol_drift")
                header.extend(self._read_exact(1))
            lines = header.decode("ascii").split("\r\n")
            fields = {}
            for line in lines[1:]:
                if line:
                    name, value = line.split(":", 1)
                    if name.lower() in fields:
                        raise SourceError("protocol_drift")
                    fields[name.lower()] = value.strip()
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if (not lines[0].startswith("HTTP/1.1 101 ") or fields.get("upgrade", "").lower() != "websocket"
                    or fields.get("connection", "").lower() != "upgrade" or fields.get("sec-websocket-accept") != accept
                    or "sec-websocket-extensions" in fields):
                raise SourceError("protocol_drift")
            initialized = self._request(1, "initialize", {"clientInfo": {"name": "uif-monitor-metadata", "version": "1.0.0"},
                "capabilities": {"experimentalApi": False, "optOutNotificationMethods": list(NOTIFICATIONS)}})
            _exact(initialized, {"codexHome", "platformFamily", "platformOs", "userAgent"})
            if (initialized["codexHome"] != self.config["expectedCodexHome"] or initialized["platformOs"] != "linux"
                    or initialized["platformFamily"] != "unix" or not isinstance(initialized["userAgent"], str)
                    or not re.search(r"(?<![0-9.])0\.167\.0(?![0-9.])", initialized["userAgent"])):
                raise SourceError("identity_mismatch")
            self._send_frame(b'{"method":"initialized"}')
            account = self._request(2, "account/read", {"refreshToken": False})
            # Do not opt into reserve experiments or request reset-credit details.
            limits = self._request(3, "account/rateLimits/read", {"excludeResetCreditDetails": True})
            account_after = self._request(4, "account/read", {"refreshToken": False})
            if account_after != account or self._peer() != peer:
                raise SourceError("identity_mismatch")
            return account, limits
        except SourceError:
            raise
        except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError, struct.error):
            raise SourceError("source_unavailable") from None
        finally:
            if self.sock is not None:
                self.sock.close()
                self.sock = None


class FooAllowance:
    source, capability = "foo_allowance", "foo_allowance"

    def __init__(self, config, *, reader_factory=LocalAllowanceRPC, clock=time.time):
        self.config, self.reader_factory, self.clock = config, reader_factory, clock

    def collect(self, now):
        if self.config["socketPath"] is None:
            return Observation(self.source, now, "unconfigured", self.capability, error_code="not_configured")
        try:
            account, payload = self.reader_factory(self.config).read()
            completed = int(self.clock())
            proof = classify_allowance(account, payload, completed)
            # The source event and proof use actual completed-read time. A
            # successful probe with unavailable eligibility cannot compact Foo.
            proof["observed_at"] = completed
            return Observation(self.source, completed, "ok", self.capability, allowance=proof)
        except SourceError as exc:
            return Observation(self.source, int(self.clock()), "unavailable", self.capability, error_code=exc.code)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return Observation(self.source, int(self.clock()), "unavailable", self.capability, error_code="protocol_drift")
