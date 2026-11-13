"""Adapt sealed UIF owner catalogue, Pool leases and original receipts."""

from collections.abc import Callable
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
import re
import sqlite3
import stat

from .ledger import (
    SNAPSHOT_SHA256, Counterpart, CurrentDispatchIntent, CurrentRow, RecoveredLedger,
    RowKey, _digest, _identity, bind_recovered_ledger_lifecycle, ledger_task_target, verify_ledger_task_input,
)
from .source import SourceHold, exact_path, file_digest
from .stokowski import (
    LEDGER_DISPATCH_INTENT, LEDGER_DISPATCH_INTENT_FIELDS, UIF, LedgerAdmission, PolledIssue, QualifiedContext,
)
from .task_identity import LedgerTaskEnvelope, LedgerTaskKey


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SourceHold("uif_owner_duplicate_key")
        value[key] = item
    return value


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _task_lease(runtime, manifest, identifier, *, ledger_only):
    field = "ledger_task" if ledger_only else "issue"
    return "rt-" + _fingerprint({
        "runtime": str(runtime), "manifest_sha256": _fingerprint(manifest), field: identifier,
    })


def _record(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise SourceHold("uif_owner_record_reference")
    path = exact_path(reference["path"])
    expected = reference["sha256"]
    if not _digest(expected):
        raise SourceHold("uif_owner_record_drift")
    limit = 4 * 1024 * 1024
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o022:
            raise SourceHold("uif_owner_record_custody")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) > limit or sha256(raw).hexdigest() != expected
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise SourceHold("uif_owner_record_drift")
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (UnicodeError, ValueError) as error:
        raise SourceHold("uif_owner_record_format") from error
    if not isinstance(value, dict):
        raise SourceHold("uif_owner_record_format")
    return value


@dataclass(frozen=True)
class ReadyRows:
    eligible: tuple[CurrentRow, ...]
    held: dict[str, str]


