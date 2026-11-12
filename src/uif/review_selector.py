"""Resolve a sealed UIF review selection for the one-attempt launcher."""

from dataclasses import dataclass, fields
from pathlib import Path
import re
import stat
import sys
from uuid import UUID

from .dispatch import _record as read_dispatch_intent, read_outcome
from .review import NativeReviewBackend, ReviewAdapter, ReviewSpec
from .review_launcher import (
    ReviewAttempt, ReviewInvocation, ReviewLauncher, ReviewLaunchSelection, ReviewSettings,
)
from .source import SourceHold, exact_path, file_digest
from .stokowski import DispatchReportBinding, LedgerAdmission, PolledIssue, UIF
from .validation import (
    StageBinding, StageJournal, ValidationAdapter, ValidationSpec, WindmillValidationBackend, digest, identity,
)


def _fields(value, record_type):
    if not isinstance(value, dict) or set(value) != {item.name for item in fields(record_type)}:
        raise SourceHold("uif_review_selector_record_shape")
    return value.copy()


def _path(value):
    if not isinstance(value, str) or not value:
        raise SourceHold("uif_review_selector_path")
    path = exact_path(value)
    if str(path) != value:
        raise SourceHold("uif_review_selector_path")
    return path


def _payload(value):
    if not isinstance(value, str):
        raise SourceHold("uif_review_selector_payload")
    return value.encode()


def _issue(value):
    issue = PolledIssue(**_fields(value, PolledIssue))
    try:
        canonical = str(UUID(issue.issue_id)) == issue.issue_id
    except (ValueError, TypeError, AttributeError):
        canonical = False
    if (not canonical or (issue.project_id, issue.project_slug) != (UIF.project_id, UIF.project_slug)
            or not isinstance(issue.identifier, str)
            or re.fullmatch(r"UIF-[1-9][0-9]*", issue.identifier) is None):
        raise SourceHold("uif_review_selector_issue")
    return issue


def _dispatch(value):
    entry = _fields(value, DispatchReportBinding)
    entry["issue"] = _issue(entry["issue"])
    entry["ledger"] = LedgerAdmission(**_fields(entry["ledger"], LedgerAdmission))
    entry["workspace"] = _path(entry["workspace"])
    entry["receipt_path"] = _path(entry["receipt_path"])
    attempt = entry["attempt_number"]
    if (attempt is not None and (type(attempt) is not int or attempt < 0)
            or not identity(entry["operation_id"])):
        raise SourceHold("uif_review_selector_dispatch")
    return DispatchReportBinding(**entry)


def decode_review_spec(value):
    """Restore the existing stage-journal wire representation into typed pins."""
    review = _fields(value, ReviewSpec)
    validation = _fields(review["validation"], ValidationSpec)
    source = _fields(validation["binding"], StageBinding)
    source["dispatch"] = _dispatch(source["dispatch"])
    for name in ("candidate", "coding_receipt"):
        source[name] = _path(source[name])
    validation["binding"] = StageBinding(**source)
    validation["args_json"] = _payload(validation["args_json"])
    review["validation"] = ValidationSpec(**validation)
    for name in ("policy", "evidence_directory"):
        review[name] = _path(review[name])
    review["pool_binding_json"] = _payload(review["pool_binding_json"])
    return ReviewSpec(**review)


_OWNER_FIELDS = {
    "schema", "store_id", "row_id", "operation_id", "owner", "epoch", "runtime", "issue",
    "policy_path", "policy_sha256", "spec", "installation_sha256", "capacity_policy_sha256",
    "provider_proof_sha256",
}


