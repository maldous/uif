"""Single-request Workers AI transport for an admitted Stokowski attempt."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
from http.client import HTTPException, HTTPSConnection
import json
import re
from types import MappingProxyType

from .account_admission import FreeRoute
from .budget import BudgetRefused, ProjectAccountRouter


MODEL = '@cf/openai/gpt-oss-20b'
GLM_MODEL = '@cf/zai-org/glm-4.7-flash'
MAX_OUTPUT_TOKENS = 2048
# Reserve the whole model input window plus the explicit output cap. Round
# Cloudflare's published rates up to the next millineuron per token.
REQUEST_MAXIMUM = 128_000 * 19 + MAX_OUTPUT_TOKENS * 28
MODEL_RESERVATIONS = MappingProxyType({
    MODEL: REQUEST_MAXIMUM,
    GLM_MODEL: 131_072 * 6 + MAX_OUTPUT_TOKENS * 37,
})
MAX_RESPONSE_BYTES = 256 * 1024


def _utc_now():
    return datetime.now(timezone.utc)


class WorkersAIRequestError(RuntimeError):
    """An unqualified response. The request must not be replayed."""


@dataclass(frozen=True)
class FreeAdmissionWindow:
    """Already authenticated budget evidence and an owner-enforceable deadline."""

    request_not_after: datetime
    usage_snapshots: dict


@dataclass(frozen=True)
class FencedFreeCall:
    """The installed receiver must bind the exact operation, route and deadline.

    A connection returned by the trusted port owns credential resolution and
    rejects a lost fence, changed route, expired deadline, retries or fallbacks
    at the physical send boundary. It must not send from the factory itself.
    """

    project: str
    attempt_id: str
    request_id: str
    request_sha256: str
    route: FreeRoute
    request_not_after: datetime


class WorkersAIClient:
    def __init__(self, router: ProjectAccountRouter, account_token, *, gateways=None,
                 model=MODEL, read_account_usage=None, open_fenced_connection=None):
        """The protected owner supplies tokens only for accounts covered by the ledger."""
        if model not in MODEL_RESERVATIONS:
            raise ValueError('Workers AI model has not been admitted')
        self.model = model
        self.request_maximum = MODEL_RESERVATIONS[model]
        self.router = router
        self.account_token = account_token
        configured = dict(gateways) if gateways is not None else {}
        account_ids = {budget.account_id for budget in router.accounts.values()}
        if not configured.keys() <= account_ids or any(
                not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name)
                for name in configured.values()):
            raise ValueError('Invalid account-bound AI Gateway configuration')
        self.gateways = MappingProxyType(configured)
        self._strict_routes = router.strict_routes
        self._read_account_usage = read_account_usage
        self._open_fenced_connection = open_fenced_connection
        if self._strict_routes is not None and any(
                route.model != self.model or route.gateway_id != self.gateways.get(route.account_id)
                for route in self._strict_routes.values()):
            raise ValueError('Actual Workers AI model or Gateway differs from the guarded route')

    @property
    def strict_ports_ready(self):
        return (self._strict_routes is None or
                (callable(self._read_account_usage) and callable(self._open_fenced_connection)))

    def reservation_options(self, timeout):
        if self._strict_routes is None:
            return {}
        if not self.strict_ports_ready:
            raise BudgetRefused('Workers AI account-wide evidence or fenced transport unavailable')
        try:
            window = self._read_account_usage(timeout)
        except (OSError, ValueError):
            raise BudgetRefused('Workers AI account-wide evidence unavailable') from None
        if (type(window) is not FreeAdmissionWindow or type(window.usage_snapshots) is not dict
                or type(window.request_not_after) is not datetime
                or window.request_not_after.tzinfo is not timezone.utc
                or not window.usage_snapshots):
            raise BudgetRefused('Workers AI account-wide evidence unavailable')
        return dict(usage_snapshots=dict(window.usage_snapshots),
                    request_not_after=window.request_not_after)

    def complete(self, *, project, attempt_id, request_id, active_projects, messages, tools,
                 timeout, review_of=None, reviewer_project=None):
        """Reserve before sending. Uncertain usage retains the full reservation."""
        if not 0 < timeout <= 600:
            raise ValueError('Invalid inference timeout')
        request = {'model': self.model, 'messages': messages, 'max_tokens': MAX_OUTPUT_TOKENS,
                   'stream': False}
        if tools:
            request['tools'] = tools
        elif self.model == GLM_MODEL:
            # Leave the output budget available for the structured report.
            request['chat_template_kwargs'] = {'enable_thinking': False}
            request['response_format'] = {'type': 'json_object'}
        canonical = json.dumps(request, sort_keys=True, separators=(',', ':'),
                               allow_nan=False).encode()
        if len(canonical) > 128_000:
            raise ValueError('Workers AI input exceeds the allowed size')
        digest = sha256(canonical).hexdigest()
        options = self.reservation_options(timeout)
        budget = self.router.reserve_request(
            project, attempt_id, request_id, self.request_maximum, active_projects,
            request_sha256=digest, review_of=review_of,
            reviewer_project=reviewer_project, **options)
        path = f'/client/v4/accounts/{budget.account_id}/ai/v1/chat/completions'
        gateway = self.gateways.get(budget.account_id)
        headers = {'Content-Type': 'application/json'}
        if self._strict_routes is None:
            try:
                token = self.account_token(budget.account_id)
            except (OSError, ValueError, KeyError):
                token = None
            if not isinstance(token, str) or not token or not token.isascii() or any(
                    char.isspace() for char in token):
                budget.settle(request_id, 0)
                raise WorkersAIRequestError('Verified Workers AI account credential unavailable')
            headers['Authorization'] = f'Bearer {token}'
            conn = HTTPSConnection('api.cloudflare.com', timeout=timeout)
        else:
            route = next((item for item in self._strict_routes.values()
                          if item.account_id == budget.account_id), None)
            deadline = options['request_not_after']
            if (route is None or route.gateway_id != gateway or route.model != self.model
                    or _utc_now() >= deadline):
                raise BudgetRefused('Guarded Workers AI route or deadline changed')
            conn = self._open_fenced_connection(FencedFreeCall(
                project, attempt_id, request_id, digest, route, deadline))
            if conn is None:
                raise BudgetRefused('Exclusive Workers AI effect fence unavailable')
        if gateway is not None:
            headers.update({'cf-aig-gateway-id': gateway, 'cf-aig-max-attempts': '1',
                            'cf-aig-skip-cache': 'true'})
        receipt = None
        try:
            if self._strict_routes is not None and _utc_now() >= deadline:
                raise BudgetRefused('Guarded Workers AI request deadline elapsed')
            conn.request('POST', path, body=canonical, headers=headers)
            response = conn.getresponse()
            if response.status == 200 and gateway is not None:
                receipt = response.getheader('cf-aig-log-id')
                if not isinstance(receipt, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', receipt):
                    # The call may have incurred usage without attributable evidence.
                    budget.hold(request_id, 'provider-unavailable')
                    raise WorkersAIRequestError('AI Gateway request receipt missing or invalid')
            if response.status == 200:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, HTTPException):
            budget.hold(request_id, 'provider-unavailable')
            raise WorkersAIRequestError('Workers AI response is uncertain') from None
        finally:
            conn.close()
        if response.status != 200:
            if response.status == 429:
                budget.hold(request_id, 'rate-limit')
            elif response.status in {401, 403}:
                budget.hold(request_id, 'authentication')
            elif 500 <= response.status <= 599:
                budget.hold(request_id, 'provider-unavailable')
            raise WorkersAIRequestError(f'Workers AI refused request (HTTP {response.status})')
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WorkersAIRequestError('Workers AI response exceeded the size limit')
        try:
            result = json.loads(raw, parse_float=Decimal)
            if result['model'] != self.model or not result['choices']:
                raise ValueError
            usage = result['usage']['neurons']
            if type(usage) not in {int, Decimal}:
                raise ValueError
            usage = Decimal(usage)
            if not usage.is_finite() or usage < 0:
                raise ValueError
            actual = int((usage * 1000).to_integral_value(rounding=ROUND_CEILING))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise WorkersAIRequestError('Workers AI response lacks verified usage') from None
        budget.settle(request_id, actual, gateway_receipt=receipt)
        if actual > self.request_maximum:
            raise WorkersAIRequestError('Workers AI usage exceeded its reservation')
        return result
