"""UIF's existing owner catalogue, guarded claim and terminal receipt authority."""

from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
import fcntl
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
from uuid import uuid4

from .admission import OwnerAdmission, OwnerStages, _fingerprint, _record, _task_lease
from .dispatch import _record as read_owned_record, _sync_directory
from .ledger import (
    RowKey, SNAPSHOT_SHA256, _identity, ledger_task_target, verify_ledger_task_admission, verify_ledger_task_input,
)
from .publication import AcceptedPublication, PublicationRequest, wire
from .refill import DeliveryCompletion, VerifiedRefill
from .source import SourceHold, exact_path, file_digest
from .stokowski import LedgerAdmission, PolledIssue, SelectedSource, SourceBoundary, UIF
from .task_identity import LedgerTaskEnvelope, LedgerTaskKey, SCHEMA as LEDGER_TASK_SCHEMA

LEDGER_PUBLICATION_REQUEST = "uif-publication-request/2"
LEDGER_REVIEW_FIELDS = frozenset({
    "schema", "store_id", "row_id", "operation_id", "owner", "epoch", "runtime", "task",
    "policy_path", "policy_sha256", "spec", "installation_sha256", "capacity_policy_sha256",
    "provider_proof_sha256",
})


def _owned_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise SourceHold("uif_owner_runtime_file_custody")
    return info


def _json_value(record):
    from .validation import decode

    return decode(json.dumps(record, allow_nan=False,
                             default=lambda value: value.decode() if isinstance(value, bytes) else str(value)))


def _publication_request(record, *, snapshot_path):
    if not isinstance(record, dict):
        raise SourceHold("uif_owner_runtime_publication_binding")
    if record.get("schema") == LEDGER_PUBLICATION_REQUEST:
        return PublicationRequest.from_ledger_record(record, snapshot_path=snapshot_path)
    if "task" in record or "schema" in record:
        raise SourceHold("uif_owner_runtime_publication_binding")
    return PublicationRequest(
        **{**record, "admission": LedgerAdmission(**record["admission"]),
           "issue": PolledIssue(**record["issue"])},
    )


def _publication_value(request):
    task = getattr(request, "task", None)
    if task is None:
        record = asdict(request)
        record.pop("task", None)
    else:
        record = request.to_record()
        if (record.get("schema") != LEDGER_PUBLICATION_REQUEST or "issue" in record
                or record.get("task") != task.to_record()
                or record.get("admission") != asdict(request.admission)):
            raise SourceHold("uif_owner_runtime_publication_binding")
    return record


def review_spec_record(spec):
    value = asdict(spec)
    dispatch = spec.binding.dispatch
    if dispatch.task is not None:
        value["validation"]["binding"]["dispatch"] = dispatch.to_record()
    return _json_value(value)


class _RegisteredAdmission(OwnerAdmission):
    def _catalogue(self):
        return self.authority.catalogue()[0]["rows"]

    def _retain_ledger_dispatch_intent(self, key, entry, path, digest):
        with self.authority.lock():
            catalogue, pin = self.authority.catalogue()
            registered, _ = self._entry(key, catalogue["rows"])
            if registered != entry or "dispatch_intent" in registered:
                raise SourceHold("uif_owner_dispatch_intent_drift")
            registered["dispatch_intent"] = dict(path=str(path), sha256=digest)
            self.authority._replace_catalogue(catalogue, pin)


