"""Explicit opt-in HTTPS delivery with exact destination allowlisting."""
from __future__ import annotations

import json
from http.client import HTTPException
import os
import re
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request
from typing import Any

from .events import validate_event, EventError
from .store import canonical


class TransportError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer token to a redirected destination.
        return None


def validate_destination(url: Any) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise TransportError("invalid_destination")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise TransportError("invalid_destination") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or port not in (None, 443)
            or parsed.path != "/v1/ingest" or any(ord(c) <= 32 or ord(c) >= 127 for c in url)):
        raise TransportError("invalid_destination")
    return url


class HTTPSReceiver:
    def __init__(self, config: dict[str, Any], *, environment=None, opener=None):
        required = {"enabled", "url", "allowedUrls", "tokenEnvs", "timeoutSeconds"}
        if not isinstance(config, dict) or set(config) != required or type(config["enabled"]) is not bool:
            raise TransportError("invalid_transport_config")
        self.config = config
        self.environment = os.environ if environment is None else environment
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        timeout = config["timeoutSeconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 10:
            raise TransportError("invalid_transport_config")
        if not isinstance(config["allowedUrls"], list) or len(config["allowedUrls"]) > 8:
            raise TransportError("invalid_transport_config")
        for url in config["allowedUrls"]:
            validate_destination(url)
        if config["url"] is not None:
            validate_destination(config["url"])
        if not isinstance(config["tokenEnvs"], dict) or len(config["tokenEnvs"]) > 16:
            raise TransportError("invalid_transport_config")
        if len(set(str(v) for v in config["tokenEnvs"].values())) != len(config["tokenEnvs"]):
            raise TransportError("invalid_transport_config")
        for source, name in config["tokenEnvs"].items():
            if source not in {"codex_registry", "codex_turns", "symphony_registry", "controller_service", "process_identity", "cloud_feed", "foo_allowance"} or not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", name):
                raise TransportError("invalid_transport_config")
        if config["enabled"] and (config["url"] is None or config["url"] not in config["allowedUrls"]):
            raise TransportError("invalid_destination")

    def send(self, event: dict[str, Any]) -> None:
        try:
            validate_event(event)
        except (EventError, TypeError, AttributeError):
            raise TransportError("invalid_event") from None
        if not self.config["enabled"]:
            raise TransportError("transport_unconfigured")
        name = self.config["tokenEnvs"].get(event["sourceId"])
        token = self.environment.get(name) if name else None
        if not isinstance(token, str) or not 32 <= len(token) <= 512 or not all(33 <= ord(c) <= 126 for c in token):
            raise TransportError("auth_unavailable")
        request = urllib.request.Request(self.config["url"], data=canonical(event).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with self.opener.open(request, timeout=self.config["timeoutSeconds"]) as response:
                if response.status not in (200, 202):
                    raise TransportError("delivery_failed")
                body = response.read(4097)
            if len(body) > 4096:
                raise TransportError("ack_mismatch")
            ack = json.loads(body)
            if (not isinstance(ack, dict) or set(ack) != {"accepted", "duplicate", "eventId", "sequence"}
                    or ack["accepted"] is not True or type(ack["duplicate"]) is not bool
                    or ack["eventId"] != event["eventId"] or type(ack["sequence"]) is not int or ack["sequence"] != event["sequence"]):
                raise TransportError("ack_mismatch")
        except TransportError:
            raise
        except (urllib.error.URLError, OSError, ValueError, TimeoutError, HTTPException):
            # Do not emit raw remote errors, response bodies, URLs or tokens.
            # A truncated/malformed HTTP response is an uncertain outcome: the
            # receiver may already have committed it. Preserve the same event
            # for durable retry rather than terminating the collection loop.
            raise TransportError("delivery_failed") from None


def flush(store, receiver: HTTPSReceiver, now: int | None = None, *, budget: int = 50, source_ids=None) -> dict[str, int]:
    result = {"sent": 0, "failed": 0}
    if not receiver.config["enabled"]:
        return result
    for _ in range(min(max(budget, 1), 100)):
        instant = lambda: int(time.time()) if now is None else now
        ready = store.pending(instant(), limit=1, source_ids=source_ids)
        if not ready:
            break
        row = ready[0]
        try:
            receiver.send(json.loads(row["event_json"]))
        except (TransportError, ValueError) as exc:
            code = str(exc) if str(exc) in {"transport_unconfigured", "auth_unavailable", "invalid_destination", "ack_mismatch"} else "delivery_failed"
            store.fail(row["id"], instant(), code)
            result["failed"] += 1
        else:
            store.acknowledge(row["id"], instant())
            result["sent"] += 1
    return result
