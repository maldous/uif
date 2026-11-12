"""Inspect original publisher effects and compatible LangGraph checkpoints."""

from collections.abc import Mapping
from dataclasses import dataclass

from .publication import AcceptedPublication, PublicationRequest, Publisher, digest, identifier, require
from .source import SourceHold


@dataclass(frozen=True)
class CheckpointPin:
    """Current graph registration and exact checkpoint selected by the owner."""

    thread_id: str
    namespace: str
    checkpoint_id: str
    run_id: str
    graph_sha256: str
    schema_sha256: str
    code_sha256: str
    completed: tuple[str, ...]
    pending: tuple[str, ...]
    receipt_sha256: str
    wire_version: int

    def __post_init__(self):
        require(all(identifier(val) for val in (self.thread_id, self.checkpoint_id, self.run_id))
                and isinstance(self.namespace, str)
                and all(digest(val) for val in (self.graph_sha256, self.schema_sha256,
                                               self.code_sha256, self.receipt_sha256))
                and type(self.wire_version) is int and self.wire_version > 0,
                "uif_checkpoint_pins")
        require(isinstance(self.completed, tuple) and isinstance(self.pending, tuple)
                and all(identifier(step) for step in self.completed + self.pending)
                and len(set(self.completed + self.pending)) == len(self.completed + self.pending),
                "uif_checkpoint_steps")

    def metadata(self, request, accepted):
        claim = request.admission
        return {
            "run_id": self.run_id, "graph_sha256": self.graph_sha256,
            "schema_sha256": self.schema_sha256, "code_sha256": self.code_sha256,
            "store_id": claim.store_id, "row_id": claim.row_id,
            "snapshot_sha256": claim.snapshot_sha256, "row_source_sha256": claim.source_sha256,
            "owner": claim.owner, "epoch": claim.epoch, "issue_id": request.issue.issue_id,
            "branch": request.source_branch, "head": request.source_head, "tree": request.source_tree,
            "accepted_head": accepted.head, "original_operation_id": claim.original_operation_id,
            "publisher_operation_id": request.publisher_operation_id, "invocation_id": request.invocation_id,
            "invocation_receipt_sha256": accepted.invocation_receipt_sha256,
            "accepted_source_sha256": accepted.accepted_source_sha256,
            "validation_operation_id": request.validation_operation_id,
            "validation_job_id": request.validation_job_id,
            "validation_receipt_sha256": request.validation_receipt_sha256,
            "capacity_lease_id": request.capacity_lease_id,
            "review_operation_id": request.review_operation_id, "reviewer": request.reviewer,
            "review_receipt_sha256": request.review_receipt_sha256,
            "receipt_sha256": self.receipt_sha256,
            "completed": list(self.completed), "pending": list(self.pending),
        }


@dataclass(frozen=True)
class LangGraphCheckpoint:
    """Read through the selected checkpointer's existing get_tuple interface.

    Supply an owner-scoped read-only saver and current trusted checkpoint pins.
    This method reads the latest tuple for the thread/namespace. Asking for the
    historical checkpoint by ID could accidentally accept a superseded tuple.
    """

    saver: object
    graph_sha256: str
    schema_sha256: str
    code_sha256: str
    wire_version: int

    def verify(self, pin, accepted):
        require((pin.graph_sha256, pin.schema_sha256, pin.code_sha256, pin.wire_version) ==
                (self.graph_sha256, self.schema_sha256, self.code_sha256, self.wire_version),
                "uif_checkpoint_incompatible")
        config = {"thread_id": pin.thread_id, "checkpoint_ns": pin.namespace}
        try:
            record = self.saver.get_tuple({"configurable": config})
            expected = {**config, "checkpoint_id": pin.checkpoint_id}
            require(record is not None and isinstance(record.metadata, Mapping)
                    and all(record.config["configurable"][key] == val for key, val in expected.items())
                    and record.checkpoint["id"] == pin.checkpoint_id
                    and type(record.checkpoint["v"]) is int
                    and record.checkpoint["v"] == pin.wire_version
                    and record.metadata.get("uif_recovery") == pin.metadata(accepted.request, accepted)
                    and record.pending_writes == [], "uif_checkpoint_incompatible")
        except (AttributeError, KeyError, TypeError) as error:
            raise SourceHold("uif_checkpoint_unreadable") from error
        return pin


@dataclass(frozen=True)
class RecoveryResult:
    publication: AcceptedPublication
    checkpoint: CheckpointPin | None


@dataclass(frozen=True)
class Recovery:
    publisher: Publisher
    checkpoints: LangGraphCheckpoint | None = None

    def observe(self, request: PublicationRequest, checkpoint: CheckpointPin | None = None) -> RecoveryResult:
        """Reconcile by original receipt under the active owner fence.

        A successful result provides evidence only. The owner retains the
        transaction that consumes a checkpoint and schedules its next step.
        """
        with self.publisher.fence(request, "recovery") as owner:
            publication = self.publisher.observe_original(request, owner)
            if checkpoint is not None:
                require(self.checkpoints is not None, "uif_checkpoint_reader_missing")
                self.checkpoints.verify(checkpoint, publication)
            owner.recheck(request)
            return RecoveryResult(publication, checkpoint)