class OwnerAuthority:
    """Operate on the explicitly registered, existing UIF catalogue and lock."""

    @classmethod
    def load(cls, config_path, config_sha256):
        return cls(dict(path=str(exact_path(config_path)), sha256=config_sha256))

    def __init__(self, registration):
        self.registration = registration
        self.config = config = _record(registration)
        expected = {"schema", "store_id", "owner", "epoch", "catalogue", "lock",
                    "snapshot", "runtime_root", "coordinator_runtime", "pool_database", "operation_locks"}
        if (set(config) != expected or config["schema"] != "uif-owner-runtime/1"
                or not all(_identity(config[name]) for name in ("store_id", "owner", "epoch"))):
            raise SourceHold("uif_owner_runtime_registration")
        for name in expected - {"schema", "store_id", "owner", "epoch"}:
            setattr(self, name + "_path", exact_path(config[name]))
        self.store_id, self.owner, self.epoch = (config[name] for name in ("store_id", "owner", "epoch"))
        if (not self.coordinator_runtime_path.is_relative_to(self.runtime_root_path)
                or self.coordinator_runtime_path == self.runtime_root_path
                or not all(path.is_relative_to(self.runtime_root_path) for path in
                           (self.lock_path, self.catalogue_path, self.pool_database_path, self.operation_locks_path))
                or self.lock_path == self.catalogue_path):
            raise SourceHold("uif_owner_runtime_path_binding")
        self.refill = None
        self.admission = _RegisteredAdmission(
            snapshot_path=self.snapshot_path, catalogue=self.catalogue_path, catalogue_sha256=None,
            runtime_root=self.runtime_root_path, pool_database=self.pool_database_path, store_id=self.store_id,
            owner=self.owner, epoch=self.epoch, guard=self.guard, fence=self.dispatch_fence,
            verify_refill=self.verify_refill,
        )
        self.admission.authority = self
        self.catalogue()

    def guard(self):
        if _record(self.registration) != self.config:
            raise SourceHold("uif_owner_runtime_registration_drift")
        for path in {self.catalogue_path.parent, self.lock_path.parent, self.runtime_root_path,
                     self.coordinator_runtime_path, self.operation_locks_path}:
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o022):
                raise SourceHold("uif_owner_runtime_directory_custody")
        if file_digest(self.snapshot_path) != SNAPSHOT_SHA256:
            raise SourceHold("uif_owner_runtime_snapshot_drift")

    def catalogue(self):
        self.guard()
        _owned_file(self.catalogue_path)
        record, digest = read_owned_record(self.catalogue_path)
        if (record.get("schema") != "uif-owner-catalogue/1"
                or (record.get("store_id"), record.get("owner"), record.get("epoch"))
                != (self.store_id, self.owner, self.epoch)
                or not isinstance(record.get("rows"), dict)):
            raise SourceHold("uif_owner_catalogue_binding")
        return record, digest

    @contextmanager
    def lock(self, *, operation=None, wait=0):
        self.guard()
        if operation is not None and not _identity(operation):
            raise SourceHold("uif_owner_runtime_operation")
        path = (self.lock_path if operation is None else
                self.operation_locks_path / (_fingerprint([self.store_id, operation]) + ".lock"))
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if operation is not None:
            flags |= os.O_CREAT
        fd = os.open(path, flags, 0o600)
        try:
            opened = os.fstat(fd)
            expected = _owned_file(path)
            if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                raise SourceHold("uif_owner_runtime_lock_drift")
            deadline = monotonic() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if monotonic() >= deadline:
                        raise SourceHold("uif_owner_runtime_operation_busy") from None
                    sleep(0.025)
            current = _owned_file(path)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise SourceHold("uif_owner_runtime_lock_drift")
            self.guard()
            yield
        finally:
            os.close(fd)

    def _replace_catalogue(self, catalogue, expected):
        if self.catalogue()[1] != expected:
            raise SourceHold("uif_owner_runtime_catalogue_drift")
        with NamedTemporaryFile(dir=self.catalogue_path.parent, prefix=".catalogue-", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                data = wire(catalogue)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, self.catalogue_path)
                _sync_directory(self.catalogue_path.parent)
            finally:
                temporary.unlink(missing_ok=True)
        if self.catalogue()[1] != sha256(data).hexdigest():
            raise SourceHold("uif_owner_runtime_catalogue_readback")

    def _active(self, admission, issue, *, task=None):
        if (admission.combined or os.path.lexists(self.coordinator_runtime_path / "cancel.json")):
            raise SourceHold("uif_owner_runtime_effect_held")
        if (issue is None) != (task is not None):
            raise SourceHold("uif_ledger_task_claim_drift")
        if task is not None:
            verify_ledger_task_admission(task, admission)
        key = RowKey(admission.store_id, admission.row_id)
        entry, runtime = self.admission._entry(key)
        if os.path.lexists(runtime / "cancel.json"):
            raise SourceHold("uif_owner_runtime_effect_held")
        current = self.admission.ledger.verify_claim(admission, issue)
        if current.counterparts != ():
            raise SourceHold("uif_owner_runtime_effect_held")
        return entry, runtime

    @contextmanager
    def dispatch_fence(self, issue, admission, *, task=None):
        with self.lock(operation=admission.original_operation_id):
            self._active(admission, issue, task=task)
            yield

    @asynccontextmanager
    async def stage_fence(self, stage, spec):
        if stage not in ("validation", "review"):
            raise SourceHold("uif_owner_runtime_stage")
        dispatch = spec.binding.dispatch
        with self.lock(operation=dispatch.operation_id):
            self._verify_stage_dispatch(dispatch)
            yield

    def _verify_stage_dispatch(self, dispatch):
        _, runtime = self._active(dispatch.ledger, dispatch.issue, task=dispatch.task)
        task = dispatch.task
        if task is not None:
            if dispatch.workspace != runtime / "workspaces" / task.key.workspace_key:
                raise SourceHold("uif_owner_runtime_stage_workspace")
            self.admission.ledger.verify_dispatch(
                dispatch.ledger, None, operation_id=dispatch.operation_id,
                receipt_path=dispatch.receipt_path, receipt_sha256=dispatch.receipt_sha256, task=task)
        return runtime

    def claim(self, row_id, issue, *, previous_operation_id=None):
        """Bind one qualified row immediately before existing coordinator dispatch."""
        if not isinstance(issue, PolledIssue):
            raise SourceHold("uif_owner_runtime_issue_binding")
        return self._claim(row_id, issue, previous_operation_id=previous_operation_id)

    def select_ledger_tasks(self):
        previous_operation_id = self.catalogue()[0].get("refill_from")
        return self.admission.select_ready(previous_operation_id=previous_operation_id, ledger_only=True)

    def claim_ledger_task(self, key):
        if not isinstance(key, LedgerTaskKey) or key.store_id != self.store_id:
            raise SourceHold("uif_owner_original_row_binding")
        previous_operation_id = self.catalogue()[0].get("refill_from")
        return self._claim(key.row_id, None, ledger_key=key, previous_operation_id=previous_operation_id)

    def _claim(self, row_id, issue, *, previous_operation_id, ledger_key=None):
        ledger = self.admission.ledger
        identifier = issue.identifier if ledger_key is None else ledger_key.workspace_key
        predecessor = completion = None
        if previous_operation_id is not None:
            completion = self.read_completion(previous_operation_id)
            predecessor = ledger._refill_predecessor(previous_operation_id)
        with self.lock():
            if os.path.lexists(self.coordinator_runtime_path / "cancel.json"):
                raise SourceHold("uif_owner_runtime_effect_held")
            if predecessor is not None and completion != self.read_completion(previous_operation_id):
                raise SourceHold("uif_owner_runtime_completion_drift")
            row = (ledger.pickup(row_id) if predecessor is None
                   else ledger._refill_after(row_id, predecessor, previous_operation_id))
            if (row.counterparts != () or row.issue != issue
                    or (issue is not None and issue.project_id != UIF.project_id)):
                raise SourceHold("uif_owner_runtime_effect_held")
            catalogue, catalogue_sha = self.catalogue()
            if ledger_key is not None and catalogue.get("refill_from") != previous_operation_id:
                raise SourceHold("uif_owner_runtime_completion_drift")
            entry, runtime = self.admission._entry(row.key, catalogue["rows"])
            if os.path.lexists(runtime / "cancel.json"):
                raise SourceHold("uif_owner_runtime_effect_held")
            inputs = _record(entry.get("admission_inputs"))
            pin_files = {
                "manifest_sha256": runtime / "manifest.json",
                "request_sha256": runtime / "requests" / (identifier + ".json"),
                "policy_sha256": runtime / "capacity-policy.json",
                "installation_sha256": runtime / "installation.json",
            }
            if (set(inputs) != {*pin_files, "source", "inputs"}
                    or not isinstance(inputs["inputs"], dict)
                    or any(file_digest(path) != inputs[field] for field, path in pin_files.items())
                    or any(file_digest(path) != digest for path, digest in inputs["inputs"].items())
                    or file_digest(runtime / "provider-proof.json") != entry.get("provider_sha256")):
                raise SourceHold("uif_owner_runtime_claim_inputs")
            source = self._source(entry)
            if str(source.checkout) != inputs["source"]:
                raise SourceHold("uif_owner_runtime_source_binding")
            manifest, _ = read_owned_record(pin_files["manifest_sha256"], inputs["manifest_sha256"])
            policy, _ = read_owned_record(pin_files["policy_sha256"], inputs["policy_sha256"])
            lease = _task_lease(runtime, manifest, identifier, ledger_only=ledger_key is not None)
            tasks = manifest.get("tasks")
            if (lease not in entry["lease_ids"]
                    or exact_path(policy["symphony"]["pool_path"]) / "state.sqlite" != self.pool_database_path
                    or manifest.get("project_id") != UIF.project_id
                    or not isinstance(tasks, dict) or set(tasks) != {identifier}
                    or not isinstance(tasks[identifier], dict) or "stage" in tasks[identifier]
                    or manifest.get("base_revision") != source.head or manifest.get("branch") != source.branch):
                raise SourceHold("uif_owner_runtime_claim_inputs")
            if ledger_key is not None:
                target = ledger_task_target(ledger_key, row.source_sha256)
                request, _ = read_owned_record(pin_files["request_sha256"], inputs["request_sha256"])
                verify_ledger_task_input(tasks[identifier], target)
                verify_ledger_task_input(request, target)
            elif tasks[identifier].get("issue_id") != issue.issue_id:
                raise SourceHold("uif_owner_runtime_claim_inputs")
            operation = row.operation_id or str(uuid4())
            self.admission._require_unique_operation(row.key, operation)
            record = dict(inputs, owner=self.owner)
            if ledger_key is None:
                record.update(version=1, issue=issue.identifier, issue_id=issue.issue_id,
                              ledger=dict(**ledger_task_target(row.key, row.source_sha256),
                                          epoch=self.epoch, original_operation_id=operation))
            else:
                envelope = LedgerTaskEnvelope.from_record(
                    dict(schema=LEDGER_TASK_SCHEMA, **target, owner=self.owner, epoch=self.epoch,
                         original_operation_id=operation), snapshot_path=self.snapshot_path)
                record.update(version=2, ledger=envelope.to_record())
            path = runtime / "current-admission.json"
            data = wire(record)
            _sync_directory(runtime)
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600), "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(runtime)
            entry.update(disposition="claimed", claim=dict(path=str(path), sha256=sha256(data).hexdigest()))
            self.admission._claim(row.key, entry, runtime, issue)
            self._replace_catalogue(catalogue, catalogue_sha)
            if ledger_key is not None:
                self.admission.ledger_context(envelope)
                return envelope
            return self.admission.context(issue).ledger

    @staticmethod
    def _source(entry):
        source = _record(entry.get("selected_source"))
        return SelectedSource(**{**source, **{name: exact_path(source[name]) for name in
                               ("checkout", "accepted_evidence", "acquisition")}})

    def selected_source(self, profile, issue):
        if profile != UIF:
            raise SourceHold("uif_owner_runtime_source_binding")
        key = self.admission._claimed_issue(issue)
        entry, _ = self.admission._entry(key)
        return self._source(entry)

    def selected_ledger_source(self, profile, task):
        if profile != UIF:
            raise SourceHold("uif_owner_runtime_source_binding")
        self.admission.ledger_context(task)
        entry, _ = self.admission._entry(RowKey(task.key.store_id, task.key.row_id))
        return self._source(entry)

    def ensure_ledger_workspace(self, task, workspace_root):
        def qualify(task):
            context = self.admission.ledger_context(task)
            self._active(context.ledger, None, task=task)
            return context

        admission = self.admission.ledger_context(task).ledger
        with self.lock(operation=admission.original_operation_id):
            boundary = SourceBoundary(
                runtime_root=self.runtime_root_path, coordinator_runtime=self.coordinator_runtime_path,
                qualify_context=qualify, selected_source=self.selected_ledger_source,
            )
            return boundary.ensure_ledger_workspace(task, workspace_root)

    def polled_issue(self, identifier):
        matches = [entry["issue"] for entry in self.catalogue()[0]["rows"].values()
                   if isinstance(entry.get("issue"), dict)
                   and entry["issue"].get("identifier") == identifier and entry.get("claim") is not None]
        if len(matches) != 1:
            raise SourceHold("uif_owner_runtime_issue_binding")
        return PolledIssue(**matches[0])

    def review_record(self, invocation):
        issue = self.polled_issue(invocation.issue)
        key = self.admission._claimed_issue(issue)
        entry, runtime = self.admission._entry(key)
        claim = self.admission._claim(key, entry, runtime, issue)
        record = _record(entry.get("review"))
        if claim is None or runtime != invocation.runtime or any(record.get(k) != v for k, v in {
                "schema": "uif-owner-review/1", "store_id": self.store_id, "row_id": key.row_id,
                "operation_id": claim["ledger"]["original_operation_id"], "owner": self.owner, "epoch": self.epoch,
                "runtime": str(runtime), "issue": issue.identifier, "policy_path": str(invocation.policy),
                "policy_sha256": invocation.policy_sha256,
        }.items()):
            raise SourceHold("uif_owner_runtime_review_binding")
        return record

    def ledger_review_record(self, task, *, runtime, policy, policy_sha256):
        if not isinstance(task, LedgerTaskEnvelope) or task.key.store_id != self.store_id:
            raise SourceHold("uif_owner_runtime_review_binding")
        key = RowKey(task.key.store_id, task.key.row_id)
        current = self.admission.read_current(key)
        entry, registered_runtime = self.admission._entry(key)
        claim = self.admission._claim(key, entry, registered_runtime, None)
        record = _record(entry.get("review"))
        if (set(record) != LEDGER_REVIEW_FIELDS
                or current.disposition != "claimed" or current.counterparts != () or current.issue is not None
                or claim is None or claim["ledger"] != task.to_record()
                or registered_runtime != exact_path(runtime)
                or any(record.get(name) != value for name, value in {
                    "schema": "uif-owner-review/2", "store_id": self.store_id, "row_id": key.row_id,
                    "operation_id": task.original_operation_id, "owner": self.owner, "epoch": self.epoch,
                    "runtime": str(registered_runtime), "task": task.to_record(),
                    "policy_path": str(exact_path(policy)),
                    "policy_sha256": policy_sha256,
                }.items())):
            raise SourceHold("uif_owner_runtime_review_binding")
        return record

    def check_review(self, selection):
        from .review_launcher import ReviewInvocation

        spec = selection.spec
        dispatch = spec.binding.dispatch
        runtime = self._verify_stage_dispatch(dispatch)
        if dispatch.task is None:
            invocation = ReviewInvocation(runtime, dispatch.issue.identifier, spec.policy, spec.policy_sha256)
            record = self.review_record(invocation)
        else:
            record = self.ledger_review_record(dispatch.task, runtime=runtime,
                                               policy=spec.policy, policy_sha256=spec.policy_sha256)
        if (record["spec"] != review_spec_record(spec)
                or any(record.get(name) != getattr(selection, name) for name in
                       ("installation_sha256", "capacity_policy_sha256", "provider_proof_sha256"))):
            raise SourceHold("uif_owner_runtime_review_binding")

    @contextmanager
    def review_fence(self, selection):
        with self.lock(operation=selection.spec.binding.dispatch.operation_id, wait=5):
            self.check_review(selection)
            yield

    async def report_attempt(self, binding, outcome):
        with self.lock(operation=binding.operation_id), self.lock():
            if outcome.operation_id != binding.operation_id:
                raise SourceHold("uif_owner_runtime_report_binding")
            catalogue, digest = self.catalogue()
            key = RowKey(binding.ledger.store_id, binding.ledger.row_id)
            entry, _ = self.admission._entry(key, catalogue["rows"])
            record = dict(operation_id=binding.operation_id, dispatch_sha256=binding.receipt_sha256,
                          status=outcome.status, lease_id=outcome.lease_id,
                          receipt_path=str(outcome.worker_receipt_path),
                          receipt_sha256=outcome.worker_receipt_sha256)
            prior = entry.get("coding_outcome")
            if outcome.status == "capacity-refused":
                from .dispatch import read_outcome

                if (binding.task is None
                        or entry.get("dispatch_intent") != {
                            "path": str(binding.receipt_path), "sha256": binding.receipt_sha256,
                        } or read_outcome(binding) != outcome):
                    raise SourceHold("uif_owner_runtime_no_start_binding")
                if entry["disposition"] == "held" and prior == record:
                    return
            self._active(binding.ledger, binding.issue, task=binding.task)
            if prior == record:
                return
            if prior is not None and (prior.get("dispatch_sha256") != binding.receipt_sha256
                                      or prior.get("status") not in ("unknown", "uncertain")):
                raise SourceHold("uif_owner_runtime_report_drift")
            entry["coding_outcome"] = record
            if outcome.status == "capacity-refused":
                entry["disposition"] = "held"
            self._replace_catalogue(catalogue, digest)

    @staticmethod
    def _completion_record(publication):
        request = publication.request
        admission = request.admission
        accepted = asdict(publication)
        accepted["request"] = _publication_value(request)
        schema = "uif-owner-delivery-completion/1"
        if getattr(request, "task", None) is not None:
            schema = "uif-owner-delivery-completion/2"
        return dict(schema=schema, outcome="completed",
                    store_id=admission.store_id, row_id=admission.row_id,
                    operation_id=admission.original_operation_id, owner=admission.owner,
                    epoch=admission.epoch, publication=accepted)

    def read_completion(self, operation):
        """Resolve a terminal owner receipt in the same retained catalogue."""
        completions = [(row_id, entry) for row_id, entry in self.catalogue()[0]["rows"].items()
                       if isinstance(entry.get("completion"), dict)
                       and entry["completion"].get("operation_id") == operation]
        if not completions:
            return None
        if len(completions) != 1:
            raise SourceHold("uif_owner_runtime_completion_ambiguous")
        row_id, entry = completions[0]
        ref = entry["completion"]["receipt"]
        if (row_id not in self.admission.ledger.rows or entry.get("disposition") != "completed"
                or exact_path(ref["path"]) != exact_path(entry["runtime"]) / "delivery-completion.json"):
            raise SourceHold("uif_owner_runtime_completion_binding")
        terminal = _record(ref)
        accepted = terminal["publication"]
        request = _publication_request(accepted["request"], snapshot_path=self.snapshot_path)
        publication = AcceptedPublication(**{**accepted, "request": request})
        if (terminal != self._completion_record(publication)
                or request.key != RowKey(self.store_id, row_id)
                or request.admission.original_operation_id != operation
                or entry.get("publication_intent") != _publication_value(request)):
            raise SourceHold("uif_owner_runtime_completion_binding")
        return DeliveryCompletion(publication, "completed", ref["sha256"])

    def complete_publication(self, publication):
        """Persist one terminal result after observing its original canonical effect."""
        from .native_publisher import durable

        if self.refill is None or not isinstance(publication, AcceptedPublication):
            raise SourceHold("uif_owner_runtime_completion_unregistered")
        publisher = self.refill.publisher
        request = publication.request
        operation = request.admission.original_operation_id
        with publisher.fence(request, "completion") as transaction:
            if publisher.observe_original(request, transaction) != publication:
                raise SourceHold("uif_owner_runtime_completion_readback")
            with self.lock():
                transaction.recheck(request)
                previous = self.read_completion(operation)
                if previous is not None:
                    if previous.publication != publication:
                        raise SourceHold("uif_owner_runtime_completion_drift")
                    return previous

                _, runtime = self._active(request.admission, request.issue, task=getattr(request, "task", None))
                catalogue, digest = self.catalogue()
                entry = catalogue["rows"][request.key.row_id]
                if entry.get("completion") is not None:
                    raise SourceHold("uif_owner_runtime_completion_drift")
                path = exact_path(runtime / "delivery-completion.json")
                record = self._completion_record(publication)
                try:
                    durable(path, record)
                except FileExistsError:
                    # The original receipt may have committed before its catalogue pointer.
                    pass
                stored, receipt_sha = read_owned_record(path)
                if stored != record:
                    raise SourceHold("uif_owner_runtime_completion_drift")
                entry.update(disposition="completed", completion=dict(
                    operation_id=operation, receipt=dict(path=str(path), sha256=receipt_sha)))
                catalogue["refill_from"] = operation
                self._replace_catalogue(catalogue, digest)
                return DeliveryCompletion(publication, "completed", receipt_sha)

    def register_stages(self, validation, review, publisher, *, checkpoints=None):
        from .recovery import Recovery

        if validation.fence != self.stage_fence or review.validation is not validation:
            raise SourceHold("uif_owner_runtime_stage_registration")
        self.register_publisher(publisher)
        return OwnerStages(self.admission, validation, review, publisher,
                           Recovery(publisher, checkpoints), self.complete_publication)

    def register_publisher(self, publisher):
        if publisher.fence != self.publication_fence:
            raise SourceHold("uif_owner_runtime_publication_unregistered")
        self.refill = VerifiedRefill(self.store_id, self.read_completion, publisher)

    def verify_refill(self, operation):
        if self.refill is None:
            raise SourceHold("uif_owner_runtime_completion_unregistered")
        return self.refill.verify_refill(operation)

    def validation_adapter(self, runtime, client, *, workspace, script_path, **options):
        from .validation import StageJournal, ValidationAdapter, WindmillValidationBackend

        runtime = exact_path(runtime)
        if (workspace != "foo" or runtime == self.runtime_root_path
                or not runtime.is_relative_to(self.runtime_root_path)):
            raise SourceHold("uif_owner_runtime_validation_binding")
        return ValidationAdapter(
            StageJournal(runtime / "operations"), self.stage_fence,
            WindmillValidationBackend(client, workspace=workspace, script_path=script_path, **options),
        )

    def _publication_entry(self, request, mode):
        if request.key.store_id != self.store_id or request.admission.combined:
            raise SourceHold("uif_owner_runtime_publication_binding")
        task = getattr(request, "task", None)
        if (request.issue is None) != (task is not None):
            raise SourceHold("uif_owner_runtime_publication_binding")
        if task is not None:
            verify_ledger_task_admission(task, request.admission)
        if mode == "active":
            entry, _ = self._active(request.admission, request.issue, task=task)
            from .native_publisher import verify_record, verify_stopped

            if self.refill is None:
                raise SourceHold("uif_owner_runtime_publication_unregistered")
            reference = entry.get("publication_readiness")
            ready = _record(reference)
            if reference["sha256"] != request.readiness_receipt_sha256:
                raise SourceHold("uif_owner_runtime_publication_readiness")
            inner = {**request.envelope(observe=True)["request"], "mode": "observe"}
            verify_record(ready, inner, request.readiness_invocation_id,
                          self.refill.publisher.command.closure_sha256)
            verify_stopped(ready["terminal"], inner, "ready")
            return entry
        entry, _ = self.admission._entry(request.key)
        if entry.get("publication_intent") != _json_value(_publication_value(request)):
            raise SourceHold("uif_owner_runtime_publication_history")
        return entry

    @contextmanager
    def publication_fence(self, request, mode):
        if mode not in ("active", "recovery", "completion"):
            raise SourceHold("uif_owner_runtime_publication_mode")
        with self.lock(operation=request.admission.original_operation_id):
            transaction = _PublicationTransaction(self, request, mode)
            transaction.recheck(request)
            try:
                yield transaction
            finally:
                transaction.closed = True


