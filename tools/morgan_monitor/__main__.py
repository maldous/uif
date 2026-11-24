"""python3 -m tools.morgan_monitor --help"""
import argparse
import json
import signal
import sqlite3
import sys

from .collector import Collector, ConfigurationError, load_config
from .events import EventError
from .mcp import serve
from .store import StoreError, read_snapshot
from .transport import TransportError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private read-only metadata monitor; delivery disabled unless explicitly configured.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect-once", "run", "check-config"):
        commands.add_parser(name).add_argument("--config", required=True)
    for name in ("outbox-status", "snapshot", "mcp"):
        commands.add_parser(name).add_argument("--state", required=True)
    args = parser.parse_args(argv)
    collector = None
    try:
        if args.command in {"outbox-status", "snapshot"}:
            snapshot = read_snapshot(args.state)
            result = snapshot["outbox"] if args.command == "outbox-status" else snapshot
        elif args.command == "mcp":
            serve(args.state, sys.stdin, sys.stdout)
            return 0
        else:
            config = load_config(args.config)
            if args.command == "check-config":
                result = {"valid": True, "transportConfigured": config["receiver"]["enabled"],
                          "cloudFeedConfigured": False}
            else:
                collector = Collector(config)
                if args.command == "run":
                    stopping = [False]
                    def stop(signum, frame):
                        stopping[0] = True
                    signal.signal(signal.SIGTERM, stop)
                    signal.signal(signal.SIGINT, stop)
                    collector.run(stop_requested=lambda: stopping[0], on_result=lambda result: print(json.dumps(result, sort_keys=True), flush=True))
                    return 0
                result = collector.collect_once()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ConfigurationError, StoreError, EventError, TransportError) as exc:
        # Only reviewed fixed codes are ever printed. No traceback/source data.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError):
        print('{"error":"collector_unavailable"}', file=sys.stderr)
        return 2
    finally:
        if collector is not None:
            collector.close()


if __name__ == "__main__":
    raise SystemExit(main())
