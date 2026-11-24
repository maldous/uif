"""Pure provider admission predicates; no client, credentials, or ledger.

Only trusted admission code may construct evidence. Evaluate inside the
existing atomic reservation boundary, and fence the corresponding sender.
A refusal is not authorization to fall back to another account or route.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re


FRESH = timedelta(seconds=90)


def _identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None


def _digest(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _natural(value):
    return type(value) is int and value >= 0


def _lifetime(now, observed, valid_until, deadline, reset):
    return (all(type(x) is datetime and x.tzinfo is timezone.utc
                for x in (now, observed, valid_until, deadline, reset))
            and now - FRESH <= observed <= now < deadline <= valid_until
            and deadline < reset)


@dataclass(frozen=True)
class CodexRoute:
    account: str
    credential_epoch: str
    model: str
    required_windows: tuple[str, ...]


@dataclass(frozen=True)
class CodexWindow:
    name: str
    remaining_lower: int
    limit: int
    unsettled_upper: int
    next_upper: int
    reset_at: datetime


@dataclass(frozen=True)
class CodexEvidence:
    route: CodexRoute
    observed_at: datetime
    valid_until: datetime
    subscription_only: bool
    complete_binding: bool
    windows: tuple[CodexWindow, ...]


def allows_codex(route, evidence, *, operation, now, deadline):
    """Apply the most restrictive window after reserving all bounded use.

    A continuation/retry must be tied to its existing verified claim by the
    caller. The caller must not call an initial dispatch a continuation.
    """
    if (type(route) is not CodexRoute or type(evidence) is not CodexEvidence
            or type(evidence.route) is not CodexRoute or evidence.route != route
            or not _identifier(route.account) or not _identifier(route.model)
            or not _digest(route.credential_epoch)
            or type(operation) is not str or operation not in ("new", "continue", "retry")
            or evidence.subscription_only is not True or evidence.complete_binding is not True
            or type(route.required_windows) is not tuple or not route.required_windows
            or not all(map(_identifier, route.required_windows))
            or len(set(route.required_windows)) != len(route.required_windows)
            or type(evidence.windows) is not tuple
            or not all(type(w) is CodexWindow and _identifier(w.name) for w in evidence.windows)
            or len(evidence.windows) != len(route.required_windows)
            or {w.name for w in evidence.windows} != set(route.required_windows)):
        return False
    stop = 15 if operation == "new" else 10
    return all(
        _lifetime(now, evidence.observed_at, evidence.valid_until, deadline, w.reset_at)
        and all(map(_natural, (w.remaining_lower, w.limit, w.unsettled_upper, w.next_upper)))
        and 0 < w.next_upper <= w.limit and w.remaining_lower <= w.limit
        and 100 * (w.remaining_lower - w.unsettled_upper - w.next_upper) > stop * w.limit
        for w in evidence.windows
    )


@dataclass(frozen=True)
class ApiRoute:
    organization: str
    project: str
    credential_epoch: str
    model: str
    operation: str


@dataclass(frozen=True)
class ApiCreditEvidence:
    route: ApiRoute
    observed_at: datetime
    valid_until: datetime
    credit_expires_at: datetime
    included_grant_total_microusd: int
    included_balance_lower_microusd: int
    included_only: bool
    hard_no_overage: bool
    complete_binding: bool


def allows_openai_api(route, evidence, *, intent=None, now, deadline, unsettled_upper_microusd,
                     request_upper_microusd):
    """Require approved project-scoped included credit and hard no-overage.

    Bounds include concurrent unsettled use and all possible charges for the
    request. Continuations and retries require an existing verified claim at
    the caller. An ordinary budget alert, paid/prepaid balance, or subscription
    entitlement is not authoritative included credit.
    """
    if type(intent) is not str or intent not in ("new", "continue", "retry"):
        return False
    stop = 15 if intent == "new" else 10
    return bool(
        type(route) is ApiRoute and type(evidence) is ApiCreditEvidence
        and type(evidence.route) is ApiRoute and evidence.route == route
        and all(map(_identifier, (route.organization, route.project, route.model, route.operation)))
        and _digest(route.credential_epoch)
        and evidence.included_only is True and evidence.hard_no_overage is True
        and evidence.complete_binding is True
        and _lifetime(now, evidence.observed_at, evidence.valid_until, deadline,
                      evidence.credit_expires_at)
        and all(map(_natural, (evidence.included_grant_total_microusd,
                              evidence.included_balance_lower_microusd,
                              unsettled_upper_microusd, request_upper_microusd)))
        and request_upper_microusd > 0
        and 0 < evidence.included_grant_total_microusd
        and evidence.included_balance_lower_microusd <= evidence.included_grant_total_microusd
        and 100 * (evidence.included_balance_lower_microusd
                   - unsettled_upper_microusd - request_upper_microusd)
        > stop * evidence.included_grant_total_microusd
    )