class _PublicationTransaction:
    def __init__(self, authority, request, mode):
        self.authority = authority
        self.request = request
        self.mode = mode
        self.closed = False

    def recheck(self, request):
        if self.closed or request != self.request:
            raise SourceHold("uif_owner_runtime_publication_fence")
        self.authority._publication_entry(request, self.mode)

    def read_intent(self, key, original_operation_id):
        request = self.request
        if key != request.key or original_operation_id != request.admission.original_operation_id:
            raise SourceHold("uif_owner_runtime_publication_history")
        self.recheck(request)
        entry, _ = self.authority.admission._entry(key)
        record = entry.get("publication_intent")
        return None if record is None else _publication_request(record, snapshot_path=self.authority.snapshot_path)

    def create_intent(self, request):
        self.recheck(request)
        if self.mode != "active" or self.read_intent(request.key, request.admission.original_operation_id) is not None:
            raise SourceHold("uif_owner_runtime_publication_history")
        with self.authority.lock():
            self.recheck(request)
            catalogue, digest = self.authority.catalogue()
            rows = catalogue["rows"]
            if any(isinstance(record := row.get("publication_intent"), dict)
                   and (record.get("publisher_operation_id") == request.publisher_operation_id
                        or record.get("invocation_id") == request.invocation_id) for row in rows.values()):
                raise SourceHold("uif_owner_runtime_publication_duplicate")
            rows[request.key.row_id]["publication_intent"] = _json_value(_publication_value(request))
            self.authority._replace_catalogue(catalogue, digest)


