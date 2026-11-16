"""Bounded metadata from existing OffMenu repository journals; never authority.

The CLI has no path/config/command selector. _observe_root is synthetic-test
injection only. Neither this module nor its CLI imports a product Controller.
Actual-store invocation needs separate authorization, not merely this source.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import stat

ROOT = "/home/user/src/offmenu/.delivery/integration"
REPOSITORY = "maldous/offmenu"
MAX_RECORD_BYTES = 262144
MAX_POINTER_BYTES = 8192
MAX_OUTPUT_BYTES = 8192
MAX_NODES = 8192
MAX_DEPTH = 32
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
STATUS = re.compile(r"[a-z][a-z0-9-]{0,95}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
DIR_FLAGS = FILE_FLAGS | os.O_DIRECTORY


class ObservationError(ValueError):
    """Fixed safe reason code, not an exception or journal body for output."""


def fingerprint(value):
    """Exact existing repository integration fingerprint recipe."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError("duplicate-json-key")
        result[key] = value
    return result


def _constant(_):
    raise ObservationError("nonfinite-json")


def _json(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        pending = [(value, 0)]
        nodes = 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if nodes > MAX_NODES or depth > MAX_DEPTH:
                raise ObservationError("json-structure-bound")
            if type(item) is float and not math.isfinite(item):
                raise ObservationError("nonfinite-json")
            if type(item) is dict:
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
        if type(value) is not dict:
            raise ObservationError("json-object-required")
        return value
    except (UnicodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ObservationError):
            raise
        raise ObservationError("malformed-json") from exc


def _root_fd(root):
    path = PurePosixPath(root)
    if not path.is_absolute() or str(path) != root or ".." in path.parts:
        raise ObservationError("invalid-root")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            new = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular_fd(parent, name, presence, field):
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        presence[field] = False
        raise
    presence[field] = True
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ObservationError("nonregular-or-linked-file")
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def _record(parent, name, limit, presence, field):
    fd, before = _regular_fd(parent, name, presence, field)
    try:
        if before.st_size > limit:
            raise ObservationError("record-size-bound")
        chunks = []
        remaining = limit + 1
        while remaining:
            block = os.read(fd, min(remaining, 65536))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) > limit:
            raise ObservationError("record-size-bound")
        if identity(before) != identity(after) or len(raw) != after.st_size:
            raise ObservationError("record-changed-during-read")
        return _json(raw), hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _directory_parts(value, root):
    if type(value) is not str or len(value) > 1024 or not value.startswith(root + "/"):
        raise ObservationError("invalid-selected-directory")
    relative = value[len(root) + 1:]
    parts = relative.split("/")
    if (len(parts) > 8 or len(relative) > 512
        or any(part in ("", ".", "..") or not TOKEN.fullmatch(part) for part in parts)
        or str(PurePosixPath(value)) != value):
        raise ObservationError("invalid-selected-directory")
    return parts


def _claim(value, root):
    if (set(value) != {"binding_sha256", "directory", "owner"}
        or type(value["binding_sha256"]) is not str
        or not HEX64.fullmatch(value["binding_sha256"])
        or type(value["owner"]) is not str or not TOKEN.fullmatch(value["owner"])):
        raise ObservationError("invalid-pointer")
    return _directory_parts(value["directory"], root)