class OwnerAdmission:
    """Read the authority supplied by the existing owner.

    The catalogue pin comes from the admitted owner configuration. The
    supplied fence is the same exclusive owner fence used by the dispatcher.
    This reader never creates a claim, opens the Pool for writes or replays an
    existing dispatch intent.
    """

    def __init__(self, *, snapshot_path, catalogue, catalogue_sha256, runtime_root,
                 pool_database, store_id, owner, epoch, guard, fence, verify_refill):
        if (not all(_identity(value) for value in (store_id, owner, epoch))
                or not callable(guard) or not callable(fence)):
            raise SourceHold("uif_owner_registration")
        self.catalogue = dict(path=str(exact_path(catalogue)), sha256=catalogue_sha256)
        self.runtime_root = exact_path(runtime_root)
        self.pool_database = exact_path(pool_database)
        self.store_id, self.owner, self.epoch = store_id, owner, epoch
        self.guard, self.fence = guard, fence
        self._intent = ContextVar("uif_fenced_dispatch_intent", default=None)
        self.ledger = RecoveredLedger(snapshot_path, store_id=store_id,
                                      read_current=self.read_current, verify_refill=verify_refill)

    def _catalogue(self):
        self.guard()
        record = _record(self.catalogue)
        if (record.get("schema") != "uif-owner-catalogue/1"
                or (record.get("store_id"), record.get("owner"), record.get("epoch"))
                != (self.store_id, self.owner, self.epoch)
                or not isinstance(record.get("rows"), dict)):
            raise SourceHold("uif_owner_catalogue_binding")
        return record["rows"]

    def _entry(self, key, catalogue=None):
        if not isinstance(key, RowKey) or key.store_id != self.store_id:
            raise SourceHold("uif_owner_original_row_binding")
        if catalogue is None:
            catalogue = self._catalogue()
        entry = catalogue.get(key.row_id)
        required = {"runtime", "source_sha256", "phase", "disposition", "lease_ids",
                    "effect", "counterparts", "readiness"}
        if not isinstance(entry, dict) or not required <= entry.keys() or not isinstance(entry["readiness"], list):
            raise SourceHold("uif_owner_row_unregistered")
        runtime = exact_path(entry["runtime"])
        if runtime == self.runtime_root or not runtime.is_relative_to(self.runtime_root):
            raise SourceHold("uif_owner_runtime_binding")
        return entry, runtime

    def _leases(self, ids):
        if not isinstance(ids, list) or not all(_identity(item) for item in ids) or len(set(ids)) != len(ids):
            raise SourceHold("uif_owner_lease_binding")
        path = exact_path(self.pool_database)
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_uid != os.getuid()):
            raise SourceHold("uif_owner_pool_custody")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as database:
                database.execute("PRAGMA query_only=ON")
                database.execute("BEGIN")
                return [row for lease in ids for row in database.execute(
                    "SELECT id,status FROM leases WHERE id=?", (lease,))]
        except sqlite3.Error as error:
            raise SourceHold("uif_owner_pool_unavailable") from error

    def _effect(self, key, entry, runtime):
        reference = entry.get("effect")
        record = _record(reference)
        if (record.get("schema") != "uif-original-effect/1"
                or (record.get("store_id"), record.get("row_id"), record.get("owner"), record.get("epoch"))
                != (key.store_id, key.row_id, self.owner, self.epoch)
                or record.get("source_sha256") != entry["source_sha256"]
                or record.get("runtime") != str(runtime)
                or record.get("lease_ids") != entry["lease_ids"]
                or (record.get("state") == "confirmed-absent" and record.get("operation_id") is not None)):
            raise SourceHold("uif_owner_effect_binding")
        return record, reference["sha256"]

    def _require_unique_operation(self, key, operation):
        # Every retained effect and current claim can reserve an operation,
        # including rows whose current disposition prevents pickup.
        catalogue = self._catalogue()
        for row_id in catalogue:
            if row_id == key.row_id:
                continue
            other_key = RowKey(key.store_id, row_id)
            entry, runtime = self._entry(other_key, catalogue)
            effect, _ = self._effect(other_key, entry, runtime)
            if effect.get("operation_id") == operation:
                raise SourceHold("uif_owner_original_operation_ambiguous")
            if entry.get("claim") is not None:
                claim = _record(entry["claim"])
                ledger = claim.get("ledger")
                if not isinstance(ledger, dict):
                    raise SourceHold("uif_owner_claim_binding")
                if ledger.get("original_operation_id") == operation:
                    raise SourceHold("uif_owner_original_operation_ambiguous")

    def _claim(self, key, entry, runtime, issue):
        reference = entry.get("claim")
        if reference is None:
            return None
        if exact_path(reference["path"]) != runtime / "current-admission.json":
            raise SourceHold("uif_owner_claim_reference")
        claim = _record(reference)
        inputs = claim.get("inputs")
        ledger = claim.get("ledger")
        if (claim.get("owner") != self.owner or not isinstance(inputs, dict) or not isinstance(ledger, dict)
                or (ledger.get("store_id"), ledger.get("row_id")) != (key.store_id, key.row_id)
                or ledger.get("epoch") != self.epoch or ledger.get("snapshot_sha256") != SNAPSHOT_SHA256
                or ledger.get("source_sha256") != entry["source_sha256"]
                or not _identity(ledger.get("original_operation_id"))):
            raise SourceHold("uif_owner_claim_binding")
        if issue is None:
            expected = {"version", "owner", "ledger", "manifest_sha256", "request_sha256",
                        "policy_sha256", "installation_sha256", "source", "inputs"}
            if (type(claim.get("version")) is not int or claim["version"] != 2
                    or set(claim) != expected or ledger.get("owner") != self.owner):
                raise SourceHold("uif_owner_claim_binding")
            task = LedgerTaskEnvelope.from_record(ledger, snapshot_path=self.ledger.snapshot_path)
            identifier = task.key.workspace_key
        else:
            if (claim.get("version") != 1
                    or (claim.get("issue"), claim.get("issue_id")) != (issue.identifier, issue.issue_id)):
                raise SourceHold("uif_owner_claim_binding")
            identifier = issue.identifier
        request_path = runtime / "requests" / (identifier + ".json")
        for path, field in ((runtime / "manifest.json", "manifest_sha256"),
                            (runtime / "capacity-policy.json", "policy_sha256"),
                            (runtime / "installation.json", "installation_sha256"),
                            (request_path, "request_sha256")):
            if file_digest(path) != claim.get(field):
                raise SourceHold("uif_owner_claim_input_drift")
        if any(file_digest(path) != digest for path, digest in inputs.items()):
            raise SourceHold("uif_owner_claim_input_drift")
        manifest = _record(dict(path=str(runtime / "manifest.json"), sha256=claim["manifest_sha256"]))
        policy = _record(dict(path=str(runtime / "capacity-policy.json"), sha256=claim["policy_sha256"]))
        if issue is None:
            target = ledger_task_target(key, entry["source_sha256"])
            tasks = manifest.get("tasks")
            if manifest.get("project_id") != UIF.project_id or not isinstance(tasks, dict) or set(tasks) != {identifier}:
                raise SourceHold("uif_ledger_task_input_binding")
            verify_ledger_task_input(tasks[identifier], target)
            if "stage" in tasks[identifier]:
                raise SourceHold("nonimplementation_workspace_refused")
            verify_ledger_task_input(_record(dict(path=str(request_path), sha256=claim["request_sha256"])), target)
        expected_lease = _task_lease(runtime, manifest, identifier, ledger_only=issue is None)
        if (expected_lease not in entry["lease_ids"]
                or exact_path(policy["symphony"]["pool_path"]) / "state.sqlite" != self.pool_database):
            raise SourceHold("uif_owner_claim_pool_binding")
        return claim

    def read_current(self, key):
        entry, runtime = self._entry(key)
        issue = PolledIssue(**entry["issue"]) if entry.get("issue") is not None else None
        if issue is not None and (issue.project_id != UIF.project_id or issue.project_slug != UIF.project_slug
                or not re.fullmatch(r"[A-Z][A-Z0-9]*-[1-9][0-9]*", issue.identifier)):
            raise SourceHold("uif_owner_issue_binding")
        claim = self._claim(key, entry, runtime, issue)
        effect, evidence = self._effect(key, entry, runtime)
        if effect["state"] == "reconciled-for-pickup" and not _identity(effect.get("operation_id")):
            raise SourceHold("uif_ledger_original_operation_missing")
        leases = self._leases(entry["lease_ids"])
        scope = self._intent.get()
        current_intent = scope[1] if scope is not None and scope[0] == key else None
        retained_intent = None
        reference = entry.get("dispatch_intent") if issue is None else None
        if reference is not None:
            identifier = LedgerTaskKey(key.store_id, key.row_id).workspace_key
            path = runtime / "attempts" / identifier / "dispatch-intent.json"
            if (claim is None or not isinstance(reference, dict) or set(reference) != {"path", "sha256"}
                    or reference["path"] != str(path) or not _digest(reference["sha256"])):
                raise SourceHold("uif_owner_dispatch_intent_drift")
            retained_intent = CurrentDispatchIntent(claim["ledger"]["original_operation_id"], path,
                                                     reference["sha256"])
            if current_intent is not None and current_intent != retained_intent:
                raise SourceHold("uif_owner_dispatch_intent_drift")
        attempts = runtime / "attempts"
        # An existing attempt outside this call's fresh create-only receipt is
        # historical/uncertain. A terminal Pool row also retains its identity.
        if os.path.lexists(attempts):
            directory = exact_path(attempts)
            entries = tuple(directory.iterdir())
            identifier = (issue.identifier if issue is not None else
                          LedgerTaskKey(key.store_id, key.row_id).workspace_key if claim is not None else None)
            if entries and (current_intent is None or identifier is None
                            or entries != (directory / identifier,)
                            or tuple(exact_path(entries[0]).iterdir()) != (current_intent.receipt_path,)):
                effect = {**effect, "state": "unknown"}
        if leases:
            effect = {**effect, "state": "unknown"}
        operation = effect.get("operation_id")
        if claim is not None:
            claimed = claim["ledger"]["original_operation_id"]
            if operation is not None and operation != claimed:
                raise SourceHold("uif_owner_original_operation_drift")
            operation = claimed
        elif os.path.lexists(runtime / "current-admission.json"):
            effect = {**effect, "state": "unknown"}
        if _identity(operation):
            self._require_unique_operation(key, operation)
        counterparts = entry["counterparts"]
        if counterparts is not None and not isinstance(counterparts, list):
            raise SourceHold("uif_ledger_allocation_unknown")
        readiness = tuple(Counterpart(**_record(ref)) for ref in entry["readiness"])
        self.guard()
        return CurrentRow(
            key, entry["source_sha256"], UIF.project_id, entry["phase"], entry["disposition"],
            effect["state"], evidence, operation, self.owner if claim else None,
            self.epoch if claim else None, tuple(counterparts) if counterparts is not None else None,
            readiness, issue, current_intent or retained_intent,
        )

    def select_ready(self, *, previous_operation_id=None, ledger_only=False):
        """Read candidates from the registered owner catalogue for the normal poll."""
        registered = self._catalogue()
        predecessor = (None if previous_operation_id is None
                       else self.ledger._refill_predecessor(previous_operation_id))
        eligible, held = [], {}
        for row_id in registered:
            try:
                row = (self.ledger.pickup(row_id) if predecessor is None
                       else self.ledger._refill_after(row_id, predecessor, previous_operation_id))
                if ledger_only and (row.issue is not None or row.counterparts != ()):
                    raise SourceHold("uif_ledger_task_intake_held")
            except SourceHold as error:
                held[row_id] = str(error)
            except FileNotFoundError:
                held[row_id] = "uif_owner_registered_evidence_missing"
            else:
                eligible.append(row)
        self._catalogue()
        return ReadyRows(tuple(eligible), held)

    def _claimed_issue(self, issue):
        if (issue.project_id, issue.project_slug) != (UIF.project_id, UIF.project_slug) or not re.fullmatch(
                r"[A-Z][A-Z0-9]*-[1-9][0-9]*", issue.identifier):
            raise SourceHold("uif_owner_issue_binding")
        candidates = [(row_id, entry) for row_id, entry in self._catalogue().items()
                      if entry.get("issue") == asdict(issue) and entry.get("claim") is not None]
        if len(candidates) != 1:
            raise SourceHold("uif_owner_issue_claim_ambiguous")
        return RowKey(self.store_id, candidates[0][0])

    def context(self, issue):
        key = self._claimed_issue(issue)
        return self.ledger.qualify_context(self._context(key, issue), issue)

    def ledger_context(self, task):
        if not isinstance(task, LedgerTaskEnvelope) or task.key.store_id != self.store_id:
            raise SourceHold("uif_ledger_task_claim_required")
        key = RowKey(task.key.store_id, task.key.row_id)
        return self.ledger.qualify_ledger_context(self._context(key, None, task=task), task)

    def _context(self, key, issue, *, task=None):
        entry, runtime = self._entry(key)
        claim = self._claim(key, entry, runtime, issue)
        current = self.read_current(key)
        if claim is None or current.operation_id is None or current.disposition != "claimed":
            raise SourceHold("uif_owner_claim_required")
        return QualifiedContext(
            runtime, _record(dict(path=str(runtime / "manifest.json"), sha256=claim["manifest_sha256"])),
            exact_path(claim["source"]),
            LedgerAdmission(
                key.store_id, SNAPSHOT_SHA256, key.row_id, current.source_sha256, current.phase,
                self.owner, self.epoch, current.operation_id, "ready", current.prior_effect,
                bool(current.counterparts), self.ledger._readiness(current),
            ),
            task=task,
        )

    def runtime_pins(self, issue, admission, *, task=None):
        # Dispatch owns the type and its runtime/provider checks.
        from .dispatch import RuntimePins

        if (issue is None) != (task is not None):
            raise SourceHold("uif_owner_dispatch_claim_drift")
        if task is None:
            self.ledger._eligible_claim(admission, issue)
        elif self.ledger_context(task).ledger != admission:
            raise SourceHold("uif_owner_dispatch_claim_drift")
        entry, _ = self._entry(RowKey(admission.store_id, admission.row_id))
        return RuntimePins(entry["claim"]["sha256"], entry["provider_sha256"])

    def _retain_ledger_dispatch_intent(self, key, entry, path, digest):
        raise SourceHold("uif_owner_dispatch_intent_retention_unregistered")

    @contextmanager
    def dispatch_fence(self, issue, admission, *, task=None):
        key = RowKey(admission.store_id, admission.row_id)
        if ((issue is None) != (task is not None)
                or (task is not None and not isinstance(task, LedgerTaskEnvelope))):
            raise SourceHold("uif_owner_dispatch_claim_drift")
        identifier = task.key.workspace_key if task is not None else issue.identifier
        fence = self.fence(issue, admission) if task is None else self.fence(None, admission, task=task)
        with fence:
            context = self.context(issue) if task is None else self.ledger_context(task)
            if context.ledger != admission:
                raise SourceHold("uif_owner_dispatch_claim_drift")
            entry, runtime = self._entry(key)
            expected = runtime / "attempts" / identifier / "dispatch-intent.json"
            if task is not None:
                claim = self._claim(key, entry, runtime, None)
                source_reference = entry.get("selected_source")
                expected_pins = {
                    "manifest_sha256": claim["manifest_sha256"], "request_sha256": claim["request_sha256"],
                    "lease_id": _task_lease(runtime, context.manifest, identifier, ledger_only=True),
                    "pool_database": str(self.pool_database),
                    "pins": {"admission_sha256": entry["claim"]["sha256"],
                             "provider_sha256": entry["provider_sha256"]},
                }
            token = self._intent.set(None)
            try:
                def record_intent(path, digest):
                    self.guard()
                    if self._intent.get() is not None or exact_path(path) != expected:
                        raise SourceHold("uif_owner_dispatch_intent_drift")
                    record = _record(dict(path=str(expected), sha256=digest))
                    if task is None:
                        bound = (record.get("schema") == "uif-dispatch-intent/1"
                                 and record.get("issue") == asdict(issue))
                    else:
                        current_entry, _ = self._entry(key)
                        if current_entry.get("selected_source") != source_reference:
                            raise SourceHold("uif_owner_dispatch_intent_drift")
                        source = _record(source_reference)
                        source_pins = {
                            "source": source.get("repository"), "source_head": source.get("head"),
                            "source_tree": source.get("tree"), "source_branch": source.get("branch"),
                            "acquisition_sha256": source.get("acquisition_sha256"),
                            "accepted_sha256": source.get("accepted_sha256"),
                        }
                        attempt = record.get("attempt")
                        bound = (set(record) == LEDGER_DISPATCH_INTENT_FIELDS
                                 and record.get("schema") == LEDGER_DISPATCH_INTENT
                                 and record.get("task") == task.to_record()
                                 and record.get("operation_id") == task.original_operation_id
                                 and record.get("workspace") == str(runtime / "workspaces" / identifier)
                                 and source.get("checkout") == str(context.source)
                                 and source_pins["source_head"] == context.manifest.get("base_revision")
                                 and source_pins["source_branch"] == context.manifest.get("branch")
                                 and all(_identity(value) and record.get(name) == value
                                         for name, value in source_pins.items())
                                 and all(record.get(name) == value for name, value in expected_pins.items())
                                 and (attempt is None or (type(attempt) is int and attempt >= 0)))
                    if not bound or record.get("admission") != asdict(admission):
                        raise SourceHold("uif_owner_dispatch_intent_drift")
                    if task is not None:
                        self._retain_ledger_dispatch_intent(key, entry, expected, digest)
                    self._intent.set((key, CurrentDispatchIntent(admission.original_operation_id, expected, digest)))

                yield record_intent
            finally:
                self._intent.reset(token)

    def bind(self, orchestrator, workspace_module, owner, *, coordinator_runtime, selected_source,
             polled_issue, report_attempt):
        return bind_recovered_ledger_lifecycle(
            orchestrator, workspace_module, owner, ledger=self.ledger, runtime_root=self.runtime_root,
            coordinator_runtime=coordinator_runtime, qualify_context=self.context,
            selected_source=selected_source, polled_issue=polled_issue, report_attempt=report_attempt,
        )

    def bind_dispatch(self, orchestrator, workspace_module, owner, *, coordinator_runtime,
                      selected_source, polled_issue, report_attempt, supervisor, supervisor_sha256):
        """Wire the concrete dispatcher into the dedicated coordinator's run_turn alias."""
        from .dispatch import DispatchBackend, read_outcome

        supervisor = exact_path(supervisor)
        if not callable(report_attempt) or not _digest(supervisor_sha256):
            raise SourceHold("uif_owner_dispatch_registration")

        async def report(binding):
            await report_attempt(binding, read_outcome(binding))

        hooks = self.bind(orchestrator, workspace_module, owner, coordinator_runtime=coordinator_runtime,
                          selected_source=selected_source, polled_issue=polled_issue, report_attempt=report)
        backend = DispatchBackend(
            boundary=hooks.boundary, polled_issue=polled_issue, runtime_pins=self.runtime_pins,
            dispatch_fence=self.dispatch_fence, register_dispatch=hooks.register_dispatch,
            supervisor=supervisor, supervisor_sha256=supervisor_sha256,
        )
        orchestrator.run_turn = backend.owned_turn
        return backend


