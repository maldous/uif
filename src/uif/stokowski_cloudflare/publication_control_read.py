"""Read an exact publication fence inside the existing control owner runtime.

The owner supplies its already-configured authenticated Access transport. This
adapter does not load credentials, register an identity, expose general HTTP,
attest a result or transfer a capability to a publisher.
"""
from dataclasses import asdict

from ..handoff_store import Hold, require
from ..quota_handoff import token
from ..shared_issue_coordinator import _fence
from .remote_authority import AccessTransport

PUBLICATION_FENCE_READ = '/v1/publication/fence/read'


class PublicationControlRead:
    def __init__(self, control_transport):
        require(type(control_transport) is AccessTransport, 'existing_control_transport_required')
        self._control = control_transport

    def __call__(self, *, fence, attempt_id):
        token(attempt_id)
        try:
            require(type(fence) is dict, 'publication_control_fence_shape')
            canonical = asdict(_fence(fence))
        except (AttributeError, KeyError, TypeError, ValueError):
            raise Hold('publication_control_fence_shape') from None
        require(canonical == fence, 'publication_control_fence_shape')
        reply = self._control.request(PUBLICATION_FENCE_READ,
                                      {'fence': canonical, 'attempt_id': attempt_id})
        require(type(reply) is dict and reply.keys() == {'current'} and type(reply['current']) is bool,
                'publication_control_reply_unknown')
        return {'current': reply['current']}
