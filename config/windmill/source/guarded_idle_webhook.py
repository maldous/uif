"""No-argument private Windmill entry for one fixed guarded idle controller."""

import os
from uuid import UUID


RUNNER_PATH = "u/admin/foo_root_jobs"
RECEIVER_COMMAND = ("/usr/bin/python3.12 -I -S -B "
                    "/var/lib/uif-stokowski/idle-upgrade/private_windmill_idle_receiver.py ")


def bind_foo_transport(wmill_client):
    """Use the existing owner-only root bridge for one immutable command."""
    def transport(run_id):
        if type(run_id) is not str or len(run_id) != 36 or str(UUID(run_id)) != run_id:
            raise PermissionError("INVALID_PRIVATE_IDLE_AUDIT_ID")
        host_job = "uif-idle-webhook-" + run_id
        # Keep both original identities. This is submission, not an idle result.
        # No SDK wait timeout (which can cancel), retries or cleanup of custody.
        child = wmill_client.run_script_by_path_async(RUNNER_PATH, args={
            "action": "run", "job_id": host_job, "command": RECEIVER_COMMAND + run_id,
            "cwd": "/", "timeout_seconds": 60,
        })
        return {"windmill_run_id": run_id, "runner_run_id": child, "host_job_id": host_job,
                "state": "submitted"}
    return transport


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