@dataclass(frozen=True)
class OwnerStages:
    """Connect verified UIF stage adapters under their existing owner fences."""

    admission: OwnerAdmission
    validation: object
    review: object
    publisher: object
    recovery: object
    complete: Callable | None = None

    def _coding(self, binding):
        from .dispatch import read_outcome

        dispatch = binding.dispatch
        self.admission.ledger.verify_claim(dispatch.ledger, dispatch.issue)
        outcome = read_outcome(dispatch)
        if (outcome.status != "succeeded"
                or (outcome.worker_receipt_path, outcome.worker_receipt_sha256)
                != (binding.coding_receipt, binding.coding_receipt_sha256)):
            raise SourceHold("uif_owner_stage_coding_receipt")
        return outcome

    async def validate(self, spec):
        self._coding(spec.binding)
        return await self.validation.tick(spec)

    async def review_candidate(self, spec):
        self._coding(spec.binding)
        return await self.review.tick(spec)

    async def publish(self, request, review_spec):
        binding = review_spec.binding
        outcome = self._coding(binding)
        validation = await self.validation.accepted(review_spec.validation)
        review = await self.review.accepted(review_spec)
        if (validation.binding != binding or review.binding != binding
                or review.validation_receipt_sha256 != validation.receipt_sha256
                or (request.admission, request.issue) != (binding.dispatch.ledger, binding.dispatch.issue)
                or (request.source_base, request.source_head, request.source_tree, request.source_branch)
                != (binding.base, binding.head, binding.tree, binding.branch)
                or (request.capacity_lease_id, request.validation_operation_id, request.validation_job_id,
                    request.validation_receipt_sha256, request.review_operation_id, request.reviewer,
                    request.review_receipt_sha256)
                != (outcome.lease_id, validation.operation_key, validation.windmill_job_id,
                    validation.receipt_sha256, review.operation_key, review.reviewer, review.receipt_sha256)):
            raise SourceHold("uif_owner_publication_stage_binding")
        if self._coding(binding) != outcome:
            raise SourceHold("uif_owner_stage_coding_receipt")
        publication = self.publisher.consume_boundary(request)
        if self.complete is not None:
            self.complete(publication)
        return publication

    def recover_delivery(self, request, checkpoint=None):
        """Reconcile the original effect and finish any interrupted owner completion."""
        if self.complete is None:
            raise SourceHold("uif_owner_runtime_completion_unregistered")
        result = self.observe_original(request, checkpoint)
        self.complete(result.publication)
        return result

    def observe_original(self, request, checkpoint=None):
        return self.recovery.observe(request, checkpoint)
