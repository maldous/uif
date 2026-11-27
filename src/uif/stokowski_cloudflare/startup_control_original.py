"""Typed request and readback of an existing control startup-bind original.

Only the existing release owner can supply the authenticated control port
and actual immutable original. No credential handling, grant, start or write
retry is implemented. An acknowledgement is not native readiness.
"""
from dataclasses import asdict, dataclass
import re

from ..handoff_store import Hold, digest, require
from ..quota_handoff import token
from .remote_authority import AuthorityUnavailable

STARTUP_BIND = '/v1/startup/bind'
TRANSITION_ORIGINAL = '/v1/transition/original'
UNIT = 'stokowski-foo.service'


def _sha(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None


@dataclass(frozen=True)
class StartupBindOriginal:
    request_id: str
    unit: str
    stage: str
    session_id: str
    coordinator_epoch: int
    valid_until: int
    evidence_sha256: str

    def __post_init__(self):
        token(self.request_id)
        token(self.stage)
        token(self.session_id)
        require(self.unit == UNIT and type(self.coordinator_epoch) is int
                and 0 < self.coordinator_epoch <= 9_007_199_254_740_991
                and type(self.valid_until) is int and 0 < self.valid_until <= 8_640_000_000_000
                and _sha(self.evidence_sha256), 'startup_original_binding')

    def body(self):
        """Exact wire body; retain this immutable original before submission."""
        return asdict(self)

    def fingerprint(self):
        """Same canonical path/body hash as the existing authority's receipt."""
        return digest({'path': STARTUP_BIND, 'body': self.body()})


def observe_original(original, *, recorded_fingerprint, installation_sha256,
                     control_session_id, read):
    """Read one retained acknowledgement through an existing trusted control port.

    The receipt lookup is keyed by the original *control* actor and installation,
    not by the native session named in the request. The server returns only
    request/unit/stage. The caller must preserve the protected exact input
    separately and use native startup proof to verify the current binding.
    """
    _validate_original(original, recorded_fingerprint, installation_sha256,
                       control_session_id, read)
    # This POST is the read-only original observation; no bind on unknown,
    # no allow-absent fallback, no automatic retries or alternative endpoint.
    reply = read(TRANSITION_ORIGINAL, {'installation_sha256': installation_sha256,
                                       'session_id': control_session_id,
                                       'request_id': original.request_id})
    return _acknowledged(original, reply)


def _validate_original(original, recorded_fingerprint, installation_sha256,
                       control_session_id, port):
    require(type(original) is StartupBindOriginal
            and _sha(recorded_fingerprint) and original.fingerprint() == recorded_fingerprint
            and _sha(installation_sha256) and callable(port), 'retained_startup_original_required')
    token(control_session_id)
    require(control_session_id != original.session_id, 'startup_control_must_not_be_native')


def _acknowledged(original, reply):
    expected = {k: getattr(original, k) for k in ('request_id', 'unit', 'stage')}
    require(type(reply) is dict and reply == expected, 'startup_original_acknowledgement_unverified')
    return dict(expected, status='original_acknowledged', native_startup_verified=False)


def submit_original_attempt(original, *, recorded_fingerprint, installation_sha256,
                         control_session_id, request):
    """Send one existing authorized control original, or read it on transport loss.

    The caller must have already durably retained the exact original and must
    enter through the existing authenticated installation handoff. This
    helper does not decide whether the original has already been sent. On a
    later invocation with uncertain history, use observe_original directly.
    """
    _validate_original(original, recorded_fingerprint, installation_sha256,
                       control_session_id, request)
    try:
        reply = request(STARTUP_BIND, original.body())
    except AuthorityUnavailable:
        try:
            return observe_original(original, recorded_fingerprint=recorded_fingerprint,
                                    installation_sha256=installation_sha256,
                                    control_session_id=control_session_id, read=request)
        except (Hold, OSError):
            raise AuthorityUnavailable('authority_startup_original_outcome_unknown') from None
    return _acknowledged(original, reply)
