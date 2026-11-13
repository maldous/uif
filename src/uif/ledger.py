"""Read original UIF reconciliation identities and qualify existing admissions."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType

from .source import SourceHold, exact_path
from .stokowski import LEDGER_DISPATCH_INTENT, UIF, LedgerAdmission, PolledIssue, SourceBoundary, bind_uif_lifecycle

SNAPSHOT_SHA256 = "1f1b8d57c464d808816325e1149d042c81f0863e11ffc750b960b40f21efdf1c"
SECTIONS = {
    "### UIF (73 records)": ("uif", 73),
    "### CQ (26 records)": ("cq", 26),
    "### CIX (19 records)": ("cix", 19),
    "### OffMenu (54 records)": ("offmenu", 54),
    "### ANQR (7 records)": ("anqr", 7),
    "## Ledger-discovery and reconciliation actions (34 records)": ("discovery", 34),
}
ROW = re.compile(r"^- \*\*([A-Za-z0-9_.:-]+)\*\*:")
TERMINAL_LINKS = frozenset({"UIF-137", "UIF-106"})


@dataclass(frozen=True)
class RowKey:
    store_id: str
    row_id: str


@dataclass(frozen=True)
class RecoveredRow:
    row_id: str
    section: str
    source_sha256: str


def read_snapshot(path):
    """Read the pinned checkpoint. The nonadditive section is outside intake."""
    raw = exact_path(path).read_bytes()
    if sha256(raw).hexdigest() != SNAPSHOT_SHA256:
        raise SourceHold("uif_ledger_snapshot_pin")

    rows = {}
    counts = {section: 0 for section, _ in SECTIONS.values()}
    section = None
    row_id = None
    body = []

    def finish():
        if row_id is None:
            return
        if row_id in rows:
            raise SourceHold("uif_ledger_duplicate_row")
        digest = sha256("".join(body).rstrip("\n").encode()).hexdigest()
        rows[row_id] = RecoveredRow(row_id, section, digest)
        counts[section] += 1

    for line in raw.decode("utf-8").splitlines(keepends=True):
        heading = line.rstrip("\n")
        if heading.startswith("#"):
            finish()
            row_id = None
            section, _ = SECTIONS.get(heading, (None, 0))
        elif section:
            match = ROW.match(line)
            if match:
                finish()
                row_id, body = match[1], [line]
            elif row_id is not None:
                body.append(line)
    finish()
    if any(counts[section] != count for section, count in SECTIONS.values()):
        raise SourceHold("uif_ledger_snapshot_counts")
    return MappingProxyType(rows)


@dataclass(frozen=True)
class Counterpart:
    project: str
    working: bool
    ready: bool
    evidence_sha256: str


@dataclass(frozen=True)
class CurrentDispatchIntent:
    """Fresh intent read back under the current claim fence, before worker effects."""

    operation_id: str
    receipt_path: Path
    receipt_sha256: str


@dataclass(frozen=True)
class CurrentRow:
    """Current facts supplied by the authorized claim and original-effect reader.

    A historical blank operation ID is never a confirmed absence. The reader
    must qualify current evidence before supplying an absence or readiness fact.
    """

    key: RowKey
    source_sha256: str
    project_id: str
    phase: str
    disposition: str
    prior_effect: str
    effect_evidence_sha256: str
    operation_id: str | None
    owner: str | None
    epoch: str | None
    counterparts: tuple[str, ...] | None
    readiness: tuple[Counterpart, ...] = ()
    issue: PolledIssue | None = None
    dispatch_intent: CurrentDispatchIntent | None = None


def _digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _identity(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def ledger_task_target(key, source_sha256):
    """Identity carried by inputs prepared before the original operation is bound."""
    return dict(store_id=key.store_id, row_id=key.row_id,
                snapshot_sha256=SNAPSHOT_SHA256, source_sha256=source_sha256)


def verify_ledger_task_input(record, target):
    issue_fields = {"issue", "issue_id", "identifier", "project_id", "project_slug", "linear"}
    if not isinstance(record, dict) or issue_fields & record.keys() or record.get("ledger_task") != target:
        raise SourceHold("uif_ledger_task_input_binding")


def verify_ledger_task_admission(task, admission):
    from .task_identity import LedgerTaskEnvelope, SCHEMA as LEDGER_TASK_SCHEMA

    if not isinstance(task, LedgerTaskEnvelope) or not isinstance(admission, LedgerAdmission):
        raise SourceHold("uif_ledger_task_claim_required")
    expected = dict(schema=LEDGER_TASK_SCHEMA, **ledger_task_target(
        RowKey(admission.store_id, admission.row_id), admission.source_sha256),
        owner=admission.owner, epoch=admission.epoch, original_operation_id=admission.original_operation_id)
    if task.to_record() != expected or task.snapshot_sha256 != admission.snapshot_sha256:
        raise SourceHold("uif_ledger_task_claim_drift")


class RecoveredLedger:
    """Read-only boundary for the existing selector and claim machinery."""

    def __init__(self, snapshot_path, *, store_id, read_current, verify_refill):
        if not _identity(store_id) or not callable(read_current) or not callable(verify_refill):
            raise SourceHold("uif_ledger_owner_registration")
        self.snapshot_path = exact_path(snapshot_path)
        self.rows = read_snapshot(self.snapshot_path)
        self.store_id = store_id
        self.read_current = read_current
        self.verify_refill = verify_refill

    def _current(self, row_id):
        row = self.rows.get(row_id)
        if row is None:
            raise SourceHold("uif_ledger_unknown_original_row")
        key = RowKey(self.store_id, row.row_id)
        current = self.read_current(key)
        if (not isinstance(current, CurrentRow) or current.key != key
                or current.source_sha256 != row.source_sha256
                or current.project_id != UIF.project_id
                or current.phase != "implementation"):
            raise SourceHold("uif_ledger_current_binding")
        if current.issue is not None and (current.issue.project_id != UIF.project_id
                or current.issue.project_slug != UIF.project_slug
                or current.issue.identifier in TERMINAL_LINKS):
            raise SourceHold("uif_ledger_issue_held")
        return current

    def _readiness(self, current):
        required = current.counterparts
        if (not isinstance(required, tuple) or any(not _identity(name) for name in required)
                or "uif" in required or len(set(required)) != len(required)):
            raise SourceHold("uif_ledger_allocation_unknown")
        proofs = {}
        for proof in current.readiness:
            if (not isinstance(proof, Counterpart) or proof.project in proofs
                    or proof.working is not True or proof.ready is not True
                    or not _digest(proof.evidence_sha256)):
                raise SourceHold("uif_combined_counterpart_held")
            proofs[proof.project] = proof.evidence_sha256
        if set(proofs) != set(required):
            raise SourceHold("uif_combined_counterpart_held")
        return sha256(json.dumps(proofs, sort_keys=True).encode()).hexdigest() if proofs else None

    def _prior_effect(self, current):
        if not _digest(current.effect_evidence_sha256) or current.prior_effect not in (
                "confirmed-absent", "reconciled-for-pickup"):
            raise SourceHold("uif_original_operation_requires_reconciliation")

    def pickup(self, row_id):
        """Qualify an original row for the existing claim. This call never claims."""
        current = self._current(row_id)
        if (current.disposition != "ready" or current.owner is not None or current.epoch is not None
                or current.dispatch_intent is not None):
            raise SourceHold("uif_ledger_pickup_held")
        self._prior_effect(current)
        if current.prior_effect == "confirmed-absent":
            if current.operation_id is not None:
                raise SourceHold("uif_ledger_first_claim_binding")
        elif not _identity(current.operation_id):
            raise SourceHold("uif_ledger_original_operation_missing")
        self._readiness(current)
        return current

    def _refill_predecessor(self, previous_operation_id):
        if not _identity(previous_operation_id):
            raise SourceHold("uif_ledger_refill_not_warranted")
        previous_row = self.verify_refill(previous_operation_id)
        if (not isinstance(previous_row, RowKey) or previous_row.store_id != self.store_id
                or previous_row.row_id not in self.rows):
            raise SourceHold("uif_ledger_refill_not_warranted")
        return previous_row

    def _refill_after(self, row_id, previous_row, previous_operation_id):
        if RowKey(self.store_id, row_id) == previous_row:
            raise SourceHold("uif_ledger_refill_original_replay")
        current = self.pickup(row_id)
        if current.operation_id == previous_operation_id:
            raise SourceHold("uif_ledger_refill_original_replay")
        return current

    def refill(self, row_id, *, previous_operation_id):
        """Use the same row checks after the existing delivery readback warrants refill."""
        predecessor = self._refill_predecessor(previous_operation_id)
        return self._refill_after(row_id, predecessor, previous_operation_id)

    def verify_claim(self, admission, issue):
        """Read back the existing claim and preserve its identity through dispatch."""
        if (not isinstance(admission, LedgerAdmission) or admission.store_id != self.store_id
                or admission.snapshot_sha256 != SNAPSHOT_SHA256):
            raise SourceHold("uif_ledger_claim_required")
        current = self._current(admission.row_id)
        readiness_sha256 = self._readiness(current)
        if (current.disposition != "claimed" or current.issue != issue
                or not all(_identity(value) for value in (current.owner, current.epoch, current.operation_id))
                or (admission.owner, admission.epoch, admission.original_operation_id)
                != (current.owner, current.epoch, current.operation_id)
                or (admission.source_sha256, admission.phase) != (current.source_sha256, current.phase)
                or admission.combined is not bool(current.counterparts)
                or admission.counterpart_readiness_sha256 != readiness_sha256):
            raise SourceHold("uif_ledger_claim_drift")
        return current

    def _eligible_claim(self, admission, issue):
        current = self.verify_claim(admission, issue)
        self._prior_effect(current)
        admission.require_implementation()
        if current.prior_effect != admission.prior_effect:
            raise SourceHold("uif_original_operation_requires_reconciliation")
        return current

    def qualify_context(self, context, issue):
        current = self._eligible_claim(context.ledger, issue)
        if current.dispatch_intent is not None:
            raise SourceHold("uif_ledger_dispatch_already_intended")
        return context

    def qualify_ledger_context(self, context, task):
        verify_ledger_task_admission(task, context.ledger)
        return self.qualify_context(context, None)

    def verify_dispatch(self, admission, issue, *, operation_id, receipt_path, receipt_sha256, task=None):
        if (issue is None) != (task is not None):
            raise SourceHold("uif_ledger_task_claim_drift")
        if task is None:
            current = self._eligible_claim(admission, issue)
        else:
            verify_ledger_task_admission(task, admission)
            current = self.verify_claim(admission, issue)
            admission.require_implementation()
            if current.dispatch_intent is None:
                self._prior_effect(current)
        if current.operation_id != operation_id:
            raise SourceHold("uif_ledger_dispatch_operation_drift")
        expected = CurrentDispatchIntent(operation_id, exact_path(receipt_path), receipt_sha256)
        if not _digest(receipt_sha256) or current.dispatch_intent != expected:
            raise SourceHold("uif_ledger_dispatch_intent_drift")
        if task is not None:
            from .admission import _record

            record = _record(dict(path=str(expected.receipt_path), sha256=receipt_sha256))
            if (record.get("schema") != LEDGER_DISPATCH_INTENT or record.get("task") != task.to_record()
                    or record.get("operation_id") != operation_id or record.get("admission") != asdict(admission)):
                raise SourceHold("uif_ledger_dispatch_intent_drift")


@dataclass(frozen=True)
class LedgerHooks:
    pickup: Callable
    refill: Callable
    register_dispatch: Callable
    boundary: SourceBoundary


def bind_recovered_ledger_lifecycle(orchestrator, workspace_module, owner, *, ledger,
                                    runtime_root, coordinator_runtime, qualify_context,
                                    selected_source, polled_issue, report_attempt):
    """Register UIF workspace and reporting using the existing owner callbacks."""
    boundary = SourceBoundary(
        runtime_root=runtime_root, coordinator_runtime=coordinator_runtime,
        qualify_context=lambda issue: ledger.qualify_context(qualify_context(issue), issue),
        selected_source=selected_source,
    )
    register = bind_uif_lifecycle(orchestrator, workspace_module, owner, boundary=boundary,
                                  polled_issue=polled_issue, report_attempt=report_attempt)

    def register_dispatch(issue, attempt, workspace_path, *, admission, operation_id,
                          receipt_path, receipt_sha256):
        selected = polled_issue(issue.identifier)
        ledger.verify_dispatch(admission, selected, operation_id=operation_id,
                               receipt_path=receipt_path, receipt_sha256=receipt_sha256)
        return register(issue, attempt, workspace_path, operation_id=operation_id,
                        receipt_path=receipt_path, receipt_sha256=receipt_sha256, ledger=admission)

    return LedgerHooks(ledger.pickup, ledger.refill, register_dispatch, boundary)
