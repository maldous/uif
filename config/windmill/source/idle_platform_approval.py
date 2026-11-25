"""Pure approval boundary for one guarded Stokowski idle transition.

No tool, environment fallback, external effect, or legacy signature issuer.
Only a private deployed controller may bind the authoritative zero-argument
lookup and protected configuration; this module is not an RPC entrypoint.
"""

from dataclasses import dataclass
import re
from uuid import UUID

SCOPE = ("foo", "stokowski-foo.service", "uif-idle", "uif1313-idle-a22fbb2")
PHASES = frozenset({"installer", "apply", "start"})
HEX = re.compile(r"[0-9a-f]{64}")
SCRIPT_HASH = re.compile(r"(?:[0-9a-f]{16}|[0-9a-f]{64})")


@dataclass(frozen=True)
class IdleOperation:
    workspace: str
    unit: str
    profile: str
    stage: str
    phase: str
    operation_id: str
    review_sha256: str
    bundle_sha256: str
    preflight_sha256: str


@dataclass(frozen=True)
class ApprovedRunnable:
    """Installed from reviewed protected configuration; not a request object."""
    path: str
    script_hash: str
    effective_principal: str
    initiating_actor: str
    operation: IdleOperation


@dataclass(frozen=True)
class CurrentJob:
    """Server-authenticated facts of one actual executing private dispatch."""
    job_id: str
    path: str
    script_hash: str
    effective_principal: str
    initiating_actor: str
    running: bool
    job_kind: str
    operation: IdleOperation
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class PlatformApproval:
    """Exact provenance for an existing guarded controller, not start authority."""
    job_id: str
    operation: IdleOperation
    expires_at: int


def _operation(operation):
    if (type(operation) is not IdleOperation or
            (operation.workspace, operation.unit, operation.profile, operation.stage) != SCOPE or
            operation.phase not in PHASES):
        raise PermissionError("FIXED_IDLE_SCOPE_REFUSED")
    for pin in (operation.operation_id, operation.review_sha256,
                operation.bundle_sha256, operation.preflight_sha256):
        if type(pin) is not str or HEX.fullmatch(pin) is None:
            raise PermissionError("EXACT_IDLE_PIN_REQUIRED")


def qualify(approved, idle_operation, *, current_job, now):
    """Call from private trusted composition while holding the selector lock.

    The controller supplies its existing guarded preflight as idle_operation.
    No result here writes an admission, selects a stage, or consumes a marker.
    """
    if type(approved) is not ApprovedRunnable or not callable(current_job):
        raise PermissionError("WINDMILL_IDLE_BINDING_UNAVAILABLE")
    _operation(idle_operation)
    _operation(approved.operation)
    if (approved.operation != idle_operation or type(approved.path) is not str
            or not approved.path.startswith(("u/", "f/")) or len(approved.path) > 200
            or type(approved.script_hash) is not str
            or SCRIPT_HASH.fullmatch(approved.script_hash) is None
            or type(approved.effective_principal) is not str
            or not approved.effective_principal.startswith(("u/", "g/"))
            or len(approved.effective_principal) <= 2
            or type(approved.initiating_actor) is not str
            or not approved.initiating_actor.isprintable()
            or not 0 < len(approved.initiating_actor) <= 200):
        raise PermissionError("PROTECTED_IDLE_CONFIGURATION_MISMATCH")
    job = current_job()
    if (type(job) is not CurrentJob or job.running is not True
            or job.job_kind != "script"
            or (job.path, job.script_hash, job.effective_principal, job.initiating_actor) !=
                (approved.path, approved.script_hash, approved.effective_principal, approved.initiating_actor)
            or job.operation != idle_operation):
        raise PermissionError("CURRENT_WINDMILL_JOB_MISMATCH")
    try:
        parsed = UUID(job.job_id) if type(job.job_id) is str else None
    except ValueError:
        parsed = None
    if parsed is None or not parsed.int or str(parsed) != job.job_id:
        raise PermissionError("CURRENT_WINDMILL_JOB_ID_INVALID")
    if (type(now) is not int or type(job.issued_at) is not int or
            type(job.expires_at) is not int or
            not job.issued_at <= now < job.expires_at <= job.issued_at + 60):
        raise PermissionError("CURRENT_WINDMILL_JOB_EXPIRED")
    return PlatformApproval(job.job_id, idle_operation, job.expires_at)
