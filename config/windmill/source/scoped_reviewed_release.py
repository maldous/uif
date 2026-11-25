"""Scoped Windmill action and private Foo handler contract.

The install adapter and reviewed pins must be bound in deployed composition,
never by the public action's caller. Do not mount the handler on generic root
SSH or a public route. No second identity provider is involved.
"""

import os
import re
from typing import NamedTuple
from uuid import UUID

FIELDS = ("operation_id", "release_sha256", "package_sha256",
          "protected_request_sha256")
PROFILE = "offmenu-native-upgrade-v1"
SHA256 = re.compile("[0-9a-f]{64}")


class Release(NamedTuple):
    operation_id: str
    release_sha256: str
    package_sha256: str
    protected_request_sha256: str


def run_id(value):
    if type(value) is not str or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError("INVALID_WINDMILL_RUN_ID")
    return value


def exact_release(request):
    if type(request) is not dict or request.keys() != set(FIELDS):
        raise ValueError("INVALID_RELEASE_FIELDS")
    values = tuple(request[key] for key in FIELDS)
    if any(type(value) is not str or not SHA256.fullmatch(value) for value in values):
        raise ValueError("INVALID_RELEASE_PIN")
    return Release(*values)


def bind_action(send_to_private_foo):
    """Bind once in the authenticated deployed action, never in request args."""
    def main(request):
        pins = exact_release(request)
        if os.environ.get("WM_WORKSPACE") != "foo":
            raise PermissionError("WRONG_WINDMILL_WORKSPACE")
        job = run_id(os.environ.get("WM_JOB_ID"))
        if send_to_private_foo is None:
            raise RuntimeError("SCOPED_FOO_CHANNEL_UNBOUND")
        return send_to_private_foo(PROFILE, job, pins._asdict())
    return main


def bind_foo_handler(approved, run_original):
    """Bind only behind private ingress, using independently reviewed pins.

    run_original must be the fixed project adapter that durably consumes and
    reconciles the original operation. The Windmill run ID is audit context,
    not the dedupe key, and a new Windmill run never creates a new operation.
    """
    def receive(profile, job, request):
        if profile != PROFILE:
            raise ValueError("WRONG_RELEASE_PROFILE")
        run_id(job)
        pins = exact_release(request)
        if approved is None or run_original is None:
            raise RuntimeError("PROTECTED_RELEASE_ADAPTER_UNBOUND")
        if pins != approved:
            raise PermissionError("UNREVIEWED_RELEASE")
        return run_original(pins.operation_id, job, pins)
    return receive


# No verified restricted transport is installed. Keep the draft entrypoint shut.
main = bind_action(None)
