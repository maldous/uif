"""Diagnostic projection of Codex app-server account/rateLimits/read v2.

This parser consumes the method's result JSON, not the JSON-RPC envelope.
It does not launch Codex, authenticate a result, prove coverage/pricing, or
provide reservation authority. Account/session and receipt provenance must
be bound independently by the eventual installed adapter.
"""

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class QuotaWindow:
    name: str
    used_percent: int
    duration_minutes: int
    resets_at_epoch_seconds: int


@dataclass(frozen=True)
class AccountRateLimitObservation:
    windows: tuple[QuotaWindow, ...]
    ordinary_usage_allowed: bool | None
    spend_control_reached: bool | None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("non-JSON constant")


def _iso_epoch(value):
    return type(value) is int and 0 < value <= 253402300799


def parse_account_rate_limits(raw, *, account, limit_id, now_epoch_seconds):
    """Read exactly the configured metered bucket and its two current windows.

    Never substitute the compatibility mirror for a missing identified bucket.
    This deliberately holds incomplete/unknown-version evidence, and the
    observed percentage is not a conservative admission headroom estimate.
    """
    if (type(raw) is not bytes or not 0 < len(raw) <= 64_000
            or type(account) is not str or not account
            or type(limit_id) is not str or not limit_id
            or not _iso_epoch(now_epoch_seconds)):
        return None
    try:
        reply = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        return None
    if type(reply) is not dict or reply.get("accountId") != account:
        return None
    buckets = reply.get("rateLimitsByLimitId")
    if type(buckets) is not dict:
        return None
    bucket = buckets.get(limit_id)
    if type(bucket) is not dict or bucket.get("limitId") != limit_id:
        return None
    allowed = reply.get("ordinaryUsageAllowed")
    reached = bucket.get("spendControlReached")
    if any(value is not None and type(value) is not bool for value in (allowed, reached)):
        return None
    windows = []
    for name in ("primary", "secondary"):
        entry = bucket.get(name)
        if type(entry) is not dict:
            return None
        used, duration, reset = (entry.get(k) for k in
                                 ("usedPercent", "windowDurationMins", "resetsAt"))
        if (type(used) is not int or not 0 <= used <= 100
                or type(duration) is not int or not 0 < duration < 2**31
                or not _iso_epoch(reset) or reset <= now_epoch_seconds):
            return None
        windows.append(QuotaWindow(name, used, duration, reset))
    return AccountRateLimitObservation(tuple(windows), allowed, reached)
