"""Pure admission predicate over trusted account evidence, not a telemetry reader."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

LIMIT_MILLINEURONS = 7_500_000
MAX_EVIDENCE_AGE = timedelta(seconds=90)


@dataclass(frozen=True)
class FreeRoute:
    account_id: str
    model: str
    gateway_id: str | None  # None denotes an explicitly attested direct route.
    credential_epoch_sha256: str


@dataclass(frozen=True)
class AccountUsageEvidence:
    """Independently authenticated projection; never deserialize raw caller input.

    upper includes all account activity through covered_through, including
    non-gateway use. gap_upper is a separate conservative bound for activity
    after covered_through through the entire prospective request lifetime.
    Unknown or sampled estimates without an authoritative bound are not proof.
    """

    route: FreeRoute
    utc_day: str
    covered_through: datetime
    valid_until: datetime
    upper: int
    gap_upper: int
    free_standard_no_fallback: bool
    completeness_verified: bool


def _utc(value):
    return type(value) is datetime and value.tzinfo is timezone.utc


def _route(value):
    return type(value) is FreeRoute and (
        value.gateway_id is None or type(value.gateway_id) is str
        and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value.gateway_id) is not None
    ) and all(
        type(item) is str and re.fullmatch(pattern, item) is not None
        for item, pattern in (
            (value.account_id, r'[a-f0-9]{32}'),
            (value.model, r'@cf/[a-z0-9_.-]+/[a-z0-9_.-]+'),
            (value.credential_epoch_sha256, r'[a-f0-9]{64}'),
        )
    )


def allows_request(
    route: FreeRoute,
    evidence: AccountUsageEvidence | None,
    *,
    now: datetime,
    request_not_after: datetime,
    outstanding_local: int,
    request_maximum: int,
) -> bool:
    """Require one fresh UTC window and strict headroom, with no overlap credit.

    Caller must obtain evidence from an authenticated cache (never network
    from the transaction), supply outstanding reservations and newer settled
    local use from that account's locked ledger, and invoke this predicate in
    the same atomic reservation boundary. Default missing evidence denies.
    """
    if (
        not _route(route)
        or type(evidence) is not AccountUsageEvidence
        or not _route(evidence.route)
        or evidence.route != route
        or evidence.free_standard_no_fallback is not True
        or evidence.completeness_verified is not True
        or not all(map(_utc, (now, request_not_after, evidence.covered_through, evidence.valid_until)))
    ):
        return False
    if (
        type(evidence.utc_day) is not str
        or not (evidence.covered_through <= now < request_not_after <= evidence.valid_until)
        or now - evidence.covered_through > MAX_EVIDENCE_AGE
        or not (
            evidence.covered_through.date() == now.date() == request_not_after.date()
            and evidence.utc_day == now.date().isoformat()
        )
    ):
        return False
    values = (evidence.upper, evidence.gap_upper, outstanding_local, request_maximum)
    return (
        all(type(value) is int and 0 <= value < LIMIT_MILLINEURONS for value in values)
        and request_maximum > 0
        and sum(values) < LIMIT_MILLINEURONS
    )
