"""Bind the current coordinator's qualified context to its selected source."""

from collections.abc import Callable, Mapping
from dataclasses import InitVar, asdict, dataclass
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING
from uuid import UUID
from weakref import ref

from .source import (
    SourceHold, acquire_checkout, branch_name, exact_path, file_digest, git, oid,
    require_local_git_storage, verify_checkout,
)

if TYPE_CHECKING:
    from .task_identity import LedgerTaskEnvelope

LEDGER_DISPATCH_REPORT = "uif-dispatch-report/2"
LEDGER_DISPATCH_INTENT = "uif-dispatch-intent/2"
LEDGER_DISPATCH_INTENT_FIELDS = frozenset({
    "schema", "task", "admission", "operation_id", "attempt", "workspace", "source",
    "source_head", "source_tree", "source_branch", "acquisition_sha256", "accepted_sha256",
    "supervisor_sha256", "requested_model", "requested_effort", "pins", "manifest_sha256",
    "request_sha256", "lease_id", "pool_database", "worker_binding",
})


@dataclass(frozen=True)
class Profile:
    name: str
    project_id: str
    project_slug: str
    repository: str
    origin: str
    accepted_branch: str

    def __post_init__(self):
        try:
            valid_id = str(UUID(self.project_id)) == self.project_id
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if (not valid_id or not re.fullmatch(r"[a-z][a-z0-9-]*", self.name)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.project_slug)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository)
                or not self.origin or not re.fullmatch(r"[a-z][a-z0-9-]*", self.accepted_branch)):
            raise SourceHold("invalid_project_profile")


UIF = Profile("uif", "ffb6c7d9-4eff-46eb-a805-99f0cc2edfa0", "21558318e036",
              "maldous/uif", "https://github.com/maldous/uif.git", "develop")


@dataclass(frozen=True)
class PolledIssue:
    identifier: str
    issue_id: str
    project_id: str
    project_slug: str


@dataclass(frozen=True)
class LedgerAdmission:
    """Normalized facts from the existing owner qualifier, never a queue row parser."""

    store_id: str
    snapshot_sha256: str
    row_id: str
    source_sha256: str
    phase: str
    owner: str
    epoch: str
    original_operation_id: str
    disposition: str
    prior_effect: str
    combined: bool
    counterpart_readiness_sha256: str | None = None

    def require_implementation(self):
        identities = (self.store_id, self.row_id, self.owner, self.epoch, self.original_operation_id)
        if (any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in identities)
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in (self.snapshot_sha256, self.source_sha256))
                or type(self.combined) is not bool):
            raise SourceHold("uif_ledger_admission_binding")
        if self.phase != "implementation" or self.disposition != "ready":
            raise SourceHold("uif_ledger_admission_held")
        if self.prior_effect not in ("confirmed-absent", "reconciled-for-pickup"):
            raise SourceHold("uif_original_operation_requires_reconciliation")
        if self.combined and (not isinstance(self.counterpart_readiness_sha256, str)
                              or re.fullmatch(r"[0-9a-f]{64}", self.counterpart_readiness_sha256) is None):
            raise SourceHold("uif_combined_counterpart_held")


@dataclass(frozen=True)
class QualifiedContext:
    """Output of the existing owner guard and current context qualifier.

    The caller must preserve the original catalog, input-pin, ownership, lease,
    stage, cancellation and provider checks before returning this result.
    """
    runtime: Path
    manifest: Mapping
    source: Path
    ledger: LedgerAdmission | None = None
    task: "LedgerTaskEnvelope | None" = None


@dataclass(frozen=True)
class SelectedSource:
    repository: str
    head: str
    tree: str
    branch: str
    checkout: Path
    accepted_branch: str
    accepted_evidence: Path
    accepted_sha256: str
    acquisition: Path
    acquisition_sha256: str


@dataclass(frozen=True)
class Workspace:
    path: Path
    identifier: str
    created: bool


