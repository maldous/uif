"""Single-request Workers AI transport for an admitted Stokowski attempt."""

from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
from http.client import HTTPException, HTTPSConnection
import json
import re
from types import MappingProxyType

from .budget import ProjectAccountRouter


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


class WorkersAIRequestError(RuntimeError):
    """An unqualified response. The request must not be replayed."""


class WorkersAIClient:
    def __init__(self, router: ProjectAccountRouter, account_token, *, gateways=None,
                 model=MODEL):
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
        budget = self.router.reserve_request(
            project, attempt_id, request_id, self.request_maximum, active_projects,
            request_sha256=sha256(canonical).hexdigest(), review_of=review_of,
            reviewer_project=reviewer_project)
        try:
            token = self.account_token(budget.account_id)
        except (OSError, ValueError, KeyError):
            token = None
        if not isinstance(token, str) or not token or not token.isascii() or any(
                char.isspace() for char in token):
            budget.settle(request_id, 0)
            raise WorkersAIRequestError('Verified Workers AI account credential unavailable')

        path = f'/client/v4/accounts/{budget.account_id}/ai/v1/chat/completions'
        gateway = self.gateways.get(budget.account_id)
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        if gateway is not None:
            headers.update({'cf-aig-gateway-id': gateway, 'cf-aig-max-attempts': '1',
                            'cf-aig-skip-cache': 'true'})
        receipt = None
        conn = HTTPSConnection('api.cloudflare.com', timeout=timeout)
        try:
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
