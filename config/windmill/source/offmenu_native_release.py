"""Composition for the fixed OffMenu native ingress, not a public entrypoint.

Only private, protected deployment composition imports this module and obtains
the maintained OffMenu modules from its verified immutable release shelf.
"""

from typing import NamedTuple

from offmenu_release_journal import bind_protected_receiver
from scoped_reviewed_release import FIELDS, PROFILE, exact_release

REPOSITORY = "maldous/offmenu"
ROUTE = ("offmenu", REPOSITORY, PROFILE)
OFFMENU_SOURCE = "f40db673ac8d1c39d6b32146198210a09b6ef309"
OFFMENU_TREE = "1880f68de14946f5dd66fe231ae943e2abb77598"


class ProtectedOffMenuApproval(NamedTuple):
    owner: str
    operation_id: str
    release_sha256: str
    package_sha256: str
    protected_request_sha256: str
    head: str
    tree: str


def bind_fixed_native_handler(approval, ingress, package, upgrade):
    """Wire the fixed canonical API; no caller-supplied action/adapter/module."""
    if type(approval) is not ProtectedOffMenuApproval or (
            approval.head, approval.tree) != (OFFMENU_SOURCE, OFFMENU_TREE):
        raise PermissionError("UNQUALIFIED_OFFMENU_SOURCE")
    if (ingress.REPOSITORY, str(ingress.SERVICE), str(ingress.TARGET)) != (
            REPOSITORY, "offmenu-stokowski-current.service", "/opt/offmenu-publisher"):
        raise PermissionError("UNQUALIFIED_OFFMENU_INGRESS")
    pins = exact_release({name: getattr(approval, name) for name in FIELDS})
    canonical = ingress.bind_private_windmill_release(
        ingress.WindmillReleaseApproval(*approval), package, upgrade)

    def read_native(action, operation, job):
        receipt = canonical(action, operation, job)
        if (type(receipt) is not dict
                or set(receipt) != {"state", "operation_id", "confirmation_sha256"}
                or receipt["operation_id"] != pins.operation_id
                or receipt["confirmation_sha256"] != pins.protected_request_sha256
                or receipt["state"] not in ("not_started", "reconcile", "installed")):
            raise RuntimeError("OFFMENU_NATIVE_RECEIPT_MISMATCH")
        return receipt["state"]

    def dispatch(operation, job):
        if read_native("replace", operation, job) != "installed":
            raise RuntimeError("OFFMENU_NATIVE_REPLACEMENT_UNCONFIRMED")

    def observe(operation, job):
        return read_native("status", operation, job)

    return bind_protected_receiver(pins, dispatch, observe)
