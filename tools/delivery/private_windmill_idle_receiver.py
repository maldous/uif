#!/usr/bin/env python3
"""Fixed private Foo receiver. Its only argument is an audit run UUID."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
from uuid import UUID

ROOT = Path("/var/lib/uif-stokowski/idle-upgrade")
CONTROLLER_SHA256 = "d95a7e181fa3c16d8cc088988438e0684161e0e91b89fe008bb05d06f3b7bfcc"


def read(name, maximum):
    for parent in (ROOT, *ROOT.parents):
        info = parent.lstat()
        if info.st_uid != 0 or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise PermissionError("unprotected private idle receiver path")
    fd = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or not 0 < info.st_size <= maximum):
            raise PermissionError("unprotected private idle receiver input")
        raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("private idle receiver input too large")
        return raw


def main(args):
    if (os.getresuid() != (0, 0, 0) or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or len(args) != 1):
        raise PermissionError("expected fixed isolated private root receiver")
    job = UUID(args[0]) if type(args[0]) is str and len(args[0]) == 36 else None
    if job is None or not job.int or str(job) != args[0]:
        raise PermissionError("invalid private Windmill audit UUID")
    source = read("guarded_service_upgrade.py", 128000)
    if hashlib.sha256(source).hexdigest() != CONTROLLER_SHA256:
        raise PermissionError("reviewed private controller changed")
    controller = ModuleType("guarded_service_upgrade")
    controller.__file__ = str(ROOT / "guarded_service_upgrade.py")
    sys.modules[controller.__name__] = controller
    exec(compile(source, controller.__file__, "exec"), controller.__dict__)
    pins = controller.record(read("private-windmill-idle-pins.json", 4096),
        ("operation_id", "source_head", "review_sha256", "bundle_sha256", "selection_sha256"))
    return controller.bind_private_webhook_idle(controller.FixedWebhookIdleStart(**pins))(args[0])


if __name__ == "__main__":
    sys.stdout.write(json.dumps(main(sys.argv[1:]), sort_keys=True, separators=(",", ":")) + "\n")