def _metadata(pointer, pointer_sha, binding, state, binding_raw_sha, state_sha, parts):
    key = fingerprint(binding)
    if (binding.get("repository") != REPOSITORY or binding.get("owner") != pointer["owner"]
        or key != pointer["binding_sha256"]):
        raise ObservationError("pointer-binding-mismatch")
    if (type(binding.get("version")) is not int or binding["version"] not in (1, 2, 4)
        or any(type(binding.get(field)) is not str or not HEX40.fullmatch(binding[field])
               for field in ("head", "base", "tree"))):
        raise ObservationError("partial-or-invalid-source-binding")
    if state.get("binding_sha256") != key:
        raise ObservationError("state-binding-mismatch")
    if type(state.get("status")) is not str or not STATUS.fullmatch(state["status"]):
        raise ObservationError("partial-or-invalid-state")
    scalars = {"status": state["status"]}
    for field in ("merge_effect", "release_effect", "tag_effect"):
        if field in state:
            value = state[field]
            if value is not None and (type(value) is not str or not STATUS.fullmatch(value)):
                raise ObservationError("invalid-effect-scalar")
            scalars[field] = value
    if "publication" in state:
        publication = state["publication"]
        if type(publication) is not dict:
            raise ObservationError("invalid-publication-shape")
        if "effect" in publication:
            effect = publication["effect"]
            if effect is not None and (type(effect) is not str or not STATUS.fullmatch(effect)):
                raise ObservationError("invalid-effect-scalar")
            scalars["publication_effect"] = effect
    return {"pointer_sha256": pointer_sha, "binding_sha256": key,
            "binding_file_sha256": binding_raw_sha, "state_file_sha256": state_sha,
            "recorded_owner": pointer["owner"], "selected_directory": "/".join(parts),
            "consistency": {"pointer_binding_match": True, "state_binding_match": True,
                            "repository_match": True}, "recorded_scalars": scalars}


def _observe_root(root):
    """Internal synthetic fixture injection; never expose it as a CLI selector.

    Cooperative same-host file locking and read-only directory descriptors are
    not authentication, distributed custody, adversarial-FS or effect fencing.
    """
    presence = dict.fromkeys(("root", "lock", "pointer", "binding", "state"))
    result = {"schema": "offmenu-integration-observation/1", "outcome": "refused",
              "record_presence": presence, "metadata": None,
              "authority": dict.fromkeys(("external_owner", "current_issuer", "lease",
                                           "epoch", "fence", "authentication", "settlement")),
              "grants_authority": False, "effects_authorized": False,
              "effect_state_verified": False, "native_custody_verified": False}
    try:
        with ExitStack() as stack:
            try:
                root_fd = _root_fd(root)
            except FileNotFoundError:
                presence["root"] = False
                raise
            presence["root"] = True
            stack.callback(os.close, root_fd)
            lock_fd, _ = _regular_fd(root_fd, "repository-owner.lock", presence, "lock")
            stack.callback(os.close, lock_fd)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ObservationError("exclusive-lock-held") from exc
            name = "active-owner.json"
            try:
                pointer, pointer_sha = _record(root_fd, name, MAX_POINTER_BYTES, presence, "pointer")
            except FileNotFoundError:
                name = "repository-claim.json"
                pointer, pointer_sha = _record(root_fd, name, MAX_POINTER_BYTES, presence, "pointer")
            parts = _claim(pointer, root)
            directory_fd = root_fd
            for part in parts:
                directory_fd = os.open(part, DIR_FLAGS, dir_fd=directory_fd)
                stack.callback(os.close, directory_fd)
            binding, binding_sha = _record(directory_fd, "binding.json", MAX_RECORD_BYTES,
                                          presence, "binding")
            state, state_sha = _record(directory_fd, "state.json", MAX_RECORD_BYTES,
                                      presence, "state")
            metadata = _metadata(pointer, pointer_sha, binding, state, binding_sha, state_sha, parts)
            again, again_sha = _record(root_fd, name, MAX_POINTER_BYTES, presence, "pointer")
            if again != pointer or again_sha != pointer_sha:
                raise ObservationError("pointer-changed-during-read")
            metadata["pointer_name"] = name
            result.update(outcome="observed", reason=None, metadata=metadata)
    except ObservationError as exc:
        result["reason"] = str(exc)
    except FileNotFoundError:
        result["reason"] = "missing-existing-record"
    except (OSError, ValueError, RecursionError):
        result["reason"] = "unreadable-or-invalid-record"
    result["observed_at"] = datetime.now(timezone.utc).isoformat()
    if len(json.dumps(result, allow_nan=False).encode()) > MAX_OUTPUT_BYTES:
        raise ObservationError("output-bound")
    return result


def observe():
    """Fixed root only. Separate authorization is required for real data access."""
    return _observe_root(ROOT)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    result = observe()
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["outcome"] == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
