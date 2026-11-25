"""Fixed, root-maintenance provisioning for one OffMenu verifier.

An independent release qualification must establish the protected shelf and
pin before this module can be enabled. It neither executes the verifier nor
grants the normal transport permission to do so.
"""

import ctypes
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import time

ROOT = Path("/etc/offmenu-verifier-bootstrap")
SOURCE = ROOT / "approved-verifier.py"
TARGET = ROOT / "verifier.py"
INTENT = ROOT / "original.intent"
RECEIPT = ROOT / "original.receipt"
HEX = re.compile(r"[0-9a-f]{64}")
# Release qualification must replace this unqualified constant in reviewed code.
# No caller or environment value can enable this draft.
APPROVED = None
LIMIT = 1024 * 1024


def pin(raw):
    return hashlib.sha256(raw).hexdigest()


def record(operation, digest):
    if any(type(value) is not str or HEX.fullmatch(value) is None
           for value in (operation, digest)):
        raise ValueError("INVALID_BOOTSTRAP_PIN")
    return f"offmenu-native-upgrade-v1 {operation} {digest}\n".encode("ascii")


def transition(read, create, operation, digest):
    """Advance only original exact bytes; injected seams are fixture-only."""
    expected = record(operation, digest)
    source = read("source")
    if source is None or pin(source) != digest:
        raise RuntimeError("APPROVED_VERIFIER_MISMATCH")
    intent, target, receipt = (read(key) for key in ("intent", "target", "receipt"))
    if intent is None:
        if target is not None or receipt is not None:
            raise RuntimeError("UNEXPLAINED_BOOTSTRAP_STATE")
        create("intent", expected)
    elif intent != expected:
        raise RuntimeError("BOOTSTRAP_ORIGINAL_MISMATCH")
    if receipt is not None and (receipt != expected or target != source):
        raise RuntimeError("BOOTSTRAP_RECEIPT_CONTRADICTION")
    if target is None:
        create("target", source)
    elif target != source:
        raise RuntimeError("BOOTSTRAP_TARGET_CONTRADICTION")
    if read("target") != source:
        raise RuntimeError("BOOTSTRAP_READBACK_FAILED")
    if receipt is None:
        create("receipt", expected)
    if read("receipt") != expected:
        raise RuntimeError("BOOTSTRAP_RECEIPT_READBACK_FAILED")
    return "verified"


def publish_noreplace(anchor, name, raw):
    """Make a fully synced file visible without ever replacing a final name."""
    # This receiver is Linux-only. Do not emulate no-replace with check/rename.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    temp = ".pending-" + secrets.token_hex(16)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o400, dir_fd=anchor)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if rename(anchor, os.fsencode(temp), anchor, os.fsencode(name), 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), name)
        published = True
        os.fsync(anchor)
    finally:
        if not published:
            os.unlink(temp, dir_fd=anchor)


def main():
    if APPROVED is None:
        raise RuntimeError("BOOTSTRAP_RELEASE_UNQUALIFIED")
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("BOOTSTRAP_REQUIRES_ROOT_MAINTENANCE")
    operation, digest, expires_at = APPROVED
    record(operation, digest)
    names = {"source": SOURCE.name, "target": TARGET.name,
             "intent": INTENT.name, "receipt": RECEIPT.name}
    anchor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in ROOT.parts[1:]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=anchor)
            os.close(anchor)
            anchor = directory
            info = os.fstat(anchor)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise PermissionError("UNPROTECTED_BOOTSTRAP_DIRECTORY")
        fcntl.flock(anchor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Harden any fully published final record left by a prior process that
        # died after the atomic rename but before its directory sync.
        os.fsync(anchor)

        def read(role):
            try:
                fd = os.open(names[role], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=anchor)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != 0 or info.st_mode & 0o022
                        or info.st_size > LIMIT):
                    raise PermissionError("UNPROTECTED_BOOTSTRAP_FILE")
                raw = stream.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise ValueError("BOOTSTRAP_FILE_TOO_LARGE")
                return raw

        def create(role, raw):
            now = time.time()
            if type(expires_at) is not int or not now < expires_at <= now + 300:
                raise PermissionError("BOOTSTRAP_AUTHORIZATION_NOT_FRESH")
            publish_noreplace(anchor, names[role], raw)

        return transition(read, create, operation, digest)
    finally:
        os.close(anchor)
