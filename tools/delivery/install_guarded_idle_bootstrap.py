#!/usr/bin/env python3
"""Fixed root maintenance installer for reviewed guarded Stokowski idle source.

Called only by the authenticated fixed maintenance action, with an approval
bound from its protected deployment configuration. Not a generic CLI, a
selector/apply issuer, or a start action.
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import ctypes
import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
import time

import guarded_service_upgrade as guard

PREPARED = Path("/var/lib/uif-stokowski/idle-bootstrap-input")
SOURCE = PREPARED / "guarded_service_upgrade.py"
BUNDLE = PREPARED / "bundle.tar"


@dataclass(frozen=True)
class BootstrapApproval:
    operation_id: str
    controller_sha256: str
    bundle_sha256: str
    launcher_sha256: str
    config_sha256: str


def _directory(path, mode):
    guard.protected_parents(path)
    try:
        path.mkdir(mode=mode)
    except FileExistsError:
        pass
    info = path.lstat()
    guard.require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                  "unprotected bootstrap directory")


def _rename_noreplace(source, target):
    """Publish a complete same-filesystem entity without a transient hardlink."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    guard.protected_parents(target)
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if rename(-100, os.fsencode(source), parent, os.fsencode(target.name), 1) != 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(target))
        os.fsync(parent)
    finally:
        os.close(parent)


