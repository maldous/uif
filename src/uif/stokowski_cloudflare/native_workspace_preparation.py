"""Detached clean checkout preparation; never publish a protected assignment."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from ..effect_authority import SourcePin, pinned_sha
from ..handoff_store import require
from .native_composition import observe_clean_assignment_workspace

_GIT_ENV = {'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_NO_REPLACE_OBJECTS': '1',
            'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_TERMINAL_PROMPT': '0'}


def _git(path, *args):
    return subprocess.check_output(
        ['git', '--no-optional-locks', '-C', str(path), *args],
        timeout=30, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_GIT_ENV,
    )


_BUNDLE_LIMIT = 512 * 1024 * 1024


def _copy_pinned_bundle(bundle, copy, expected):
    # Validation, size and contents refer to the same opened input, even
    # if the basename later changes. O_NONBLOCK avoids a FIFO-open hang.
    fd = os.open(bundle, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(fd, 'rb', closefd=False) as origin:
            initial = os.fstat(origin.fileno())
            require(stat.S_ISREG(initial.st_mode) and 0 < initial.st_size <= _BUNDLE_LIMIT,
                    'authority_detached_bundle_type_or_size')
            digest = hashlib.sha256()
            remaining = initial.st_size
            with copy.open('xb') as output:
                while remaining:
                    part = origin.read(min(remaining, 65536))
                    require(bool(part), 'authority_detached_bundle_changed')
                    remaining -= len(part)
                    digest.update(part)
                    output.write(part)
                require(not origin.read(1), 'authority_detached_bundle_changed')
            final = os.fstat(origin.fileno())
            require((initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns)
                    == (final.st_size, final.st_mtime_ns, final.st_ctime_ns),
                    'authority_detached_bundle_changed')
            require(digest.hexdigest() == expected, 'authority_detached_bundle_digest')
    finally:
        os.close(fd)


def prepare_detached_workspace(root: Path, bundle: Path, bundle_sha256: str, source: SourcePin):
    """Create a new private staging checkout from exclusive, verified local inputs.

    The caller already owns the quiescent source and isolated staging root.
    This never exposes the candidate at the real issue path or binds a claim.
    """
    pinned_sha(bundle_sha256)
    require(type(source) is SourcePin and root.is_absolute() and root.is_dir()
            and root.resolve(strict=True) == root and not root.stat().st_mode & 0o077,
            'authority_detached_workspace_root')
    require(bundle.is_absolute() and bundle.parent.resolve(strict=True) == bundle.parent,
            'authority_detached_bundle_path')
    stage = Path(tempfile.mkdtemp(prefix='.uif-workspace-', dir=root))
    # Preserve the newly created candidate on failure. Do not recursively
    # delete by name or treat a retained partial candidate as retry input.
    copy = stage / '.uif-input.bundle'
    _copy_pinned_bundle(bundle, copy, bundle_sha256)
    _git(stage, 'init', '--template=', '--object-format=sha1')
    head = 'refs/heads/' + source.branch
    require(_git(stage, 'bundle', 'list-heads', str(copy), head).decode().strip()
            == source.head + ' ' + head, 'authority_detached_bundle_ref')
    _git(stage, 'bundle', 'verify', str(copy))
    _git(stage, 'bundle', 'unbundle', str(copy))
    copy.unlink()
    _git(stage, 'update-ref', head, source.head)
    _git(stage, 'symbolic-ref', 'HEAD', head)
    _git(stage, 'config', '--local', 'remote.origin.url', f'https://github.com/{source.repository}.git')
    config = stage / '.git/config'
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    _git(stage, 'reset', '--hard', source.head)
    observation = observe_clean_assignment_workspace(stage, source, config_sha)
    return stage, observation