@dataclass(frozen=True)
class LedgerIntake:
    select_ready: Callable
    claim: Callable
    ensure_workspace: Callable


def bind_owner_coordinator(authority, orchestrator, workspace_module, owner, *,
                           supervisor=None, supervisor_sha256=None, intake="linear"):
    """Bind explicit ledger intake or the dedicated coordinator's issue hooks."""
    if getattr(owner, "_uif_owner_authority", None) is not None:
        raise SourceHold("uif_owner_runtime_already_registered")
    if intake == "ledger":
        hooks = LedgerIntake(authority.select_ledger_tasks, authority.claim_ledger_task,
                             authority.ensure_ledger_workspace)
        owner._uif_owner_authority = authority
        return hooks
    if intake != "linear":
        raise SourceHold("uif_owner_runtime_intake_binding")
    ensure_client = owner._ensure_linear_client
    original_dispatch = owner._dispatch
    candidate_fetches = {}

    def previous_operation():
        return authority.catalogue()[0].get("refill_from")

    def candidate_issue(issue):
        return PolledIssue(issue.identifier, issue.id, UIF.project_id, UIF.project_slug)

    def eligible(previous):
        result = authority.admission.select_ready(previous_operation_id=previous)
        matches = {}
        duplicates = set()
        for row in result.eligible:
            if row.issue is None or row.counterparts != ():
                continue
            if row.issue in matches:
                duplicates.add(row.issue)
            matches[row.issue] = row.key.row_id
        return {issue: row for issue, row in matches.items() if issue not in duplicates}

    def current_or_none(issue):
        try:
            context = authority.admission.context(issue)
        except (SourceHold, FileNotFoundError):
            return None
        return context if context.ledger.combined is False else None

    def fetch_client():
        client = ensure_client()
        if id(client) in candidate_fetches:
            return client
        original_fetch = client.fetch_candidate_issues

        async def fetch(project_slug, states):
            if project_slug != UIF.project_slug:
                raise SourceHold("uif_owner_runtime_issue_binding")
            observed = await original_fetch(project_slug, states)
            ready = eligible(previous_operation())
            return [issue for issue in observed if candidate_issue(issue) in ready
                    or current_or_none(candidate_issue(issue)) is not None]

        client.fetch_candidate_issues = fetch
        candidate_fetches[id(client)] = client
        return client

    def dispatch(issue, attempt_num=None):
        selected = candidate_issue(issue)
        try:
            if current_or_none(selected) is None:
                previous = previous_operation()
                row_id = eligible(previous).get(selected)
                if row_id is None:
                    raise SourceHold("uif_owner_runtime_pickup_held")
                authority.claim(row_id, selected, previous_operation_id=previous)
            original_dispatch(issue, attempt_num=attempt_num)
        except (SourceHold, FileNotFoundError) as error:
            owner._release_slot(issue.id)
            owner.claimed.discard(issue.id)
            logging.getLogger(__name__).warning("UIF owner admission held: %s", type(error).__name__)

    backend = authority.admission.bind_dispatch(
        orchestrator, workspace_module, owner, coordinator_runtime=authority.coordinator_runtime_path,
        selected_source=authority.selected_source, polled_issue=authority.polled_issue,
        report_attempt=authority.report_attempt, supervisor=supervisor, supervisor_sha256=supervisor_sha256,
    )
    owner._ensure_linear_client = fetch_client
    owner._dispatch = dispatch
    owner._uif_owner_authority = authority
    return backend
