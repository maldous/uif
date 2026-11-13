"""Verify and acquire a selected Git source using the existing bundle transport."""

from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


class SourceHold(ValueError):
    """Source evidence is unavailable, has drifted, or needs reconciliation."""


def exact_path(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve():
        raise SourceHold("source_path_alias")
    return path


def oid(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{40}", value) is not None


def branch_name(value):
    return (isinstance(value, str) and re.fullmatch(r"(?:feature|bugfix)/[a-z0-9][a-z0-9/-]{0,159}", value)
            and not value.endswith("/") and "//" not in value)


def file_digest(path):
    path = exact_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o022):
            raise SourceHold("source_evidence_custody")
        result = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SourceHold("source_evidence_drift")
        return result


def git(root, *args):
    env = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
    }
    try:
        return subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "gc.auto=0", "-c", "maintenance.auto=false",
             "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
             "-C", str(root), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, timeout=90,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as error:
        raise SourceHold("source_git_command") from error


def _raise_walk_error(error):
    raise error


def require_local_git_storage(root):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    indirections = {(".", "commondir"), ("./objects/info", "alternates")}
    try:
        metadata = os.open(root / ".git", flags)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise SourceHold("source_repository_root") from error
    try:
        try:
            objects = os.open("objects", flags, dir_fd=metadata)
        except (FileNotFoundError, NotADirectoryError) as error:
            raise SourceHold("source_shared_git_storage") from error
        os.close(objects)
        for parent, directories, files, directory in os.fwalk(
                ".", dir_fd=metadata, follow_symlinks=False, onerror=_raise_walk_error):
            for name in (*directories, *files):
                if ((parent, name) in indirections
                        or stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)):
                    raise SourceHold("source_shared_git_storage")
    finally:
        os.close(metadata)


def repository_identity(root, head, tree, ref):
    root = exact_path(root)
    require_local_git_storage(root)
    if git(root, "rev-parse", "--show-toplevel") != str(root):
        raise SourceHold("source_repository_root")
    if (git(root, "rev-parse", "--verify", ref) != head
            or git(root, "cat-file", "-t", head) != "commit"
            or git(root, "rev-parse", head + "^{tree}") != tree):
        raise SourceHold("source_ref_head_tree")


def _checkout_custody(root, *, normalize=False):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for _, _, names, directory in os.fwalk(".", dir_fd=fd, follow_symlinks=False, onerror=_raise_walk_error):
            if os.fstat(directory).st_uid != os.geteuid():
                raise SourceHold("workspace_owner")
            if normalize:
                os.fchmod(directory, 0o700)
                try:
                    os.removexattr(directory, "system.posix_acl_default")
                except OSError as error:
                    if error.errno != errno.ENODATA:
                        raise
            if os.fstat(directory).st_mode & 0o022:
                raise SourceHold("workspace_permissions")
            try:
                os.getxattr(directory, "system.posix_acl_default")
            except OSError as error:
                if error.errno != errno.ENODATA:
                    raise
            else:
                raise SourceHold("workspace_default_acl")
            for name in names:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise SourceHold("workspace_file_type")
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    info = os.fstat(child)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1):
                        raise SourceHold("workspace_file_custody")
                    if normalize:
                        os.fchmod(child, stat.S_IMODE(info.st_mode) & 0o700)
                        os.fsync(child)
                    if os.fstat(child).st_mode & 0o022:
                        raise SourceHold("workspace_file_permissions")
                finally:
                    os.close(child)
            if normalize:
                os.fsync(directory)
    finally:
        os.close(fd)


def verify_checkout(root, head, tree, branch):
    if not oid(head) or not oid(tree) or not branch_name(branch):
        raise SourceHold("workspace_source_identity")
    root = exact_path(root)
    _checkout_custody(root)
    repository_identity(root, head, tree, "refs/heads/" + branch)
    if (git(root, "symbolic-ref", "HEAD") != "refs/heads/" + branch
            or git(root, "rev-parse", "HEAD") != head
            or git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all")):
        raise SourceHold("workspace_requires_reconciliation")


@contextmanager
def _destination_lock(parent):
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SourceHold("workspace_parent_custody")
        # The parent must already exist in the qualified runtime. Hold its
        # inode for the copy and no-replace publication, without another store.
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield directory
    finally:
        os.close(directory)


def acquire_checkout(repository, destination, *, head, tree, reference, branch, recheck):
    """Use an already-qualified local ref. Existing workspaces are read-only."""
    repository, destination = exact_path(repository), exact_path(destination)
    if (not oid(head) or not oid(tree) or not branch_name(branch)
            or not isinstance(reference, str) or not re.fullmatch(r"refs/[a-zA-Z0-9/_-]+", reference)
            or repository.is_relative_to(destination) or destination.is_relative_to(repository)):
        raise SourceHold("source_acquisition_binding")
    recheck()
    repository_identity(repository, head, tree, reference)
    with _destination_lock(destination.parent) as target:
        if destination.exists():
            verify_checkout(destination, head, tree, branch)
            recheck()
            return False
        with tempfile.TemporaryDirectory(prefix=".uif-source-", dir=destination.parent) as preparation:
            directory = Path(preparation)
            bundle = directory / "selected.bundle"
            work = directory / "workspace"
            git(repository, "bundle", "create", str(bundle), reference)
            work.mkdir(mode=0o700)
            git(work, "init", "--quiet", "--template=")
            git(work, "fetch", "--no-tags", "--no-write-fetch-head", str(bundle),
                reference + ":refs/heads/" + branch)
            git(work, "checkout", "--quiet", branch)
            _checkout_custody(work, normalize=True)
            verify_checkout(work, head, tree, branch)
            recheck()
            repository_identity(repository, head, tree, reference)
            current = destination.parent.stat()
            parent = os.fstat(target)
            if (current.st_dev, current.st_ino) != (parent.st_dev, parent.st_ino):
                raise SourceHold("workspace_parent_drift")
            rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
            if rename is None:
                raise SourceHold("workspace_noreplace_unavailable")
            if rename(-100, os.fsencode(work), target, os.fsencode(destination.name), 1):
                raise OSError(ctypes.get_errno(), "workspace no-replace rename refused")
            os.fsync(target)
            return True
