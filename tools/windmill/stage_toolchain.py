"""Stage one pinned public Go archive, without download, extraction or execution.

This is an operator-invoked provisioning utility, not a service or Foo endpoint.
Its fixed protected destination must already exist under separately authorized
root provisioning. A source checkout does not authorize running it. Existing
bytes and permissions are never repaired or replaced. A failed/uncertain write
must be reconciled at the same fixed destination before another host action.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import sys


ARCHIVE_NAME = "go1.27.4.linux-amd64.tar.gz"
ARCHIVE_SHA256 = "7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840"
MAX_ARCHIVE_BYTES = 256 << 20
DIRECTORY = Path("/opt/morgan-toolchains/archives")
REFUSAL = "Pinned archive staging failed or is uncertain; reconcile the fixed destination."


def require(value):
    if not value:
        raise ValueError(REFUSAL)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_archive(path):
    """Hold and verify an authorized existing regular file before any write."""
    path = path.absolute()
    require(path.resolve(strict=True) == path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        # The existing Foo source custodian is the helper's fixed 'user'
        # account. Root staging may read its separately authorized archive;
        # this is not permission to chown or relax the protected source.
        source_uid = pwd.getpwnam("user").pw_uid
        require(before.st_uid in (0, source_uid) and not before.st_mode & 0o022)
        require(0 < before.st_size <= MAX_ARCHIVE_BYTES)
        data = stream.read(MAX_ARCHIVE_BYTES + 1)
        require(len(data) == before.st_size)
        require(identity(before) == identity(os.fstat(stream.fileno())))
        require(identity(before) == identity(path.stat(follow_symlinks=False)))
    require(hashlib.sha256(data).hexdigest() == ARCHIVE_SHA256)
    return data


def open_directory():
    """Anchor each fixed ancestor to a protected root-owned directory handle."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in DIRECTORY.parts[1:]:
            current = os.fstat(descriptor)
            require(stat.S_ISDIR(current.st_mode) and current.st_uid == 0)
            require(not current.st_mode & 0o022)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        current = os.fstat(descriptor)
        require(stat.S_ISDIR(current.st_mode) and current.st_uid == 0)
        require(not current.st_mode & 0o022)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def existing_archive(directory, data):
    """A verified existing destination is a read-only reconciliation, not replay."""
    try:
        descriptor = os.open(ARCHIVE_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0)
        require(before.st_nlink == 1 and not before.st_mode & 0o077)
        require(before.st_size == len(data))
        content = stream.read(MAX_ARCHIVE_BYTES + 1)
        require(content == data)
        require(identity(before) == identity(os.fstat(stream.fileno())))
        require(identity(before) == identity(os.stat(ARCHIVE_NAME, dir_fd=directory,
                                                    follow_symlinks=False)))
    return True


def publish_archive(directory, data):
    """Publish exactly one new file by exclusive link, never overwrite or extract."""
    name = ".go-archive-stage-" + secrets.token_hex(16)
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
            require(stat.S_ISREG(written.st_mode) and written.st_nlink == 1)
            require(written.st_uid == 0 and not written.st_mode & 0o077)
            require(written.st_size == len(data))
            require(identity(written) == identity(os.stat(name, dir_fd=directory,
                                                        follow_symlinks=False)))
        os.link(name, ARCHIVE_NAME, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
    finally:
        # This unpredictable exclusively created name is the only removable
        # object. Never delete the final destination or the original source.
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    require(existing_archive(directory, data))


def stage(path):
    require(os.name == "posix" and os.geteuid() == 0)
    data = read_archive(path)
    directory = open_directory()
    try:
        existed = existing_archive(directory, data)
        if not existed:
            publish_archive(directory, data)
        return {"version": 1, "state": "already-staged" if existed else "staged-not-installed",
                "archive": ARCHIVE_NAME, "sha256": ARCHIVE_SHA256, "bytes": len(data),
                "helper_changed": False, "tool_executed": False, "source_removed": False}
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = stage(args.archive)
    except (OSError, ValueError, TypeError, KeyError):
        print(REFUSAL, file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