class SourceBoundary:
    """Existing poller/workspace hook boundary, with no queue or publisher."""

    def __init__(self, *, runtime_root, coordinator_runtime, qualify_context: Callable[[PolledIssue], QualifiedContext],
                 selected_source: Callable[[Profile, PolledIssue], SelectedSource], optional_profiles=()):
        self.runtime_root = exact_path(runtime_root)
        self.coordinator_runtime = exact_path(coordinator_runtime)
        if not self.coordinator_runtime.is_relative_to(self.runtime_root):
            raise SourceHold("coordinator_outside_runtime_root")
        self.qualify_context = qualify_context
        self.selected_source = selected_source
        profiles = (UIF, *optional_profiles)
        self.profiles = {profile.project_id: profile for profile in profiles}
        if (len(self.profiles) != len(profiles)
                or len({profile.project_slug for profile in profiles}) != len(profiles)
                or len({profile.name for profile in profiles}) != len(profiles)
                or len({profile.repository for profile in profiles}) != len(profiles)):
            raise SourceHold("duplicate_project_profile")

    def profile(self, project_id, project_slug):
        profile = self.profiles.get(project_id)
        if profile is None or profile.project_slug != project_slug:
            raise SourceHold("unconfigured_project_route")
        return profile

    def _bind(self, issue, *, require_ledger):
        profile = self.profile(issue.project_id, issue.project_slug)
        try:
            valid_id = str(UUID(issue.issue_id)) == issue.issue_id
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if not valid_id or not re.fullmatch(r"[A-Z][A-Z0-9]*-[1-9][0-9]*", issue.identifier):
            raise SourceHold("issue_identifier")
        context = self.qualify_context(issue)
        ledger = None
        if require_ledger:
            ledger = context.ledger
            if not isinstance(ledger, LedgerAdmission):
                raise SourceHold("uif_ledger_admission_required")
            ledger.require_implementation()
        manifest = context.manifest
        tasks = manifest.get("tasks", {})
        if (set(tasks) != {issue.identifier} or manifest.get("project_id") != profile.project_id
                or tasks[issue.identifier].get("issue_id") != issue.issue_id):
            raise SourceHold("manifest_issue_binding")
        if "stage" in tasks[issue.identifier]:
            raise SourceHold("nonimplementation_workspace_refused")
        return self._bind_source(profile, issue, context, ledger)

    def _bind_ledger_task(self, task):
        from .ledger import ledger_task_target, verify_ledger_task_input

        context = self.qualify_context(task)
        ledger = context.ledger
        if not isinstance(ledger, LedgerAdmission) or context.task != task:
            raise SourceHold("uif_ledger_admission_required")
        ledger.require_implementation()
        identifier = task.key.workspace_key
        tasks = context.manifest.get("tasks")
        if (context.manifest.get("project_id") != UIF.project_id
                or not isinstance(tasks, dict) or set(tasks) != {identifier}):
            raise SourceHold("uif_ledger_task_input_binding")
        verify_ledger_task_input(tasks[identifier], ledger_task_target(task.key, task.source_sha256))
        if "stage" in tasks[identifier]:
            raise SourceHold("nonimplementation_workspace_refused")
        return self._bind_source(UIF, task, context, ledger)

    def _bind_source(self, profile, identity, context, ledger):
        manifest = context.manifest
        selected = self.selected_source(profile, identity)
        runtime, checkout = exact_path(context.runtime), exact_path(selected.checkout)
        acquisition = exact_path(selected.acquisition)
        if (not runtime.is_relative_to(self.runtime_root)
                or not checkout.is_relative_to(self.runtime_root)
                or not acquisition.is_relative_to(self.runtime_root)
                or checkout != exact_path(context.source)
                or selected.repository != profile.repository
                or selected.accepted_branch != profile.accepted_branch
                or not oid(selected.head) or not oid(selected.tree) or not branch_name(selected.branch)
                or manifest.get("base_revision") != selected.head or manifest.get("branch") != selected.branch):
            raise SourceHold("sealed_task_source_binding")
        if (file_digest(selected.accepted_evidence) != selected.accepted_sha256
                or file_digest(acquisition) != selected.acquisition_sha256):
            raise SourceHold("sealed_task_source_evidence")
        return runtime, selected, ledger

    def ensure_workspace(self, issue, workspace_root, *, require_ledger=False):
        return self._ensure_workspace(workspace_root, issue.identifier,
                                      lambda: self._bind(issue, require_ledger=require_ledger))

    def ensure_ledger_workspace(self, task, workspace_root):
        from .task_identity import LedgerTaskEnvelope

        if not isinstance(task, LedgerTaskEnvelope):
            raise SourceHold("uif_ledger_task_claim_required")
        return self._ensure_workspace(workspace_root, task.key.workspace_key,
                                      lambda: self._bind_ledger_task(task))

    def _ensure_workspace(self, workspace_root, identifier, bind):
        runtime, selected, ledger = bind()
        if exact_path(workspace_root) != self.coordinator_runtime / "workspaces":
            raise SourceHold("workspace_outside_coordinator_runtime")
        target = runtime / "workspaces" / identifier

        def recheck():
            if bind() != (runtime, selected, ledger):
                raise SourceHold("workspace_selection_changed")
            verify_checkout(selected.checkout, selected.head, selected.tree, selected.branch)

        created = acquire_checkout(selected.checkout, target, head=selected.head, tree=selected.tree,
                                   reference="refs/heads/" + selected.branch, branch=selected.branch,
                                   recheck=recheck)
        return Workspace(target, identifier, created)


