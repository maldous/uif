"""Drive the registered UIF ledger lifecycle without a second work store."""

import argparse
import asyncio
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
import fcntl
from hashlib import sha256
import logging
import math
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
from types import ModuleType

from .admission import OwnerStages, _record
from .dispatch import DispatchBackend, DispatchOutcome, read_outcome
from .ledger import RowKey
from .owner_runtime import OwnerAuthority, bind_owner_coordinator
from .publication import PublicationRequest, Publisher, PublisherCommand
from .recovery import CheckpointPin, LangGraphCheckpoint
from .review import NativeReviewBackend, ReviewAdapter, ReviewEvidence, ReviewSpec
from .source import SourceHold, exact_path, file_digest
from .stokowski import DispatchReportBinding, LEDGER_DISPATCH_REPORT
from .task_identity import LedgerTaskEnvelope, LedgerTaskKey
from .validation import ValidationAdapter, ValidationEvidence, ValidationSpec, WindmillValidationBackend


REVIEW_DRAIN_SECONDS = 630


@dataclass(frozen=True)
class OperationPlan:
    """Registered adapters and exact original stage inputs for one runtime."""

    validation: ValidationAdapter
    review: ReviewAdapter
    validation_spec: Callable[[DispatchReportBinding, DispatchOutcome], ValidationSpec]
    review_spec: Callable[[ValidationSpec, ValidationEvidence], ReviewSpec]
    publication_request: Callable[[ReviewSpec, ValidationEvidence, ReviewEvidence, DispatchOutcome], PublicationRequest]
    checkpoint: CheckpointPin | None = None


@dataclass(frozen=True)
class CoordinatorBindings:
    supervisor: Path
    supervisor_sha256: str
    publisher: Publisher
    plan_for: Callable[[LedgerTaskKey, Path], OperationPlan]
    checkpoints: LangGraphCheckpoint | None = None


@dataclass(frozen=True)
class Progress:
    status: str
    row_id: str | None = None


@dataclass(frozen=True)
class _Original:
    key: LedgerTaskKey
    entry: dict | None = None
    runtime: Path | None = None
    task: LedgerTaskEnvelope | None = None
    stages: tuple[OperationPlan, OwnerStages] | None = None


