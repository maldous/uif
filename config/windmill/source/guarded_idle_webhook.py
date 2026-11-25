"""No-argument private Windmill entry for one fixed guarded idle controller."""

import os
from uuid import UUID


def bind_webhook(private_transport):
    """Deploy only with the existing authenticated endpoint ACL and fixed carrier."""
    def main():
        if private_transport is None or os.environ.get("WM_WORKSPACE") != "foo":
            raise PermissionError("PRIVATE_IDLE_WEBHOOK_UNBOUND")
        raw = os.environ.get("WM_JOB_ID")
        try:
            job = UUID(raw) if type(raw) is str and len(raw) == 36 else None
        except ValueError:
            job = None
        if job is None or not job.int or str(job) != raw:
            raise PermissionError("CURRENT_PLATFORM_RUN_ID_REQUIRED")
        # The ACL authorizes the fixed action. This UUID is audit context only.
        # The native controller owns the durable original operation.
        return private_transport(raw)
    return main


main = bind_webhook(None)