def _publish_once(target, mode, write):
    guard.protected_parents(target)
    fd, temporary = tempfile.mkstemp(prefix=".private-idle-bootstrap-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            write(out)
            os.fchmod(out.fileno(), mode)
            out.flush()
            os.fsync(out.fileno())
        _rename_noreplace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _same_file(target, exact, mode=0o600):
    try:
        raw = guard.read_record(target.parent, target.name)
    except FileNotFoundError:
        _publish_once(target, mode, lambda out: out.write(exact))
    else:
        guard.require(raw == exact, "existing bootstrap file differs from original")
        _sync_parent(target)


def _materialize_stage(review):
    target = guard.STAGES / guard.FIXED_STAGE
    if target.exists() or target.is_symlink():
        guard.sealed_selection(review)
        _sync_parent(target)
        return
    guard.protected_parents(target)
    with tempfile.TemporaryDirectory(prefix=".parked-idle-", dir=target.parent) as scratch:
        tree = Path(scratch) / "closure"
        tree.mkdir(mode=0o755)
        with guard.protected_open(guard.PACKETS / guard.FIXED_STAGE / 'bundle.tar',
                                  guard.MAX_BUNDLE) as bundle:
            guard.verify_bundle(bundle, review, guard.PROFILES[guard.PROFILE_NAME].unit, guard.FIXED_STAGE)
            bundle.seek(0)
            directories = {}
            with tarfile.open(fileobj=bundle, mode="r:") as archive:
                for item in archive:
                    relative = PurePosixPath(item.name)
                    guard.require(bool(relative.parts) and not relative.is_absolute() and
                                  ".." not in relative.parts and not item.mode & 0o7022,
                                  "unsafe bootstrap archive path or metadata")
                    path = tree / relative
                    if item.isdir():
                        path.mkdir(mode=0o755, parents=True, exist_ok=True)
                        directories[path] = item.mode & 0o777
                    else:
                        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                        with archive.extractfile(item) as reader, path.open("xb") as out:
                            shutil.copyfileobj(reader, out, length=1024 * 1024)
                            out.flush()
                            os.fchmod(out.fileno(), item.mode & 0o777)
                            os.fsync(out.fileno())
            for parent, _, _ in os.walk(tree, topdown=False):
                directory = Path(parent)
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fchmod(fd, directories.get(directory, 0o755))
                    os.fsync(fd)
                finally:
                    os.close(fd)
        _rename_noreplace(tree, target)
    guard.sealed_selection(review)


def _bundle_once(target, expected):
    try:
        pin = guard.bytes_pin(target, guard.MAX_BUNDLE)
    except FileNotFoundError:
        def copy(out):
            with guard.protected_open(BUNDLE, guard.MAX_BUNDLE) as stream:
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
                guard.require(digest.hexdigest() == expected, "bootstrap bundle changed")
        _publish_once(target, 0o444, copy)
        pin = guard.bytes_pin(target, guard.MAX_BUNDLE)
    guard.require(pin == expected, "original installed bundle changed")
    _sync_parent(target)


def _parked_witness(unit, unit_sha):
    raw = guard.read_record(PREPARED, "quiescence.json")
    witness = guard.record(raw, ("schema", "unit", "parked_unit_sha256", "observed_at",
                                 "inflight", "children_gone", "unresolved_operation_ids"))
    now = time.time()
    guard.require(witness["schema"] == "uif.guarded-upgrade-quiescence/1"
                  and (witness["unit"], witness["parked_unit_sha256"]) == (unit, unit_sha)
                  and type(witness["observed_at"]) is int and now - 60 < witness["observed_at"] <= now
                  and type(witness["inflight"]) is int and witness["inflight"] == 0
                  and witness["children_gone"] is True and witness["unresolved_operation_ids"] == [],
                  "fresh original parked quiescence required")
    return raw


def install(approved):
    """Install control files, stage and drop-in once; never emit admission."""
    guard.require(type(approved) is BootstrapApproval and os.getresuid() == (0, 0, 0)
                  and all(guard.hash_pin(value) for value in vars(approved).values()),
                  "fixed reviewed bootstrap approval required")
    unit = guard.PROFILES[guard.PROFILE_NAME].unit
    old = guard.BOOTSTRAP_PRIOR
    intent_path = guard.RECEIPTS / "private-webhook-bootstrap.intent.json"
    intent = guard.encode({"schema": "uif.private-idle-bootstrap/1",
                           "unit": unit, "prior_unit_sha256": old, **vars(approved)})
    # A staged drop-in makes NeedDaemonReload=yes. A same-original recovery
    # of the completed files is read-only rather than revalidating old cat.
    try:
        existing_intent = guard.read_record(guard.RECEIPTS, intent_path.name)
    except FileNotFoundError:
        existing_intent = None
    guard.require(existing_intent in (None, intent), "original bootstrap intent differs")
    review = {key: getattr(approved, key) for key in
              ("bundle_sha256", "launcher_sha256", "config_sha256")}
    if existing_intent is not None and guard.DROPIN.exists():
        with guard.selector_lock(shared=True):
            guard.require(guard.read_record(guard.RECEIPTS, intent_path.name) == intent
                          and guard.bytes_pin(guard.DROPIN) == guard.digest(guard.selector_dropin())
                          and guard.bytes_pin(guard.TOOL_PATH) == approved.controller_sha256,
                          "bootstrap retained files require reconciliation")
            guard.sealed_selection(review)
            _sync_parent(guard.DROPIN)
        return {"operation_id": approved.operation_id, "installed_files": True,
                "service_started": False, "daemon_reload_performed": False}
    # This entry is for the documented parked preimage, never a replacement.
    guard.require(guard.stopped_unit_sha(unit) == old and guard.no_marker(guard.PARKED_STAGE)
                  and guard.no_marker(guard.FIXED_STAGE), "original parked unit or markers changed")
    with guard.protected_open(SOURCE, guard.MAX_RECORD) as stream:
        controller = stream.read()
    guard.require(hashlib.sha256(controller).hexdigest() == approved.controller_sha256,
                  "bootstrap controller source changed")
    with guard.protected_open(BUNDLE, guard.MAX_BUNDLE) as stream:
        guard.verify_bundle(stream, review, unit, guard.FIXED_STAGE)
    _directory(guard.ROOT, 0o755)
    _directory(guard.RECEIPTS, 0o700)
    _directory(guard.PACKETS / guard.FIXED_STAGE, 0o700)
    _same_file(guard.LOCK, b"fixed uif idle selector lock\n", mode=0o644)
    with guard.selector_lock(shared=False):
        guard.require(guard.stopped_unit_sha(unit) == old
                      and guard.no_marker(guard.PARKED_STAGE) and guard.no_marker(guard.FIXED_STAGE),
                      "parked unit changed under bootstrap lock")
        observed = _parked_witness(unit, old)
        _same_file(intent_path, intent)
        _same_file(guard.TOOL_PATH, controller, mode=0o444)
        _same_file(guard.SELECTOR, guard.encode({"mode": "parked", "stage": guard.PARKED_STAGE}))
        packet_bundle = guard.PACKETS / guard.FIXED_STAGE / "bundle.tar"
        _bundle_once(packet_bundle, approved.bundle_sha256)
        _materialize_stage(review)
        guard.require(guard.stopped_unit_sha(unit) == old and _parked_witness(unit, old) == observed
                      and guard.no_marker(guard.PARKED_STAGE) and guard.no_marker(guard.FIXED_STAGE),
                      "parked proof changed before provisioning the drop-in")
        _same_file(guard.DROPIN, guard.selector_dropin(), mode=0o644)
        guard.require(guard.bytes_pin(guard.DROPIN) == guard.digest(guard.selector_dropin())
                      and guard.bytes_pin(guard.TOOL_PATH) == approved.controller_sha256,
                      "bootstrap readback changed")
    # Reload is deliberately not issued here: host owner obtains the reviewed
    # post-bootstrap effective unit digest before the guarded apply accepts it.
    return {"operation_id": approved.operation_id, "installed_files": True,
            "service_started": False, "daemon_reload_performed": False}