def acquire_uif_task_source(repository, destination, *, head, tree, branch, evidence, sha256, owner_guard):
    """Consume existing accepted UIF facts. The owner supplies acceptance and fencing.

    Product adapters continue to supply their separately acquired sealed sources.
    This path creates no intake grant, accepted ref, or publication.
    """
    repository = exact_path(repository)

    def recheck():
        owner_guard()
        require_local_git_storage(repository)
        if (git(repository, "remote", "get-url", "origin") != UIF.origin
                or file_digest(evidence) != sha256):
            raise SourceHold("accepted_uif_source_drift")

    return acquire_checkout(repository, destination, head=head, tree=tree,
                            reference="refs/heads/" + UIF.accepted_branch, branch=branch, recheck=recheck)


def bind_workspace_receiver(orchestrator, workspace_module, *, boundary, polled_issue, require_ledger=False):
    """Wire workspace acquisition and retain terminal or uncertain work.

    The poller supplies the qualified issue identity. Existing policy
    remains responsible for shell hooks and publication.
    """
    async def ensure_workspace(root, identifier, hooks):
        issue = polled_issue(identifier)
        if issue.identifier != identifier:
            raise SourceHold("workspace_polled_issue_binding")
        result = boundary.ensure_workspace(issue, root, require_ledger=require_ledger)
        return workspace_module.WorkspaceResult(result.path, result.identifier, result.created)

    async def retain_workspace(workspace_root, issue_identifier, hooks):
        # The original workspace stays available for owner reconciliation.
        return None

    orchestrator.ensure_workspace = workspace_module.ensure_workspace = ensure_workspace
    orchestrator.remove_workspace = workspace_module.remove_workspace = retain_workspace


