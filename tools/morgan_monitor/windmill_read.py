"""Optional private Windmill script adapter, not deployed by this repository.

An operator must explicitly provision a worker with this package and authorized
read-only access to the collector state, then bind a fixed state path server-side.
No caller-provided path, credential, arbitrary query, or source write is accepted.
"""
import os
from .store import StoreError, read_snapshot


def main() -> dict:
    path = os.environ.get("MORGAN_MONITOR_STATE_PATH")
    if not path:
        return {"error": "state_unconfigured"}
    try:
        return read_snapshot(path)
    except StoreError:
        return {"error": "state_unavailable"}
