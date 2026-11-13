"""Keep the completed finite proof originals outside ordinary execution."""
import io
import os
import runpy
import sys

from uif.host_jobs import MAX_WIRE, decode, wire

RETIRED = ('uif-neutral-epoch1-proof-io', 'uif-neutral-epoch1-proof-cancel', 'uif-neutral-epoch1-proof-io-recovery1', 'uif-neutral-epoch1-proof-io-recovery2')


def admit(raw):
    request = decode(raw)
    return (raw == wire(request) and request.get('job_id') not in RETIRED
            and request.get('action') in {'status', 'cancel'})


def main(entrypoint):
    if len(sys.argv) != 1 or sys.stdin.isatty() or os.environ.get('SSH_TTY'):
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_WIRE + 1)
        if not admit(raw):
            return 2
    except (OSError, ValueError, TypeError):
        return 2
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')
    runpy.run_path(entrypoint, run_name='__main__')
    return 0
