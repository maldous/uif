"""Detached same-role verification for a trusted credential renewal adapter.

This module does not contact the issuer, read secrets, prove that a credential
changed, or introduce a runtime gate. Callers must obtain both status records
from the affected runtime through its already authenticated role transport:
Morgan's internal authenticated status reader or native RemoteAuthority.status.
"""
from dataclasses import dataclass
import re

from .effect_authority import AuthorityIdentity


class IdentityReadbackUnavailable(ValueError):
    """Redacted disposition, safe to record without response bodies."""


@dataclass(frozen=True)
class IdentityReadbackExpectation:
    """Trusted installed-role pins, never taken from a candidate response."""
    role: str
    identity: AuthorityIdentity
    coordinator_epoch: int
    installation_sha256: str
    stopped: bool


def verifies_same_role(expected, *, before, after):
    """Return the redacted role/epoch only if both readbacks match installed pins.

    This verifies identity continuity, never issuer rotation or secret-store
    publication. Neither observation may be substituted with an admin read.
    """
    if (type(expected) is not IdentityReadbackExpectation
            or expected.role not in ("morgan", "native")
            or type(expected.identity) is not AuthorityIdentity
            or type(expected.coordinator_epoch) is not int or expected.coordinator_epoch <= 0
            or type(expected.stopped) is not bool
            or type(expected.installation_sha256) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", expected.installation_sha256)):
        raise IdentityReadbackUnavailable("installed_identity_expectation_unavailable")
    identity, installation = expected.identity, expected.installation_sha256
    for observation in (before, after):
        if (type(observation) is not dict
                or observation.get("role") != expected.role
                or observation.get("authority_id") != identity.authority_id
                or observation.get("session_id") != identity.session_id
                or observation.get("policy_sha256") != identity.policy_sha256
                or observation.get("deployment_sha256") != identity.deployment_sha256
                or observation.get("installation_sha256") != installation
                or observation.get("commissioned_sha256") != installation
                or type(observation.get("coordinator_epoch")) is not int
                or observation["coordinator_epoch"] != expected.coordinator_epoch
                or observation.get("stopped") is not expected.stopped):
            raise IdentityReadbackUnavailable("same_runtime_role_identity_unverified")
    return {"role": expected.role, "coordinator_epoch": expected.coordinator_epoch,
            "stopped": expected.stopped}
