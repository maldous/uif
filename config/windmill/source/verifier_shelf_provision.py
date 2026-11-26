"""Fixed root maintenance action for a reviewed verifier shelf, never app admission."""

import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
from tempfile import TemporaryDirectory
from typing import NamedTuple

HEX = re.compile("[0-9a-f]{64}")
PART = re.compile("[A-Za-z0-9_.+-]{1,200}")
LIMIT = 64 * 1024 * 1024


class Profile(NamedTuple):
    stage: Path
    parent: Path
    shelf: str
    release: str
    manifest_sha256: str
    ingress: str


# Reviewed inputs, not caller-supplied paths or commands. The detached stage
# must supply a canonical digest-map projection of its exact release closure.
PROFILES = {
    "offmenu": Profile(
        Path("/home/user/src/offmenu-publisher-preparation-20261125-a1/ingress-release-4676f2d-verifier-a1"),
        Path("/usr/local/lib"), "offmenu-native-upgrade",
        "c292384551558da7766d6a107dfc2c00dfd589bfa01e622b5c326e2cf26a9f1a",
        "170f0d65ddc957f3704b0ecfb1438b8d814e870ceca7734e4a6b0d74087596b4",
        "tools/delivery/offmenu_native_upgrade_ingress.py"),
    "offmenu-v2": Profile(
        Path("/home/user/src/offmenu-publisher-preparation-20261125-a1/ingress-release-fixed-v2-verifier-a1"),
        Path("/usr/local/lib"), "offmenu-native-upgrade-v2",
        "1208a7d41aa5b754cd4ced82ff18f5aadbb0de758202a13bb73adcd76bfccae4",
        "e9a593ab451b3f964d0596c0b2b7d0d4a53f08ad8447cac49bc76ac6b5d9e599",
        "tools/delivery/offmenu_native_upgrade_ingress.py"),
}


def encode(item):
    return (json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def protected_directory(path, root_owned):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if root_owned and (info.st_uid != 0 or info.st_mode & 0o022):
                raise PermissionError("UNPROTECTED_SHELF_PARENT")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_at(directory, parts, limit, owner=None):
    fd = os.dup(directory)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size > limit or
                    (owner is not None and (info.st_uid != owner or info.st_mode & 0o022))):
                raise RuntimeError("UNQUALIFIED_SHELF_INPUT")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise RuntimeError("SHELF_INPUT_TOO_LARGE")
            return raw
    finally:
        os.close(fd)


def planned_files(raw, expected):
    manifest = json.loads(raw)
    if (type(manifest) is not dict or not 0 < len(manifest) <= 50000
            or encode(manifest) != raw or hashlib.sha256(raw).hexdigest() != expected):
        raise RuntimeError("REVIEWED_SHELF_MANIFEST_MISMATCH")
    for path, digest in manifest.items():
        parts = PurePosixPath(path).parts
        if (not parts or len(parts) > 30 or "/".join(parts) != path or
                any(not PART.fullmatch(part) or part in {".", ".."} for part in parts)
                or type(digest) is not str or not HEX.fullmatch(digest)):
            raise RuntimeError("INVALID_SHELF_MANIFEST_ENTRY")
    return manifest


def manifest_files(raw, profile):
    """Project exact detached metadata onto the canonical native release-id map."""
    if hashlib.sha256(raw).hexdigest() != profile.manifest_sha256:
        raise RuntimeError("DETACHED_SHELF_MANIFEST_MISMATCH")
    metadata = json.loads(raw)
    if metadata.get("schema") != "offmenu-detached-verifier-candidate-v1":
        raise RuntimeError("DETACHED_SHELF_MANIFEST_SCHEMA")
    flat = {path: file["sha256"] for path, file in metadata["files"].items()}
    return planned_files(encode(flat), profile.release)


