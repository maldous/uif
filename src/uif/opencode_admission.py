"""Offline admission predicates for exact OpenCode no-spend routes.

This module neither launches OpenCode nor performs a request. Its caller must
supply a trusted, freshly observed route record and guard the final transport.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    binary_sha256: str
    account: str
    upstream: str | None = None


@dataclass(frozen=True)
class FreeEvidence:
    route: Route
    observed_at: datetime
    expires_at: datetime
    free_only: bool
    no_paid_fallback: bool


def _provider_known(route):
    if type(route) is not Route or type(route.model) is not str:
        return False
    if route.model.lower().rsplit("/", 1)[-1].split(":", 1)[0] in {"auto", "default", "latest"}:
        return False
    if route.provider == "openrouter":
        # The qualified endpoint slug may include variant, region and tier.
        # The evidence issuer must prove it resolves uniquely.
        return (type(route.upstream) is str
                and len(route.upstream) <= 64
                and bool(re.fullmatch(r"[a-z0-9_.-]+(?:/[a-z0-9_.-]+){0,3}", route.upstream))
                and bool(re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+:free", route.model)))
    return (route.provider == "groq" and route.upstream is None
            and bool(re.fullmatch(r"[a-z0-9_.-]+(?:/[a-z0-9_.-]+)?", route.model)))


def admissible(route: Route, evidence: FreeEvidence, *, now: datetime,
               observed_binary_sha256: str) -> bool:
    """Check freshness and identity; caller owns provenance of all observations."""
    if not _provider_known(route) or type(evidence) is not FreeEvidence:
        return False
    times = (now, evidence.observed_at, evidence.expires_at)
    if not all(type(value) is datetime and value.utcoffset() == timedelta(0)
               for value in times):
        return False
    if not (evidence.route == route and evidence.free_only is True
            and evidence.no_paid_fallback is True
            and evidence.observed_at <= now < evidence.expires_at
            and now - evidence.observed_at <= timedelta(seconds=90)
            and all(isinstance(value, str) for value in (
                route.provider, route.model, route.binary_sha256,
                route.account, observed_binary_sha256,
            ))
            and bool(route.account.strip())
            and re.fullmatch(r"[a-f0-9]{64}", route.binary_sha256)
            and route.binary_sha256 == observed_binary_sha256):
        return False
    return True


def request_matches(route: Route, *, method: str, url: str, body: dict) -> bool:
    """Refuse service/route/model substitutions at the final chat boundary.

    No server-side tools, plugins or extra provider parameters are admitted.
    This is not proof of quota, prices, client retries, or user data permission.
    """
    if not _provider_known(route) or method != "POST" or type(body) is not dict or body.get("model") != route.model:
        return False
    common = {"model", "messages", "stream", "max_tokens", "max_completion_tokens"}
    if route.provider == "openrouter":
        if url != "https://openrouter.ai/api/v1/chat/completions":
            return False
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+:free", route.model):
            return False
        flags = body.get("provider")
        if (type(flags) is not dict
                or flags.keys() != {"allow_fallbacks", "max_price", "only", "require_parameters"}
                or flags["allow_fallbacks"] is not False
                or flags["require_parameters"] is not True
                or flags["only"] != [route.upstream]):
            return False
        ceiling = flags["max_price"]
        return (body.keys() <= common | {"provider"} and type(ceiling) is dict
                and ceiling.keys() == {"prompt", "completion", "request"}
                and all(type(value) is int and value == 0 for value in ceiling.values()))
    return (route.provider == "groq"
            and bool(re.fullmatch(r"[a-z0-9_.-]+(?:/[a-z0-9_.-]+)?", route.model))
            and url == "https://api.groq.com/openai/v1/chat/completions"
            and body.keys() <= common)


class Refused(ValueError):
    """The request must not reach the provider."""


class SingleChatAttempt:
    """Single-turn relay gate; does not perform I/O or identify client roles.

    Instantiate once per admitted turn, not once per incoming request. A trusted
    launcher must distinguish the main caller from helpers before calling this
    gate. Even after a success, failure or timeout, do not replenish the slot.
    """
    def __init__(self, route: Route, *, max_output_tokens: int):
        from threading import Lock
        if not _provider_known(route):
            raise ValueError("exact route and upstream required")
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("positive output ceiling required")
        self._route = route
        self._ceiling = max_output_tokens
        self._lock = Lock()
        self._attempts = 0
        self._blocked = False

    @property
    def attempts(self):
        with self._lock:
            return self._attempts

    def stop(self):
        """Close the turn on a terminal transport outcome, including 402/429."""
        with self._lock:
            self._blocked = True

    def admit(self, *, purpose: str, method: str, url: str, body: dict):
        with self._lock:
            if self._blocked or self._attempts:
                raise Refused("turn request limit reached")
            # No arbitrary extra knobs, tools, or client-directed billing flags.
            keys = {"model", "messages", "stream", "stream_options", "max_completion_tokens"}
            if (purpose != "main" or type(body) is not dict or not body.keys() <= keys
                    or body.get("model") != self._route.model):
                raise Refused("unexpected chat shape")
            messages = body.get("messages")
            cap = body.get("max_completion_tokens", self._ceiling)
            streaming = body.get("stream", False)
            if (type(messages) is not list or not messages
                    or type(streaming) is not bool
                    or type(cap) is not int or not 0 < cap <= self._ceiling):
                raise Refused("invalid chat bounds")
            if "stream_options" in body:
                options = body["stream_options"]
                if (not streaming or type(options) is not dict
                        or options.keys() != {"include_usage"}
                        or options["include_usage"] is not True):
                    raise Refused("unsupported stream options")
            normalized = []
            for message in messages:
                if (type(message) is not dict or message.keys() != {"role", "content"}
                        or message["role"] not in ("system", "user", "assistant")
                        or type(message["content"]) is not str):
                    raise Refused("only text messages allowed")
                normalized.append(dict(message))
            wire = {"model": self._route.model, "messages": normalized,
                    "stream": streaming, "max_completion_tokens": cap}
            if self._route.provider == "openrouter":
                wire["provider"] = {"allow_fallbacks": False,
                                    "only": [self._route.upstream],
                                    "require_parameters": True,
                                    "max_price": {"prompt": 0, "completion": 0, "request": 0}}
            if not request_matches(self._route, method=method, url=url, body=wire):
                raise Refused("wrong provider destination")
            self._attempts += 1
            return wire
