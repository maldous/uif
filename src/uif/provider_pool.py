"""Source-only advisor for owner-admitted, separately qualified provider routes.

Selection never acquires proof, reserves capacity, makes a transport call, or
grants authority. Supply observations produced by the real admitted adapters;
a structurally valid observation is not authentication. The existing Pool and
the fenced effect receiver must independently verify and consume their bindings.
"""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import re

from .handoff_store import digest
from .quota_handoff import FRESH_SECONDS, SHA, TOKEN, LinearKey

# Deliberately no OpenAI metered API, other OpenCode backends, or experimental
# routes. Adding a route type needs independent review; adding a descriptor or
# a credential reference alone cannot enable a provider.
_CLIENT = {
    "codex": "codex",
    "cloudflare": "workers-ai",
    "groq": "opencode",
    "openrouter": "opencode",
    "openai-responses": "responses",
}
_ROLES = frozenset({"author", "review"})
_INTENTS = frozenset({"new", "continuation", "retry"})
_HELD = frozenset({"throttled", "hard-quota", "soft-quota", "auth-failure", "transient", "unknown"})
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_MODEL = re.compile(r"[A-Za-z0-9@][A-Za-z0-9@./_:-]{0,191}")
_OPENROUTER_FREE_MODEL = re.compile(r"[a-z0-9_.-]+/[a-z0-9_.-]+:free")


