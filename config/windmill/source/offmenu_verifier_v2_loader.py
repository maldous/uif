"""Use the reviewed fixed OffMenu V2 shelf inside the protected owner process."""
from contextlib import contextmanager
from importlib import import_module
import os
from pathlib import Path
import re
import stat
import sys
from typing import NamedTuple

from verifier_shelf_provision import PROFILES, protected_directory, verify_tree

PROFILE = PROFILES["offmenu-v2"]
SHELF = PROFILE.parent / PROFILE.shelf
MODULE_NAMES = ("offmenu_native_upgrade_ingress", "native_harness_package",
                "native_harness_upgrade")
SHA256 = re.compile(r"[a-f0-9]{64}")


class LoadedOffMenuV2(NamedTuple):
    release_sha256: str
    ingress: object
    package: object
    upgrade: object


def _fixed_private_process():
    return (os.getresuid() == (0, 0, 0) and sys.flags.isolated
            and sys.flags.no_site and sys.dont_write_bytecode)


def _shelf_names(paths):
    names = set()
    for path in paths:
        for item in path.iterdir():
            name = item.name.split(".", 1)[0]
            if name.isidentifier() and (item.is_dir() or item.suffix in (".py", ".so")):
                names.add(name)
    return names


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


@contextmanager
def open_fixed_offmenu_v2(expected_release_sha256):
    """Hold the verified import graph for one existing protected operation.

    Preparation uses the reviewed compiled profile digest; installation uses
    the digest from existing authenticated owner approval. This helper does
    not issue approval or accept a Windmill request. Keep lazy imports within
    this context, then exit the dedicated interpreter.
    """
    if not _fixed_private_process():
        raise PermissionError("OFFMENU_V2_PRIVATE_PROCESS_REQUIRED")
    if (type(expected_release_sha256) is not str
            or not SHA256.fullmatch(expected_release_sha256)
            or expected_release_sha256 != PROFILE.release):
        raise ValueError("OFFMENU_V2_RELEASE_PIN_REQUIRED")
    fd = protected_directory(SHELF, True)
    try:
        selected = _identity(os.fstat(fd))

        def still_selected():
            if _identity(os.stat(SHELF, follow_symlinks=False)) != selected:
                raise RuntimeError("OFFMENU_V2_SHELF_CHANGED")

        still_selected()
        verify_tree(SHELF, expected_release_sha256, 0)
        still_selected()
        delivery, deps = SHELF / "tools/delivery", SHELF / "deps"
        protected = (delivery, deps)
        if sys.modules.keys() & _shelf_names(protected):
            raise ImportError("OFFMENU_V2_MIXED_MODULE_GRAPH")
        original = sys.path[:]
        roots = (Path(sys.base_prefix).resolve(), Path(sys.base_exec_prefix).resolve())
        system = [entry for entry in original
                  if entry and Path(entry).is_absolute()
                  and any(Path(entry).resolve().is_relative_to(root) for root in roots)]
        sys.path[:] = [*map(str, protected), *system]
        try:
            modules = []
            for name in MODULE_NAMES:
                module = import_module(name)
                if getattr(module, "__file__", None) != str(delivery / (name + ".py")):
                    raise ImportError("OFFMENU_V2_WRONG_MODULE_ORIGIN")
                modules.append(module)
            if modules[0].CODE != SHELF:
                raise ImportError("OFFMENU_V2_WRONG_INGRESS_SHELF")
            still_selected()
            yield LoadedOffMenuV2(expected_release_sha256, *modules)
        finally:
            sys.path[:] = original
    finally:
        os.close(fd)
