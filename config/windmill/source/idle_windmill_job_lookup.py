"""Source candidate for reading the actual Windmill job, only inside fixed code.

The deployed composition supplies its default authenticated wmill.Windmill
client; job token stays with its SDK. No generic Foo command or dispatch here.
"""

from datetime import datetime, timezone
import os
from uuid import UUID

from idle_platform_approval import CurrentJob, SCOPE


def _started(value):
    if type(value) is not str or not 1 <= len(value) <= 60:
        raise ValueError("MISSING_SERVER_JOB_START")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("MISSING_SERVER_JOB_TIME_ZONE")
    return int(parsed.astimezone(timezone.utc).timestamp())


def bind_server_lookup(authenticated_client, exact_operation):
    """Zero-argument in-runnable lookup; exact_operation is protected code data."""
    def current_job():
        job_id = os.environ.get("WM_JOB_ID")
        try:
            canonical = str(UUID(job_id))
        except (TypeError, ValueError, AttributeError):
            raise PermissionError("CURRENT_WINDMILL_CONTEXT_UNAVAILABLE") from None
        if canonical != job_id or os.environ.get("WM_WORKSPACE") != SCOPE[0]:
            raise PermissionError("CURRENT_WINDMILL_CONTEXT_UNAVAILABLE")
        raw = authenticated_client.get_job(job_id)
        if (type(raw) is not dict or raw.get("id") != job_id
                or raw.get("workspace_id") != SCOPE[0]
                or raw.get("type") != "QueuedJob" or raw.get("job_kind") != "script"
                or raw.get("running") is not True or raw.get("canceled") is not False
                or raw.get("args") != {} or raw.get("parent_job") is not None
                # Only affirmative server provenance qualifies; omitted/null
                # trigger_kind is not evidence of an interactive launch.
                or raw.get("trigger_kind") != "ui"
                or raw.get("is_flow_step") is not False
                or raw.get("is_retry") is not False
                or any(raw.get(field) is not None
                       for field in ("trigger_path", "trigger", "schedule_path"))):
            raise PermissionError("UNQUALIFIED_SERVER_WINDMILL_JOB")
        issued = _started(raw.get("started_at"))
        return CurrentJob(job_id, raw.get("script_path"), raw.get("script_hash"),
                          raw.get("permissioned_as"), raw.get("created_by"), True,
                          raw["job_kind"], exact_operation, issued, issued + 60)
    return current_job