@dataclass(frozen=True)
class DispatchReportBinding:
    issue: PolledIssue | None
    attempt_number: int | None
    workspace: Path
    operation_id: str
    receipt_path: Path
    receipt_sha256: str
    ledger: LedgerAdmission | None = None
    # Existing stage journals use dataclasses.asdict recursively for v1.
    task: InitVar["LedgerTaskEnvelope | None"] = None

    def __post_init__(self, task):
        if task is not None:
            from .ledger import verify_ledger_task_admission

            verify_ledger_task_admission(task, self.ledger)
            self.ledger.require_implementation()
            if (self.issue is not None or self.operation_id != task.original_operation_id
                    or (self.attempt_number is not None
                        and (type(self.attempt_number) is not int or self.attempt_number < 0))
                    or not isinstance(self.workspace, Path) or not isinstance(self.receipt_path, Path)
                    or exact_path(self.workspace).name != task.key.workspace_key
                    or self.workspace.parent.name != "workspaces"
                    or exact_path(self.receipt_path) != self.workspace.parent.parent / "attempts"
                    / task.key.workspace_key / "dispatch-intent.json"
                    or not isinstance(self.receipt_sha256, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", self.receipt_sha256)):
                raise SourceHold("uif_ledger_dispatch_report_binding")
        elif self.issue is None:
            raise SourceHold("uif_ledger_dispatch_report_binding")
        object.__setattr__(self, "task", task)

    def to_record(self):
        record = asdict(self)
        record.update(workspace=str(self.workspace), receipt_path=str(self.receipt_path))
        if self.task is not None:
            del record["issue"]
            record.update(schema=LEDGER_DISPATCH_REPORT, task=self.task.to_record())
        return record

    @classmethod
    def from_ledger_record(cls, record, *, snapshot_path):
        from .task_identity import LedgerTaskEnvelope

        expected = {"schema", "task", "attempt_number", "workspace", "operation_id",
                    "receipt_path", "receipt_sha256", "ledger"}
        if (not isinstance(record, dict) or set(record) != expected
                or record["schema"] != LEDGER_DISPATCH_REPORT
                or not isinstance(record["ledger"], dict)
                or not all(isinstance(record[name], str) for name in ("workspace", "receipt_path"))):
            raise SourceHold("uif_ledger_dispatch_report_shape")
        try:
            admission = LedgerAdmission(**record["ledger"])
        except TypeError as error:
            raise SourceHold("uif_ledger_dispatch_report_shape") from error
        if asdict(admission) != record["ledger"]:
            raise SourceHold("uif_ledger_dispatch_report_shape")
        task = LedgerTaskEnvelope.from_record(record["task"], snapshot_path=snapshot_path)
        workspace = exact_path(record["workspace"])
        receipt = exact_path(record["receipt_path"])
        if str(workspace) != record["workspace"] or str(receipt) != record["receipt_path"]:
            raise SourceHold("uif_ledger_dispatch_report_shape")
        return cls(None, record["attempt_number"], workspace, record["operation_id"],
                   receipt, record["receipt_sha256"], admission, task=task)


def bind_uif_lifecycle(orchestrator, workspace_module, owner, *, boundary, polled_issue, report_attempt):
    """Wire the UIF-owned hooks in the dedicated coordinator before it starts.

    The existing qualifier supplies ledger admission at workspace pickup.
    The returned registrar is called by guarded dispatch after its original
    create-only receipt is written and before worker effects. The platform
    reporter consumes that exact binding and owns effect reconciliation.
    """
    if not callable(report_attempt):
        raise SourceHold("uif_report_boundary_required")

    def uif_issue(identifier):
        selected = polled_issue(identifier)
        if (selected.identifier != identifier
                or (selected.project_id, selected.project_slug) != (UIF.project_id, UIF.project_slug)):
            raise SourceHold("uif_lifecycle_issue_binding")
        return selected

    async def reject_hook(*args, **kwargs):
        raise SourceHold("uif_shell_hook_refused")

    dispatches = {}

    def check_attempt(issue, attempt, workspace_path, binding):
        selected = binding.issue
        if (issue.id != selected.issue_id or issue.identifier != selected.identifier
                or attempt.issue_id != selected.issue_id or attempt.issue_identifier != selected.identifier
                or attempt.attempt != binding.attempt_number
                or exact_path(attempt.workspace_path) != binding.workspace
                or exact_path(workspace_path) != binding.workspace):
            raise SourceHold("uif_report_attempt_binding")
        if file_digest(binding.receipt_path) != binding.receipt_sha256:
            raise SourceHold("uif_report_receipt_drift")

    def register_dispatch(issue, attempt, workspace_path, *, operation_id, receipt_path, receipt_sha256, ledger=None):
        selected = uif_issue(issue.identifier)
        workspace, receipt = exact_path(workspace_path), exact_path(receipt_path)
        if (not isinstance(operation_id, str) or not operation_id.strip() or operation_id != operation_id.strip()
                or not workspace.is_relative_to(boundary.runtime_root)
                or workspace.name != selected.identifier or workspace.parent.name != "workspaces"
                or not receipt.is_relative_to(workspace.parent.parent)
                or (ledger is not None and ledger.original_operation_id != operation_id)):
            raise SourceHold("uif_report_dispatch_binding")
        binding = DispatchReportBinding(selected, attempt.attempt, workspace, operation_id, receipt, receipt_sha256, ledger)
        check_attempt(issue, attempt, workspace, binding)
        key = id(attempt)
        previous = dispatches.get(key)
        if previous is not None and previous[0]() is attempt:
            if previous[1] != binding:
                raise SourceHold("uif_report_dispatch_rebind")
            return binding
        dispatches[key] = (ref(attempt, lambda _: dispatches.pop(key, None)), binding)
        return binding

    async def publish_run_report(issue, attempt, workspace_path, state_name):
        # Reporting failure must not change the worker outcome or cause replay.
        try:
            recorded = dispatches.get(id(attempt))
            if recorded is None or recorded[0]() is not attempt:
                raise SourceHold("uif_report_dispatch_missing")
            binding = recorded[1]
            check_attempt(issue, attempt, workspace_path, binding)
            await report_attempt(binding)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "UIF attempt report unavailable (%s); consult the original operation",
                type(error).__name__,
            )

    bind_workspace_receiver(orchestrator, workspace_module, boundary=boundary,
                            polled_issue=uif_issue, require_ledger=True)
    workspace_module.run_hook = reject_hook
    owner._publish_run_report = publish_run_report
    return register_dispatch
