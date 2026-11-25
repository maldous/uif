"""Durable Windmill handoff within the canonical OffMenu operation packet."""

import fcntl
import os
from pathlib import Path
import stat

from offmenu_verifier_bootstrap import publish_noreplace
from scoped_reviewed_release import bind_foo_handler, exact_release, run_id

PACKETS = Path("/var/lib/offmenu-publisher/upgrade-ingress")
INTENT = "windmill-release.intent"


def _open_protected(path):
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("PROTECTED_RELEASE_REQUIRES_ROOT")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise PermissionError("UNPROTECTED_RELEASE_PACKET")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _run_with_packet(packet, job, pins, dispatch_original, observe_original):
    """Same transition used by the production packet and detached fixtures."""
    fcntl.flock(packet, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Recover a complete record published before an interrupted directory sync.
    os.fsync(packet)
    record = ("offmenu-native-upgrade-v1 " + " ".join(pins) + "\n").encode("ascii")
    try:
        fd = os.open(INTENT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=packet)
    except FileNotFoundError:
        # An existing canonical effect without our handoff is never replayed.
        if observe_original(pins.operation_id) != "not_started":
            raise RuntimeError("ORIGINAL_EFFECT_ALREADY_EXISTS_OR_UNKNOWN")
        publish_noreplace(packet, INTENT, record)
        dispatch_original(pins.operation_id, job)
    else:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.fstat(packet).st_uid or info.st_mode & 0o022 or
                    stream.read(len(record) + 1) != record):
                raise RuntimeError("ORIGINAL_HANDOFF_MISMATCH")
    status = observe_original(pins.operation_id)
    # No effect receipt or equivocal observation means manual original readback,
    # never another dispatch, even with a different Windmill-generated run ID.
    if status != "installed":
        raise RuntimeError("ORIGINAL_RELEASE_REQUIRES_RECONCILIATION")
    return status


def bind_offmenu_original(dispatch_original, observe_original):
    """Only private deployed composition may bind the fixed canonical adapter."""
    def run_original(operation, job, pins):
        run_id(job)
        pins = exact_release(pins._asdict())
        if operation != pins.operation_id:
            raise ValueError("ORIGINAL_OPERATION_MISMATCH")
        packet = _open_protected(PACKETS / pins.operation_id)
        try:
            return _run_with_packet(packet, job, pins, dispatch_original, observe_original)
        finally:
            os.close(packet)
    return run_original


def bind_protected_receiver(approved, dispatch_original, observe_original):
    """Compose the private Foo receiver with the durable canonical handoff."""
    return bind_foo_handler(approved, bind_offmenu_original(dispatch_original, observe_original))
