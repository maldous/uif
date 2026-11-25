#!/usr/bin/bash
# Execute these exact reviewed bytes inside the authenticated root maintenance route.
set -euo pipefail
[[ $# == 0 ]] || exit 64
exec /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3.12 -I -S -B - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType

ROOT = Path("/var/lib/uif-stokowski/idle-bootstrap-input")
GUARD = "d95a7e181fa3c16d8cc088988438e0684161e0e91b89fe008bb05d06f3b7bfcc"
INSTALL = "b78eb955af9b680bb19880268fbeb917c36ce3c038916845c22ab211a9b11628"


def read(name, bound):
    for path in (ROOT, *ROOT.parents):
        state = path.lstat()
        if not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or state.st_mode & 0o022:
            raise PermissionError("unprotected bootstrap input directory")
    fd = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        state = os.fstat(stream.fileno())
        if (not stat.S_ISREG(state.st_mode) or state.st_uid != 0 or state.st_nlink != 1
                or state.st_mode & 0o022 or not 0 < state.st_size <= bound):
            raise PermissionError("unprotected bootstrap input file")
        data = stream.read(bound + 1)
        if len(data) > bound:
            raise PermissionError("overlarge bootstrap input")
        return data


def load(name, data):
    module = ModuleType(name)
    module.__file__ = str(ROOT / (name + ".py"))
    sys.modules[name] = module
    exec(compile(data, module.__file__, "exec"), module.__dict__)
    return module


if os.getresuid() != (0, 0, 0):
    raise PermissionError("protected root bootstrap required")
guard = read("guarded_service_upgrade.py", 128000)
install = read("install_guarded_idle_bootstrap.py", 128000)
if (hashlib.sha256(guard).hexdigest(), hashlib.sha256(install).hexdigest()) != (GUARD, INSTALL):
    raise PermissionError("bootstrap executable source pin mismatch")
controller = load("guarded_service_upgrade", guard)
builder = load("install_guarded_idle_bootstrap", install)
pins = controller.record(read("bootstrap-pins.json", 4096),
    ("operation_id", "controller_sha256", "bundle_sha256", "launcher_sha256", "config_sha256"))
if pins["controller_sha256"] != GUARD:
    raise PermissionError("protected bootstrap config does not bind reviewed controller")
result = builder.install(builder.BootstrapApproval(**pins))
sys.stdout.buffer.write(controller.encode(result) + b"\n")
PY