def rename_directory(source, anchor, src, dst):
    """Same-filesystem atomic publication; refuse kernels without no-replace."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source, os.fsencode(src), anchor, os.fsencode(dst), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), dst)
    os.fsync(anchor)


def publish_noreplace(anchor, name, raw):
    """Publish only a complete fsynced record, using the same rename as the shelf."""
    temp = ".pending-" + secrets.token_hex(16)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o400, dir_fd=anchor)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        rename_directory(anchor, anchor, temp, name)
    finally:
        try:
            os.unlink(temp, dir_fd=anchor)
        except FileNotFoundError:
            pass


def copy_closure(stage, files, destination):
    total = 0
    for path, digest in sorted(files.items()):
        relative = PurePosixPath(path)
        raw = read_at(stage, relative.parts, LIMIT)
        total += len(raw)
        if total > 1024 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError("SHELF_MEMBER_DIGEST_MISMATCH")
        file = destination / relative
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with file.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
    for base, _, _ in os.walk(destination, topdown=False):
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(fd, 0o755)
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_installed(profile):
    result = subprocess.run(
        ["/usr/bin/python3.12", "-I", "-S", "-B",
         str(profile.parent / profile.shelf / profile.ingress), "release-id"],
        capture_output=True, check=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if result.stdout != encode({"release_sha256": profile.release}) or result.stderr:
        raise RuntimeError("INSTALLED_SHELF_READBACK_MISMATCH")


def provision(profile, operation, anchor):
    """Protected composition: caller passes only the selected compiled profile and original."""
    if type(operation) is not str or not HEX.fullmatch(operation):
        raise ValueError("INVALID_SHELF_ORIGINAL")
    fcntl.flock(anchor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.fsync(anchor)
    owner = os.fstat(anchor).st_uid
    intent = "." + profile.shelf + ".intent"
    receipt = "." + profile.shelf + ".receipt"
    record = encode({"operation_id": operation, "release_sha256": profile.release,
                     "shelf": profile.shelf})

    def prior(name):
        try:
            return read_at(anchor, (name,), 4096, owner)
        except FileNotFoundError:
            return None

    old, done = prior(intent), prior(receipt)
    try:
        os.stat(profile.shelf, dir_fd=anchor, follow_symlinks=False)
        exists = True
    except FileNotFoundError:
        exists = False
    if (old not in (None, record) or done not in (None, record) or
            (old is None and (exists or done is not None)) or (done is not None and not exists)):
        raise RuntimeError("SHELF_ORIGINAL_REQUIRES_RECONCILIATION")

    if not exists:
        stage = protected_directory(profile.stage, False)
        try:
            raw = read_at(stage, ("release-manifest.json",), LIMIT // 4)
            manifest = manifest_files(raw, profile)
            if profile.ingress not in manifest:
                raise RuntimeError("REVIEWED_INGRESS_MISSING")
            if old is None:
                publish_noreplace(anchor, intent, record)
            with TemporaryDirectory(prefix="." + profile.shelf + ".pending-", dir=profile.parent) as tmp:
                candidate = Path(tmp) / "closure"
                candidate.mkdir(mode=0o700)
                closure = os.open("release", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage)
                try:
                    copy_closure(closure, manifest, candidate)
                finally:
                    os.close(closure)
                temp = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    # Move the sealed closure, not its scratch container.
                    rename_directory(temp, anchor, "closure", profile.shelf)
                finally:
                    os.close(temp)
        finally:
            os.close(stage)
    # Verify installed root custody and full expected bytes before executing it.
    installed = os.open(profile.shelf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=anchor)
    try:
        info = os.fstat(installed)
        if info.st_uid != owner or info.st_mode & 0o022:
            raise RuntimeError("INSTALLED_SHELF_CUSTODY_MISMATCH")
        verify_tree(profile.parent / profile.shelf, profile.release, owner)
    finally:
        os.close(installed)
    verify_installed(profile)
    if done is None:
        publish_noreplace(anchor, receipt, record)
    return record


def verify_tree(shelf, expected, owner):
    files = {}
    total = 0
    def fail(error):
        raise error
    for base, names, leaves in os.walk(shelf, followlinks=False, onerror=fail):
        for name in names + leaves:
            path = Path(base) / name
            info = path.lstat()
            if info.st_uid != owner or info.st_mode & 0o022:
                raise RuntimeError("INSTALLED_SHELF_CUSTODY_MISMATCH")
            if name in names:
                if not stat.S_ISDIR(info.st_mode):
                    raise RuntimeError("INSTALLED_SHELF_ALIAS")
            else:
                total += info.st_size
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size > LIMIT or total > 1024 * 1024 * 1024 or len(files) >= 50000):
                    raise RuntimeError("INSTALLED_SHELF_MEMBER_REFUSED")
                with path.open("rb") as stream:
                    files[str(path.relative_to(shelf))] = hashlib.file_digest(stream, "sha256").hexdigest()
    if hashlib.sha256(encode(files)).hexdigest() != expected:
        raise RuntimeError("INSTALLED_SHELF_CONTENT_MISMATCH")


def main(argv):
    if len(argv) != 2 or argv[0] not in PROFILES:
        raise ValueError("UNKNOWN_SHELF_PROFILE")
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("SHELF_REQUIRES_ROOT_MAINTENANCE")
    profile = PROFILES[argv[0]]
    anchor = protected_directory(profile.parent, True)
    try:
        return provision(profile, argv[1], anchor)
    finally:
        os.close(anchor)


if __name__ == "__main__":
    import sys
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise RuntimeError("FIXED_ISOLATED_MAINTENANCE_INVOCATION_REQUIRED")
    sys.stdout.buffer.write(main(sys.argv[1:]))
