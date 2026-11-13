"""Constrain the ordinary controller grant to sealed neutral launches."""
from contextlib import nullcontext
from dataclasses import asdict
import os
import sqlite3
import sys

from uif import role_broker as broker

from ordinary_start_window import check_window, new_work
from project_binding import project_scope


def main():
    args = sys.argv[1:]
    if (not sys.flags.isolated or os.getuid() != 0 or os.geteuid() != 0
            or len(args) != 2 or args[0] not in {'start', 'observe', 'cancel'}
            or not broker._sha(args[1])):
        return 2
    os.umask(0o077)
    try:
        with new_work() if args[0] == "start" else nullcontext():
            cfg = broker._policy()
            directory = broker._dir(broker._path(cfg['controller_root']) / args[1],
                                    cfg['controller_uid'], 0o077)
            request = broker._request(directory, cfg['controller_uid'])
            if not isinstance(request, broker.NeutralLaunchRequest) or 'neutral' not in cfg['roles']:
                return 2
            if request.binding['job_id'] in ('uif-neutral-epoch1-proof-io', 'uif-neutral-epoch1-proof-cancel', 'uif-neutral-epoch1-proof-io-recovery1', 'uif-neutral-epoch1-proof-io-recovery2'):
                return 2
            if args[0] == "start":
                if not project_scope(request.binding):
                    return 2
                check_window()
            result = broker._handle_original(
                cfg, args[0], directory, request, release_gate=new_work if args[0] == "start" else None,
                project_work=True)
        observation = broker._neutral_observation(cfg, directory, request, result)
        print(broker._wire({'schema': broker.SCHEMA, **asdict(observation)}).decode())
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return 2
    return 0
