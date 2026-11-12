#!/usr/bin/python3
"""Fixed entrypoint for an independently qualified ordinary-user forced command."""
import os
import sys

from uif.host_jobs import MAX_WIRE, decode, load_descriptor, wire

DESCRIPTOR = "/etc/uif-stokowski/foo-user-exec.json"
COMMAND = "/usr/local/libexec/foo-user-exec"


def main():
    if (len(sys.argv) != 1 or sys.stdin.isatty() or os.environ.get("SSH_TTY")
            or os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND):
        return 2
    try:
        host = load_descriptor(DESCRIPTOR)
        raw = sys.stdin.buffer.read(MAX_WIRE + 1)
        request = decode(raw)
        if wire(request) != raw:
            return 2
        result = host.handle(request)
    except (OSError, ValueError, KeyError, TypeError):
        return 2
    sys.stdout.buffer.write(wire(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
