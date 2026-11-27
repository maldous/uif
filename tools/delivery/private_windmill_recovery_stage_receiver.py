#!/usr/bin/env python3
"""Fixed maintenance receiver for staging only, never service activation."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType

ROOT = Path("/var/lib/uif-stokowski/idle-upgrade")
SOURCES = (
    ("guarded_service_upgrade", "d95a7e181fa3c16d8cc088988438e0684161e0e91b89fe008bb05d06f3b7bfcc"),
    ("install_guarded_idle_bootstrap", "b78eb955af9b680bb19880268fbeb917c36ce3c038916845c22ab211a9b11628"),
    ("install_fresh_idle_recovery_stage", "2da0a54e1eb3bda72862bfa6e207c442c4a6bfbde66f2be2a627863ec4bdcda9"),
)
PINS = "private-windmill-recovery-stage-pins.json"
FIELDS = ("operation_id", "prior_dropin_sha256", "retained_selector_sha256", "members")


def read(name, maximum):
    for parent in (ROOT, *ROOT.parents):
        info = parent.lstat()
        if info.st_uid != 0 or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise PermissionError("unprotected private recovery stage path")
    fd = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or not 0 < info.st_size <= maximum):
            raise PermissionError("unprotected private recovery stage input")
        raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("private recovery stage input too large")
        return raw


def main(args):
    if (os.getresuid() != (0, 0, 0) or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or args):
        raise PermissionError("expected isolated fixed root staging receiver with no arguments")

    # Read and authenticate the entire fixed implementation before loading any
    # module. The protected deployment configuration supplies only release pins.
    inputs = []
    for name, expected in SOURCES:
        raw = read(name + ".py", 128000)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise PermissionError("reviewed recovery stage source changed")
        inputs.append((name, raw))
    pins_raw = read(PINS, 4096)

    loaded = {}
    for name, raw in inputs:
        module = ModuleType(name)
        module.__file__ = str(ROOT / (name + ".py"))
        sys.modules[name] = loaded[name] = module
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
    guard = loaded["guarded_service_upgrade"]
    installer = loaded["install_fresh_idle_recovery_stage"]
    pins = guard.record(pins_raw, FIELDS)
    members = pins.pop("members")
    guard.require(type(members) is dict and members.keys() == set(installer.MEMBERS),
                  "expected exact recovery stage member pins")
    return installer.stage(installer.RecoveryStageApproval(
        **pins, member_sha256=tuple(members[name] for name in installer.MEMBERS)))


if __name__ == "__main__":
    sys.stdout.write(json.dumps(main(sys.argv[1:]), sort_keys=True, separators=(",", ":")) + "\n")