class LedgerCoordinator:
    """Concurrent originals through the existing owner, stage and Pool fences."""

    def __init__(self, authority, bindings):
        if (not isinstance(authority, OwnerAuthority) or not isinstance(bindings, CoordinatorBindings)
                or not isinstance(bindings.publisher, Publisher) or not callable(bindings.plan_for)
                or not callable(getattr(bindings.publisher.command, "call", None))
                or not callable(getattr(bindings.publisher.remote, "verify", None))
                or (bindings.checkpoints is not None and not isinstance(bindings.checkpoints, LangGraphCheckpoint))
                or file_digest(bindings.supervisor) != bindings.supervisor_sha256):
            raise SourceHold("uif_ledger_coordinator_registration")
        self.authority = authority
        self.bindings = bindings
        command = bindings.publisher.command
        if isinstance(command, PublisherCommand) and file_digest(command.executable) != command.executable_sha256:
            raise SourceHold("uif_ledger_coordinator_publisher_pin")
        self.dispatch = DispatchBackend.for_ledger(
            authority, supervisor=bindings.supervisor, supervisor_sha256=bindings.supervisor_sha256)
        self.intake = bind_owner_coordinator(authority, None, None, self, intake="ledger")
        authority.register_publisher(bindings.publisher)
        self._review_waiters = set()

    def _plan(self, key, runtime):
        plan = self.bindings.plan_for(key, runtime)
        if (not isinstance(plan, OperationPlan) or not isinstance(plan.validation, ValidationAdapter)
                or not isinstance(plan.review, ReviewAdapter)
                or not isinstance(plan.validation.backend, WindmillValidationBackend)
                or plan.validation.backend.registration.get("workspace") != "foo"
                or not all(callable(getattr(plan.validation.backend.client, method, None))
                           for method in ("get", "post"))
                or not isinstance(plan.review.backend, NativeReviewBackend)
                or plan.review.backend.pool_database != self.authority.pool_database_path
                or file_digest(plan.review.backend.executable) != plan.review.backend.executable_sha256
                or plan.validation.journal.root != runtime / "operations"
                or plan.review.journal.root != runtime / "operations"
                or plan.review.validation is not plan.validation
                or plan.validation.fence != self.authority.stage_fence
                or plan.review.fence != self.authority.stage_fence
                or not all(callable(value) for value in
                           (plan.validation_spec, plan.review_spec, plan.publication_request))
                or (plan.checkpoint is not None and (not isinstance(plan.checkpoint, CheckpointPin)
                                                     or self.bindings.checkpoints is None))):
            raise SourceHold("uif_ledger_coordinator_stage_registration")
        return plan

    def _register_plan(self, plan):
        stages = self.authority.register_stages(
            plan.validation, plan.review, self.bindings.publisher, checkpoints=self.bindings.checkpoints)
        return plan, stages

    def _claimed(self, key, entry, runtime):
        claim = self.authority.admission._claim(RowKey(key.store_id, key.row_id), entry, runtime, None)
        if claim is None:
            raise SourceHold("uif_ledger_coordinator_claim_missing")
        return LedgerTaskEnvelope.from_record(claim["ledger"], snapshot_path=self.authority.snapshot_path)

    def _read_dispatch(self, task, reference):
        record = _record(reference)
        report = DispatchReportBinding.from_ledger_record({
            "schema": LEDGER_DISPATCH_REPORT, "task": record["task"], "attempt_number": record["attempt"],
            "workspace": record["workspace"], "operation_id": record["operation_id"],
            "receipt_path": reference["path"], "receipt_sha256": reference["sha256"],
            "ledger": record["admission"],
        }, snapshot_path=self.authority.snapshot_path)
        if report.task != task:
            raise SourceHold("uif_ledger_coordinator_dispatch_binding")
        with self.authority.dispatch_fence(None, report.ledger, task=task):
            self.authority.admission.ledger.verify_dispatch(
                report.ledger, None, operation_id=report.operation_id,
                receipt_path=report.receipt_path, receipt_sha256=report.receipt_sha256, task=task)
            return report, read_outcome(report)

    async def _coding(self, task, entry, runtime, stop):
        if entry.get("dispatch_intent") is not None:
            report, outcome = self._read_dispatch(task, entry["dispatch_intent"])
        else:
            request, workspace = self._coding_inputs(task.key, entry, runtime)
            acquired = self.intake.ensure_workspace(task, self.authority.coordinator_runtime_path / "workspaces")
            if acquired.path != workspace or acquired.identifier != task.key.workspace_key:
                raise SourceHold("uif_ledger_coordinator_workspace")
            if stop.is_set():
                return None
            report, outcome = await self.dispatch.owned_ledger_turn(
                task, workspace=workspace, prompt=request["prompt"], timeout=request["timeout_seconds"])
        await self.authority.report_attempt(report, outcome)
        return report, outcome

    @staticmethod
    def _coding_inputs(key, entry, runtime):
        inputs = _record(entry.get("admission_inputs"))
        path = runtime / "requests" / (key.workspace_key + ".json")
        request = _record(dict(path=str(path), sha256=inputs.get("request_sha256")))
        if (set(request) != {"prompt", "timeout_seconds", "ledger_task"}
                or not isinstance(request["prompt"], str) or not request["prompt"].strip()
                or type(request["timeout_seconds"]) is not int or not 1 <= request["timeout_seconds"] <= 600):
            raise SourceHold("uif_ledger_coordinator_coding_request")
        return request, runtime / "workspaces" / key.workspace_key

    def _completion(self, task, publication):
        completed = self.authority.read_completion(task.original_operation_id)
        if completed is None or completed.publication != publication or completed.outcome != "completed":
            raise SourceHold("uif_ledger_coordinator_completion")
        return Progress("completed", task.key.row_id)

    async def _advance(self, task, entry, runtime, plan, stages, stop):
        row_id = task.key.row_id
        if entry.get("publication_intent") is not None:
            request = PublicationRequest.from_ledger_record(
                entry["publication_intent"], snapshot_path=self.authority.snapshot_path)
            if request.task != task:
                raise SourceHold("uif_ledger_coordinator_publication_binding")
            if stop.is_set():
                return Progress("stopped", row_id)
            recovery = await asyncio.to_thread(stages.recover_delivery, request, plan.checkpoint)
            return self._completion(task, recovery.publication)

        if stop.is_set():
            return Progress("stopped", row_id)
        coding = await self._coding(task, entry, runtime, stop)
        if coding is None or stop.is_set():
            return Progress("stopped", row_id)
        report, outcome = coding
        if outcome.status != "succeeded":
            return Progress("coding_" + outcome.status, row_id)

        validation = plan.validation_spec(report, outcome)
        if (not isinstance(validation, ValidationSpec) or validation.binding.dispatch != report
                or (validation.binding.coding_receipt, validation.binding.coding_receipt_sha256)
                != (outcome.worker_receipt_path, outcome.worker_receipt_sha256)):
            raise SourceHold("uif_ledger_coordinator_validation_binding")
        if stop.is_set():
            return Progress("stopped", row_id)
        result = await stages.validate(validation)
        if result.status != "succeeded":
            return Progress("validation_" + result.status, row_id)
        validated = await stages.validation.accepted(validation)
        if stop.is_set():
            return Progress("stopped", row_id)

        review = plan.review_spec(validation, validated)
        if (not isinstance(review, ReviewSpec) or review.validation != validation
                or review.validation_receipt_sha256 != validated.receipt_sha256):
            raise SourceHold("uif_ledger_coordinator_review_binding")
        if stop.is_set():
            return Progress("stopped", row_id)
        try:
            result = await stages.review_candidate(review)
        finally:
            # Submission retains the launcher wait task separately from the stage.
            # Keep it even when a later scan constructs a different row plan.
            for waiter in plan.review.backend._children - self._review_waiters:
                self._review_waiters.add(waiter)
                waiter.add_done_callback(self._review_waiters.discard)
        if result.status != "succeeded":
            return Progress("review_" + result.status, row_id)
        reviewed = await stages.review.accepted(review)
        if stop.is_set():
            return Progress("stopped", row_id)

        request = plan.publication_request(review, validated, reviewed, outcome)
        if not isinstance(request, PublicationRequest) or request.task != task:
            raise SourceHold("uif_ledger_coordinator_publication_binding")
        if stop.is_set():
            return Progress("stopped", row_id)
        return self._completion(task, await stages.publish(request, review))

    async def tick(self, stop):
        originals, progress = self._schedule(stop, {})
        if progress is not None and not originals:
            return progress
        results = await asyncio.gather(*(self._run_original(original, stop) for original in originals),
                                       return_exceptions=True)
        if len(results) == 1 and isinstance(results[0], BaseException):
            raise results[0]
        for index, result in enumerate(results):
            if isinstance(result, (SourceHold, OSError)):
                results[index] = Progress("held", originals[index].key.row_id)
            elif isinstance(result, BaseException):
                raise result
        return results[0] if len(results) == 1 else tuple(results)

    @contextmanager
    def _admission_fence(self):
        # Serialize selection and claims, then release before any original executes.
        self.authority.guard()
        path = self.authority.coordinator_runtime_path
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SourceHold("uif_ledger_coordinator_busy") from None
            self.authority.guard()
            opened, current = os.fstat(fd), path.lstat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise SourceHold("uif_ledger_coordinator_runtime_drift")
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _capacity_limit(runtime, entry):
        inputs = _record(entry.get("admission_inputs"))
        policy = _record(dict(path=str(runtime / "capacity-policy.json"), sha256=inputs.get("policy_sha256")))
        symphony = policy.get("symphony")
        limit = symphony.get("max_concurrent_agents") if isinstance(symphony, dict) else None
        if type(limit) is not int or not 1 <= limit <= 64:
            raise SourceHold("uif_ledger_coordinator_pool_limit")
        return limit

    def _pool_has_slot(self, limit, active_count):
        if active_count >= limit:
            return False
        path = exact_path(self.authority.pool_database_path)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise SourceHold("uif_ledger_coordinator_pool_custody")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                occupied, = db.execute(
                    "SELECT COUNT(*) FROM leases WHERE status IN ('running','cancel_requested','uncertain')"
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceHold("uif_ledger_coordinator_pool_unavailable") from error
        # Advisory pacing only. The supervisor admits the exact lease and resources atomically.
        return occupied < limit

    def _schedule(self, stop, pending):
        if stop.is_set() or os.path.lexists(self.authority.coordinator_runtime_path / "cancel.json"):
            return (), Progress("stopped")
        with self._admission_fence():
            return self._select_originals(stop, pending)

    def _select_originals(self, stop, pending):
        catalogue, _ = self.authority.catalogue()
        if any(not isinstance(entry, dict) for entry in catalogue["rows"].values()):
            raise SourceHold("uif_ledger_coordinator_catalogue_shape")
        active = {row_id for row_id, entry in catalogue["rows"].items()
                  if entry.get("disposition") == "claimed" and entry.get("issue") is None
                  and entry.get("counterparts") == []}
        originals = []
        for row_id in sorted(active - pending.keys())[:max(0, 64 - len(pending))]:
            originals.append(_Original(LedgerTaskKey(self.authority.store_id, row_id)))
        try:
            new = self._admit_one(stop, active | pending.keys(), catalogue["rows"])
        except (SourceHold, OSError) as error:
            if not originals and not pending:
                raise
            logging.getLogger(__name__).warning("UIF ledger intake held (%s)", type(error).__name__)
            new = None
        if new is not None:
            originals.append(new)
        return originals, Progress("busy" if pending else "idle")

    def _admit_one(self, stop, active, rows):
        if stop.is_set() or len(active) >= 64:
            return None
        # A claim can write its receipt before the catalogue update. The owner
        # selector holds that row; fence replacement admission until it is reconciled.
        for row_id, entry in rows.items():
            if (entry.get("disposition") == "ready" and entry.get("issue") is None
                    and entry.get("counterparts") == [] and entry.get("claim") is None):
                _, runtime = self.authority.admission._entry(RowKey(self.authority.store_id, row_id), rows)
                try:
                    (runtime / "current-admission.json").lstat()
                except FileNotFoundError:
                    continue
                raise SourceHold("uif_ledger_coordinator_claim_unreconciled")
        selection = self.intake.select_ready()
        held = None
        for selected in selection.eligible:
            key = LedgerTaskKey(selected.key.store_id, selected.key.row_id)
            entry, runtime = self.authority.admission._entry(selected.key)
            try:
                limit = self._capacity_limit(runtime, entry)
            except (SourceHold, OSError) as error:
                if held is None:
                    held = error
                continue
            if not self._pool_has_slot(limit, len(active)):
                return None
            try:
                self._coding_inputs(key, entry, runtime)
                plan = self._plan(key, runtime)
            except (SourceHold, FileNotFoundError) as error:
                if held is None:
                    held = error
                continue
            stages = self._register_plan(plan)
            if stop.is_set():
                return None
            task = self.intake.claim(key)
            entry, claimed_runtime = self.authority.admission._entry(selected.key)
            if claimed_runtime != runtime:
                raise SourceHold("uif_ledger_coordinator_runtime_drift")
            return _Original(key, entry, runtime, task, stages)
        if held is not None:
            raise held
        return None

    async def _run_original(self, original, stop):
        key, entry, runtime = original.key, original.entry, original.runtime
        if entry is None:
            entry, runtime = self.authority.admission._entry(RowKey(key.store_id, key.row_id))
        task = original.task or self._claimed(key, entry, runtime)
        plan, stages = original.stages or self._register_plan(self._plan(key, runtime))
        return await self._advance(task, entry, runtime, plan, stages, stop)

    async def run(self, stop, *, interval_seconds=5):
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise SourceHold("uif_ledger_coordinator_interval")
        logger = logging.getLogger(__name__)
        pending, previous = {}, {}

        def report(row_id, work):
            try:
                progress = work.result()
            except (SourceHold, OSError) as error:
                progress = Progress("held", row_id)
                if progress != previous.get(row_id):
                    logger.warning("UIF ledger held %s (%s)", row_id, type(error).__name__)
            else:
                if progress != previous.get(row_id):
                    logger.info("UIF ledger %s %s", progress.status, row_id)
            previous[row_id] = progress

        try:
            while not stop.is_set():
                for row_id in tuple(pending):
                    if pending[row_id].done():
                        report(row_id, pending.pop(row_id))
                try:
                    originals, _ = self._schedule(stop, pending)
                except (SourceHold, OSError) as error:
                    logger.warning("UIF ledger selection held (%s)", type(error).__name__)
                else:
                    for original in originals:
                        pending[original.key.row_id] = asyncio.create_task(self._run_original(original, stop))
                try:
                    await asyncio.wait_for(stop.wait(), interval_seconds)
                except TimeoutError:
                    pass
        finally:
            stop.set()
            await asyncio.gather(*pending.values(), return_exceptions=True)
            if self._review_waiters:
                _, unsettled = await asyncio.wait(self._review_waiters, timeout=REVIEW_DRAIN_SECONDS)
                if unsettled:
                    logger.error("UIF ledger shutdown held: %d review launchers still pending", len(unsettled))
                    raise SourceHold("uif_ledger_coordinator_review_drain_timeout")
            for row_id, work in pending.items():
                report(row_id, work)


def load_coordinator(owner_path, owner_sha256, bindings_path, bindings_sha256):
    """Load the owner and a release-pinned adapter composition supplied by its installer."""
    authority = OwnerAuthority.load(owner_path, owner_sha256)
    path = exact_path(bindings_path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022
                or info.st_uid not in (0, os.geteuid())):
            raise SourceHold("uif_ledger_coordinator_bindings_custody")
        raw = stream.read()
    if sha256(raw).hexdigest() != bindings_sha256:
        raise SourceHold("uif_ledger_coordinator_bindings_pin")
    module = ModuleType("_uif_ledger_bindings_" + bindings_sha256)
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, str(path), "exec", dont_inherit=True), vars(module))
        factory = getattr(module, "bind_ledger_coordinator", None)
        if not callable(factory):
            raise SourceHold("uif_ledger_coordinator_bindings_factory")
        return LedgerCoordinator(authority, factory(authority))
    except Exception:
        sys.modules.pop(module.__name__, None)
        raise


async def _serve(coordinator, interval_seconds):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for signum in signals:
        loop.add_signal_handler(signum, stop.set)
    try:
        await coordinator.run(stop, interval_seconds=interval_seconds)
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, type=Path)
    parser.add_argument("--owner-sha256", required=True)
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--bindings-sha256", required=True)
    parser.add_argument("--interval-seconds", default=5, type=float)
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval_seconds) or args.interval_seconds <= 0:
        parser.error("interval must be positive and finite")
    logging.basicConfig(level=logging.INFO)
    try:
        coordinator = load_coordinator(args.owner, args.owner_sha256, args.bindings, args.bindings_sha256)
        asyncio.run(_serve(coordinator, args.interval_seconds))
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as error:
        logging.getLogger(__name__).error("UIF ledger startup held (%s)", type(error).__name__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
