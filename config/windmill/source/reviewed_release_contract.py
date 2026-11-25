"""Inert source contract for the first Foo reviewed-release profile.

No caller document, environment field, or legacy root receipt supplies
authenticated current-job identity. Do not add this draft to deployed schemas.
"""

import re

ACTION = "install-reviewed-release"
PROFILE = "offmenu-native-upgrade-v1"
FIELDS = frozenset({
    "action", "profile", "operation_id", "release_sha256", "package_sha256",
    "protected_request_sha256",
})
SHA256 = re.compile(r"[0-9a-f]{64}")


def qualify_request(request):
    """Check wire syntax; successful syntax validation is not admission."""
    if type(request) is not dict or request.keys() != FIELDS:
        raise ValueError("INVALID_REVIEWED_RELEASE_FIELDS")
    if request["action"] != ACTION or request["profile"] != PROFILE:
        raise ValueError("UNKNOWN_REVIEWED_RELEASE_PROFILE")
    for key in FIELDS - {"action", "profile"}:
        value = request[key]
        if type(value) is not str or SHA256.fullmatch(value) is None:
            raise ValueError("INVALID_REVIEWED_RELEASE_DIGEST")


def main(request):
    """Refuse before touching transport or disk until the owner binds a verifier."""
    qualify_request(request)
    return {"status": "denied", "reason": "TRUSTED_CURRENT_JOB_BINDING_UNAVAILABLE"}