def _match(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _time(value):
    # NaN, infinities, booleans and out-of-range timestamps do not qualify.
    return type(value) in (int, float) and 0 <= value < 2**53


@dataclass(frozen=True)
class ProviderRoute:
    route_id: str
    provider: str
    client: str
    model: str
    client_build_sha256: str
    account_sha256: str
    credential_epoch_sha256: str
    adapter_route_sha256: str
    workspace_id: str
    project_id: str
    repository: str
    roles: frozenset[str]


@dataclass(frozen=True)
class IncludedResponsesRequest:
    opt_in: bool
    payload_sha256: str
    deadline: float
    upper_microusd: int


@dataclass(frozen=True)
class IncludedResponsesGrant:
    # The separately qualified adapter's exact dataclass types are checked
    # at use, not inferred from the shape of an object supplied by a caller.
    route: object
    evidence: object
    receipt_sha256: str
    request_bound_sha256: str
    unsettled_upper_microusd: int


@dataclass(frozen=True)
class PoolRequest:
    key: LinearKey
    repository: str
    source_sha256: str
    scope_sha256: str
    operation_id: str
    purpose: str
    role: str
    intent: str
    route_ids: tuple[str, ...]
    original_route: ProviderRoute | None = None
    author_account_sha256: str | None = None
    responses: IncludedResponsesRequest | None = None


@dataclass(frozen=True)
class RouteAvailability:
    route: ProviderRoute
    request: PoolRequest
    classification: str
    observed_at: float
    expires_at: float
    receipt_sha256: str
    included_only: bool
    fallback_disabled: bool
    can_reserve: bool
    responses_grant: IncludedResponsesGrant | None = None


@dataclass(frozen=True)
class PoolSelection:
    route: ProviderRoute | None
    observation: RouteAvailability | None
    hold: str | None
    rejected: tuple[tuple[str, str], ...] = ()


def responses_request_binding(request: PoolRequest, route: ProviderRoute) -> str:
    """Canonical quote binding for an already admitted Responses request/route."""
    # For continuation/retry the caller also verifies original_route == route.
    return digest({
        "schema": "uif-included-responses-request/1",
        "request": asdict(replace(request, original_route=None)),
        "route": {**asdict(route), "roles": sorted(route.roles)},
    })


def _valid_request(request):
    if (type(request) is not PoolRequest or type(request.key) is not LinearKey
            or not _match(_REPO, request.repository)
            or not _match(SHA, request.source_sha256) or not _match(SHA, request.scope_sha256)
            or not _match(TOKEN, request.operation_id)
            or type(request.purpose) is not str or request.purpose != "ordinary-software"
            or type(request.role) is not str or request.role not in _ROLES
            or type(request.intent) is not str or request.intent not in _INTENTS
            or type(request.route_ids) is not tuple or not request.route_ids
            or not all(_match(TOKEN, route_id) for route_id in request.route_ids)
            or len(request.route_ids) != len(set(request.route_ids))):
        return False
    if request.role == "review":
        if not _match(SHA, request.author_account_sha256):
            return False
    elif request.author_account_sha256 is not None:
        return False
    if request.intent == "new":
        return request.original_route is None
    return (type(request.original_route) is ProviderRoute
            and _match(TOKEN, request.original_route.route_id)
            and request.route_ids == (request.original_route.route_id,))


def _route_hold(route, request):
    if (type(route.provider) is not str or route.provider not in _CLIENT
            or type(route.client) is not str or route.client != _CLIENT[route.provider]):
        return "provider-not-allowlisted"
    if (not _match(_MODEL, route.model)
            or route.model.lower().rsplit("/", 1)[-1].split(":", 1)[0] in {"auto", "default", "latest"}
            or not all(_match(SHA, value) for value in (
                route.client_build_sha256, route.account_sha256, route.credential_epoch_sha256,
                route.adapter_route_sha256))
            or type(route.workspace_id) is not str or type(route.project_id) is not str
            or not _match(_REPO, route.repository)
            or type(route.roles) is not frozenset
            or not route.roles or not route.roles <= _ROLES):
        return "route-invalid"
    if route.provider == "openrouter" and not _match(_OPENROUTER_FREE_MODEL, route.model):
        return "openrouter-free-model-required"
    if route.provider == "openai-responses":
        call = request.responses
        if (type(call) is not IncludedResponsesRequest or call.opt_in is not True
                or not _match(SHA, call.payload_sha256) or not _time(call.deadline)
                or type(call.upper_microusd) is not int
                or not 0 < call.upper_microusd < 2**63):
            return "responses-explicit-included-request-required"
    if (route.workspace_id != request.key.workspace_id
            or route.project_id != request.key.project_id
            or route.repository != request.repository
            or request.role not in route.roles):
        return "route-out-of-scope"
    if request.original_route is not None and route != request.original_route:
        return "original-route-drift"
    if request.role == "review" and route.account_sha256 == request.author_account_sha256:
        return "independent-review-required"
    return None


def _responses_grant_hold(observation, route, request, now):
    call, grant = request.responses, observation.responses_grant
    if type(grant) is not IncludedResponsesGrant:
        return "responses-typed-grant-required"
    if (not _match(SHA, grant.receipt_sha256) or grant.receipt_sha256 != observation.receipt_sha256
            or not _match(SHA, grant.request_bound_sha256)
            or grant.request_bound_sha256 != responses_request_binding(request, route)):
        return "responses-grant-binding-mismatch"

    # This independent provider policy may be absent in an uncomposed build.
    # Absence does not disable the other admitted families or enable metered use.
    try:
        from .openai_quota_admission import ApiCreditEvidence, ApiRoute, allows_openai_api
    except ImportError:
        return "responses-adapter-unavailable"
    adapter_route = grant.route
    if type(adapter_route) is not ApiRoute or type(grant.evidence) is not ApiCreditEvidence:
        return "responses-typed-grant-required"
    if (type(adapter_route.operation) is not str or adapter_route.operation != "responses.create"
            or type(adapter_route.model) is not str or type(adapter_route.credential_epoch) is not str
            or adapter_route.model != route.model
            or adapter_route.credential_epoch != route.credential_epoch_sha256):
        return "responses-grant-binding-mismatch"
    if call.deadline > observation.expires_at:
        return "responses-included-grant-unproved"

    try:
        admitted = allows_openai_api(
            adapter_route, grant.evidence,
            intent="continue" if request.intent == "continuation" else request.intent,
            now=datetime.fromtimestamp(now, timezone.utc),
            deadline=datetime.fromtimestamp(call.deadline, timezone.utc),
            unsettled_upper_microusd=grant.unsettled_upper_microusd,
            request_upper_microusd=call.upper_microusd,
        )
    except (OverflowError, OSError, ValueError):
        return "responses-included-grant-unproved"
    if not admitted:
        return "responses-included-grant-unproved"
    if (route.adapter_route_sha256 != digest(asdict(adapter_route))
            # Project scope remains in the route fingerprint. Projects at the
            # same organization are not different accounts for reviewer checks.
            or route.account_sha256 != digest({"organization": adapter_route.organization})
            or grant.evidence.observed_at.timestamp() != observation.observed_at
            or grant.evidence.valid_until.timestamp() < observation.expires_at):
        return "responses-grant-binding-mismatch"
    return None


def _availability_hold(observation, route, request, now):
    if observation.route != route or observation.request != request:
        return "evidence-binding-mismatch"
    if (not _time(observation.observed_at) or not _time(observation.expires_at)
            or not _match(SHA, observation.receipt_sha256)
            or type(observation.classification) is not str):
        return "evidence-invalid"
    if (not observation.observed_at <= now < observation.expires_at
            or now - observation.observed_at > FRESH_SECONDS):
        return "evidence-stale"
    if observation.classification != "available":
        return (observation.classification
                if observation.classification in _HELD else "evidence-invalid")
    if observation.included_only is not True or observation.fallback_disabled is not True:
        return "included-only-routing-unproved"
    if observation.can_reserve is not True:
        return "capacity-unproved"
    if route.provider == "openai-responses":
        return _responses_grant_hold(observation, route, request, now)
    return None


def select_route(request, *, routes, observations, now, owner_paused, pending_original,
                 route_order=None):
    """Choose only from this request's routes.

    Every configured route is one project/role binding, and each observation is
    exact to the contemplated operation. An optional evaluation order must be
    a permutation of the admitted route IDs. It does not change the request.
    The caller must separately enforce custody, atomic reservation and the
    original-operation fence before dispatch.
    """
    if owner_paused is not False:
        return PoolSelection(None, None, "owner-paused-or-unknown")
    if pending_original is not False:
        return PoolSelection(None, None, "original-operation-reconciliation-required")
    if type(routes) is not tuple or type(observations) is not tuple or not _time(now):
        return PoolSelection(None, None, "pool-input-unavailable")
    if not _valid_request(request):
        return PoolSelection(None, None, "request-not-admitted")
    if route_order is None:
        route_order = request.route_ids
    elif (type(route_order) is not tuple or len(route_order) != len(request.route_ids)
            or any(type(route_id) is not str for route_id in route_order)
            or set(route_order) != set(request.route_ids)):
        return PoolSelection(None, None, "route-order-invalid")

    rejected = []
    for route_id in route_order:
        matching = [route for route in routes
                    if type(route) is ProviderRoute and type(route.route_id) is str
                    and route.route_id == route_id]
        if len(matching) != 1:
            rejected.append((route_id, "route-unregistered" if not matching else "route-ambiguous"))
            continue
        route = matching[0]
        hold = _route_hold(route, request)
        if hold is not None:
            rejected.append((route_id, hold))
            continue
        rows = [row for row in observations if type(row) is RouteAvailability
                and type(row.route) is ProviderRoute and type(row.route.route_id) is str
                and row.route.route_id == route_id]
        applicable = [row for row in rows if type(row.request) is PoolRequest and row.request == request]
        if len(applicable) != 1:
            reason = ("evidence-ambiguous" if applicable else
                      "evidence-binding-mismatch" if rows else "evidence-missing")
            rejected.append((route_id, reason))
            continue
        observation = applicable[0]
        hold = _availability_hold(observation, route, request, now)
        if hold is None:
            return PoolSelection(route, observation, None, tuple(rejected))
        rejected.append((route_id, hold))
    return PoolSelection(None, None, "no-admitted-route", tuple(rejected))