class ReviewSelector:
    """Resolve only the review sealed by the current UIF owner."""

    def __init__(self, authority, review, settings):
        if not isinstance(review, ReviewAdapter) or not isinstance(settings, ReviewSettings):
            raise SourceHold("uif_review_selector_registration")
        if review.validation.backend.registration.get("workspace") != "foo":
            raise SourceHold("uif_review_selector_windmill_workspace")
        self.authority = authority
        self.review = review
        self.settings = settings

    def select(self, invocation):
        if not isinstance(invocation, ReviewInvocation):
            raise SourceHold("uif_review_selector_invocation")
        record = self.authority.review_record(invocation)
        if (not isinstance(record, dict) or set(record) != _OWNER_FIELDS
                or record["schema"] != "uif-owner-review/1"
                or (record["runtime"], record["issue"], record["policy_path"], record["policy_sha256"])
                != (str(invocation.runtime), invocation.issue, str(invocation.policy), invocation.policy_sha256)):
            raise SourceHold("uif_review_selector_sealed_record")
        spec = decode_review_spec(record["spec"])
        invocation.require(spec)
        binding = spec.binding
        admission = binding.dispatch.ledger
        if (tuple(record[k] for k in ("store_id", "row_id", "operation_id", "owner", "epoch"))
                != (admission.store_id, admission.row_id, admission.original_operation_id,
                    admission.owner, admission.epoch)):
            raise SourceHold("uif_review_selector_owner_binding")
        backend = self.review.backend
        outcome = read_outcome(binding.dispatch)
        if (outcome.status != "succeeded" or outcome.lease_id == spec.lease
                or (outcome.worker_receipt_path, outcome.worker_receipt_sha256)
                != (binding.coding_receipt, binding.coding_receipt_sha256)):
            raise SourceHold("uif_review_selector_original_coding")
        intent, _ = read_dispatch_intent(binding.dispatch.receipt_path, binding.dispatch.receipt_sha256)
        if intent.get("pool_database") != str(backend.pool_database):
            raise SourceHold("uif_review_selector_original_pool")
        # Reuse the launcher qualification. It reads the exact successful
        # validation journal entry, candidate bytes, policy and author receipts.
        ReviewAttempt(spec, settings=self.settings, backend=backend,
                      validation=self.review.validation).qualify()
        return ReviewLaunchSelection(spec, record["installation_sha256"], record["capacity_policy_sha256"],
                                     record["provider_proof_sha256"])


class _ExistingStageJournal(StageJournal):
    """Attach to the coordinator's existing journal without creating state."""

    def __init__(self, runtime):
        self.root = exact_path(runtime / "operations")
        self.path = exact_path(self.root / "operations.sqlite")
        for path in (self.root.parent, self.root, self.path):
            self._private(path)
        if not self.root.is_dir() or not self.path.is_file():
            raise SourceHold("uif_review_selector_existing_journal_required")


@dataclass(frozen=True)
class ReviewWorkerRegistration:
    settings: ReviewSettings
    executable: Path
    executable_sha256: str
    evidence_uid: int
    pool_database: Path
    windmill_workspace: str
    windmill_script_path: str
    runner_receipts: Path | None = None
    runner_uid: int | None = None
    inspect_version: int | None = None

    def __post_init__(self):
        if self.windmill_workspace != "foo":
            raise SourceHold("uif_review_selector_windmill_workspace")
        if not isinstance(self.settings, ReviewSettings):
            raise SourceHold("uif_review_selector_registration")

    def check_executable(self, invoked_path):
        path = exact_path(self.executable)
        if (exact_path(invoked_path) != path or not digest(self.executable_sha256)
                or file_digest(path) != self.executable_sha256 or not path.stat().st_mode & stat.S_IXUSR):
            raise SourceHold("uif_review_selector_executable_binding")

    def open_review(self, runtime, stage_fence):
        # The finite review worker reads the original validation result from
        # the journal. It needs the same registration pins and no API client.
        backend = WindmillValidationBackend(
            None, workspace=self.windmill_workspace, script_path=self.windmill_script_path,
            runner_receipts=self.runner_receipts, runner_uid=self.runner_uid, inspect_version=self.inspect_version,
        )
        journal = _ExistingStageJournal(runtime)
        validation = ValidationAdapter(journal, stage_fence, backend)
        review = NativeReviewBackend(self.executable, executable_sha256=self.executable_sha256,
                                     evidence_uid=self.evidence_uid, pool_database=self.pool_database)
        return ReviewAdapter(journal, stage_fence, review, validation=validation)


def invoke_review(argv, *, authority, registration, invoked_path):
    """Use the existing launcher with a concrete selector and registered owner."""

    registration.check_executable(invoked_path)
    invocation = ReviewInvocation.parse(argv)
    # Resolve custody before opening the requested runtime's journal. The
    # owner fence rechecks the selection before the launcher can start.
    record = authority.review_record(invocation)
    if not isinstance(record, dict) or record.get("runtime") != str(invocation.runtime):
        raise SourceHold("uif_review_selector_unregistered_runtime")
    review = registration.open_review(invocation.runtime, authority.stage_fence)
    selector = ReviewSelector(authority, review, registration.settings)
    launcher = ReviewLauncher(settings=registration.settings, review=review,
                              fence=authority.review_fence, check_current=authority.check_review)
    return launcher.invoke(argv, selector.select)


def main(argv, *, config_path, config_sha256, registration):
    """Entry for the fixed installed executable; only review flags come from argv."""

    from .owner_runtime import OwnerAuthority
    from .supervisor import _trusted

    invoked_path = _trusted(Path(sys.argv[0]), executable=True)
    authority = OwnerAuthority.load(exact_path(config_path), config_sha256)
    outcome = invoke_review(argv, authority=authority, registration=registration, invoked_path=invoked_path)
    return 0 if outcome["status"] == "succeeded" else 1
