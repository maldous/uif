"""Project metadata from an already-installed, authenticated app-server port.

No connection, login, process, model call, or quota admission is implemented.
The port owns RPC correlation, session continuity, byte caps and its deadline.
"""

from dataclasses import asdict, dataclass, replace
import json
from typing import Protocol

from .codex_quota_observation import (
    AccountRateLimitObservation, _invalid_constant, _iso_epoch, _unique,
    parse_account_rate_limits,
)
from .quota_handoff import FRESH_SECONDS

MAX_REPLY_BYTES = 64_000
_RPC_METHODS = ("account/read", "account/rateLimits/read")


@dataclass(frozen=True)
class BoundMetadataReplies:
    """Return type of a trusted port, never constructed from upstream JSON.

    The stable account and session are transport provenance. The port must
    reject auth/epoch changes across the two reads and distinguish a new fetch
    from cached results. Neither ID is inferred from email or provider JSON.
    """
    account_id: str
    session_id: str
    observed_at: int
    account_result: bytes
    limits_result: bytes


class TrustedMetadataPort(Protocol):
    def read_bound(
        self, *, methods: tuple[str, str], refresh_token: bool,
        maximum_reply_bytes: int, timeout_seconds: float,
    ) -> BoundMetadataReplies | None: ...


@dataclass(frozen=True)
class CodexMetadataBinding:
    """The installed owner supplies these identifiers and a no-refresh port."""
    port: TrustedMetadataPort
    account_id: str
    session_id: str
    limit_id: str

    def read(self, clock):
        return observe_signed_in_metadata(
            self.port, account_id=self.account_id, session_id=self.session_id,
            limit_id=self.limit_id, clock=clock,
        )


def describe_codex_metadata(observation, *, now):
    """Expose fresh diagnostics, which never grant a route or a reservation."""
    trusted = (type(observation) is AccountRateLimitObservation
               and _iso_epoch(observation.observed_at_epoch_seconds)
               and 0 <= now - observation.observed_at_epoch_seconds <= FRESH_SECONDS
               and all(now < window.resets_at_epoch_seconds for window in observation.windows))
    return {
        "provider": "codex",
        "client": "app-server",
        "funding": "included-subscription-candidate",
        "dispatch_hold": "same-session-entitlement-and-fenced-sender-required",
        "metadata_state": "current" if trusted else "unavailable" if observation is None else "stale",
        "account_observation": asdict(observation) if trusted else None,
    }


def _parse(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REPLY_BYTES:
        return None
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                       parse_constant=_invalid_constant)
    return value if type(value) is dict else None


def observe_signed_in_metadata(
    port: TrustedMetadataPort | None, *, account_id: str, session_id: str,
    limit_id: str, clock, timeout_seconds: float = 2.0,
) -> AccountRateLimitObservation | None:
    """Collect a bounded, same-session diagnostic; absence stays unavailable."""
    if (port is None or not callable(clock)
            or any(type(v) is not str or not 0 < len(v) <= 192
                   for v in (account_id, session_id, limit_id))
            or type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 5):
        return None
    try:
        started = clock()
        if not _iso_epoch(started):
            return None
        bound = port.read_bound(
            methods=_RPC_METHODS, refresh_token=False,
            maximum_reply_bytes=MAX_REPLY_BYTES, timeout_seconds=timeout_seconds,
        )
        now = clock()
        if (type(bound) is not BoundMetadataReplies or not _iso_epoch(now)
                or not _iso_epoch(bound.observed_at)
                or not started <= bound.observed_at <= now
                or now - started > timeout_seconds
                or bound.account_id != account_id or bound.session_id != session_id):
            return None
        identity, limits = _parse(bound.account_result), _parse(bound.limits_result)
        if identity is None or limits is None or identity.get("requiresOpenaiAuth") is not True:
            return None
        native = identity.get("account")
        if type(native) is not dict or native.get("type") != "chatgpt":
            return None
        # Official wire examples do not promise accountId in limits_result.
        # Bind the parser's expected identity using the trusted port, not JSON.
        if "accountId" in limits and limits["accountId"] != bound.account_id:
            return None
        limits["accountId"] = bound.account_id
        observation = parse_account_rate_limits(
            json.dumps(limits, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            account=account_id, limit_id=limit_id, now_epoch_seconds=now,
        )
        return replace(observation, observed_at_epoch_seconds=bound.observed_at) if observation else None
    except (AttributeError, OSError, TimeoutError, ValueError, RecursionError):
        return None
