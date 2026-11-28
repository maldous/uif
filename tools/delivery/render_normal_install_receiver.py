"""Render a fixed normal install receiver for the existing protected owner release."""

from hashlib import sha256
import re


# Load dependencies first, with imports restricted to these sibling bytes and
# the isolated interpreter's standard library.
SOURCE_MODULES = (
    "guarded_service_upgrade",
    "install_guarded_idle_bootstrap",
    "commissioned_normal_service",
    "read_normal_effective_state",
    "stage_commissioned_normal_runtime",
    "commit_commissioned_normal_selection",
    "observe_guarded_normal_preimage",
    "install_commissioned_normal_selection",
)
MAX_SOURCE = 128_000


def render_normal_install_receiver(sources, original_sha256):
    """Bind reviewed sibling bytes and the independently retained original pin.

    The renderer consumes no owner files. The protected owner supplies the
    canonical original and the three exact artifacts it names when mounting
    the independent release.
    """
    if type(original_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", original_sha256):
        raise ValueError("expected independent normal selection original pin")
    if type(sources) is not dict or sources.keys() != set(SOURCE_MODULES):
        raise ValueError("expected exact normal installer source modules")

    pins = []
    for module in SOURCE_MODULES:
        raw = sources[module]
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_SOURCE:
            raise ValueError("invalid reviewed normal installer source bytes")
        compile(raw, module + ".py", "exec", dont_inherit=True)
        pins.append((module, sha256(raw).hexdigest()))

    source_lines = "\n".join(f"    ({module!r}, {pin!r})," for module, pin in pins)
    rendered = (
        "#!/usr/bin/env python3\n"
        '"""Fixed normal installer receiver in the protected maintenance release."""\n'
        f"SOURCES = (\n{source_lines}\n)\n"
        f"ORIGINAL_SHA256 = {original_sha256!r}\n"
        f"MAX_SOURCE = {MAX_SOURCE}\n"
        + _RECEIVER
    ).encode("utf-8")
    compile(rendered, "private_windmill_normal_install_receiver.py", "exec", dont_inherit=True)
    return rendered


_RECEIVER = '''
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType


ROOT = Path("/var/lib/uif-stokowski/idle-upgrade/normal-install-v1")


@contextmanager
def _protected_directory():
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in ROOT.parts[1:]:
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise PermissionError("unprotected normal installer directory")
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError("unprotected normal installer directory")
        yield fd
    finally:
        os.close(fd)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_pinned(directory, name, maximum, expected):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (before.st_uid != 0 or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o022 or not 0 < before.st_size <= maximum):
            raise PermissionError("unprotected normal installer input")
        raw = stream.read(before.st_size + 1)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (len(raw) != before.st_size
                or _identity(before) != _identity(os.fstat(stream.fileno()))
                or _identity(before) != _identity(named)):
            raise PermissionError("normal installer input changed during read")
    if sha256(raw).hexdigest() != expected:
        raise PermissionError("normal installer input pin differs")
    return raw


def _load_modules(inputs):
    loaded = {}
    for name, raw in inputs:
        module = ModuleType(name)
        module.__file__ = str(ROOT / (name + ".py"))
        sys.modules[name] = loaded[name] = module
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
    return loaded


def main():
    if (sys.argv[1:] or os.getresuid() != (0, 0, 0) or not sys.flags.isolated
            or not sys.flags.no_site or not sys.flags.dont_write_bytecode):
        raise PermissionError("expected isolated fixed root normal installer with no arguments")

    with _protected_directory() as directory:
        inputs = [(name, _read_pinned(directory, name + ".py", MAX_SOURCE, expected))
                  for name, expected in SOURCES]
        raw = _read_pinned(directory, "selection-original.json", MAX_SOURCE, ORIGINAL_SHA256)
        loaded = _load_modules(inputs)
        transaction = loaded["commit_commissioned_normal_selection"]
        staging = loaded["stage_commissioned_normal_runtime"]
        guard = loaded["guarded_service_upgrade"]
        normal = loaded["commissioned_normal_service"]
        original = transaction.decode_selection_original(raw, ORIGINAL_SHA256)
        archive = _read_pinned(directory, "bundle.tar", staging.MAX_ARCHIVE,
                               original.staged.archive_sha256)
        selector = _read_pinned(directory, "selection.json", guard.MAX_RECORD,
                                original.approved_normal_selector_sha256)
        dropin = _read_pinned(directory, normal.DROPIN, guard.MAX_RECORD,
                              original.approved_normal_dropin_sha256)
    installer = loaded["install_commissioned_normal_selection"]
    return installer._stage_and_select_from_trusted_owner(
        lambda: (original, archive, selector, dropin))


if __name__ == "__main__":
    sys.stdout.write(json.dumps(main(), sort_keys=True, separators=(",", ":")) + "\\n")
'''
